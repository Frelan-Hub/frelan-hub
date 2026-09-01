"""Interpretation Layer — the Prompt Renderer.

Pure functions that build Markdown prompts from the immutable contract plus the
current runtime state and ledger. Nothing here mutates state or performs I/O:
given the same instance, each function returns the same string. That keeps the
renderer trivially testable and keeps prompt wording out of the interpreter.
"""

from __future__ import annotations

from pathlib import Path

from frelan.deliverables import render_output_instructions, render_output_request
from frelan.enums import LedgerEntryType
from frelan.mission_instance import MissionInstance

# -- Context budgets ------------------------------------------------------
#
# These are sized against what the delivery layer can actually put into a
# composer, NOT against what a model could read. The renderer must not import a
# transport (layer isolation), so the number is restated here as a documented
# assumption: the smallest composer any shipped adapter supports is ~9,000
# chars (ChatGPT, deliberately under OpenAI's 10k paste-to-attachment line —
# see frelan/transport/adapters.py, which stays authoritative).
#
# The previous budgets allowed ~100k chars per prompt against that 9k ceiling,
# so every turn from round 2 onward overflowed and had to be delivered as a
# file attachment. Measured on the 2026-08-19 general_inquiry run: prompts grew
# 1.5k -> 35k and 8 of 10 turns spilled. Budgets now target:
#
#     boilerplate (~1,500) + transcript (<=7,000) = ~8,500 < 9,000
#
# so a mission with no injected reference files stays inline on every turn.
# Nothing is lost by trimming here: each engine's own conversation already
# holds the earlier exchange (see render_turn_prompt).
#
# Window size is one full round of peers rather than a fixed 10 — a fixed count
# scaled the prompt with participant count for no benefit.
RECENT_CONTEXT_LIMIT = 4
# Per-response cap. Deep-research turns run 8-13k chars each; embedding several
# of them uncapped is what produced 134k-char prompts. Newest responses win;
# anything dropped is announced in the block, never silently omitted.
MAX_EMBED_RESPONSE_CHARS = 2_500
RECENT_CONTEXT_CHAR_BUDGET = 7_000

# Reference files are inlined into EVERY prompt, so an unbounded set compounds:
# each harvested artifact or downloaded file permanently enlarges every
# subsequent turn.
MAX_EMBED_FILE_CHARS = 6_000
REFERENCE_FILES_CHAR_BUDGET = 12_000
# The synthesis turn is the one that should see the whole mission, so it gets a
# wider window than an ordinary turn — still bounded, never unlimited. This one
# turn may still exceed a composer and be delivered as an attachment; that is an
# accepted cost for the final synthesis alone, not for every turn.
SYNTHESIS_CONTEXT_CHAR_BUDGET = 30_000
# ...and a wider per-response cap inside that window. At the turn-prompt cap of
# 2,500 the synthesiser was writing from stubs: on the 2026-08-19 run the
# consolidation turns it was supposed to synthesise ran 24-25k chars each, so
# roughly 90% of the substance was cut before it ever arrived. The total stays
# bounded by SYNTHESIS_CONTEXT_CHAR_BUDGET; what changes is that fewer, fuller
# responses survive instead of many truncated ones — and newest-first budgeting
# means the survivors are the converged ones.
SYNTHESIS_MAX_EMBED_RESPONSE_CHARS = 6_000

# A participant's standing brief (``participants[].instructions``) is rendered
# into every one of its prompts — that is what "standing" means — so it is
# budgeted like everything else that repeats. Kept small deliberately: a brief
# that needs more than this is a reference file, not a brief.
PARTICIPANT_BRIEF_CHAR_BUDGET = 1_500


def _round_window(instance: MissionInstance) -> int:
    """How many recent responses a turn prompt embeds: one full round of peers.

    Bounded below by 2 so a two-participant mission still sees an exchange, and
    above by ``RECENT_CONTEXT_LIMIT`` so a large panel cannot re-inflate the
    prompt the budgets exist to cap.
    """
    return max(2, min(len(instance.mission.participants), RECENT_CONTEXT_LIMIT))

