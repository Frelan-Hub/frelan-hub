"""Focused tests for the entry point (main.py).

The interactive run path is exercised by the interpreter tests via a fake
transport; here we pin the parts main.py owns directly: output writing, argument
defaults, and the invalid-contract exit code. Outputs go to a temp dir, never
the real ``outputs/``.
"""

from __future__ import annotations

import json

import main as entrypoint
from frelan.enums import LedgerEntryType, RuntimeStatus
from frelan.ledger import Ledger
from frelan.mission_instance import MissionInstance


def _finished_instance(mission, *, with_recommendation: bool) -> MissionInstance:
    inst = MissionInstance(mission=mission, ledger=Ledger())
    inst.ledger.append(LedgerEntryType.SYSTEM, "started")
    inst.ledger.append(
        LedgerEntryType.RESPONSE, "a point", participant_id="chatgpt", role="proposer"
    )
    inst.set_status(RuntimeStatus.CONVERGED)
    if with_recommendation:
        inst.context["final_recommendation"] = "Adopt X. Reasoning: it scales."
    return inst


def test_write_outputs_creates_all_artifacts(make_mission, tmp_path):
    inst = _finished_instance(make_mission(), with_recommendation=True)
    written = entrypoint.write_outputs(
        inst, tmp_path, evidence_log=tmp_path / "evidence-log.jsonl"
    )

    names = {p.name for p in written}
    assert {
        "ledger.md", "checkpoints.md", "recommendation.md", "metadata.json", "evidence.json"
    } <= names
    assert (tmp_path / "recommendation.md").read_text(encoding="utf-8").find("Adopt X") != -1

    metadata = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "converged"
    assert metadata["mission_id"] == "m1"
    assert metadata["turns"] == 1
    assert metadata["peer_scoring"] is False  # fixture mission is not gated


def test_write_outputs_omits_recommendation_when_absent(make_mission, tmp_path):
    inst = _finished_instance(make_mission(), with_recommendation=False)
    written = entrypoint.write_outputs(
        inst, tmp_path, evidence_log=tmp_path / "evidence-log.jsonl"
    )
    assert not (tmp_path / "recommendation.md").exists()
    assert {"ledger.md", "checkpoints.md", "metadata.json", "evidence.json"} == {
        p.name for p in written
    }


def test_write_outputs_appends_one_evidence_line_per_run(make_mission, tmp_path):
    inst = _finished_instance(make_mission(), with_recommendation=True)
    log = tmp_path / "evidence-log.jsonl"

    entrypoint.write_outputs(inst, tmp_path / "run1", evidence_log=log)
    entrypoint.write_outputs(inst, tmp_path / "run2", evidence_log=log)

    evidence = json.loads((tmp_path / "run1" / "evidence.json").read_text(encoding="utf-8"))
    assert set(evidence["participants"]) == {"chatgpt", "gemini"}

    lines = [json.loads(l) for l in log.read_text(encoding="utf-8").strip().splitlines()]
    assert len(lines) == 2  # cumulative: one line per mission run
    assert lines[0]["mission_id"] == "m1"
    assert lines[0]["participants"]["chatgpt"]["engine"] == "chatgpt"
    assert "score_means" in lines[0]["participants"]["chatgpt"]


def test_measured_latency_survives_the_round_trip_into_the_report(make_mission, tmp_path):
    """A timed run must reach ``--report``, not just the per-run evidence.json.

    The reporter reads ``mean_turn_seconds`` off the cumulative log line, so the
    two shapes have to agree. Asserting on a hand-built log row cannot catch the
    writer dropping the key — only writing a real run and reading it back can.
    """
    from frelan.report import engine_summary, load_log

    inst = MissionInstance(mission=make_mission(), ledger=Ledger())
    for seconds in (10.0, 20.0):
        inst.ledger.append(
            LedgerEntryType.RESPONSE,
            "a point",
            participant_id="chatgpt",
            role="proposer",
            duration_seconds=seconds,
        )
    inst.set_status(RuntimeStatus.CONVERGED)

    log = tmp_path / "evidence-log.jsonl"
    entrypoint.write_outputs(inst, tmp_path / "run1", evidence_log=log)

    line = json.loads(log.read_text(encoding="utf-8").strip())
    assert line["participants"]["chatgpt"]["mean_turn_seconds"] == 15.0
    assert engine_summary(load_log(log))["chatgpt"]["mean_turn_seconds"] == 15.0


