"""Focused tests for the Mission Instance (mission_instance.py).

These pin the execution-pointer arithmetic the interpreter depends on: turn
cycling, round rollover, checkpoint cadence, phase completion, and phase
advancement. This is the module most likely to harbour an off-by-one, so the
transitions are exercised explicitly.
"""

from __future__ import annotations

from frelan.enums import CheckpointDecision, RuntimeStatus
from frelan.ledger import Ledger
from frelan.mission_contract import ExecutionPhase
from frelan.mission_instance import MissionInstance


def _instance(mission) -> MissionInstance:
    return MissionInstance(mission=mission, ledger=Ledger())


def test_current_participant_follows_declared_turn_order(make_mission):
    inst = _instance(make_mission())
    assert inst.current_participant().id == "chatgpt"
    inst.advance_turn()
    assert inst.current_participant().id == "gemini"


def test_round_rolls_over_after_last_participant(make_mission):
    inst = _instance(make_mission())
    assert inst.advance_turn() is False  # chatgpt -> gemini, mid-round
    assert inst.advance_turn() is True  # gemini done -> round complete
    assert inst.turn_index == 0
    assert inst.round_number == 2
    assert inst.rounds_completed == 1


def test_checkpoint_due_on_cadence(make_mission):
    inst = _instance(make_mission(checkpoint_interval=2))
    inst.advance_turn(); inst.advance_turn()  # round 1 complete
    assert inst.is_checkpoint_due() is False  # 1 % 2 != 0
    inst.advance_turn(); inst.advance_turn()  # round 2 complete
    assert inst.is_checkpoint_due() is True  # 2 % 2 == 0


def test_phase_completion_respects_max_rounds(make_mission):
    phases = (
        ExecutionPhase(
            id="p1",
            name="Phase 1",
            objective="one round only",
            participant_ids=("chatgpt", "gemini"),
            max_rounds=1,
        ),
    )
    inst = _instance(make_mission(phases=phases))
    assert inst.is_phase_complete() is False
    inst.advance_turn(); inst.advance_turn()  # one full round done
    assert inst.is_phase_complete() is True


def test_advance_phase_resets_pointer_and_stops_at_last(make_mission):
    phases = (
        ExecutionPhase(id="p1", name="P1", objective="a", participant_ids=("chatgpt",)),
        ExecutionPhase(id="p2", name="P2", objective="b", participant_ids=("gemini",)),
    )
    inst = _instance(make_mission(phases=phases))
    inst.advance_turn()  # move things off the initial pointer
    assert inst.advance_phase() is True
    assert inst.phase_index == 1
    assert inst.round_number == 1
    assert inst.turn_index == 0
    assert inst.advance_phase() is False  # no phase after the last


def test_global_round_cap(make_mission):
    inst = _instance(make_mission(gov_max_rounds=1))
    assert inst.is_round_cap_reached() is False
    inst.advance_turn(); inst.advance_turn()  # 1 round completed
    assert inst.is_round_cap_reached() is True


def test_record_checkpoint_appends_history(make_mission):
    inst = _instance(make_mission())
    inst.advance_turn(); inst.advance_turn()  # so rounds_completed == 1
    record = inst.record_checkpoint(CheckpointDecision.CONTINUE, note="looks good")
    assert inst.checkpoint_history == [record]
    assert record.decision is CheckpointDecision.CONTINUE
    assert record.round_number == 1


def test_status_starts_initialized_and_is_settable(make_mission):
    inst = _instance(make_mission())
    assert inst.status is RuntimeStatus.INITIALIZED
    inst.set_status(RuntimeStatus.RUNNING)
    assert inst.status is RuntimeStatus.RUNNING
