"""The renderer's budget must fit the delivery layer's smallest composer.

The 2026-08-19 general_inquiry run overflowed on 8 of 10 turns because the
renderer was allowed ~100k chars per prompt while the narrowest adapter can
type ~9k. These tests lock the corrected relationship: a files-free turn prompt
stays inline no matter how long the transcript grows, and the participant is
always told when it is seeing a partial discussion.
"""

from __future__ import annotations

from frelan.enums import LedgerEntryType
from frelan.ledger import Ledger
from frelan.mission_instance import MissionInstance
from frelan.prompt_renderer import (
    MAX_EMBED_RESPONSE_CHARS,
    RECENT_CONTEXT_CHAR_BUDGET,
    SYNTHESIS_CONTEXT_CHAR_BUDGET,
    SYNTHESIS_MAX_EMBED_RESPONSE_CHARS,
    _round_window,
    render_synthesis_prompt,
    render_turn_prompt,
)

# The smallest max_inline_chars across shipped adapters (ChatGPT). Restated
# rather than imported: the renderer must not depend on a transport, so this
# test guards the documented assumption instead of the import.
SMALLEST_COMPOSER = 9_000


def _instance(mission) -> MissionInstance:
    return MissionInstance(mission=mission, ledger=Ledger())


def _fill(inst: MissionInstance, turns: int, size: int = 6_000) -> None:
    for i in range(turns):
        inst.ledger.append(
            LedgerEntryType.RESPONSE,
            f"resp-{i} " + "Y" * size,
            participant_id="gemini" if i % 2 else "chatgpt",
            role="peer",
        )


def test_turn_prompt_stays_inline_as_the_transcript_grows(make_mission):
    """No injected files: every turn must fit the narrowest composer."""
    for turns in (0, 2, 10, 40):
        inst = _instance(make_mission())  # ledger entries are immutable; rebuild
        _fill(inst, turns)
        prompt = render_turn_prompt(inst)
        assert len(prompt) < SMALLEST_COMPOSER, (
            f"{turns} prior responses produced a {len(prompt)}-char prompt, "
            f"over the {SMALLEST_COMPOSER}-char composer floor"
        )


def test_prompt_size_stops_growing_with_the_transcript(make_mission):
    """The old design grew 1.5k -> 35k over ten turns; this one plateaus."""
    inst = _instance(make_mission())
    _fill(inst, 6)
    early = len(render_turn_prompt(inst))
    _fill(inst, 30)
    late = len(render_turn_prompt(inst))
    # Only the omitted-count digits differ; the payload itself is flat.
    assert late - early < 50, "prompt size must not scale with transcript length"


def test_window_trimmed_responses_are_announced_not_dropped_silently(make_mission):
    inst = _instance(make_mission())
    _fill(inst, 12, size=200)  # small enough that the char budget never bites
    prompt = render_turn_prompt(inst)

    assert "omitted to fit the context budget" in prompt
    # And the participant is told where the rest went, so it does not ask for a resend.
    assert "earlier in this conversation" in prompt


def test_window_is_one_round_of_peers(make_mission):
    inst = _instance(make_mission())
    assert _round_window(inst) == len(inst.mission.participants)


def test_budget_is_under_the_composer_floor():
    """The arithmetic the module comment promises: boilerplate + window < floor."""
    assert RECENT_CONTEXT_CHAR_BUDGET + 1_500 < SMALLEST_COMPOSER


# -- the synthesis window is wider on purpose -------------------------------


def test_the_synthesis_turn_sees_more_of_each_response_than_a_turn_prompt(
    make_mission,
):
    """At the turn-prompt cap the synthesiser wrote from stubs.

    The 2026-08-19 consolidation turns ran 24-25k chars each and arrived at the
    synthesis truncated to 2,500 — roughly 90% cut before it was ever read.
    """
    inst = _instance(make_mission())
    body = "unique-consolidation-detail " * 180  # ~5k chars, over the turn cap
    inst.ledger.append(
        LedgerEntryType.RESPONSE, body, participant_id="gemini", role="peer"
    )

    turn = render_turn_prompt(inst)
    synthesis = render_synthesis_prompt(inst)

    assert MAX_EMBED_RESPONSE_CHARS < len(body) <= SYNTHESIS_MAX_EMBED_RESPONSE_CHARS
    assert "truncated" in turn.lower()
    assert body.strip() in synthesis


def test_the_synthesis_window_stays_bounded(make_mission):
    """Wider is not unlimited — an unbounded transcript is what broke delivery."""
    inst = _instance(make_mission())
    _fill(inst, 40, size=8_000)
    prompt = render_synthesis_prompt(inst)

    assert len(prompt) < SYNTHESIS_CONTEXT_CHAR_BUDGET + 5_000
    assert "omitted to fit the context budget" in prompt