def test_limit_overrides_parses_engine_keys_and_ignores_invalid():
    from types import SimpleNamespace

    mission = SimpleNamespace(
        metadata={
            "claude_chat_budget_chars": "250000",
            "chatgpt_max_inline_chars": "8000",
            "claude_max_inline_chars": "oops",  # invalid -> warn + ignore
            "author": "someone",  # unrelated metadata untouched
        }
    )
    overrides = entrypoint._limit_overrides(mission)
    assert overrides == {
        "claude": {"chat_budget_chars": 250000},
        "chatgpt": {"max_inline_chars": 8000},
    }


def test_refresh_policy_parses_metadata_and_ignores_invalid():
    from types import SimpleNamespace

    mission = SimpleNamespace(
        metadata={
            "refresh_stalled_seconds": "45",
            "refresh_max_per_turn": "1",
            "refresh_lag_seconds": "oops",  # invalid -> warn + default
            "author": "someone",
        }
    )
    policy = entrypoint._refresh_policy(mission)
    assert policy == {"stalled_refresh_seconds": 45, "max_refreshes_per_turn": 1}


def test_ledger_meta_roundtrip(tmp_path):
    path = tmp_path / "ledger.jsonl"
    entrypoint._write_ledger_meta(
        path, {"mission_path": "missions/app_planning.yaml", "claude_injected": True}
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"seq": 1, "timestamp": "t", "entry_type": "system", "content": "s"}
            )
            + "\n"
        )
        f.write("{corrupt line\n")  # must be skipped, not fatal

    meta, entries = entrypoint._load_resume_file(path)
    assert meta["claude_injected"] is True
    assert meta["mission_path"] == "missions/app_planning.yaml"
    assert len(entries) == 1


def _response(seq: int, content: str, participant: str) -> dict:
    return {
        "seq": seq,
        "timestamp": "t",
        "entry_type": "response",
        "content": content,
        "phase_id": "debate",
        "round_number": 1,
        "participant_id": participant,
        "role": "peer",
    }


def test_replay_entries_rebuilds_execution_pointer(make_mission):
    inst = MissionInstance(mission=make_mission(), ledger=Ledger())
    entries = [
        {"seq": 1, "timestamp": "t", "entry_type": "system", "content": "started"},
        _response(2, "a", "chatgpt"),
        _response(3, "b", "gemini"),  # round 1 completes here
        _response(4, "c", "chatgpt"),  # round 2, first turn done
    ]

    entrypoint._replay_entries(inst, entries)

    assert inst.rounds_completed == 1
    assert inst.round_number == 2
    assert inst.turn_index == 1  # gemini speaks next — completed turns never re-run
    assert len(inst.ledger.entries) == 4  # history restored verbatim
    assert inst.context["chatgpt"] == "c"


def test_replay_entries_marks_finished_missions_completed(make_mission):
    from dataclasses import replace as dc_replace

    mission = make_mission()
    # Cap the only phase at one round so two responses finish the mission.
    mission = dc_replace(
        mission, phases=(dc_replace(mission.phases[0], max_rounds=1),)
    )
    inst = MissionInstance(mission=mission, ledger=Ledger())

    entrypoint._replay_entries(
        inst, [_response(1, "a", "chatgpt"), _response(2, "b", "gemini")]
    )

    assert inst.status is RuntimeStatus.COMPLETED  # resume refuses to re-run it


def test_parse_args_defaults():
    args = entrypoint._parse_args([])
    assert str(args.mission).endswith("frelan_debate.yaml")
    # None is the sentinel for "no -o given"; the run directory is resolved
    # later so a fresh run gets its own timestamped directory.
    assert args.output_dir is None


def test_parse_args_keeps_an_explicit_output_dir(tmp_path):
    args = entrypoint._parse_args(["-o", str(tmp_path)])
    assert args.output_dir == tmp_path


def test_main_returns_2_on_invalid_mission(tmp_path):
    missing = tmp_path / "does_not_exist.yaml"
    assert entrypoint.main([str(missing)]) == 2


def test_inject_claude_peer_joins_every_phase_last(make_mission):
    mission = make_mission()
    assert len(mission.participants) == 2

    injected = entrypoint._inject_claude_peer(mission)

    assert len(injected.participants) == 3
    claude = injected.participants[-1]
    assert claude.id == "claude"
    # Equity: same peer role as the other engines, not a special reviewer.
    assert claude.assigned_engine.role == "peer_analyst"
    # Legacy vocabulary: fixture declares only "reasoning".
    assert claude.assigned_engine.required_capabilities == ("reasoning",)
    # Full peer: present in EVERY phase, appended last, with the peer note.
    for phase in injected.phases:
        assert phase.participant_ids[-1] == "claude"
        assert "peer analysis" in phase.instructions


