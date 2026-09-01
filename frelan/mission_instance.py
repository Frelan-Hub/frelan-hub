"""Interpretation Layer — the Mission Instance.

A ``MissionInstance`` is the **single authoritative runtime state** for one
execution of a Mission Contract. Everything that changes during a run lives
here and nowhere else: the execution pointer (phase / round / turn), the
lifecycle status, the checkpoint history, the working context, and a reference
to the canonical ledger.

State transitions are expressed as small, cohesive methods so the *interpreter*
can own control flow while the *instance* owns "where are we now". The contract
it points at (``mission``) remains immutable throughout.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from frelan.enums import CheckpointDecision, RuntimeStatus
from frelan.ledger import Ledger
from frelan.mission_contract import ExecutionPhase, Mission, Participant


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    """An immutable record of one checkpoint decision made by the Founder."""

    round_number: int
    phase_id: str
    decision: CheckpointDecision
    note: str = ""


@dataclass(slots=True)
class MissionInstance:
    """The one object that changes during a run; the source of truth for state.

    The execution pointer is three fields:

    - ``phase_index``  — which phase (index into ``mission.phases``)
    - ``round_number`` — 1-based round *within the current phase*, in progress
    - ``turn_index``   — which participant within the current round

    ``rounds_completed`` is a separate monotonic counter across the whole
    mission, used for the checkpoint cadence and the global round cap. Keeping it
    distinct from ``round_number`` is what lets checkpoints span phase
    boundaries cleanly.
    """

    mission: Mission
    ledger: Ledger
    status: RuntimeStatus = RuntimeStatus.INITIALIZED
    phase_index: int = 0
    round_number: int = 1
    turn_index: int = 0
    rounds_completed: int = 0
    checkpoint_history: list[CheckpointRecord] = field(default_factory=list)
    context: dict[str, str] = field(default_factory=dict)

    # -- read-only queries -------------------------------------------------

    def current_phase(self) -> ExecutionPhase:
        return self.mission.phases[self.phase_index]

    def current_participant(self) -> Participant:
        phase = self.current_phase()
        participant_id = phase.participant_ids[self.turn_index]
        return self.mission.participant(participant_id)

    def is_checkpoint_due(self) -> bool:
        """True after a round boundary that lands on the checkpoint cadence."""
        interval = self.mission.governance.checkpoint_interval
        return self.rounds_completed > 0 and self.rounds_completed % interval == 0

    def is_phase_complete(self) -> bool:
        """True when the current phase's ``max_rounds`` (if any) is exhausted."""
        max_rounds = self.current_phase().max_rounds
        if max_rounds is None:
            return False
        completed_in_phase = self.round_number - 1
        return completed_in_phase >= max_rounds

    def is_round_cap_reached(self) -> bool:
        """True when governance's global ``max_rounds`` (if any) is reached."""
        cap = self.mission.governance.max_rounds
        return cap is not None and self.rounds_completed >= cap

    def is_last_phase(self) -> bool:
        return self.phase_index >= len(self.mission.phases) - 1

    # -- state transitions -------------------------------------------------

    def set_status(self, status: RuntimeStatus) -> None:
        self.status = status

    def advance_turn(self) -> bool:
        """Advance to the next participant. Return ``True`` if a round completed.

        When the last participant of the round has taken their turn, the round
        rolls over: ``turn_index`` resets, and both the in-phase round counter
        and the global completed-round counter advance.
        """
        self.turn_index += 1
        if self.turn_index < len(self.current_phase().participant_ids):
            return False
        self.turn_index = 0
        self.round_number += 1
        self.rounds_completed += 1
        return True

    def advance_phase(self) -> bool:
        """Move to the next phase. Return ``False`` if there is no next phase."""
        if self.is_last_phase():
            return False
        self.phase_index += 1
        self.round_number = 1
        self.turn_index = 0
        return True

    def record_checkpoint(
        self, decision: CheckpointDecision, note: str = ""
    ) -> CheckpointRecord:
        """Append a checkpoint decision to the history and return the record."""
        record = CheckpointRecord(
            round_number=self.rounds_completed,
            phase_id=self.current_phase().id,
            decision=decision,
            note=note,
        )
        self.checkpoint_history.append(record)
        return record
