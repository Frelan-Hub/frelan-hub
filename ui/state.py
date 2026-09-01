"""Session state, configuration persistence, and live-stream draining.

The dashboard is now eight views rather than one page, and that breaks the
pattern the old single-page app relied on. Streamlit discards the session-state
entry for any widget that was not rendered on the current run, so a topic typed
on **Setup** would be gone the moment the Founder opened **Agents**. Every
setting therefore lives in one plain dictionary, ``st.session_state.cfg``, and
widgets only *mirror* into it through an ``on_change`` callback. The launcher
reads ``cfg`` and never a widget key.

The same file owns the console-log drain and checkpoint detection, because both
are derived from one append-only list of subprocess output lines and it should
be obvious that there is only one.
"""

from __future__ import annotations

import queue
from pathlib import Path
from typing import Any, Callable

import streamlit as st

from ui import runs

# Text the browser transport prints when it is blocked waiting on a governance
# decision. Matching the runtime's own line keeps detection honest: the panel is
# offered when the process is actually waiting, not on a guess about timing.
CHECKPOINT_MARK = "Checkpoint — choose:"
USER_ACTION_MARK = "[USER ACTION]"
TERMINATED_MARK = "[SYSTEM] Subprocess has terminated."

INPUTS_DIR = Path("inputs")

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg"}

# What a run is configured with. Plain values only — anything here must survive
# being carried between views, which a widget's own state does not.
CFG_DEFAULTS: dict[str, Any] = {
    "meeting_key": "",
    "topic": "",
    "claude_peer": False,
    "auto_pilot": False,
    "cdp_url": "http://localhost:9223",
    "inject_files": [],
    "inject_images": [],
    "output_root": "outputs",
    "refresh_seconds": 1.0,
}

# Everything else the session tracks. Grouped in one place so the set of things
# a session remembers is readable at a glance instead of scattered down the file.
SESSION_DEFAULTS: dict[str, Any] = {
    "page": "Overview",
    "process": None,
    "logs": [],
    "is_running": False,
    "launch_pending": False,
    "run_complete": False,
    "user_stopped": False,
    "run_record": None,
    "view_dir": None,
    "ledger_dir": None,
    "ledger_offset": 0,
    "ledger_entries": [],
    "launch_error": "",
}


def init() -> None:
    """Create any missing session keys. Safe to call on every rerun."""
    if "cfg" not in st.session_state:
        st.session_state.cfg = dict(CFG_DEFAULTS)
    else:
        for key, value in CFG_DEFAULTS.items():
            st.session_state.cfg.setdefault(key, value)
    if "logs_queue" not in st.session_state:
        st.session_state.logs_queue = queue.Queue()
    for key, value in SESSION_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = list(value) if isinstance(value, list) else value


def cfg() -> dict[str, Any]:
    return st.session_state.cfg


def get(key: str, default: Any = None) -> Any:
    return st.session_state.cfg.get(key, default)


def put(key: str, value: Any) -> None:
    st.session_state.cfg[key] = value


def persist(widget_key: str, config_key: str) -> Callable[[], None]:
    """An ``on_change`` callback that mirrors a widget's value into ``cfg``.

    Using a callback rather than reading the widget's return value matters on a
    multi-view app: the callback fires in the same run as the interaction, so
    the value is in ``cfg`` before any view — including one the Founder is about
    to navigate to — reads it.
    """

    def _mirror() -> None:
        st.session_state.cfg[config_key] = st.session_state[widget_key]

    return _mirror


def go(page: str) -> Callable[[], None]:
    """An ``on_click`` callback that switches the active view."""

    def _switch() -> None:
        st.session_state.page = page

    return _switch


# --------------------------------------------------------------------------- #
# Live process state
# --------------------------------------------------------------------------- #


def process_alive() -> bool:
    proc = st.session_state.process
    return proc is not None and proc.poll() is None


def is_running() -> bool:
    """True while a Conductor subprocess is alive.

    Reconciles the flag against the operating system on every call, so a run
    that died without printing its terminator does not leave the dashboard
    claiming to be live forever.
    """
    if st.session_state.is_running and st.session_state.process is not None:
        if st.session_state.process.poll() is not None:
            _mark_finished()
    return bool(st.session_state.is_running)


def _mark_finished() -> None:
    st.session_state.is_running = False
    st.session_state.launch_pending = False
    record = st.session_state.run_record
    if st.session_state.user_stopped:
        status = runs.STATUS_STOPPED
    else:
        st.session_state.run_complete = True
        status = runs.STATUS_COMPLETED
    if record is not None and record.status == runs.STATUS_RUNNING:
        st.session_state.run_record = runs.record_status(
            Path(get("output_root", "outputs")), record, status
        )


def drain_logs() -> None:
    """Move queued subprocess lines into the session's log list.

    Called by the live fragments rather than by the page body: the drain has to
    happen on every refresh tick, and after fragments took over the refreshing
    the page body no longer runs every second.
    """
    try:
        while True:
            line = st.session_state.logs_queue.get_nowait()
            st.session_state.logs.append(line)
            if TERMINATED_MARK in line and st.session_state.is_running:
                _mark_finished()
    except queue.Empty:
        pass


