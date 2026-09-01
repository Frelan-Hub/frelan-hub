"""Interpretation Layer — read-only reporting over the cumulative evidence log.

``evidence-log.jsonl`` accumulates one line per mission run. This module turns
that history into readable tables: how each engine scores per meeting type, how
often peer scoring actually lands, and how briefed runs compare to unbriefed
ones.

Governance boundary (the same one evidence.py and discovery.py hold): this
module REPORTS. It never decides, never promotes a template, never writes a
capability confidence, and never modifies the log it reads. Interpreting these
numbers is a governance activity that belongs to the Founder.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from frelan.evidence import SCORE_AXES


def load_log(path: Path) -> list[dict[str, Any]]:
    """Read the evidence log, skipping unparseable lines rather than halting.

    A corrupt line is a damaged record, not a reason to refuse the other 200.
    """
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and "participants" in row:
            rows.append(row)
    return rows


def _overall(score_means: dict[str, Any]) -> float | None:
    """The mean across whichever score axes a participant actually received."""
    values = [
        score_means[axis]
        for axis in SCORE_AXES
        if isinstance(score_means.get(axis), (int, float))
    ]
    return round(mean(values), 2) if values else None


def engine_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-engine aggregates across every logged run."""
    acc: dict[str, dict[str, list]] = defaultdict(
        lambda: {"overall": [], "axes": defaultdict(list), "turns": [], "citations": [], "seconds": []}
    )
    for row in rows:
        for participant in row.get("participants", {}).values():
            engine = participant.get("engine") or "unknown"
            bucket = acc[engine]
            means = participant.get("score_means") or {}
            overall = _overall(means)
            if overall is not None:
                bucket["overall"].append(overall)
            for axis in SCORE_AXES:
                if isinstance(means.get(axis), (int, float)):
                    bucket["axes"][axis].append(means[axis])
            if isinstance(participant.get("turns"), int):
                bucket["turns"].append(participant["turns"])
            if isinstance(participant.get("citations"), int):
                bucket["citations"].append(participant["citations"])
            seconds = participant.get("mean_turn_seconds")
            if isinstance(seconds, (int, float)):
                bucket["seconds"].append(seconds)

    return {
        engine: {
            "runs": len(bucket["overall"]) or sum(1 for _ in bucket["turns"]),
            "scored_runs": len(bucket["overall"]),
            "overall": round(mean(bucket["overall"]), 2) if bucket["overall"] else None,
            "axes": {
                axis: round(mean(values), 2)
                for axis, values in bucket["axes"].items()
                if values
            },
            "mean_turns": round(mean(bucket["turns"]), 1) if bucket["turns"] else None,
            "mean_citations": (
                round(mean(bucket["citations"]), 1) if bucket["citations"] else None
            ),
            "mean_turn_seconds": (
                round(mean(bucket["seconds"]), 1) if bucket["seconds"] else None
            ),
        }
        for engine, bucket in sorted(acc.items())
    }


def capture_rate(rows: list[dict[str, Any]]) -> dict[str, int | float]:
    """How reliably peer scoring actually produced data.

    Peer scores are the evidence backbone, so a silent capture failure matters
    as much as a low score. Reported, never corrected for.
    """
    total = scored = 0
    for row in rows:
        for participant in row.get("participants", {}).values():
            total += 1
            if participant.get("score_means"):
                scored += 1
    return {
        "participant_rows": total,
        "with_scores": scored,
        "rate": round(scored / total, 3) if total else 0.0,
    }


