"""Run identity, persistence, and incremental ledger reads.

These guard the three defects the old dashboard shipped with, each of which was
silent: every run was pinned to one flat ``outputs/`` directory, the previous
run's ledger was deleted to make room for the next, and a run had no identity at
all. Every test here writes under ``tmp_path`` — a test must never append to the
real outputs tree.
"""

from __future__ import annotations

import json

from ui import runs


def _record(root, name="Debate"):
    return runs.allocate_run(root, mission_path="missions/x.yaml", mission_name=name)


def test_run_ids_are_unique_and_monotonic(tmp_path):
    first = _record(tmp_path)
    second = _record(tmp_path)
    third = _record(tmp_path)
    assert [first.run_id, second.run_id, third.run_id] == [1, 2, 3]
    assert first.label == "#0001"
    assert third.label == "#0003"


def test_a_new_run_never_reuses_a_directory(tmp_path):
    directories = {_record(tmp_path).run_dir for _ in range(3)}
    assert len(directories) == 3, "two runs shared a directory"
    for directory in directories:
        assert (tmp_path / directory.split("\\")[-1].split("/")[-1]).is_dir()


def test_the_id_counter_survives_a_deleted_directory(tmp_path):
    """An ID is how the Founder refers to a run. Deleting the run's artifacts
    must not hand its number to a different run later."""
    first = _record(tmp_path)
    for child in first.path.iterdir():
        child.unlink()
    first.path.rmdir()
    assert runs.allocate_run(
        tmp_path, mission_path="missions/x.yaml"
    ).run_id == first.run_id + 1


def test_allocating_updates_the_resume_pointer(tmp_path):
    """The CLI and the dashboard must agree on which run is the last one."""
    record = _record(tmp_path)
    pointer = tmp_path / ".last-run"
    assert pointer.read_text(encoding="utf-8").strip() == record.run_dir


def test_status_updates_supersede_without_losing_history(tmp_path):
    record = _record(tmp_path)
    runs.record_status(tmp_path, record, runs.STATUS_COMPLETED)
    listed = {r.run_dir: r for r in runs.list_runs(tmp_path)}
    assert listed[record.run_dir].status == runs.STATUS_COMPLETED
    lines = (tmp_path / runs.RUN_REGISTRY).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, "the registry is append-only; nothing is rewritten"


