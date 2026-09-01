"""Founder rating capture + briefed flag (MISSION-LIBRARY-RESOLUTION.md §7 #2)."""

from __future__ import annotations

import json

import main as entrypoint
from frelan.evidence import collect_evidence
from frelan.ledger import Ledger
from frelan.mission_instance import MissionInstance


def _instance(make_mission) -> MissionInstance:
    return MissionInstance(mission=make_mission(), ledger=Ledger())


def test_rating_accepts_1_to_5_and_skips_everything_else() -> None:
    assert entrypoint._prompt_founder_rating(lambda _p: "4") == 4
    for skip in ("", "0", "6", "abc", "  "):
        assert entrypoint._prompt_founder_rating(lambda _p, _s=skip: _s) is None


def test_briefed_flag_reflects_session_briefing(make_mission) -> None:
    plain = collect_evidence(_instance(make_mission))
    assert plain["briefed"] is False

    briefed = _instance(make_mission)
    briefed.context["topic_override"] = "a custom topic"
    assert collect_evidence(briefed)["briefed"] is True


def test_rating_lands_in_evidence_outputs(make_mission, tmp_path) -> None:
    log = tmp_path / "evidence-log.jsonl"
    entrypoint.write_outputs(
        _instance(make_mission), tmp_path, evidence_log=log, founder_rating=5
    )

    evidence = json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["founder_rating"] == 5

    line = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert line["founder_rating"] == 5
    assert "briefed" in line