def note(line: str) -> None:
    st.session_state.logs.append(line)


def scan_checkpoint(logs: list[str]) -> tuple[bool, list[str]]:
    """``(decision_pending, summary_lines)`` for a list of console lines.

    Pending means the last checkpoint menu the runtime printed has not yet had a
    decision sent after it. Scanning from the end rather than looking at the
    last few lines is what makes this correct across several checkpoints in one
    run: the old "is the menu in the last five lines" test went false as soon as
    anything else was printed, and true again for a menu already answered.

    Pure, and separate from :func:`checkpoint_state`, so the rule can be tested
    without a script run context.
    """
    menu_idx = -1
    for i in range(len(logs) - 1, -1, -1):
        if CHECKPOINT_MARK in logs[i]:
            menu_idx = i
            break
    if menu_idx < 0:
        return False, []
    for line in logs[menu_idx + 1 :]:
        if line.startswith(USER_ACTION_MARK):
            return False, []

    start = 0
    for j in range(menu_idx - 1, -1, -1):
        if logs[j].startswith(USER_ACTION_MARK) or CHECKPOINT_MARK in logs[j]:
            start = j + 1
            break
    summary = [line for line in logs[start:menu_idx] if line.strip()]
    return True, summary


def checkpoint_state() -> tuple[bool, list[str]]:
    """:func:`scan_checkpoint` over this session's captured console output."""
    return scan_checkpoint(st.session_state.logs)


def send_decision(key: str, human: str) -> None:
    """Write a checkpoint key to the running subprocess's stdin.

    An ``on_click`` callback, so one click sends exactly one key. The old code
    ran this in the page body and called ``st.rerun()`` from inside it, which is
    what allowed a decision to be written twice for a single click.
    """
    proc = st.session_state.process
    if proc is None or proc.poll() is not None or proc.stdin is None:
        note("[UI] Ignored — no live Conductor process to steer.")
        return
    pending, _ = checkpoint_state()
    if not pending:
        note("[UI] Ignored — no checkpoint is waiting for a decision.")
        return
    try:
        proc.stdin.write(f"{key}\n")
        proc.stdin.flush()
    except Exception as exc:  # a dead pipe must read as a failure, not a decision
        note(f"[UI] Could not send decision: {exc}")
        return
    note(f"{USER_ACTION_MARK} {human}")


# --------------------------------------------------------------------------- #
# Which run is being looked at
# --------------------------------------------------------------------------- #


def active_dir() -> Path | None:
    """The directory of the run currently executing, if any."""
    record = st.session_state.run_record
    return record.path if record is not None else None


def view_dir() -> Path | None:
    """The run directory the read-only views should read.

    Defaults to the active run, so during a mission every view follows it. Set
    explicitly by History, which is how an old run can be inspected without
    disturbing — or being overwritten by — the one that is executing.
    """
    chosen = st.session_state.view_dir
    if chosen is not None:
        return Path(chosen)
    return active_dir()


def set_view_dir(path: Path | str | None) -> None:
    st.session_state.view_dir = None if path is None else str(path)


def viewing_active_run() -> bool:
    active = active_dir()
    return active is not None and view_dir() == active


def ledger_entries() -> list[dict]:
    """Every ledger entry for the viewed run, read incrementally.

    Only bytes appended since the last read are parsed. At a one-second refresh
    over a ledger that grows past a hundred kilobytes, re-parsing the whole file
    every tick was the single most expensive thing the dashboard did.
    """
    target = view_dir()
    if target is None:
        st.session_state.ledger_dir = None
        st.session_state.ledger_offset = 0
        st.session_state.ledger_entries = []
        return []
    if st.session_state.ledger_dir != str(target):
        st.session_state.ledger_dir = str(target)
        st.session_state.ledger_offset = 0
        st.session_state.ledger_entries = []
    fresh, offset = runs.read_ledger(
        Path(target) / "ledger.jsonl", st.session_state.ledger_offset
    )
    if fresh:
        st.session_state.ledger_entries = st.session_state.ledger_entries + fresh
    st.session_state.ledger_offset = offset
    # The first line is the run's ``_meta`` header, not a transcript entry.
    return [e for e in st.session_state.ledger_entries if "entry_type" in e]


def ledger_meta() -> dict:
    for entry in st.session_state.ledger_entries:
        if "_meta" in entry:
            meta = entry["_meta"]
            return meta if isinstance(meta, dict) else {}
    return {}


# --------------------------------------------------------------------------- #
# Uploads
# --------------------------------------------------------------------------- #


def store_uploads(uploaded) -> tuple[list[str], list[str]]:
    """Save uploaded files to ``inputs/`` and sort them into files and images.

    Written to disk at upload time rather than at launch time: an upload has to
    survive the Founder navigating to another view, and Streamlit's uploader
    state does not.
    """
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    images: list[str] = []
    for item in uploaded or []:
        path = INPUTS_DIR / item.name
        try:
            path.write_bytes(item.getbuffer())
        except Exception as exc:
            note(f"[UI] Could not save upload {item.name}: {exc}")
            continue
        if path.suffix.lower() in IMAGE_SUFFIXES:
            images.append(str(path.resolve()))
        else:
            files.append(str(path))
    return files, images
