"""The artifacts must report the objective the run was actually conducted under.

Every prompt is rendered from ``topic_override`` when the Founder supplies one,
so an artifact printing the contract's default objective contradicts the
discussion it summarises.
"""

from __future__ import annotations

import json

from frelan.enums import RuntimeStatus
from frelan.ledger import Ledger
from frelan.mission_instance import MissionInstance

import main


OVERRIDE = "Design a three-storey clinic for a sloped site in Cebu."


def _instance(make_mission, *, override: str | None) -> MissionInstance:
    instance = MissionInstance(mission=make_mission(), ledger=Ledger())
    instance.status = RuntimeStatus.COMPLETED
    instance.context["final_recommendation"] = "Build it in phases."
    if override is not None:
        instance.context["topic_override"] = override
    return instance


def test_recommendation_reports_the_override_not_the_contract_default(
    tmp_path, make_mission
):
    instance = _instance(make_mission, override=OVERRIDE)

    main.write_outputs(instance, tmp_path, evidence_log=tmp_path / "ev.jsonl")

    text = (tmp_path / "recommendation.md").read_text(encoding="utf-8")
    assert f"**Objective:** {OVERRIDE}" in text
    assert instance.mission.objective not in text


def test_recommendation_falls_back_to_the_contract_objective(tmp_path, make_mission):
    instance = _instance(make_mission, override=None)

    main.write_outputs(instance, tmp_path, evidence_log=tmp_path / "ev.jsonl")

    text = (tmp_path / "recommendation.md").read_text(encoding="utf-8")
    assert f"**Objective:** {instance.mission.objective}" in text


def test_metadata_records_both_objectives_and_the_override(tmp_path, make_mission):
    instance = _instance(make_mission, override=OVERRIDE)

    main.write_outputs(instance, tmp_path, evidence_log=tmp_path / "ev.jsonl")

    meta = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert meta["objective"] == OVERRIDE
    assert meta["contract_objective"] == instance.mission.objective
    assert meta["topic_override"] == OVERRIDE


def test_metadata_topic_override_is_null_when_unbriefed(tmp_path, make_mission):
    instance = _instance(make_mission, override=None)

    main.write_outputs(instance, tmp_path, evidence_log=tmp_path / "ev.jsonl")

    meta = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert meta["topic_override"] is None
    assert meta["objective"] == meta["contract_objective"]
