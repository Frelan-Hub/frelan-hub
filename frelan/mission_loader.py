"""Interpretation Layer — the Mission Loader.

Loads a Mission Contract from YAML or JSON, validates it against the rules in
``MISSION-CONTRACT.md`` (Section 5), and returns an immutable ``Mission``.

Validation is the system boundary: everything downstream trusts that a
successfully loaded ``Mission`` is well-formed. The loader therefore reports
*all* discovered problems together (not just the first) so a contract author
can fix them in one pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from frelan.enums import (
    DEFAULT_INTERACTION,
    DEFAULT_PARTICIPANT_TYPE,
    INTERACTION_SUPPORT,
    ParticipantType,
)
from frelan.mission_contract import (
    AssignedEngine,
    Capability,
    ExecutionPhase,
    GovernancePolicy,
    Mission,
    OutputDefinition,
    Participant,
)

# Declarative per-phase context policy (MISSION-CONTRACT.md 3.3). Optional;
# "auto" is the default and reproduces the behaviour of contracts written
# before the field existed.
_PHASE_CONTEXT_POLICIES = frozenset({"auto", "none", "delta", "full"})

# Declarative per-phase interaction pattern. Optional; "sequential" is the
# default and reproduces the behaviour of contracts written before the field
# existed. The set comes from ``frelan.enums`` so the vocabulary has one
# spelling: an interaction the runtime cannot execute must be refused at the
# boundary, loudly, rather than silently degrading to sequential mid-run.
_PHASE_INTERACTIONS = frozenset(INTERACTION_SUPPORT)

# What a participant is. Declarative — the interpreter never branches on it.
_PARTICIPANT_TYPES = frozenset(t.value for t in ParticipantType)

_REQUIRED_TOP_LEVEL = (
    "id",
    "name",
    "objective",
    "capabilities",
    "participants",
    "phases",
    "governance",
    "outputs",
)


class MissionValidationError(Exception):
    """Raised when a contract is structurally or semantically invalid.

    Carries the full list of problems on ``.errors``.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        joined = "\n- ".join(errors)
        super().__init__(f"invalid mission contract:\n- {joined}")


def load_mission(path: str | Path) -> Mission:
    """Read, validate, and build a ``Mission`` from ``path`` (.yaml/.yml/.json)."""
    path = Path(path)
    raw = _read(path)
    errors = _validate(raw)
    if errors:
        raise MissionValidationError(errors)
    return _build(raw)


# -- reading ---------------------------------------------------------------


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise MissionValidationError([f"contract file not found: {path}"])
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text)
    elif suffix == ".json":
        data = json.loads(text)
    else:
        raise MissionValidationError(
            [f"unsupported contract format {suffix!r}; use .yaml, .yml or .json"]
        )
    if not isinstance(data, dict):
        raise MissionValidationError(["contract root must be a mapping/object"])
    return data


# -- validation ------------------------------------------------------------


def _validate(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in _REQUIRED_TOP_LEVEL:
        if key not in raw:
            errors.append(f"missing required top-level key: {key!r}")

    capability_ids = _validate_capabilities(raw.get("capabilities"), errors)
    participant_ids = _validate_participants(
        raw.get("participants"), capability_ids, errors
    )
    _validate_phases(raw.get("phases"), participant_ids, errors)
    _validate_governance(raw.get("governance"), participant_ids, errors)
    _validate_outputs(raw.get("outputs"), errors)
    return errors


def _require_str(mapping: Any, key: str, where: str, errors: list[str]) -> None:
    value = mapping.get(key) if isinstance(mapping, dict) else None
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where}.{key} must be a non-empty string")


def _check_duplicates(ids: list[str], noun: str, errors: list[str]) -> None:
    seen: set[str] = set()
    for identifier in ids:
        if identifier in seen:
            errors.append(f"duplicate {noun} id: {identifier!r}")
        seen.add(identifier)


def _validate_capabilities(value: Any, errors: list[str]) -> set[str]:
    if not isinstance(value, list):
        if value is not None:
            errors.append("'capabilities' must be a list")
        return set()
    ids: list[str] = []
    for i, cap in enumerate(value):
        where = f"capabilities[{i}]"
        if not isinstance(cap, dict):
            errors.append(f"{where} must be a mapping")
            continue
        _require_str(cap, "id", where, errors)
        _require_str(cap, "description", where, errors)
        if isinstance(cap.get("id"), str):
            ids.append(cap["id"])
    _check_duplicates(ids, "capability", errors)
    return set(ids)