def test_inject_claude_peer_uses_new_taxonomy_when_declared(make_mission):
    from dataclasses import replace

    from frelan.mission_contract import Capability

    mission = replace(
        make_mission(),
        capabilities=(
            Capability(id="reasoning.strategic", description="strategic"),
            Capability(id="critique", description="challenge"),
            Capability(id="synthesis", description="merge"),
        ),
    )

    injected = entrypoint._inject_claude_peer(mission)

    assert injected.participants[-1].assigned_engine.required_capabilities == (
        "reasoning.strategic",
        "critique",
    )


def test_inject_claude_peer_is_idempotent(make_mission):
    once = entrypoint._inject_claude_peer(make_mission())
    twice = entrypoint._inject_claude_peer(once)
    assert twice is once


def _scripted(lines):
    queue = list(lines)
    return lambda _prompt="": queue.pop(0)


def test_prompt_meeting_type_selects_template():
    # Menu is discovered from missions/ (§7 change #1); "1" picks the first.
    templates = entrypoint._discover_meeting_types()
    path, claude = entrypoint._prompt_meeting_type(_scripted(["1", "n"]))
    assert path == templates[0][1]
    assert claude is False


def test_prompt_meeting_type_enter_keeps_default():
    path, claude = entrypoint._prompt_meeting_type(_scripted(["", ""]))
    assert path is None
    assert claude is False


def test_prompt_meeting_type_can_include_claude():
    templates = entrypoint._discover_meeting_types()
    path, claude = entrypoint._prompt_meeting_type(_scripted(["1", "y"]))
    assert path == templates[0][1]
    assert claude is True


def test_prompt_meeting_type_skips_claude_question_when_flagged():
    # Only ONE scripted line: asking the Claude question would raise IndexError.
    templates = entrypoint._discover_meeting_types()
    path, claude = entrypoint._prompt_meeting_type(_scripted(["1"]), ask_claude=False)
    assert path == templates[0][1]
    assert claude is False


# --------------------------------------------------------------------------- #
# The structural record a run writes about itself
# --------------------------------------------------------------------------- #


def test_metadata_records_the_missions_shape(make_mission, tmp_path):
    """History compares experiments from this record, so it must be the run's."""
    from frelan.mission_contract import ExecutionPhase

    mission = make_mission(
        phases=(
            ExecutionPhase(
                id="research",
                name="Research",
                objective="Find out.",
                participant_ids=("chatgpt", "gemini"),
                interaction="parallel",
                stage="research",
                context="none",
            ),
        )
    )
    inst = _finished_instance(mission, with_recommendation=True)
    entrypoint.write_outputs(
        inst, tmp_path, evidence_log=tmp_path / "evidence-log.jsonl"
    )
    meta = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))

    assert meta["interactions"] == ["parallel"]
    assert meta["stages"] == ["research"]
    assert meta["phases"][0]["interaction"] == "parallel"
    assert meta["phases"][0]["context"] == "none"
    assert [(p["id"], p["type"], p["model"]) for p in meta["participants"]] == [
        ("chatgpt", "model", "chatgpt"),
        ("gemini", "model", "gemini"),
    ]


def test_metadata_reports_an_absent_workflow_as_absent(make_mission, tmp_path):
    inst = _finished_instance(make_mission(), with_recommendation=True)
    entrypoint.write_outputs(
        inst, tmp_path, evidence_log=tmp_path / "evidence-log.jsonl"
    )
    meta = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert meta["workflow"] == ""
    assert meta["stages"] == []
    assert meta["interactions"] == ["sequential"]


def test_the_injected_claude_peer_is_a_model_participant(make_mission):
    """``--claude`` seats an engine, not a configured agent."""
    injected = entrypoint._inject_claude_peer(make_mission())
    claude = injected.participant("claude")
    assert claude.type == "model"
    assert claude.instructions == ""


def test_injecting_claude_preserves_every_phase_interaction(make_mission):
    from frelan.mission_contract import ExecutionPhase

    mission = make_mission(
        phases=(
            ExecutionPhase(
                id="research", name="Research", objective="Find out.",
                participant_ids=("chatgpt", "gemini"),
                interaction="parallel", stage="research",
            ),
        )
    )
    injected = entrypoint._inject_claude_peer(mission)
    assert injected.phases[0].interaction == "parallel"
    assert injected.phases[0].stage == "research"
    assert injected.phases[0].participant_ids == ("chatgpt", "gemini", "claude")