_TRUNCATION_MARK = "\n[... truncated for context length ...]"


def _recent_exchange(
    instance: MissionInstance,
    limit: int | None = None,
    budget: int = RECENT_CONTEXT_CHAR_BUDGET,
    embed_cap: int = MAX_EMBED_RESPONSE_CHARS,
) -> str:
    """The embedded discussion window: the newest responses that fit ``budget``.

    ``limit`` defaults to one round of peers (``_round_window``) rather than a
    fixed count, so the window tracks the panel instead of the transcript.
    ``embed_cap`` is the per-response ceiling; the synthesis turn raises it.
    """
    if limit is None:
        limit = _round_window(instance)
    all_responses = [
        e
        for e in instance.ledger.entries
        if e.entry_type is LedgerEntryType.RESPONSE
    ]
    responses = all_responses[-limit:]
    if not responses:
        return "_No prior responses yet — you are opening the discussion._"

    # Budget newest-first so the freshest exchange always survives; older
    # responses fall off once the budget is spent. Responses trimmed by the
    # window are counted too: dropping silently would break the invariant that
    # a participant is always told when it is seeing a partial discussion.
    blocks, budget_omitted = _render_response_blocks(responses, budget, embed_cap)
    omitted = (len(all_responses) - len(responses)) + budget_omitted
    if omitted:
        blocks.insert(
            0,
            f"_[{omitted} earlier response(s) omitted to fit the context budget. "
            "They were delivered to you earlier in this conversation — scroll "
            "back rather than asking for them to be resent.]_",
        )
    return "\n\n".join(blocks)


def _last_prompt_for(instance: MissionInstance, participant_id: str) -> tuple[int, str]:
    """``(index, text)`` of a participant's most recent PROMPT entry.

    ``(-1, "")`` when it has not spoken yet. This is the whole basis of delta
    prompting and send-once references: the ledger already records exactly what
    each engine was told, so "what does it not know yet" is a query, not state
    the runtime has to maintain.
    """
    for i in range(len(instance.ledger.entries) - 1, -1, -1):
        entry = instance.ledger.entries[i]
        if (
            entry.entry_type is LedgerEntryType.PROMPT
            and entry.participant_id == participant_id
        ):
            return i, entry.content or ""
    return -1, ""


def _unseen_responses(instance: MissionInstance, participant_id: str) -> list:
    """Responses appended since this participant's last prompt, excluding its own.

    Its own turn and everything before it are in its browser conversation
    already — that conversation IS the context store, so re-sending it grew
    every prompt without adding anything the engine did not have.
    """
    index, _ = _last_prompt_for(instance, participant_id)
    return [
        e
        for e in instance.ledger.entries[index + 1 :]
        if e.entry_type is LedgerEntryType.RESPONSE
        and e.participant_id != participant_id
    ]


def _render_response_blocks(
    entries: list,
    budget: int,
    embed_cap: int = MAX_EMBED_RESPONSE_CHARS,
) -> tuple[list[str], int]:
    """Newest-first budgeting shared by the full window and the delta window."""
    blocks: list[str] = []
    total = 0
    omitted = 0
    for entry in reversed(entries):
        who = entry.participant_id or "unknown"
        role = f" ({entry.role})" if entry.role else ""
        content = (entry.content or "").strip()
        if len(content) > embed_cap:
            content = content[:embed_cap] + _TRUNCATION_MARK
        block = f"**{who}{role}:**\n{content}"
        if blocks and total + len(block) > budget:
            omitted += 1
            continue
        blocks.append(block)
        total += len(block)
    blocks.reverse()
    return blocks, omitted


