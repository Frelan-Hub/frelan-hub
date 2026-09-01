"""Tests for the read-only evidence reporter (frelan/report.py)."""

from __future__ import annotations

import json
from pathlib import Path

from frelan.report import (
    briefing_comparison,
    capture_rate,
    engine_summary,
    load_log,
    meeting_type_summary,
    render_report,
)


def _participant(engine: str, *, overall: float | None, turns: int = 3, seconds=None):
    means = (
        {axis: overall for axis in ("evidence_quality", "reasoning_depth")}
        if overall is not None
        else {}
    )
    entry = {"engine": engine, "score_means": means, "turns": turns, "citations": 2}
    if seconds is not None:
        entry["mean_turn_seconds"] = seconds
    return entry


def _row(**overrides):
    row = {
        "ts": "2026-07-13T00:00:00+00:00",
        "run_dir": "outputs/run-x",
        "mission_id": "m1",
        "meeting_type": "app_planning",
        "briefed": False,
        "founder_rating": None,
        "status": "completed",
        "participants": {
            "chatgpt": _participant("chatgpt", overall=4.0),
            "gemini": _participant("gemini", overall=5.0),
        },
    }
    row.update(overrides)
    return row


def _write(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "evidence-log.jsonl"
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    return path


# -- loading ----------------------------------------------------------------


def test_missing_log_loads_as_empty(tmp_path):
    assert load_log(tmp_path / "nope.jsonl") == []


def test_corrupt_lines_are_skipped_not_fatal(tmp_path):
    path = tmp_path / "evidence-log.jsonl"
    path.write_text(
        json.dumps(_row()) + "\n{ not json\n\n" + json.dumps(_row()) + "\n",
        encoding="utf-8",
    )
    assert len(load_log(path)) == 2


def test_lines_without_participants_are_ignored(tmp_path):
    path = _write(tmp_path, [_row()])
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "x", "note": "not an evidence row"}) + "\n")
    assert len(load_log(path)) == 1


# -- aggregates -------------------------------------------------------------


def test_engine_summary_averages_across_runs():
    rows = [
        _row(participants={"chatgpt": _participant("chatgpt", overall=4.0)}),
        _row(participants={"chatgpt": _participant("chatgpt", overall=5.0)}),
    ]
    summary = engine_summary(rows)
    assert summary["chatgpt"]["overall"] == 4.5
    assert summary["chatgpt"]["scored_runs"] == 2


def test_engine_summary_ignores_unscored_participants():
    rows = [
        _row(participants={"claude": _participant("claude", overall=None)}),
        _row(participants={"claude": _participant("claude", overall=3.0)}),
    ]
    summary = engine_summary(rows)
    assert summary["claude"]["scored_runs"] == 1
    assert summary["claude"]["overall"] == 3.0


def test_engine_summary_reports_latency_only_when_measured():
    unmeasured = engine_summary([_row(participants={"a": _participant("a", overall=4.0)})])
    assert unmeasured["a"]["mean_turn_seconds"] is None

    measured = engine_summary(
        [_row(participants={"a": _participant("a", overall=4.0, seconds=42.0)})]
    )
    assert measured["a"]["mean_turn_seconds"] == 42.0


def test_capture_rate_counts_participants_with_scores():
    rows = [
        _row(
            participants={
                "a": _participant("a", overall=4.0),
                "b": _participant("b", overall=None),
            }
        )
    ]
    assert capture_rate(rows) == {
        "participant_rows": 2,
        "with_scores": 1,
        "rate": 0.5,
    }


def test_briefing_comparison_splits_by_the_briefed_flag():
    rows = [
        _row(briefed=True, participants={"a": _participant("a", overall=5.0)}),
        _row(briefed=False, participants={"a": _participant("a", overall=3.0)}),
    ]
    comparison = briefing_comparison(rows)
    assert comparison["briefed"]["overall"] == 5.0
    assert comparison["unbriefed"]["overall"] == 3.0


def test_meeting_type_summary_labels_missing_types():
    summary = meeting_type_summary([_row(meeting_type="")])
    assert "(unlabelled)" in summary


def test_meeting_type_summary_counts_ratings_and_completions():
    rows = [
        _row(meeting_type="app_planning", founder_rating=5, status="completed"),
        _row(meeting_type="app_planning", founder_rating=None, status="running"),
    ]
    data = meeting_type_summary(rows)["app_planning"]
    assert data == {
        "runs": 2,
        "completed": 1,
        "founder_ratings": 1,
        "mean_rating": 5.0,
        "overall": 4.5,
    }


# -- rendering --------------------------------------------------------------


def test_empty_log_renders_a_readable_notice():
    assert "No runs recorded yet" in render_report([])


def test_report_flags_a_low_capture_rate():
    rows = [
        _row(
            participants={
                "a": _participant("a", overall=4.0),
                "b": _participant("b", overall=None),
            }
        )
    ]
    text = render_report(rows)
    assert "50%" in text
    assert "evidence backbone" in text


def test_report_flags_the_absence_of_founder_ratings():
    assert "no promotion signal" in render_report([_row()])


def test_report_does_not_flag_ratings_when_present():
    assert "no promotion signal" not in render_report([_row(founder_rating=4)])


def test_report_reads_the_real_log_shape(tmp_path):
    """End-to-end over a file, in the shape write_outputs actually emits."""
    path = _write(tmp_path, [_row(), _row(briefed=True)])
    text = render_report(load_log(path))
    assert "Runs recorded: 2" in text
    assert "chatgpt" in text
