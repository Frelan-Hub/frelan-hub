"""Per-phase context policy (step 5) — declarative, never interpreter logic.

A phase whose instructions say "do not reference the other peer" was still
receiving the other peer's answers. `context` lets the contract say what the
prompt carries; the interpreter never sees the field.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from frelan.enums import LedgerEntryType
from frelan.ledger import Ledger
from frelan.mission_contract import ExecutionPhase
from frelan.mission_instance import MissionInstance
from frelan.mission_loader import MissionValidationError, load_mission
from frelan.prompt_renderer import render_turn_prompt


def _instance(mission) -> MissionInstance:
    return MissionInstance(mission=mission, ledger=Ledger())


def _with_policy(mission, policy: str):
    phases = tuple(replace(p, context=policy) for p in mission.phases)
    return replace(mission, phases=phases)


def _seed(inst: MissionInstance) -> None:
    inst.ledger.append(LedgerEntryType.PROMPT, "p", participant_id="chatgpt", role="peer")
    inst.ledger.append(LedgerEntryType.RESPONSE, "chatgpt said ALPHA", participant_id="chatgpt", role="peer")
    inst.ledger.append(LedgerEntryType.PROMPT, "p", participant_id="gemini", role="peer")
    inst.ledger.append(LedgerEntryType.RESPONSE, "gemini said BRAVO", participant_id="gemini", role="peer")


def test_default_is_auto_and_needs_no_contract_change():
    phase = ExecutionPhase(id="x", name="X", objective="o", participant_ids=("chatgpt",))
    assert phase.context == "auto"


def test_none_withholds_peer_answers(make_mission):
    inst = _instance(_with_policy(make_mission(), "none"))
    _seed(inst)
    prompt = render_turn_prompt(inst)

    assert "BRAVO" not in prompt
    assert "deliberately independent" in prompt


def test_full_always_sends_the_window(make_mission):
    inst = _instance(_with_policy(make_mission(), "full"))
    _seed(inst)
    prompt = render_turn_prompt(inst)

    assert "## Discussion so far" in prompt
    assert "BRAVO" in prompt
    assert "ALPHA" in prompt  # full means full, including its own earlier turn


def test_delta_sends_only_new_work_even_on_a_first_turn(make_mission):
    inst = _instance(_with_policy(make_mission(), "delta"))
    inst.ledger.append(LedgerEntryType.RESPONSE, "gemini said BRAVO", participant_id="gemini", role="peer")
    prompt = render_turn_prompt(inst)  # chatgpt has never been prompted

    assert "## Since your last turn" in prompt
    assert "BRAVO" in prompt
    # No "scroll back" note on a first turn: there is nothing to scroll back to.
    assert "above in this same conversation" not in prompt


def test_auto_matches_pre_existing_behaviour(make_mission):
    inst = _instance(make_mission())  # default policy
    _seed(inst)
    assert "## Since your last turn" in render_turn_prompt(inst)


def _contract_source() -> str:
    """A shipped contract with its own context declarations stripped."""
    # Found by name, not by path: templates move between category folders as
    # they are promoted (MISSION-LIBRARY-RESOLUTION.md §10).
    shipped = next(Path("missions").rglob("general_inquiry.yaml"))
    raw = shipped.read_text(encoding="utf-8")
    return "\n".join(
        line
        for line in raw.splitlines()
        if not line.strip().startswith("context:")
        and "answers are withheld" not in line
        and "forbid referencing the peers" not in line
    )


def test_loader_rejects_an_unknown_policy(tmp_path):
    contract = tmp_path / "bad.yaml"
    contract.write_text(
        _contract_source().replace(
            "    max_rounds: 1", "    max_rounds: 1\n    context: sometimes", 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(MissionValidationError) as exc:
        load_mission(contract)
    assert any("context must be one of" in e for e in exc.value.errors)


def test_loader_accepts_every_documented_policy(tmp_path):
    source = _contract_source()
    for policy in ("auto", "none", "delta", "full"):
        contract = tmp_path / f"{policy}.yaml"
        contract.write_text(
            source.replace("    max_rounds: 1", f"    max_rounds: 1\n    context: {policy}", 1),
            encoding="utf-8",
        )
        assert load_mission(contract).phases[0].context == policy