def _delta_exchange(instance: MissionInstance, participant_id: str) -> str:
    """The "since your last turn" window: only what this engine has not seen."""
    entries = _unseen_responses(instance, participant_id)
    if not entries:
        return (
            "_Nothing new since your last turn — continue from where the "
            "discussion stood._"
        )
    blocks, omitted = _render_response_blocks(entries, RECENT_CONTEXT_CHAR_BUDGET)
    if omitted:
        blocks.insert(
            0,
            f"_[{omitted} response(s) omitted to fit the context budget.]_",
        )
    return "\n\n".join(blocks)


def _reference_files_section(
    instance: MissionInstance, already_delivered: str = ""
) -> list[str]:
    """The Reference Files block, bounded by a total character budget.

    Insertion order is preserved and honoured as priority: the Founder's own
    startup files are registered first and therefore survive, while artifacts
    generated mid-discussion fill whatever budget remains. Files that do not fit
    are named but not inlined — a silently vanishing reference is worse than a
    visible one the participant can ask about.

    ``already_delivered`` is this participant's previous prompt. A file whose
    name appears there was inlined for it once already and is now referenced by
    name only: re-inlining the same body into every subsequent prompt is what
    made a single injected file enlarge the whole rest of the mission.
    """
    files = instance.context.get("injected_files") or {}
    if not files:
        return []

    parts = ["", "## Reference Files"]
    spent = 0
    omitted: list[str] = []
    resent: list[str] = []
    for filepath, content in files.items():
        name = Path(filepath).name
        if name and name in already_delivered:
            resent.append(name)
            continue
        body = (content or "").strip()
        if len(body) > MAX_EMBED_FILE_CHARS:
            body = body[:MAX_EMBED_FILE_CHARS] + _TRUNCATION_MARK
        if spent and spent + len(body) > REFERENCE_FILES_CHAR_BUDGET:
            omitted.append(name)
            continue
        spent += len(body)
        parts += [
            "",
            f"### File: {name} ({filepath})",
            "```",
            body,
            "```",
        ]
    if resent:
        parts += [
            "",
            f"_[Already provided to you earlier in this conversation: "
            f"{', '.join(resent)}. Scroll back for the contents; they have not "
            f"changed.]_",
        ]
    if omitted:
        parts += [
            "",
            f"_[{len(omitted)} reference file(s) not inlined to fit the context "
            f"budget: {', '.join(omitted)}. Ask for one if you need it.]_",
        ]
    return parts


def _reference_images_section(instance: MissionInstance) -> list[str]:
    """The Reference Images block (paths/descriptions only — never inlined)."""
    images = instance.context.get("injected_images") or []
    if not images:
        return []
    parts = ["", "## Reference Images"]
    for img in images:
        parts += ["", f"- **Image Ref:** `{img}` (Multi-modal reference context)"]
    return parts


def _standing_brief_section(participant) -> list[str]:
    """A participant's own standing brief, if the contract gives it one.

    Distinct from the phase's ``instructions``, which apply to everybody in the
    phase: this applies to this participant in every phase, and is what lets a
    contract seat two agents on the same engine with different working
    identities. Absent for a plain model participant, which is the default.
    """
    brief = (getattr(participant, "instructions", "") or "").strip()
    if not brief:
        return []
    if len(brief) > PARTICIPANT_BRIEF_CHAR_BUDGET:
        brief = brief[:PARTICIPANT_BRIEF_CHAR_BUDGET] + _TRUNCATION_MARK
    return ["", "## Your standing brief", "", brief]