def briefing_comparison(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Briefed (topic override supplied) vs. unbriefed runs."""
    groups: dict[str, list[float]] = {"briefed": [], "unbriefed": []}
    counts: dict[str, int] = {"briefed": 0, "unbriefed": 0}
    for row in rows:
        key = "briefed" if row.get("briefed") else "unbriefed"
        counts[key] += 1
        for participant in row.get("participants", {}).values():
            overall = _overall(participant.get("score_means") or {})
            if overall is not None:
                groups[key].append(overall)
    return {
        key: {
            "runs": counts[key],
            "scored_participants": len(values),
            "overall": round(mean(values), 2) if values else None,
        }
        for key, values in groups.items()
    }


def meeting_type_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-meeting-type run counts, ratings, and mean peer score."""
    acc: dict[str, dict[str, list]] = defaultdict(
        lambda: {"overall": [], "ratings": [], "statuses": []}
    )
    for row in rows:
        key = row.get("meeting_type") or "(unlabelled)"
        bucket = acc[key]
        bucket["statuses"].append(row.get("status"))
        if isinstance(row.get("founder_rating"), int):
            bucket["ratings"].append(row["founder_rating"])
        for participant in row.get("participants", {}).values():
            overall = _overall(participant.get("score_means") or {})
            if overall is not None:
                bucket["overall"].append(overall)
    return {
        key: {
            "runs": len(bucket["statuses"]),
            "completed": sum(1 for s in bucket["statuses"] if s == "completed"),
            "founder_ratings": len(bucket["ratings"]),
            "mean_rating": (
                round(mean(bucket["ratings"]), 2) if bucket["ratings"] else None
            ),
            "overall": round(mean(bucket["overall"]), 2) if bucket["overall"] else None,
        }
        for key, bucket in sorted(acc.items())
    }


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """A plain fixed-width table — no dependency, terminal-safe."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()
    out = [line, "  ".join("-" * w for w in widths).rstrip()]
    for row in rows:
        out.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return out


def _fmt(value: Any) -> str:
    return "-" if value is None else str(value)


def render_report(rows: list[dict[str, Any]]) -> str:
    """The whole report as text."""
    if not rows:
        return (
            "# Evidence Report\n\n"
            "_No runs recorded yet. Every mission appends one line to the "
            "evidence log when it finishes._\n"
        )

    out = ["# Evidence Report", "", f"Runs recorded: {len(rows)}", ""]

    out += ["## Per-engine (mean across runs)", ""]
    out += _table(
        ["engine", "scored runs", "overall", "turns", "citations", "sec/turn"],
        [
            [
                engine,
                str(data["scored_runs"]),
                _fmt(data["overall"]),
                _fmt(data["mean_turns"]),
                _fmt(data["mean_citations"]),
                _fmt(data["mean_turn_seconds"]),
            ]
            for engine, data in engine_summary(rows).items()
        ],
    )

    out += ["", "## Per-meeting-type", ""]
    out += _table(
        ["meeting type", "runs", "completed", "ratings", "mean rating", "overall"],
        [
            [
                key,
                str(data["runs"]),
                str(data["completed"]),
                str(data["founder_ratings"]),
                _fmt(data["mean_rating"]),
                _fmt(data["overall"]),
            ]
            for key, data in meeting_type_summary(rows).items()
        ],
    )

    out += ["", "## Briefed vs. unbriefed", ""]
    out += _table(
        ["group", "runs", "scored participants", "overall"],
        [
            [key, str(data["runs"]), str(data["scored_participants"]), _fmt(data["overall"])]
            for key, data in briefing_comparison(rows).items()
        ],
    )

    capture = capture_rate(rows)
    out += [
        "",
        "## Peer-score capture",
        "",
        f"{capture['with_scores']} of {capture['participant_rows']} participant rows "
        f"carry peer scores ({capture['rate']:.0%}).",
    ]
    if capture["rate"] < 0.8:
        out += [
            "",
            "Note: peer scores are the evidence backbone, so a capture rate this "
            "low means the accumulated comparisons rest on partial data.",
        ]

    ratings = sum(1 for r in rows if isinstance(r.get("founder_rating"), int))
    if not ratings:
        out += [
            "",
            "Note: no run carries a founder rating, so the Mission Library has no "
            "promotion signal to rank templates by.",
        ]
    return "\n".join(out) + "\n"
