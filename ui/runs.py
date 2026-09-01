"""Run identity, persistence, and artifact reading for the dashboard.

Three problems this module exists to fix, all of them consequences of the old
dashboard pinning every run to a single flat ``outputs/`` directory:

1. **Run persistence.** Each run now gets its own ``outputs/run-<UTC-stamp>/``
   directory, allocated with the runtime's own naming helper so the CLI and the
   dashboard agree on what a run directory is. Nothing is ever deleted to make
   room for the next run.
2. **Unique run IDs.** A run gets a monotonic integer the moment it is
   allocated, rendered as ``#0043``. The header shows it; History lists by it.
   The counter lives in an append-only registry beside the run directories, so
   it survives an app restart — session state cannot be the source of truth for
   an identifier that outlives the session.
3. **Cheap live reads.** ``read_ledger`` reads only the bytes appended since the
   caller's last offset, so a one-second refresh does not re-parse a
   several-hundred-kilobyte ledger every tick.

Deliberately free of Streamlit imports: this is the part worth testing, and a
test should not need a script run context to exercise it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

# The runtime owns what a run directory is called and where the resume pointer
# lives. Importing them keeps one canonical source rather than a second spelling
# of the same convention that can drift.
from main import DEFAULT_OUTPUT_DIR, LAST_RUN_POINTER, _new_run_dir

# Append-only registry of runs launched from the dashboard. Named like the
# resume pointer it sits beside: infrastructure for the outputs tree, not a run
# artifact. Later records for the same run_id supersede earlier ones, exactly
# the way the mission ledger treats its own append-only history.
RUN_REGISTRY = ".runs.jsonl"

# Artifacts a run directory may contain, in the order a reader wants them.
KNOWN_ARTIFACTS = (
    "ledger.md",
    "checkpoints.md",
    "metadata.json",
    "evidence.json",
    "ledger.jsonl",
)

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_STOPPED = "stopped"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"


@dataclass(frozen=True)
class RunRecord:
    """One launched run. ``run_id`` is None for a run found on disk only."""

    run_dir: str
    mission_path: str = ""
    mission_name: str = ""
    run_id: int | None = None
    status: str = STATUS_UNKNOWN
    started_at: str = ""
    ended_at: str = ""
    topic: str = ""
    claude_peer: bool = False
    auto_pilot: bool = False
    # What the run WAS, structurally. Read from the run's own metadata.json for
    # a run found on disk, and from the contract at launch for one started here.
    # Every field defaults to empty: a run recorded before these existed reports
    # nothing rather than a plausible-looking guess.
    meeting_type: str = ""
    workflow: str = ""
    interactions: tuple[str, ...] | list[str] = ()
    models: tuple[str, ...] | list[str] = ()
    options: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        """``#0043`` for a registered run; the directory name for a found one."""
        if self.run_id is None:
            return Path(self.run_dir).name
        return f"#{self.run_id:04d}"

    @property
    def path(self) -> Path:
        return Path(self.run_dir)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, raw: dict) -> "RunRecord":
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        known.setdefault("run_dir", "")
        return cls(**known)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


def _registry_path(root: Path) -> Path:
    return Path(root) / RUN_REGISTRY


def _read_registry(root: Path) -> list[dict]:
    """Every registry line, oldest first.

    A corrupt line is skipped rather than fatal: a half-written record must
    never cost the Founder the whole run history.
    """
    path = _registry_path(root)
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _append_registry(root: Path, payload: dict) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with _registry_path(root).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def next_run_id(root: Path) -> int:
    """One past the highest ID ever registered under ``root``.

    Derived from the registry rather than from a count of directories: deleting
    an old run directory must not hand its number to a new run, because the
    number is how the Founder refers to that run afterwards.
    """
    highest = 0
    for row in _read_registry(root):
        rid = row.get("run_id")
        if isinstance(rid, int) and rid > highest:
            highest = rid
    return highest + 1


def allocate_run(
    root: Path | None = None,
    *,
    mission_path: Path | str,
    mission_name: str = "",
    topic: str = "",
    claude_peer: bool = False,
    auto_pilot: bool = False,
    shape: dict | None = None,
    options: dict | None = None,
) -> RunRecord:
    """Reserve an ID and a fresh directory for a run about to start.

    The ``.last-run`` pointer is updated too, so ``python main.py --resume``
    with no ``-o`` resumes a run that was started from the dashboard. Without
    that, the two front ends would disagree about which run is "the last one".
    """
    root = DEFAULT_OUTPUT_DIR if root is None else Path(root)
    root.mkdir(parents=True, exist_ok=True)

    run_dir = _new_run_dir(root)
    run_dir.mkdir(parents=True, exist_ok=True)
    (root / LAST_RUN_POINTER).write_text(str(run_dir), encoding="utf-8")

    record = RunRecord(
        run_id=next_run_id(root),
        run_dir=str(run_dir),
        mission_path=str(mission_path),
        mission_name=mission_name,
        status=STATUS_RUNNING,
        started_at=now_iso(),
        topic=topic,
        claude_peer=claude_peer,
        auto_pilot=auto_pilot,
        # Recorded at launch so History can describe a run that is still going,
        # and so a run whose metadata.json never lands (stopped, crashed) is
        # still identifiable by what it was.
        meeting_type=str((shape or {}).get("meeting_type") or ""),
        workflow=str((shape or {}).get("workflow") or ""),
        interactions=list((shape or {}).get("interactions") or []),
        models=sorted(
            {
                str(seat.get("engine"))
                for seat in ((shape or {}).get("roster") or [])
                if seat.get("engine")
            }
        ),
        options=options or {},
    )
    _append_registry(root, asdict(record))
    return record