def render_turn_prompt(instance: MissionInstance, participant=None) -> str:
    """The prompt handed to one participant for the current round.

    ``participant`` defaults to whoever's turn it currently is, which is every
    sequential turn. A parallel round passes each participant explicitly,
    because all of that round's prompts are rendered from the same round-start
    state *before* any of them is delivered — that is what makes the round
    parallel rather than merely context-isolated.
    """
    mission = instance.mission
    phase = instance.current_phase()
    if participant is None:
        participant = instance.current_participant()
    engine = participant.assigned_engine

    # Check for topic/objective override
    objective = instance.context.get("topic_override", mission.objective).strip()

    parts = [
        f"# {mission.name}",
        "",
        f"**Mission objective:** {objective}",
        "",
        f"You are **{participant.display_name}**, acting as the "
        f"**{engine.role}** in this discussion.",
    ]
    parts += _standing_brief_section(participant)
    parts += [
        "",
        f"## Current phase — {phase.name} (round {instance.round_number})",
        "",
        f"**Phase objective:** {phase.objective.strip()}",
    ]
    if phase.instructions.strip():
        parts += ["", f"**Instructions:** {phase.instructions.strip()}"]

    # Check for custom instructions/prompt injection
    if "prompt_inject" in instance.context:
        parts += [
            "",
            "## Custom Instructions / Context Override",
            "",
            instance.context["prompt_inject"].strip(),
        ]

    # What this participant has already been told, from the ledger. Its browser
    # conversation still holds all of it, so the prompt carries only the
    # difference: reference files it has not seen, and responses since its last
    # turn. This is what stops prompt size scaling with the transcript.
    _, prior_prompt = _last_prompt_for(instance, participant.id)
    # A rolled-over conversation remembers nothing, so a delta would silently
    # strand that engine for the rest of the mission. Re-brief it in full.
    context_lost = participant.id in (instance.context.get("context_reset") or ())
    first_turn = not prior_prompt or context_lost

    parts += _reference_files_section(
        instance, already_delivered="" if context_lost else prior_prompt
    )
    parts += _reference_images_section(instance)

    # The phase decides how much discussion this prompt carries (contract field
    # `context`; MISSION-CONTRACT.md §3.3). Read as data — never branched on by
    # the interpreter, which stays unaware that context policy exists.
    policy = getattr(phase, "context", "auto") or "auto"
    if policy == "none":
        parts += [
            "",
            "## Discussion so far",
            "",
            "_This phase is deliberately independent: the other participants' "
            "answers are withheld so your response is genuinely your own. "
            "Answer from the objective and instructions alone._",
        ]
    elif policy == "full" or (policy == "auto" and first_turn):
        parts += ["", "## Discussion so far", "", _recent_exchange(instance)]
    else:
        parts += ["", "## Since your last turn", "", _delta_exchange(instance, participant.id)]
        if not first_turn:
            parts += [
                "",
                "_The rest of this discussion — including your own earlier turns — "
                "is above in this same conversation. Work from it. If you cannot "
                "see it, say so in one line and answer from what is in this "
                "message._",
            ]

    parts += [
        "",
        "## Your turn",
        "",
        f"Respond now, in your role as **{engine.role}**. Address the discussion "
        "above directly and keep to the phase objective.",
    ]

    # Reciprocal peer scoring: metadata-gated, final phase only. The scores
    # become the mission's evidence record (frelan/evidence.py) — how output
    # fidelity is measured instead of assumed.
    if (
        mission.metadata.get("peer_scoring") == "true"
        and phase.id == mission.phases[-1].id
    ):
        peers = [
            p
            for p in mission.participants
            if p.id != participant.id and p.id in phase.participant_ids
        ]
        if peers:
            targets = ", ".join(f"`{p.id}` ({p.display_name})" for p in peers)
            parts += [
                "",
                "## Contribution Scoring",
                "",
                "At the very end of your response, score each peer's contribution "
                f"to this discussion. Peers to score: {targets}. Never score "
                "yourself. Emit one fenced block per peer, exactly in this "
                "format, with integer scores from 1 (poor) to 5 (excellent):",
                "",
                "```frelan-scores",
                "target: <participant_id>",
                "evidence_quality: <1-5>",
                "reasoning_depth: <1-5>",
                "actionability: <1-5>",
                "responsiveness: <1-5>",
                "justification: <one line>",
                "```",
            ]

    parts += [
        "",
        "**SYSTEM INSTRUCTION:** Provide your answer strictly as plain text/markdown. "
        "Do not generate interactive UI elements, multiple-choice questionnaires, or A/B testing selections. "
        "Answer the prompt completely in this single turn without asking the user to choose an option.",
    ]
    return "\n".join(parts) + "\n"