def test_history_includes_runs_started_outside_the_dashboard(tmp_path):
    """A run started in a terminal is still the Founder's history."""
    registered = _record(tmp_path)
    foreign = tmp_path / "run-20260101T000000Z"
    foreign.mkdir()
    (foreign / "metadata.json").write_text(
        json.dumps(
            {
                "mission_name": "CLI run",
                "status": "completed",
                "started_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    listed = runs.list_runs(tmp_path)
    directories = {r.run_dir for r in listed}
    assert str(foreign) in directories
    assert str(registered.path) in directories
    found = next(r for r in listed if r.run_dir == str(foreign))
    assert found.run_id is None, "a run with no registry entry has no dashboard ID"
    assert found.mission_name == "CLI run"


def test_history_is_newest_first(tmp_path):
    older = _record(tmp_path, "older")
    newer = _record(tmp_path, "newer")
    listed = runs.list_runs(tmp_path)
    assert listed[0].run_dir == newer.run_dir
    assert listed[1].run_dir == older.run_dir


def test_a_corrupt_registry_line_costs_only_that_line(tmp_path):
    _record(tmp_path)
    with (tmp_path / runs.RUN_REGISTRY).open("a", encoding="utf-8") as fh:
        fh.write("{ this is not json\n")
    assert len(runs.list_runs(tmp_path)) == 1


# --------------------------------------------------------------------------- #
# Incremental ledger reads
# --------------------------------------------------------------------------- #


def _write(path, *entries):
    with path.open("a", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def test_only_new_bytes_are_parsed(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write(ledger, {"entry_type": "system", "content": "one"})
    first, offset = runs.read_ledger(ledger)
    assert [e["content"] for e in first] == ["one"]

    _write(ledger, {"entry_type": "system", "content": "two"})
    second, offset = runs.read_ledger(ledger, offset)
    assert [e["content"] for e in second] == ["two"], "an old entry was re-parsed"


def test_a_half_written_line_is_left_for_the_next_read(tmp_path):
    """The ledger is appended to by a live subprocess, so its tail may be
    incomplete. Consuming a partial record would skip it forever."""
    ledger = tmp_path / "ledger.jsonl"
    _write(ledger, {"entry_type": "system", "content": "complete"})
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write('{"entry_type": "system", "content": "par')

    entries, offset = runs.read_ledger(ledger)
    assert [e["content"] for e in entries] == ["complete"]

    with ledger.open("a", encoding="utf-8") as fh:
        fh.write('tial"}\n')
    entries, _ = runs.read_ledger(ledger, offset)
    assert [e["content"] for e in entries] == ["partial"]


def test_a_shrunken_ledger_is_read_from_the_start(tmp_path):
    """A file shorter than the caller's offset was replaced, so the offset is
    meaningless and the read starts over.

    This is what an offset can detect. A replacement that happens to be the same
    length or longer is indistinguishable from an append, which is why the
    dashboard resets the offset whenever the run directory changes rather than
    relying on this.
    """
    ledger = tmp_path / "ledger.jsonl"
    _write(ledger, {"entry_type": "system", "content": "a much longer first entry"})
    _, offset = runs.read_ledger(ledger)
    ledger.write_text("", encoding="utf-8")
    _write(ledger, {"entry_type": "system", "content": "fresh"})
    entries, _ = runs.read_ledger(ledger, offset)
    assert [e["content"] for e in entries] == ["fresh"]


def test_a_missing_ledger_reads_as_empty(tmp_path):
    assert runs.read_ledger(tmp_path / "nope.jsonl") == ([], 0)


# --------------------------------------------------------------------------- #
# Derived numbers
# --------------------------------------------------------------------------- #


ENTRIES = [
    {"entry_type": "system", "content": "started"},
    {
        "entry_type": "response",
        "participant_id": "chatgpt",
        "round_number": 1,
        "phase_id": "positions",
        "content": "a" * 100,
        "duration_seconds": 10.0,
        "timestamp": "2026-01-01T00:00:00+00:00",
    },
    {
        "entry_type": "response",
        "participant_id": "gemini",
        "round_number": 1,
        "phase_id": "positions",
        "content": "b" * 50,
        "timestamp": "2026-01-01T00:01:00+00:00",
    },
    {"entry_type": "checkpoint", "round_number": 1, "content": "CONTINUE"},
    {
        "entry_type": "response",
        "participant_id": "chatgpt",
        "round_number": 2,
        "phase_id": "debate",
        "content": "c" * 200,
        "duration_seconds": 20.0,
        "timestamp": "2026-01-01T00:02:00+00:00",
    },
]


def test_summarise_counts_only_what_the_ledger_states():
    summary = runs.summarise(ENTRIES)
    assert summary["turns"] == 3
    assert summary["rounds"] == 2
    assert summary["agents"] == ["chatgpt", "gemini"]
    assert summary["checkpoints"] == 1
    assert summary["phase"] == "debate"


def test_agent_stats_are_per_engine():
    stats = runs.agent_stats(ENTRIES, "chatgpt")
    assert stats["turns"] == 2
    assert stats["total_chars"] == 300
    assert stats["mean_chars"] == 150
    assert stats["mean_seconds"] == 15.0
    assert stats["last_phase"] == "debate"

    quiet = runs.agent_stats(ENTRIES, "claude")
    assert quiet["turns"] == 0
    assert quiet["mean_seconds"] is None


# --------------------------------------------------------------------------- #
# Where a run is, and what it was
# --------------------------------------------------------------------------- #

_PHASES = [
    {
        "id": "research",
        "name": "Stage 1",
        "stage": "research",
        "interaction": "parallel",
        "context": "none",
        "participants": ["chatgpt", "gemini"],
    },
    {
        "id": "build",
        "name": "Stage 2",
        "stage": "build",
        "interaction": "sequential",
        "context": "auto",
        "participants": ["chatgpt", "gemini"],
    },
]


def test_position_reads_the_phase_from_the_ledger_not_from_a_guess():
    entries = [
        {"entry_type": "response", "participant_id": "chatgpt", "phase_id": "build",
         "round_number": 2},
    ]
    spot = runs.position(entries, _PHASES)
    assert spot["phase_id"] == "build"
    assert spot["stage"] == "build"
    assert spot["last_speaker"] == "chatgpt"
    assert spot["round"] == 2


def test_a_sequential_phase_names_the_expected_next_speaker():
    entries = [
        {"entry_type": "response", "participant_id": "chatgpt", "phase_id": "build",
         "round_number": 1},
    ]
    assert runs.position(entries, _PHASES)["next_speaker"] == "gemini"


def test_a_parallel_phase_has_no_single_next_speaker():
    """Drawing a turn order over a parallel round would describe a run that is
    not happening."""
    entries = [
        {"entry_type": "response", "participant_id": "chatgpt", "phase_id": "research",
         "round_number": 1},
    ]
    spot = runs.position(entries, _PHASES)
    assert spot["next_speaker"] == ""
    assert spot["working"] == ["chatgpt", "gemini"]


def test_position_before_anything_has_run_points_at_the_first_phase():
    spot = runs.position([], _PHASES)
    assert spot["phase_id"] == "research"
    assert spot["last_speaker"] == ""


def test_run_shape_reads_the_runs_own_record(tmp_path):
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "mission_name": "R-A-B",
                "meeting_type": "research_architect_build",
                "workflow": "research-architect-build",
                "interactions": ["parallel", "sequential"],
                "participants": [{"id": "chatgpt", "model": "chatgpt"}],
            }
        ),
        encoding="utf-8",
    )
    shape = runs.run_shape(run_dir)
    assert shape["workflow"] == "research-architect-build"
    assert shape["interactions"] == ["parallel", "sequential"]


def test_run_shape_is_empty_for_a_run_that_has_not_written_metadata(tmp_path):
    (tmp_path / "run-2").mkdir()
    assert runs.run_shape(tmp_path / "run-2") == {}
    assert runs.run_shape(None) == {}


def test_a_run_found_on_disk_carries_what_it_recorded(tmp_path):
    run_dir = tmp_path / "run-3"
    run_dir.mkdir()
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "mission_name": "R-A-B",
                "meeting_type": "research_architect_build",
                "workflow": "research-architect-build",
                "interactions": ["parallel"],
                "participants": [
                    {"id": "chatgpt", "model": "chatgpt"},
                    {"id": "gemini", "model": "gemini"},
                ],
            }
        ),
        encoding="utf-8",
    )
    record = next(r for r in runs.list_runs(tmp_path) if r.run_dir == str(run_dir))
    assert record.meeting_type == "research_architect_build"
    assert record.models == ["chatgpt", "gemini"]


def test_an_older_run_reports_nothing_rather_than_a_guess(tmp_path):
    """Runs recorded before these fields existed must stay honestly blank."""
    run_dir = tmp_path / "run-4"
    run_dir.mkdir()
    (run_dir / "metadata.json").write_text(
        json.dumps({"mission_name": "Old Debate"}), encoding="utf-8"
    )
    record = next(r for r in runs.list_runs(tmp_path) if r.run_dir == str(run_dir))
    assert record.meeting_type == "" and record.workflow == ""
    assert record.interactions == [] and record.models == []