def record_status(
    root: Path | None,
    record: RunRecord,
    status: str,
    *,
    ended_at: str | None = None,
) -> RunRecord:
    """Append a superseding record carrying the run's new status."""
    root = DEFAULT_OUTPUT_DIR if root is None else Path(root)
    updated = replace(
        record,
        status=status,
        ended_at=ended_at if ended_at is not None else now_iso(),
    )
    _append_registry(root, asdict(updated))
    return updated


def list_runs(root: Path | None = None) -> list[RunRecord]:
    """Every known run, newest first.

    Two sources, merged: the registry (runs launched from the dashboard, which
    carry an ID and the options they ran with) and a scan of ``run-*``
    directories (runs launched from a terminal, which carry only what their
    ``metadata.json`` says). A run started in a terminal is still part of the
    Founder's history and must not be invisible here.
    """
    root = DEFAULT_OUTPUT_DIR if root is None else Path(root)
    folded: dict[str, RunRecord] = {}
    for row in _read_registry(root):
        record = RunRecord.from_dict(row)
        if record.run_dir:
            folded[str(Path(record.run_dir))] = record

    if root.is_dir():
        for candidate in sorted(root.iterdir()):
            if not candidate.is_dir() or not candidate.name.startswith("run-"):
                continue
            key = str(candidate)
            if key not in folded:
                folded[key] = _record_from_disk(candidate)
        # The legacy flat directory: every pre-per-run-directory dashboard run
        # overwrote it. Its last occupant is still readable, so list it rather
        # than pretend the artifacts are not there.
        if (root / "metadata.json").is_file():
            folded.setdefault(str(root), _record_from_disk(root))

    runs = list(folded.values())
    runs.sort(key=lambda r: (r.started_at or "", r.run_dir), reverse=True)
    return runs


def _record_from_disk(run_dir: Path) -> RunRecord:
    meta = read_json(run_dir / "metadata.json") or {}
    participants = meta.get("participants")
    models: list[str] = []
    if isinstance(participants, list):
        models = sorted(
            {
                str(entry.get("model"))
                for entry in participants
                if isinstance(entry, dict) and entry.get("model")
            }
        )
    interactions = meta.get("interactions")
    return RunRecord(
        run_dir=str(run_dir),
        mission_name=str(meta.get("mission_name", "")),
        status=str(meta.get("status") or STATUS_UNKNOWN),
        started_at=str(meta.get("started_at", "")),
        ended_at=str(meta.get("ended_at", "")),
        topic=str(meta.get("topic_override") or ""),
        meeting_type=str(meta.get("meeting_type") or ""),
        workflow=str(meta.get("workflow") or ""),
        interactions=[str(i) for i in interactions] if isinstance(interactions, list) else [],
        models=models,
    )


def run_shape(run_dir: Path | str | None) -> dict:
    """The structural record a finished run wrote about itself.

    Straight from that run's ``metadata.json`` — never re-derived from whatever
    contract the dashboard happens to be pointing at now, which may be a
    different meeting type entirely. ``{}`` when the run has not written it yet
    (a live run writes metadata at the end) or predates the field.
    """
    if run_dir is None:
        return {}
    meta = read_json(Path(run_dir) / "metadata.json") or {}
    keys = ("meeting_type", "workflow", "interactions", "stages", "phases",
            "participants")
    return {k: meta[k] for k in keys if k in meta}


def find_run(root: Path | None, run_dir: Path | str) -> RunRecord | None:
    target = str(Path(run_dir))
    for record in list_runs(root):
        if str(record.path) == target:
            return record
    return None


# --------------------------------------------------------------------------- #
# Ledger + artifacts
# --------------------------------------------------------------------------- #