def _validate_participants(
    value: Any, capability_ids: set[str], errors: list[str]
) -> set[str]:
    if not isinstance(value, list):
        if value is not None:
            errors.append("'participants' must be a list")
        return set()
    if not value:
        errors.append("'participants' must contain at least one participant")
    ids: list[str] = []
    for i, part in enumerate(value):
        where = f"participants[{i}]"
        if not isinstance(part, dict):
            errors.append(f"{where} must be a mapping")
            continue
        _require_str(part, "id", where, errors)
        _require_str(part, "display_name", where, errors)
        if isinstance(part.get("id"), str):
            ids.append(part["id"])

        # Optional identity fields. An unrecognised type is refused here rather
        # than shown in the dashboard as a participant of unknown kind.
        participant_type = part.get("type")
        if participant_type is not None and participant_type not in _PARTICIPANT_TYPES:
            allowed = ", ".join(sorted(_PARTICIPANT_TYPES))
            errors.append(f"{where}.type must be one of: {allowed}")
        instructions = part.get("instructions")
        if instructions is not None and not isinstance(instructions, str):
            errors.append(f"{where}.instructions must be a string")
        _validate_assigned_engine(
            part.get("assigned_engine"), f"{where}.assigned_engine",
            capability_ids, errors,
        )
    _check_duplicates(ids, "participant", errors)
    return set(ids)


