"""Starting and stopping the Conductor, from the dashboard.

A run started here is the same ``main.py`` subprocess a Founder would start in a
terminal: the dashboard is a control plane over the runtime, and it changes no
contract to drive it. What it does own is the run's identity and its directory,
which it resolves before launch and passes with an explicit ``-o`` — the one
case the runtime documents as "used verbatim".

Two defects fixed here are worth naming, because both were silent:

- **Double-fire.** Start ran in the page body and called ``st.rerun()`` from
  inside itself, so a fast second click — or any rerun landing between the click
  and the flag being written — could launch a second Conductor against the same
  Chrome session. Start and Stop are now ``on_click`` callbacks, which fire once
  per click, and the guard is checked against the live process, not just a flag.
- **Run persistence.** Every run was pinned to a flat ``outputs/`` directory and
  the previous run's ``ledger.jsonl`` was *deleted* to make room. A run now gets
  its own directory and nothing is deleted.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import streamlit as st

from ui import library, runs, state


def _python() -> str:
    """The interpreter to launch the Conductor with.

    ``sys.executable`` is the environment Streamlit itself is running in, which
    is the venv the Founder started the dashboard from. The old hardcoded
    ``.venv\\Scripts\\python.exe`` was relative to the working directory and
    silently wrong the moment the app was launched from anywhere else.
    """
    if sys.executable:
        return sys.executable
    return str(Path(".venv") / "Scripts" / "python.exe")


def _reader(process, log_queue) -> None:
    try:
        for line in iter(process.stdout.readline, ""):
            if line:
                log_queue.put(line.rstrip())
    except Exception as exc:
        log_queue.put(f"[FATAL EXCEPTION READING OUTPUT] {exc}")
    finally:
        log_queue.put(state.TERMINATED_MARK)


def build_args(mission_path: Path, run_dir: Path, cfg: dict) -> list[str]:
    """The exact command line for a run. Pure, so it can be shown and tested.

    ``--claude`` is omitted for a contract declaring
    ``metadata.claude_peer: "unsupported"``. The runtime would refuse the flag
    anyway; not sending it keeps the command line the dashboard displays equal
    to the run the dashboard actually gets.
    """
    args = [
        _python(),
        "main.py",
        str(mission_path),
        "-o",
        str(run_dir),
        "--cdp-url",
        cfg.get("cdp_url", "http://localhost:9223"),
    ]
    if cfg.get("claude_peer") and library.claude_peer_allowed(mission_path):
        args.append("--claude")
    if cfg.get("auto_pilot"):
        args.append("--auto")
    topic = (cfg.get("topic") or "").strip()
    if topic:
        args += ["--topic", topic]
    files = [f for f in cfg.get("inject_files", []) if f]
    if files:
        args += ["--inject-files", ",".join(files)]
    images = [i for i in cfg.get("inject_images", []) if i]
    if images:
        args += ["--inject-images", ",".join(images)]
    return args


def can_start() -> bool:
    return not (state.is_running() or st.session_state.launch_pending)


def start() -> None:
    """Launch a Conductor run. An ``on_click`` callback — never call it inline."""
    if not can_start():
        return
    # Claimed before anything slow happens, so a second event arriving while the
    # process is still being spawned is refused rather than queued.
    st.session_state.launch_pending = True
    st.session_state.launch_error = ""

    cfg = state.cfg()
    types = library.meeting_type_map()
    key = cfg.get("meeting_key") or next(iter(types), "")
    if key not in types:
        st.session_state.launch_pending = False
        st.session_state.launch_error = "No meeting type is selected."
        return
    mission_path = types[key][1]

    root = Path(cfg.get("output_root", "outputs"))
    # What this run IS, read from the contract about to be executed. Recorded on
    # the registry line so History can compare experiments without re-loading a
    # contract that may have been edited since.
    # Recorded as what the run will actually seat, not as what the toggle says:
    # a contract declaring claude_peer: unsupported gets two peers whatever the
    # toggle holds, and a History row claiming three would be a false record.
    claude_peer = bool(cfg.get("claude_peer")) and library.claude_peer_allowed(
        mission_path
    )
    shape = library.shape(mission_path)
    shape["roster"] = library.roster(mission_path, claude_peer=claude_peer)
    try:
        record = runs.allocate_run(
            root,
            mission_path=mission_path,
            mission_name=library.mission_name(mission_path),
            topic=(cfg.get("topic") or "").strip(),
            claude_peer=claude_peer,
            auto_pilot=bool(cfg.get("auto_pilot")),
            shape=shape,
            options={"cdp_url": cfg.get("cdp_url", "")},
        )
    except OSError as exc:
        st.session_state.launch_pending = False
        st.session_state.launch_error = f"Could not create a run directory: {exc}"
        return

    # Fresh view state for a fresh run. The ledger cache is reset rather than the
    # ledger file deleted — the previous run's transcript is evidence.
    st.session_state.logs = []
    st.session_state.run_complete = False
    st.session_state.user_stopped = False
    st.session_state.run_record = record
    st.session_state.ledger_dir = None
    st.session_state.ledger_offset = 0
    st.session_state.ledger_entries = []
    state.set_view_dir(None)

    args = build_args(mission_path, record.path, cfg)
    st.session_state.logs.append(f"[SYSTEM] Mission {record.label} → {record.path}")
    st.session_state.logs.append(f"[SYSTEM] {' '.join(args)}")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            env=env,
            bufsize=1,
        )
    except Exception as exc:
        st.session_state.launch_pending = False
        st.session_state.launch_error = f"Could not launch the Conductor: {exc}"
        st.session_state.run_record = runs.record_status(
            root, record, runs.STATUS_FAILED
        )
        return

    st.session_state.process = process
    st.session_state.is_running = True
    st.session_state.launch_pending = False
    threading.Thread(
        target=_reader,
        args=(process, st.session_state.logs_queue),
        daemon=True,
    ).start()


def stop() -> None:
    """Terminate the run. An ``on_click`` callback.

    ``user_stopped`` is set before the signal so the finish path records a stop
    rather than a completion — an aborted mission reported as "task complete"
    is a lie the evidence log would carry forward.
    """
    process = st.session_state.process
    if process is None or process.poll() is not None:
        st.session_state.is_running = False
        return
    st.session_state.user_stopped = True
    try:
        process.terminate()
        state.note("[SYSTEM] Stop requested. Subprocess terminated.")
    except Exception as exc:
        state.note(f"[SYSTEM] Could not terminate the Conductor: {exc}")
