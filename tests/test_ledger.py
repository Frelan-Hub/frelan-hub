"""Focused tests for the Ledger (ledger.py).

These pin the canonical-record guarantees the interpreter relies on: 1-based
monotonic sequencing, deterministic timestamps, snapshot immutability,
checkpoint filtering, and faithful serialization.
"""

from __future__ import annotations

from datetime import datetime, timezone

from frelan.enums import LedgerEntryType
from frelan.ledger import Ledger


def _fixed_clock():
    stamp = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
    return lambda: stamp


def test_autosave_persists_every_entry_as_jsonl(tmp_path):
    import json

    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(clock=_fixed_clock(), autosave_path=path)
    ledger.append(LedgerEntryType.SYSTEM, "started")
    ledger.append(
        LedgerEntryType.RESPONSE, "hi", participant_id="chatgpt", role="peer"
    )

    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2  # persisted the moment each entry was appended
    assert lines[1]["participant_id"] == "chatgpt"
    assert lines[1]["entry_type"] == "response"


def test_restore_entry_preserves_seq_and_timestamp():
    ledger = Ledger()
    entry = ledger.restore_entry(
        {
            "seq": 7,
            "timestamp": "2026-07-11T00:00:00+00:00",
            "entry_type": "response",
            "content": "x",
            "phase_id": "debate",
            "round_number": 2,
            "participant_id": "gemini",
            "role": "peer",
        }
    )
    assert entry.seq == 7  # history restored verbatim, not re-stamped
    assert entry.timestamp == "2026-07-11T00:00:00+00:00"
    assert entry.entry_type is LedgerEntryType.RESPONSE
    assert ledger.entries[-1] is entry


def test_append_assigns_monotonic_1_based_seq():
    ledger = Ledger(clock=_fixed_clock())
    first = ledger.append(LedgerEntryType.PROMPT, "a")
    second = ledger.append(LedgerEntryType.RESPONSE, "b")
    assert (first.seq, second.seq) == (1, 2)


def test_append_stamps_injected_clock():
    ledger = Ledger(clock=_fixed_clock())
    entry = ledger.append(LedgerEntryType.SYSTEM, "started")
    assert entry.timestamp == "2026-07-07T12:00:00+00:00"


def test_entries_snapshot_is_immutable_and_frozen_in_time():
    ledger = Ledger(clock=_fixed_clock())
    ledger.append(LedgerEntryType.PROMPT, "a")
    snapshot = ledger.entries  # take a snapshot...
    ledger.append(LedgerEntryType.RESPONSE, "b")  # ...then keep appending
    assert len(snapshot) == 1  # old snapshot must not have grown
    assert isinstance(snapshot, tuple)


def test_checkpoint_summaries_filters_to_checkpoints_only():
    ledger = Ledger(clock=_fixed_clock())
    ledger.append(LedgerEntryType.PROMPT, "a")
    ledger.append(LedgerEntryType.CHECKPOINT, "converged")
    ledger.append(LedgerEntryType.RESPONSE, "b")
    ledger.append(LedgerEntryType.CHECKPOINT, "continue")
    summaries = ledger.checkpoint_summaries()
    assert [e.content for e in summaries] == ["converged", "continue"]


def test_to_dict_round_trips_fields():
    ledger = Ledger(clock=_fixed_clock())
    ledger.append(
        LedgerEntryType.RESPONSE,
        "hello",
        phase_id="debate",
        round_number=2,
        participant_id="gemini",
        role="critic",
        duration_seconds=12.5,
    )
    data = ledger.to_dict()
    assert data["entries"][0] == {
        "seq": 1,
        "timestamp": "2026-07-07T12:00:00+00:00",
        "entry_type": "response",
        "content": "hello",
        "phase_id": "debate",
        "round_number": 2,
        "participant_id": "gemini",
        "role": "critic",
        "duration_seconds": 12.5,
    }


def test_duration_survives_the_persistence_round_trip():
    """How long a turn took must survive resume, not be re-derived."""
    ledger = Ledger(clock=_fixed_clock())
    original = ledger.append(
        LedgerEntryType.RESPONSE, "hello", participant_id="gemini", duration_seconds=8.25
    )

    restored = Ledger(clock=_fixed_clock()).restore_entry(original.to_dict())

    assert restored.duration_seconds == 8.25


def test_records_written_before_durations_restore_as_unmeasured():
    """Backward compatibility: an older ledger.jsonl has no duration field."""
    legacy = {
        "seq": 1,
        "timestamp": "2026-07-07T12:00:00+00:00",
        "entry_type": "response",
        "content": "hello",
        "participant_id": "gemini",
    }

    restored = Ledger(clock=_fixed_clock()).restore_entry(legacy)

    assert restored.duration_seconds is None


def test_to_markdown_includes_content_and_handles_empty():
    empty = Ledger(clock=_fixed_clock())
    assert "No entries recorded" in empty.to_markdown()

    ledger = Ledger(clock=_fixed_clock())
    ledger.append(LedgerEntryType.RESPONSE, "the answer is 42", participant_id="chatgpt")
    md = ledger.to_markdown()
    assert "the answer is 42" in md
    assert "chatgpt" in md