def read_ledger(path: Path, offset: int = 0) -> tuple[list[dict], int]:
    """Entries appended since ``offset``, and the new offset.

    Reads bytes, not lines, and stops at the last complete newline: the ledger
    is being appended to by another process while this runs, so its tail may be
    a half-written record. Consuming that would raise on partial JSON and then
    skip the record forever once the writer finished the line.

    A file shorter than ``offset`` was replaced, so the read starts over. A
    replacement that is the same length or longer cannot be told from an append
    by offset alone; the caller resets the offset when the run directory
    changes, which is the case that actually arises.
    """
    path = Path(path)
    if not path.is_file():
        return [], 0
    size = path.stat().st_size
    if size < offset:  # file replaced or truncated — start over
        offset = 0
    if size == offset:
        return [], offset
    with path.open("rb") as fh:
        fh.seek(offset)
        chunk = fh.read(size - offset)
    cut = chunk.rfind(b"\n")
    if cut == -1:
        return [], offset  # nothing complete yet
    complete = chunk[: cut + 1]
    entries: list[dict] = []
    for line in complete.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries, offset + len(complete)


def read_json(path: Path) -> dict | None:
    """Parsed JSON, or None for missing, unreadable, or partly-written files."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return None
    return parsed if isinstance(parsed, dict) else None


def run_artifacts(run_dir: Path) -> list[Path]:
    """Known artifacts present in a run directory, in reading order."""
    run_dir = Path(run_dir)
    return [run_dir / name for name in KNOWN_ARTIFACTS if (run_dir / name).is_file()]


def turn_entries(entries: list[dict]) -> list[dict]:
    return [e for e in entries if e.get("entry_type") == "response"]


def summarise(entries: list[dict]) -> dict:
    """Headline numbers for the status cards, straight from ledger entries.

    Counts only what the ledger states. ``rounds`` is the highest round number
    seen, not an estimate of how many remain — an unknown stays unknown.
    """
    responses = turn_entries(entries)
    rounds = [
        e.get("round_number") for e in entries if isinstance(e.get("round_number"), int)
    ]
    participants = [e.get("participant_id") for e in responses if e.get("participant_id")]
    phases = [e.get("phase_id") for e in entries if e.get("phase_id")]
    return {
        "turns": len(responses),
        "rounds": max(rounds) if rounds else 0,
        "agents": sorted(set(participants)),
        "checkpoints": sum(1 for e in entries if e.get("entry_type") == "checkpoint"),
        "phase": phases[-1] if phases else "",
        "last_at": responses[-1].get("timestamp", "") if responses else "",
    }


def position(entries: list[dict], phases: list[dict]) -> dict:
    """Where the run actually is, from the ledger and the contract's phases.

    Facts first: ``last_speaker`` and ``phase`` come from the ledger and are
    what happened. ``next_speaker`` is the only derived value, taken from the
    phase's declared turn order, and it is labelled as *expected* wherever it is
    shown — the runtime, not the dashboard, decides who speaks.

    For a phase declaring ``parallel`` there is no single next speaker: the
    whole round is in flight together, so ``working`` names the round's
    participants and ``next_speaker`` stays empty. A dashboard that drew an
    A → B → C chain over a parallel round would be describing a run that is not
    happening.
    """
    by_id = {ph.get("id"): ph for ph in phases}
    responses = turn_entries(entries)
    last = responses[-1] if responses else None
    phase_id = (last or {}).get("phase_id") or (phases[0].get("id") if phases else "")
    phase = by_id.get(phase_id, phases[0] if phases else {})
    members = list(phase.get("participants") or [])
    interaction = phase.get("interaction") or "sequential"

    last_speaker = str((last or {}).get("participant_id") or "")
    next_speaker = ""
    if members and interaction != "parallel":
        if last_speaker in members:
            next_speaker = members[(members.index(last_speaker) + 1) % len(members)]
        else:
            next_speaker = members[0]

    return {
        "phase_id": phase.get("id", ""),
        "phase_name": phase.get("name", ""),
        "stage": phase.get("stage", ""),
        "interaction": interaction,
        "participants": members,
        "last_speaker": last_speaker,
        "next_speaker": next_speaker,
        "working": members if interaction == "parallel" else [],
        "round": (last or {}).get("round_number") or 0,
    }


def agent_stats(entries: list[dict], participant_id: str) -> dict:
    """Per-engine live status: turns taken, characters produced, last spoken."""
    mine = [
        e
        for e in turn_entries(entries)
        if str(e.get("participant_id", "")).lower() == participant_id.lower()
    ]
    chars = sum(len(e.get("content") or "") for e in mine)
    durations = [
        e.get("duration_seconds")
        for e in mine
        if isinstance(e.get("duration_seconds"), (int, float))
    ]
    return {
        "turns": len(mine),
        "total_chars": chars,
        "mean_chars": round(chars / len(mine)) if mine else 0,
        "mean_seconds": round(sum(durations) / len(durations), 1) if durations else None,
        "last_at": mine[-1].get("timestamp", "") if mine else "",
        "last_phase": mine[-1].get("phase_id", "") if mine else "",
        "last_role": mine[-1].get("role", "") if mine else "",
    }