def _validate_assigned_engine(
    value: Any, where: str, capability_ids: set[str], errors: list[str]
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{where} must be a mapping")
        return
    _require_str(value, "role", where, errors)
    _require_str(value, "transport_provider", where, errors)
    _require_str(value, "execution_engine", where, errors)

    required = value.get("required_capabilities", [])
    if not isinstance(required, list):
        errors.append(f"{where}.required_capabilities must be a list")
        return
    for cap_id in required:
        if cap_id not in capability_ids:
            errors.append(
                f"{where}.required_capabilities references undeclared "
                f"capability: {cap_id!r}"
            )


def _validate_phases(
    value: Any, participant_ids: set[str], errors: list[str]
) -> None:
    if not isinstance(value, list):
        if value is not None:
            errors.append("'phases' must be a list")
        return
    if not value:
        errors.append("'phases' must contain at least one phase")
    ids: list[str] = []
    for i, phase in enumerate(value):
        where = f"phases[{i}]"
        if not isinstance(phase, dict):
            errors.append(f"{where} must be a mapping")
            continue
        _require_str(phase, "id", where, errors)
        _require_str(phase, "name", where, errors)
        _require_str(phase, "objective", where, errors)
        if isinstance(phase.get("id"), str):
            ids.append(phase["id"])

        # Validated here for the same reason governance.max_rounds is: an
        # unchecked value survives loading and only fails inside
        # is_phase_complete's comparison, mid-mission, after the browser work
        # for the run has already been spent.
        phase_max = phase.get("max_rounds")
        if phase_max is not None and not _is_positive_int(phase_max):
            errors.append(f"{where}.max_rounds must be a positive integer or null")

        # Context policy is optional; an unrecognised value must fail here
        # rather than silently degrade to the default halfway through a run.
        context_policy = phase.get("context")
        if context_policy is not None and context_policy not in _PHASE_CONTEXT_POLICIES:
            allowed = ", ".join(sorted(_PHASE_CONTEXT_POLICIES))
            errors.append(f"{where}.context must be one of: {allowed}")

        # An interaction the runtime cannot execute must fail at load, not be
        # quietly run as something else. Naming the supported set in the error
        # is what makes "unsupported interactions fail clearly" true for the
        # contract author rather than only for the runtime.
        interaction = phase.get("interaction")
        if interaction is not None and interaction not in _PHASE_INTERACTIONS:
            allowed = ", ".join(
                f"{name} ({status})"
                for name, status in sorted(INTERACTION_SUPPORT.items())
            )
            errors.append(
                f"{where}.interaction must be one of: {allowed}; "
                f"got {interaction!r}"
            )

        stage = phase.get("stage")
        if stage is not None and not isinstance(stage, str):
            errors.append(f"{where}.stage must be a string")

        members = phase.get("participant_ids")
        if not isinstance(members, list) or not members:
            errors.append(f"{where}.participant_ids must be a non-empty list")
        else:
            for pid in members:
                if pid not in participant_ids:
                    errors.append(
                        f"{where}.participant_ids references undeclared "
                        f"participant: {pid!r}"
                    )
    _check_duplicates(ids, "phase", errors)


def _validate_governance(
    value: Any, participant_ids: set[str], errors: list[str]
) -> None:
    if not isinstance(value, dict):
        if value is not None:
            errors.append("'governance' must be a mapping")
        return
    interval = value.get("checkpoint_interval")
    if not _is_positive_int(interval):
        errors.append("governance.checkpoint_interval must be an integer >= 1")
    max_rounds = value.get("max_rounds")
    if max_rounds is not None and not _is_positive_int(max_rounds):
        errors.append("governance.max_rounds must be a positive integer or null")
    synthesiser = value.get("synthesiser")
    if synthesiser is not None and synthesiser not in participant_ids:
        errors.append(
            "governance.synthesiser references undeclared participant: "
            f"{synthesiser!r}"
        )


def _validate_outputs(value: Any, errors: list[str]) -> None:
    if not isinstance(value, list):
        if value is not None:
            errors.append("'outputs' must be a list")
        return
    ids: list[str] = []
    for i, output in enumerate(value):
        where = f"outputs[{i}]"
        if not isinstance(output, dict):
            errors.append(f"{where} must be a mapping")
            continue
        for key in ("id", "title", "description", "filename"):
            _require_str(output, key, where, errors)
        # An output filename is joined onto the run directory, so it must be a
        # bare name. Rejected here rather than sanitised at write time: the
        # loader is the boundary, and a contract asking to write outside the run
        # directory is a contract to fix, not to quietly rewrite.
        filename = output.get("filename")
        if isinstance(filename, str) and filename.strip():
            if _escapes_output_dir(filename):
                errors.append(
                    f"{where}.filename must be a bare filename with no path "
                    f"separators, drive letter, or '..': {filename!r}"
                )
        if isinstance(output.get("id"), str):
            ids.append(output["id"])
    _check_duplicates(ids, "output", errors)


def _escapes_output_dir(filename: str) -> bool:
    """True when ``filename`` is anything other than a bare relative name."""
    name = filename.strip()
    return (
        "/" in name
        or "\\" in name
        or ".." in name
        or Path(name).is_absolute()
        or name in (".", "")
    )


def _is_positive_int(value: Any) -> bool:
    # bool is a subclass of int; reject it explicitly.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


# -- building --------------------------------------------------------------


def _build(raw: dict[str, Any]) -> Mission:
    capabilities = tuple(
        Capability(id=c["id"], description=c["description"])
        for c in raw["capabilities"]
    )
    participants = tuple(
        Participant(
            id=p["id"],
            display_name=p["display_name"],
            type=p.get("type") or DEFAULT_PARTICIPANT_TYPE,
            instructions=p.get("instructions") or "",
            assigned_engine=AssignedEngine(
                role=p["assigned_engine"]["role"],
                required_capabilities=tuple(
                    p["assigned_engine"].get("required_capabilities", [])
                ),
                transport_provider=p["assigned_engine"]["transport_provider"],
                execution_engine=p["assigned_engine"]["execution_engine"],
            ),
        )
        for p in raw["participants"]
    )
    phases = tuple(
        ExecutionPhase(
            id=ph["id"],
            name=ph["name"],
            objective=ph["objective"],
            participant_ids=tuple(ph["participant_ids"]),
            instructions=ph.get("instructions", ""),
            max_rounds=ph.get("max_rounds"),
            context=ph.get("context") or "auto",
            interaction=ph.get("interaction") or DEFAULT_INTERACTION,
            stage=ph.get("stage") or "",
        )
        for ph in raw["phases"]
    )
    gov = raw["governance"]
    governance = GovernancePolicy(
        checkpoint_interval=gov["checkpoint_interval"],
        max_rounds=gov.get("max_rounds"),
        convergence_note=gov.get("convergence_note", ""),
        escalation_note=gov.get("escalation_note", ""),
        synthesiser=gov.get("synthesiser"),
    )
    outputs = tuple(
        OutputDefinition(
            id=o["id"],
            title=o["title"],
            description=o["description"],
            filename=o["filename"],
        )
        for o in raw["outputs"]
    )
    metadata = MappingProxyType(
        {str(k): str(v) for k, v in raw.get("metadata", {}).items()}
    )
    return Mission(
        id=raw["id"],
        name=raw["name"],
        objective=raw["objective"],
        participants=participants,
        capabilities=capabilities,
        phases=phases,
        governance=governance,
        outputs=outputs,
        metadata=metadata,
    )