def render_checkpoint_summary(instance: MissionInstance) -> str:
    """Context shown to the Founder when a checkpoint is due.

    The transport prints the actual [C]/[V]/[E]/[T] menu; this supplies the
    surrounding context and the contract's convergence guidance.
    """
    mission = instance.mission
    phase = instance.current_phase()
    parts = [
        "# Checkpoint",
        "",
        f"**Mission:** {mission.name}",
        f"**Phase:** {phase.name}",
        f"**Rounds completed:** {instance.rounds_completed}",
        "",
        "## Most recent exchange",
        "",
        _recent_exchange(instance, limit=len(mission.participants)),
    ]
    if mission.governance.convergence_note.strip():
        parts += [
            "",
            "## Convergence guidance",
            "",
            mission.governance.convergence_note.strip(),
        ]
    return "\n".join(parts) + "\n"


def render_synthesis_prompt(
    instance: MissionInstance,
    output=None,
    index: int = 0,
    produced_titles: tuple[str, ...] = (),
    include_transcript: bool = True,
) -> str:
    """The final synthesis prompt, sent to produce the recommendation.

    Issued once the mission reaches a successful terminal state (converged or
    naturally completed).

    With ``output`` unset this is the original single-turn synthesis: one engine
    turns the whole discussion into one recommendation (plus, for a
    multi-deliverable mission, every declared output in that one reply).

    With ``output`` set the turn produces exactly that one deliverable, and the
    interpreter issues one such prompt per declared output. ``include_transcript``
    is then False for every turn after the first: the transcript is already in
    that engine's own conversation, so resending it would push a ~30k prompt
    down the attachment ladder four times over for nothing.
    """
    mission = instance.mission
    objective = instance.context.get("topic_override", mission.objective).strip()

    if output is None:
        parts = [
            f"# {mission.name} — Final Synthesis",
            "",
            f"**Mission objective:** {objective}",
            "",
            "The structured discussion is complete. Produce a single, decisive "
            "**final recommendation** that synthesises the whole discussion below. "
            "State the recommendation first, then the key reasoning, then the main "
            "risk or caveat.",
        ]
        # A multi-deliverable mission asks for every declared output in this one
        # response; each is written to its own file afterwards.
        parts += render_output_instructions(mission.outputs)
    else:
        parts = [
            f"# {mission.name} — Final Synthesis",
            "",
            f"**Mission objective:** {objective}",
            "",
            "The structured discussion is complete. This mission produces "
            f"{len(mission.outputs)} separate documents, one per turn — write "
            "them as finished deliverables, not as a summary of the discussion. "
            "Depth is wanted here: prefer the complete, specific version over "
            "the short one, and write out every section the document needs.",
        ]
        parts += render_output_request(
            output, index, len(mission.outputs), produced_titles
        )

    parts += _reference_files_section(instance)
    parts += _reference_images_section(instance)

    if include_transcript:
        parts += [
            "",
            "## Discussion transcript",
            "",
            # A wider budget than a turn prompt gets: this is the one turn that
            # is supposed to see the whole mission. It is still bounded — an
            # unbounded transcript is what broke browser delivery — and anything
            # dropped is announced in the block itself rather than silently
            # omitted under a heading promising the "full" discussion.
            _recent_exchange(
                instance,
                limit=len(instance.ledger.entries) or 1,
                budget=SYNTHESIS_CONTEXT_CHAR_BUDGET,
                embed_cap=SYNTHESIS_MAX_EMBED_RESPONSE_CHARS,
            ),
        ]

    parts += [
        "",
        "**SYSTEM INSTRUCTION:** Provide your answer strictly as plain text/markdown. "
        "Do not generate interactive UI elements, multiple-choice questionnaires, or A/B testing selections. "
        "Answer the prompt completely in this single turn without asking the user to choose an option.",
    ]
    return "\n".join(parts) + "\n"
