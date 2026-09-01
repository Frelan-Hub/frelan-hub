"""Meeting-type menu and contract briefs, without Streamlit.

The menu is the filesystem scan ``main.py`` uses for the CLI menu — one source
of truth for what the Mission Library contains, so adding a meeting type stays
"drop a .yaml in missions/", never "edit two menus"
(MISSION-LIBRARY-RESOLUTION.md §8.4).

Kept free of Streamlit so a test can assert on the brief itself rather than on
a rendered caption, and so the view layer is free to cache these calls without
the cache reaching into rendering.
"""

from __future__ import annotations

from pathlib import Path

from main import (
    DEFAULT_MISSION,
    _discover_meeting_types,
    _meeting_type_brief,
    claude_peer_supported,
)
from frelan.enums import INTERACTION_SUPPORT
from frelan.mission_loader import load_mission

# A long objective is shown cut at a word boundary with the whole text one click
# away — never silently dropped. 240 characters is about four lines of caption.
OBJECTIVE_INLINE_CHARS = 240

# Kept under the old name too: the dashboard's tests and any external reader
# refer to it, and renaming a constant is not worth breaking them over.
_OBJECTIVE_INLINE_CHARS = OBJECTIVE_INLINE_CHARS

LEGACY_LABEL = "Legacy debate (frelan_debate.yaml)"


def meeting_type_map() -> dict[str, tuple[str, Path]]:
    """``{key: (label, path)}`` for the meeting-type picker.

    The legacy default is a dev fixture and so is excluded from the library
    scan; it is appended explicitly because a select box has no "press Enter to
    keep the default" fallback the way the CLI menu does.
    """
    scanned = list(enumerate(_discover_meeting_types(), start=1))
    types: dict[str, tuple[str, Path]] = {
        str(i): (f"[{group}] {label}" if group else label, path)
        for i, (label, path, group) in scanned
    }
    types[str(len(scanned) + 1)] = (LEGACY_LABEL, DEFAULT_MISSION)
    return types


def brief(path: Path) -> dict[str, str]:
    """What a meeting type is for, what it drives at, and its phase skeleton.

    Returns ``{}`` for a contract that does not load — the same silence the
    library scan gives it, rather than a confident-looking empty brief.
    """
    return _meeting_type_brief(path)


def split_objective(objective: str) -> tuple[str, bool]:
    """``(text_to_show, was_truncated)`` — cut at a word, never mid-word."""
    if not objective or len(objective) <= OBJECTIVE_INLINE_CHARS:
        return objective, False
    head = objective[:OBJECTIVE_INLINE_CHARS].rsplit(" ", 1)[0]
    return f"{head}…", True


def declared_outputs(path: Path) -> list[dict]:
    """The mission's declared final-answer files: title, filename, description.

    Read straight from the contract's ``outputs`` block — the authoritative
    source of which files are deliverables — so the panel names the right file
    for whichever meeting type is selected, not a hardcoded ``recommendation.md``.
    Returns ``[]`` if the contract does not load.
    """
    try:
        mission = load_mission(path)
    except Exception:
        return []
    return [
        {"title": o.title, "filename": o.filename, "description": o.description}
        for o in mission.outputs
    ]


def claude_peer_allowed(path: Path) -> bool:
    """Whether the contract at ``path`` permits Claude as an injected peer.

    The dashboard's own reading of ``metadata.claude_peer`` — the same key
    ``main.py`` refuses on — so a toggle can be disabled before a run is started
    rather than the run being refused after it. Permissive for a contract that
    does not load, which the Setup view reports on its own.
    """
    try:
        return claude_peer_supported(load_mission(path))
    except Exception:
        return True


