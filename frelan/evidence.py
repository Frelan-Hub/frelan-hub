"""Interpretation Layer — Evidence collection.

Pure functions that extract operational evidence from a finished mission:
reciprocal peer scores (the ``frelan-scores`` blocks the renderer requests on
the final phase) plus objective per-participant metrics from the ledger.

Boundary (CAPABILITY-ORCHESTRATION.md §7/§18): missions PRODUCE evidence; they
never write their own capability standing. Reading the accumulated evidence
(``--report``) and acting on it is a separate, human step.
"""

from __future__ import annotations

import re
from statistics import mean
from typing import Any

from frelan.enums import LedgerEntryType
from frelan.mission_instance import MissionInstance

SCORE_AXES = ("evidence_quality", "reasoning_depth", "actionability", "responsiveness")

_BLOCK_RE = re.compile(r"```frelan-scores\s*\n(.*?)\n```", re.DOTALL)
_CITATION_RE = re.compile(r"https?://\S+|\[\d+\]")


def _parse_block_body(block: str) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "target":
            entry["target"] = value
        elif key == "justification":
            entry["justification"] = value
        elif key in SCORE_AXES:
            try:
                entry[key] = min(5, max(1, int(float(value))))
            except ValueError:
                continue
    return entry


def _bare_bodies(text: str) -> list[str]:
    """Score blocks WITHOUT fences.

    Responses are harvested from the rendered DOM via innerText, which strips
    markdown fences entirely (a real run parsed 0 scores while the blocks were
    plainly visible on screen). Recover them as 'target:' key-value line runs.
    """
    bodies: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        key = stripped.split(":", 1)[0].strip().lower() if ":" in stripped else ""
        if key == "target":
            if current:
                bodies.append("\n".join(current))
            current = [line]
        elif current is not None:
            if key in SCORE_AXES or key == "justification":
                current.append(line)
            else:
                bodies.append("\n".join(current))
                current = None
    if current:
        bodies.append("\n".join(current))
    return bodies


def parse_score_blocks(text: str) -> list[dict[str, Any]]:
    """Extract ``frelan-scores`` blocks from a response.

    Tolerant by design — models format loosely and the DOM strips fences:
    fenced and bare blocks are both accepted, unknown keys are ignored,
    scores are clamped to 1..5, malformed values are skipped, and blocks
    missing a ``target`` or carrying no score at all are dropped.
    """
    scores: list[dict[str, Any]] = []
    for block in list(_BLOCK_RE.findall(text)) + _bare_bodies(text):
        entry = _parse_block_body(block)
        if (
            entry.get("target")
            and any(axis in entry for axis in SCORE_AXES)
            and entry not in scores  # fenced blocks also match the bare scan
        ):
            scores.append(entry)
    return scores


def collect_evidence(instance: MissionInstance) -> dict[str, Any]:
    """Build the JSON-ready evidence record for one mission run.

    Peer scores: parsed from every RESPONSE entry; the scorer is the entry's
    participant. Self-scores and scores for unknown targets carry no evidence
    and are discarded. Objective metrics are deterministic facts from the
    ledger — they are never "random": turns, response volume, citations, and
    harvested artifacts per participant.
    """
    mission = instance.mission
    responses = [
        e for e in instance.ledger.entries if e.entry_type is LedgerEntryType.RESPONSE
    ]
    systems = [
        e for e in instance.ledger.entries if e.entry_type is LedgerEntryType.SYSTEM
    ]

    participants: dict[str, dict[str, Any]] = {}
    for p in mission.participants:
        own = [e for e in responses if e.participant_id == p.id]
        chars = [len(e.content) for e in own]
        # Turns recorded before durations were captured are unmeasured, not
        # zero-latency; they are excluded rather than averaged in as 0.
        durations = [
            e.duration_seconds for e in own if e.duration_seconds is not None
        ]
        participants[p.id] = {
            "engine": p.assigned_engine.execution_engine,
            "capabilities": list(p.assigned_engine.required_capabilities),
            "metrics": {
                "turns": len(own),
                "total_chars": sum(chars),
                "mean_chars": int(mean(chars)) if chars else 0,
                "timed_turns": len(durations),
                "mean_turn_seconds": round(mean(durations), 1) if durations else None,
                "total_turn_seconds": round(sum(durations), 1) if durations else None,
                "citations": sum(len(_CITATION_RE.findall(e.content)) for e in own),
                "artifacts": sum(
                    1
                    for e in systems
                    if "[HARVESTED ARTIFACT]" in e.content and p.display_name in e.content
                ),
            },
            "scores_received": {"by_scorer": {}, "means": {}},
        }

    for entry in responses:
        scorer = entry.participant_id or ""
        for score in parse_score_blocks(entry.content):
            target = score["target"]
            if target == scorer or target not in participants:
                continue
            axes = {axis: score[axis] for axis in SCORE_AXES if axis in score}
            if "justification" in score:
                axes["justification"] = score["justification"]
            participants[target]["scores_received"]["by_scorer"][scorer] = axes

    for pdata in participants.values():
        by_scorer = pdata["scores_received"]["by_scorer"]
        for axis in SCORE_AXES:
            values = [s[axis] for s in by_scorer.values() if axis in s]
            if values:
                pdata["scores_received"]["means"][axis] = round(mean(values), 2)

    entries = instance.ledger.entries
    return {
        "mission_id": mission.id,
        "mission_name": mission.name,
        "meeting_type": mission.metadata.get("meeting_type", ""),
        # What the run was actually about — the override when the Founder
        # supplied one, since that is what every prompt was rendered from.
        "objective": instance.context.get(
            "topic_override", mission.objective
        ).strip(),
        # Did the Founder supply a Session Briefing this run? Lets accumulated
        # evidence compare briefed vs. standard runs (MISSION-CONFIGURATION-
        # RESOLUTION.md §6). Derived from context, never from the contract.
        "briefed": bool(
            instance.context.get("topic_override")
            or instance.context.get("prompt_inject")
        ),
        "status": instance.status.value,
        "started_at": entries[0].timestamp if entries else None,
        "ended_at": entries[-1].timestamp if entries else None,
        "participants": participants,
    }
