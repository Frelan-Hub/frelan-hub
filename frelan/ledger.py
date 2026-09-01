"""Interpretation Layer — the Ledger.

The ledger is not merely a log; it is the **canonical execution record**. Every
prompt, response, checkpoint decision, and system event is appended here in
order. Downstream artifacts (Markdown reports, runtime metadata) are derived
from it, and it is the substrate a future context-refresh or replay feature
would read from.

The ledger is append-only. Entries, once written, are immutable frozen
dataclasses; the ordered sequence only ever grows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from frelan.enums import LedgerEntryType


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """A single immutable entry in the execution record.

    ``timestamp`` is an ISO-8601 string in UTC. The optional context fields
    (``phase_id`` / ``round_number`` / ``participant_id`` / ``role``) are absent
    for system-level entries that are not tied to a specific turn.

    ``duration_seconds`` is how long a RESPONSE took to obtain, measured around
    the transport call. It is recorded explicitly rather than inferred from
    timestamps: a turn's PROMPT and RESPONSE are both appended *after* the
    response arrives, so the gap between their timestamps is under a
    millisecond and says nothing about how long the model actually took.
    """

    seq: int
    timestamp: str
    entry_type: LedgerEntryType
    content: str
    phase_id: str | None = None
    round_number: int | None = None
    participant_id: str | None = None
    role: str | None = None
    duration_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (replay / metadata hook)."""
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "entry_type": self.entry_type.value,
            "content": self.content,
            "phase_id": self.phase_id,
            "round_number": self.round_number,
            "participant_id": self.participant_id,
            "role": self.role,
            "duration_seconds": self.duration_seconds,
        }


class Ledger:
    """Append-only, ordered execution record.

    A ``clock`` may be injected for deterministic timestamps in tests; it
    defaults to wall-clock UTC.
    """

    def __init__(
        self,
        clock: Callable[[], datetime] = _utc_now,
        autosave_path: Path | None = None,
    ) -> None:
        self._entries: list[LedgerEntry] = []
        self._clock = clock
        # When set, every entry is persisted to this .jsonl file the moment it
        # is appended — a killed process never loses completed rounds.
        self._autosave_path = autosave_path

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        """An immutable snapshot of the record so far."""
        return tuple(self._entries)

    def append(
        self,
        entry_type: LedgerEntryType,
        content: str,
        *,
        phase_id: str | None = None,
        round_number: int | None = None,
        participant_id: str | None = None,
        role: str | None = None,
        duration_seconds: float | None = None,
    ) -> LedgerEntry:
        """Append a new entry and return it.

        ``seq`` is assigned automatically (1-based) and ``timestamp`` is stamped
        from the injected clock.
        """
        entry = LedgerEntry(
            seq=len(self._entries) + 1,
            timestamp=self._clock().isoformat(),
            entry_type=entry_type,
            content=content,
            phase_id=phase_id,
            round_number=round_number,
            participant_id=participant_id,
            role=role,
            duration_seconds=duration_seconds,
        )
        self._entries.append(entry)
        self._autosave(entry)
        return entry

    def restore_entry(self, data: dict[str, Any]) -> LedgerEntry:
        """Re-add a previously persisted entry verbatim (mission resume).

        ``seq`` and ``timestamp`` are preserved from the persisted record so
        the restored ledger is byte-for-byte the same history, not a re-stamped
        copy.
        """
        entry = LedgerEntry(
            seq=int(data.get("seq") or len(self._entries) + 1),
            timestamp=str(data.get("timestamp") or self._clock().isoformat()),
            entry_type=LedgerEntryType(data["entry_type"]),
            content=str(data.get("content", "")),
            phase_id=data.get("phase_id"),
            round_number=data.get("round_number"),
            participant_id=data.get("participant_id"),
            role=data.get("role"),
            # Absent in records written before durations were captured; such a
            # turn is simply unmeasured, not zero.
            duration_seconds=data.get("duration_seconds"),
        )
        self._entries.append(entry)
        self._autosave(entry)
        return entry

    def _autosave(self, entry: LedgerEntry) -> None:
        if self._autosave_path is None:
            return
        try:
            with self._autosave_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")
        except OSError as exc:
            # Persistence must never halt a live mission; warn and continue.
            print(f"[WARNING] Ledger autosave to {self._autosave_path} failed: {exc}")

    def checkpoint_summaries(self) -> tuple[LedgerEntry, ...]:
        """All checkpoint entries, in order."""
        return tuple(
            e for e in self._entries if e.entry_type is LedgerEntryType.CHECKPOINT
        )

    def to_dict(self) -> dict[str, Any]:
        """Full serialization for replay / future context refresh."""
        return {"entries": [e.to_dict() for e in self._entries]}

    def to_markdown(self) -> str:
        """Render the whole ledger as a readable Markdown document."""
        lines = ["# Mission Ledger", ""]
        if not self._entries:
            lines.append("_No entries recorded._")
            return "\n".join(lines) + "\n"

        for entry in self._entries:
            header = f"## [{entry.seq:03d}] {entry.entry_type.value.upper()}"
            lines.append(header)

            meta_parts = [f"time: {entry.timestamp}"]
            if entry.phase_id is not None:
                meta_parts.append(f"phase: {entry.phase_id}")
            if entry.round_number is not None:
                meta_parts.append(f"round: {entry.round_number}")
            if entry.participant_id is not None:
                who = entry.participant_id
                if entry.role is not None:
                    who += f" ({entry.role})"
                meta_parts.append(f"participant: {who}")
            lines.append("- " + " · ".join(meta_parts))

            lines.append("")
            lines.append(entry.content.strip())
            lines.append("")

        return "\n".join(lines) + "\n"