def roster(path: Path, *, claude_peer: bool) -> list[dict]:
    """The engines this mission will actually seat, and in what role.

    Claude is appended when the Founder has asked for a third peer, because
    ``--claude`` injects it into every phase as an equal peer — the roster the
    Agents view shows must match the roster the run will use, not the roster
    the contract file happens to name.

    A contract declaring ``metadata.claude_peer: "unsupported"`` is the case
    where those two differ: the runtime will refuse the injection, so the seat
    must not be drawn either. Showing it would be the dashboard promising a
    roster the run cannot deliver.
    """
    try:
        mission = load_mission(path)
    except Exception:
        return []
    seats = [
        {
            "id": p.id,
            "display_name": p.display_name,
            "role": p.assigned_engine.role,
            # ``engine`` is the Execution-Layer selector the contract declares;
            # the dashboard shows it under the heading "Model", which is what it
            # means to a reader. The field keeps its name so nothing that reads
            # a roster has to change.
            "engine": p.assigned_engine.execution_engine,
            "transport": p.assigned_engine.transport_provider,
            "capabilities": list(p.assigned_engine.required_capabilities),
            "type": p.type,
            "standing_brief": p.instructions.strip(),
            "injected": False,
        }
        for p in mission.participants
    ]
    if (
        claude_peer
        and claude_peer_supported(mission)
        and not any(s["id"].lower() == "claude" for s in seats)
    ):
        seats.append(
            {
                "id": "claude",
                "display_name": "Claude",
                "role": "peer",
                "engine": "claude",
                "transport": "browser",
                "capabilities": [],
                "type": "model",
                "standing_brief": "",
                "injected": True,
            }
        )
    return seats


def shape(path: Path) -> dict:
    """What the selected mission IS, structurally — for the dashboard.

    Meeting type, workflow, the interaction each phase declares, and the support
    status of each of those interactions. The status comes from
    ``frelan.enums.INTERACTION_SUPPORT``, the same table the loader validates
    against, so the dashboard can never present a pattern as functional that the
    runtime would refuse — nor claim as proven one that is only implemented.

    Returns ``{}`` for a contract that does not load.
    """
    try:
        mission = load_mission(path)
    except Exception:
        return {}
    phases = [
        {
            "id": ph.id,
            "name": ph.name,
            "objective": ph.objective,
            "stage": ph.stage,
            "interaction": ph.interaction,
            "context": ph.context,
            "participants": list(ph.participant_ids),
            "max_rounds": ph.max_rounds,
        }
        for ph in mission.phases
    ]
    used = sorted({ph["interaction"] for ph in phases})
    return {
        "meeting_type": mission.metadata.get("meeting_type", ""),
        "format": " ".join(mission.metadata.get("format", "").split()),
        "workflow": mission.metadata.get("workflow", ""),
        "phases": phases,
        "stages": [ph["stage"] for ph in phases if ph["stage"]],
        "interactions": used,
        "interaction_support": {
            name: INTERACTION_SUPPORT.get(name, "unknown") for name in used
        },
        "synthesiser": mission.governance.synthesiser
        or (mission.participants[0].id if mission.participants else ""),
        # Same keys a finished run writes into metadata.json, so a consumer can
        # read either source without knowing which it got. Without this the
        # provenance panel could name the synthesiser but not what backs it.
        "participants": [
            {
                "id": p.id,
                "display_name": p.display_name,
                "type": p.type,
                "model": p.assigned_engine.execution_engine,
                "role": p.assigned_engine.role,
            }
            for p in mission.participants
        ],
    }


def interaction_catalogue() -> list[tuple[str, str]]:
    """``[(name, support)]`` for every interaction the runtime can execute.

    The dashboard lists exactly this and nothing more. A pattern that is only a
    concept in the documentation must not appear as a choice here.
    """
    return sorted(INTERACTION_SUPPORT.items())


def governance(path: Path) -> dict:
    """The contract's governance policy, for the Governance view."""
    try:
        mission = load_mission(path)
    except Exception:
        return {}
    policy = mission.governance
    return {
        "checkpoint_interval": policy.checkpoint_interval,
        "max_rounds": policy.max_rounds,
        "convergence_note": policy.convergence_note,
        "escalation_note": policy.escalation_note,
        "synthesiser": policy.synthesiser
        or (mission.participants[0].id if mission.participants else ""),
        "phases": [
            {
                "id": ph.id,
                "name": ph.name,
                "objective": ph.objective,
                "participants": list(ph.participant_ids),
                "max_rounds": ph.max_rounds,
                "context": ph.context,
                # Carried so the Governance view can show the phase completely.
                # Interaction is NOT governance — it is how the participants
                # work, not how the mission is controlled — and the view labels
                # it as such rather than folding it into the policy table.
                "interaction": ph.interaction,
                "stage": ph.stage,
            }
            for ph in mission.phases
        ],
    }


def mission_name(path: Path) -> str:
    try:
        return load_mission(path).name
    except Exception:
        return Path(path).stem
