"""Interpretation Layer — the Mission Interpreter.

The generic runtime. It executes a ``MissionInstance`` by stepping through the
declared phases, requesting prompts, coordinating the transport, evaluating
checkpoints, and handling termination. It contains execution *mechanics* only:
it knows nothing about any specific mission, model, or transport.

Dependency rule: the interpreter depends on the ``Transport`` protocol, never on
a concrete adapter. ``main.py`` chooses which transport to inject.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from frelan.deliverables import ensure_wrapped
from frelan.enums import (
    DEFAULT_INTERACTION,
    CheckpointDecision,
    Interaction,
    LedgerEntryType,
    RuntimeStatus,
)
from frelan.mission_instance import MissionInstance
from frelan.prompt_renderer import (
    render_checkpoint_summary,
    render_synthesis_prompt,
    render_turn_prompt,
)
from frelan.prompt_adapter import (
    adapt,
)
from frelan.transport.base import Transport

# Founder checkpoint decisions that end the mission, and the status each maps to.
_DECISION_STATUS = {
    CheckpointDecision.CONVERGED: RuntimeStatus.CONVERGED,
    CheckpointDecision.ESCALATE: RuntimeStatus.ESCALATED,
    CheckpointDecision.TERMINATE: RuntimeStatus.TERMINATED,
}

# Terminal states that warrant producing a final recommendation.
_SUCCESSFUL_TERMINALS = (RuntimeStatus.CONVERGED, RuntimeStatus.COMPLETED)

# Runtime-context keys the transport may own and mutate mid-run (the `P`
# checkpoint menu). Synced transport -> instance at the start of every turn.
_TRANSPORT_OVERRIDES = (
    "topic_override",
    "prompt_inject",
    "injected_files",
    "injected_images",
    # Participants whose conversation was rolled over and must be re-briefed
    # in full instead of receiving a delta prompt.
    "context_reset",
)

# Fenced code blocks a participant emits, and the leading comment that names the
# file one should be written to.
_CODE_BLOCK_RE = re.compile(r"```([a-zA-Z0-9_\-\+]*)\s*\n(.*?)\n```", re.DOTALL)
_FILENAME_RE = re.compile(
    r"(?:#|//|--|/\*|<!--)\s*(?:filename|filepath|file|path)\s*[:=]\s*"
    r"([a-zA-Z0-9_\-\.\/\\:]+)",
    re.IGNORECASE,
)
# Only the first few lines of a block are inspected for the filename comment.
_FILENAME_SCAN_LINES = 4
# How much of a harvested artifact travels in later prompts. The whole file is
# always written to disk; only the inlined copy is capped, because reference
# files are re-sent on every subsequent turn.
MAX_SHARED_ARTIFACT_CHARS = 12_000

# The sentinel a transport returns when the Founder abandoned a turn to edit the
# run's overrides. Not a response: the turn is re-rendered and re-run.
_EDIT_PROMPT = "__EDIT_PROMPT__"

# Recorded in place of a reply when a parallel turn could not be delivered or
# could not be read. Written into the ledger as that turn's content, so the
# transcript states what happened instead of showing a silent gap.
_UNDELIVERED_NOTE = (
    "[NO RESPONSE] This round's prompt could not be delivered to {name}. "
    "Nothing was received for this turn."
)
_UNANSWERED_NOTE = (
    "[NO RESPONSE] {name} was prompted but no reply could be collected for "
    "this turn."
)


def resolve_artifact_path(artifact_dir: Path, filename: str) -> Path | None:
    """Resolve a participant-supplied ``filename`` inside ``artifact_dir``.

    Returns ``None`` when the name would escape the directory. Containment is
    decided by resolving the final path and comparing it to the resolved root —
    NOT by scrubbing substrings. String scrubbing silently failed for
    drive-absolute names: ``Path("outputs") / "C:/Windows/x"`` discards the base
    entirely on Windows and yields ``C:\\Windows\\x``, so a response naming such
    a path wrote outside the run directory.
    """
    name = (filename or "").strip().replace("\\", "/").strip("/")
    if not name:
        return None
    root = artifact_dir.resolve()
    try:
        candidate = (artifact_dir / name).resolve()
    except (OSError, ValueError):
        return None
    if candidate == root or not candidate.is_relative_to(root):
        return None
    return candidate


class MissionInterpreter:
    """Executes a Mission Instance through a transport, generically.

    ``artifact_dir`` is where files a participant creates mid-discussion are
    written. The interpreter stays generic — it is handed a directory and never
    decides which one; ``main.py`` remains the only module that knows the run
    layout.
    """

    def __init__(
        self, transport: Transport, artifact_dir: Path | str = Path("outputs")
    ) -> None:
        self._transport = transport
        self._artifact_dir = Path(artifact_dir)

    def run(self, instance: MissionInstance) -> None:
        """Run the instance to a terminal state, mutating it in place."""
        instance.set_status(RuntimeStatus.RUNNING)
        instance.ledger.append(
            LedgerEntryType.SYSTEM,
            f"Mission '{instance.mission.name}' started.",
        )
        while instance.status is RuntimeStatus.RUNNING:
            # The phase declares HOW its participants work together; the
            # interpreter reads that one field and dispatches. It still knows
            # nothing about any specific mission, model, or transport.
            if self._interaction(instance) == Interaction.PARALLEL.value:
                round_completed = self._run_parallel_round(instance)
            else:
                self._run_turn(instance)
                round_completed = instance.advance_turn()
            if round_completed:  # a full round just completed
                self._after_round(instance)
        self._finalize(instance)

    @staticmethod
    def _interaction(instance: MissionInstance) -> str:
        """The current phase's declared interaction pattern, defaulted."""
        phase = instance.current_phase()
        return getattr(phase, "interaction", DEFAULT_INTERACTION) or DEFAULT_INTERACTION

    # -- one participant's turn -------------------------------------------

    def _run_turn(self, instance: MissionInstance) -> None:
        """One sequential turn: render, deliver, collect, record."""
        while True:
            self._sync_overrides(instance)
            participant = instance.current_participant()
            prompt = self._render(instance, participant)
            response, elapsed = self._exchange(participant, prompt)
            if response == _EDIT_PROMPT:
                continue  # abandoned attempt; the retry is timed on its own
            self._record_turn(instance, participant, prompt, response, elapsed)
            break

    def _render(self, instance: MissionInstance, participant) -> str:
        """Render engine-agnostically, then adapt for the assigned engine.

        The engine id is passed through, never branched on, so this keeps the
        interpreter's model-agnosticism (see AssignedEngine). The adapted prompt
        is what the ledger records — the audit trail must show what was actually
        delivered, not its pre-adaptation form.
        """
        return adapt(
            render_turn_prompt(instance, participant),
            participant.assigned_engine.execution_engine,
        )

    def _exchange(self, participant, prompt: str) -> tuple[str, float]:
        """Deliver one prompt, collect one reply; return ``(reply, seconds)``.

        Timed here because the ledger's own timestamps cannot supply it: a
        turn's PROMPT and RESPONSE entries are both appended after the response
        arrives, so they are stamped microseconds apart.
        """
        started = time.monotonic()
        self._transport.deliver_prompt(participant, prompt)
        response = self._transport.collect_response(participant)
        return response, time.monotonic() - started

    def _record_turn(
        self,
        instance: MissionInstance,
        participant,
        prompt: str,
        response: str,
        elapsed: float,
    ) -> None:
        """Harvest artifacts, then write both halves of the turn to the ledger."""
        phase = instance.current_phase()
        role = participant.assigned_engine.role
        self._harvest_and_inject_created_files(participant, response, instance)
        instance.ledger.append(
            LedgerEntryType.PROMPT, prompt,
            phase_id=phase.id, round_number=instance.round_number,
            participant_id=participant.id, role=role,
        )
        instance.ledger.append(
            LedgerEntryType.RESPONSE, response,
            phase_id=phase.id, round_number=instance.round_number,
            participant_id=participant.id, role=role,
            duration_seconds=round(elapsed, 3),
        )
        instance.context[participant.id] = response

    # -- a parallel round --------------------------------------------------

    def _run_parallel_round(self, instance: MissionInstance) -> bool:
        """Run the rest of this round with every participant working at once.

        Three steps, and the order of the first two is the whole mechanism:

        1. **Render everything first.** Every prompt is built from the same
           round-start ledger, so no participant can see another's answer from
           this round. That is the semantic difference from a sequential round,
           and it holds by construction rather than by instruction.
        2. **Deliver everything, then collect.** ``deliver_prompt`` submits and
           returns; ``collect_response`` is what waits. Delivering all of them
           before collecting any is what makes the engines generate at the same
           time — real concurrency over the existing transport protocol, with no
           threads, no async, and no transport change. (Playwright's sync API is
           not thread-safe, so threading this would have meant rewriting the
           transport layer to buy the same overlap.)
        3. **Record in declaration order.** The ledger reads the same whichever
           engine finished first, so a parallel run stays comparable with a
           sequential one and with itself.

        Failures are independent: a participant whose delivery or collection
        raises is recorded as a failed turn and the round continues without it.
        Losing one engine must not cost the whole round.

        Starts from ``turn_index`` rather than from zero, so a ``--resume`` that
        landed mid-round finishes that round instead of re-running turns that
        are already on disk.
        """
        self._sync_overrides(instance)
        phase = instance.current_phase()
        pending = phase.participant_ids[instance.turn_index :]
        participants = [instance.mission.participant(pid) for pid in pending]

        prompts = [self._render(instance, p) for p in participants]

        instance.ledger.append(
            LedgerEntryType.SYSTEM,
            f"[PARALLEL] Phase '{phase.id}' round {instance.round_number}: "
            f"{len(participants)} participant(s) prompted together "
            f"({', '.join(p.id for p in participants)}). Every prompt was "
            "rendered before any was delivered, so none carries another's "
            "answer from this round.",
        )

        # Fan out. A per-participant delivery time is kept so each turn's
        # recorded duration is measured from when THAT engine was asked, which
        # is the honest reading of "how long it took". Collection is still
        # sequential polling, so a later participant's figure can include time
        # it spent already finished but not yet read.
        delivered_at: dict[str, float | None] = {}
        for participant, prompt in zip(participants, prompts):
            try:
                self._transport.deliver_prompt(participant, prompt)
                delivered_at[participant.id] = time.monotonic()
            except Exception as exc:  # one engine failing must not end the round
                delivered_at[participant.id] = None
                instance.ledger.append(
                    LedgerEntryType.SYSTEM,
                    "[PARALLEL FAILURE] Could not deliver this round's prompt to "
                    f"{participant.display_name}: {exc}. The other participants "
                    "continue; this turn is recorded as undelivered.",
                )

        # Fan in, in declaration order.
        for participant, prompt in zip(participants, prompts):
            start = delivered_at[participant.id]
            if start is None:
                self._record_turn(
                    instance, participant, prompt,
                    _UNDELIVERED_NOTE.format(name=participant.display_name), 0.0,
                )
                continue
            try:
                response = self._transport.collect_response(participant)
                elapsed = time.monotonic() - start
            except Exception as exc:
                instance.ledger.append(
                    LedgerEntryType.SYSTEM,
                    "[PARALLEL FAILURE] Could not collect this round's reply from "
                    f"{participant.display_name}: {exc}. The other participants "
                    "continue; this turn is recorded as unanswered.",
                )
                self._record_turn(
                    instance, participant, prompt,
                    _UNANSWERED_NOTE.format(name=participant.display_name), 0.0,
                )
                continue
            if response == _EDIT_PROMPT:
                # The Founder abandoned this turn to edit the run's overrides.
                # Re-run just this participant, sequentially: the rest of the
                # round has already been delivered and cannot be un-asked.
                prompt, response, elapsed = self._redo_turn(instance, participant)
            self._record_turn(instance, participant, prompt, response, elapsed)

        # Advance the pointer once per participant handled; the last of them is
        # the round boundary.
        completed = False
        for _ in participants:
            completed = instance.advance_turn()
        return completed

    def _redo_turn(
        self, instance: MissionInstance, participant
    ) -> tuple[str, str, float]:
        """Re-render and re-run one participant's turn after an override edit."""
        while True:
            self._sync_overrides(instance)
            prompt = self._render(instance, participant)
            response, elapsed = self._exchange(participant, prompt)
            if response != _EDIT_PROMPT:
                return prompt, response, elapsed

    def _sync_overrides(self, instance: MissionInstance) -> None:
        """Mirror the transport's live override attributes into the context.

        A transport that does not expose an override is simply skipped; one that
        exposes it as ``None`` means "cleared", so the key is removed rather than
        left stale.
        """
        for key in _TRANSPORT_OVERRIDES:
            if not hasattr(self._transport, key):
                continue
            value = getattr(self._transport, key)
            if value is not None:
                instance.context[key] = value
            else:
                instance.context.pop(key, None)

    # -- round boundary control flow --------------------------------------

    def _after_round(self, instance: MissionInstance) -> None:
        # 1. A global round cap ends the mission immediately.
        if instance.is_round_cap_reached():
            instance.set_status(RuntimeStatus.COMPLETED)
            return
        # 2. On the checkpoint cadence, the Founder decides (may end the mission).
        if instance.is_checkpoint_due():
            if self._checkpoint(instance) is not RuntimeStatus.RUNNING:
                return
        # 3. A finished phase advances to the next, or completes the mission.
        if instance.is_phase_complete():
            if not instance.advance_phase():
                instance.set_status(RuntimeStatus.COMPLETED)

    def _checkpoint(self, instance: MissionInstance) -> RuntimeStatus:
        """Ask the Founder; record the decision; return the resulting status."""
        summary = render_checkpoint_summary(instance)
        decision = self._transport.ask_checkpoint(summary)
        instance.record_checkpoint(decision)
        instance.ledger.append(
            LedgerEntryType.CHECKPOINT,
            f"Founder decision: {decision.value}",
            phase_id=instance.current_phase().id,
            round_number=instance.rounds_completed,
        )
        new_status = _DECISION_STATUS.get(decision)
        if new_status is not None:
            instance.set_status(new_status)
            return new_status
        return RuntimeStatus.RUNNING  # CONTINUE

    # -- termination -------------------------------------------------------

    def _finalize(self, instance: MissionInstance) -> None:
        if instance.status in _SUCCESSFUL_TERMINALS:
            self._synthesize(instance)
        instance.ledger.append(
            LedgerEntryType.SYSTEM,
            f"Mission ended with status: {instance.status.value}.",
        )

    def _synthesize(self, instance: MissionInstance) -> None:
        """Run the synthesis turn(s) that produce the final recommendation.

        The synthesiser is whichever participant the contract declares. Falling
        back to the first participant preserves existing missions, but declaring
        it is preferred: list position is not a reason for one engine to always
        hold the final word in a discussion of equal peers.

        A mission declaring two or more outputs gets **one turn per output**
        rather than one turn carrying all of them. A browser reply is the hard
        ceiling on a deliverable (the largest ever recorded here is ~29k chars),
        so asking for a four-document bundle in one turn does not produce four
        documents — it produces four summaries. The per-output replies are
        joined and parsed exactly as a single multi-section reply would be, so
        nothing downstream changes.
        """
        synthesiser = self._synthesiser(instance)
        outputs = instance.mission.outputs
        if len(outputs) < 2:
            instance.context["final_recommendation"] = self._synthesis_turn(
                instance, synthesiser, render_synthesis_prompt(instance)
            )
            return

        sections: list[str] = []
        produced: list[str] = []
        for index, output in enumerate(outputs):
            prompt = render_synthesis_prompt(
                instance,
                output=output,
                index=index,
                produced_titles=tuple(produced),
                # Only the first turn carries the transcript; it is already in
                # this engine's conversation for every turn after it.
                include_transcript=(index == 0),
            )
            response = self._synthesis_turn(instance, synthesiser, prompt)
            section, wrapped = ensure_wrapped(response, output)
            if wrapped:
                instance.ledger.append(
                    LedgerEntryType.SYSTEM,
                    f"[DELIVERABLE] {synthesiser.display_name} returned "
                    f"'{output.title}' without its sentinel lines; the reply was "
                    f"wrapped as '{output.id}' so it is not lost.",
                )
            sections.append(section)
            produced.append(output.title)

        instance.context["final_recommendation"] = "\n\n".join(sections)

    def _synthesis_turn(
        self, instance: MissionInstance, synthesiser, prompt: str
    ) -> str:
        """Deliver one synthesis prompt, ledger both halves, return the reply."""
        prompt = adapt(prompt, synthesiser.assigned_engine.execution_engine)
        instance.ledger.append(
            LedgerEntryType.PROMPT, prompt,
            participant_id=synthesiser.id, role="synthesiser",
        )
        self._transport.deliver_prompt(synthesiser, prompt)
        response = self._transport.collect_response(synthesiser)
        instance.ledger.append(
            LedgerEntryType.RESPONSE, response,
            participant_id=synthesiser.id, role="synthesiser",
        )
        return response

    @staticmethod
    def _synthesiser(instance: MissionInstance):
        """The declared synthesiser, or the first participant if none is set."""
        declared = instance.mission.governance.synthesiser
        if declared:
            try:
                return instance.mission.participant(declared)
            except KeyError:
                # The loader rejects unknown ids, so this only happens to a
                # contract built in code; fall back rather than lose the run.
                pass
        return instance.mission.participants[0]

    def _harvest_and_inject_created_files(
        self, participant, response: str, instance: MissionInstance
    ) -> None:
        """Write files a participant declared in fenced blocks, and share them.

        A block whose first lines carry a ``# filename: ...`` comment is written
        under ``artifact_dir`` and registered as a reference file, so the other
        participants receive it on their next turn. Names that would escape the
        directory are refused and recorded — the content is model-supplied and is
        never trusted to address the filesystem.
        """
        for _lang, content in _CODE_BLOCK_RE.findall(response):
            filename = self._declared_filename(content)
            if not filename:
                continue

            target_path = resolve_artifact_path(self._artifact_dir, filename)
            if target_path is None:
                instance.ledger.append(
                    LedgerEntryType.SYSTEM,
                    f"[REFUSED ARTIFACT] {participant.display_name} declared filename "
                    f"'{filename}', which resolves outside {self._artifact_dir}. "
                    "Nothing was written.",
                )
                continue

            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(content, encoding="utf-8")
            except OSError as exc:
                instance.ledger.append(
                    LedgerEntryType.SYSTEM,
                    f"[ERROR] Failed to save harvested file '{filename}': {exc}",
                )
                continue

            instance.ledger.append(
                LedgerEntryType.SYSTEM,
                f"[HARVESTED ARTIFACT] {participant.display_name} created file "
                f"'{target_path}'. Automatically injected as a reference file for "
                "subsequent turns!",
            )
            # The file on disk is complete; what gets SHARED is capped, because
            # a reference file is re-inlined into every subsequent prompt. An
            # uncapped artifact permanently enlarges the rest of the mission.
            shared = content
            if len(shared) > MAX_SHARED_ARTIFACT_CHARS:
                shared = (
                    content[:MAX_SHARED_ARTIFACT_CHARS]
                    + f"\n[... truncated for context; the complete file is at {target_path} ...]"
                )
            files = instance.context.get("injected_files") or {}
            files[str(target_path)] = shared
            instance.context["injected_files"] = files
            if hasattr(self._transport, "injected_files"):
                self._transport.injected_files = files

    @staticmethod
    def _declared_filename(content: str) -> str | None:
        """The filename a code block names in its first lines, if any."""
        for line in content.splitlines()[:_FILENAME_SCAN_LINES]:
            match = _FILENAME_RE.search(line)
            if match:
                return match.group(1).strip()
        return None
