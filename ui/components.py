"""Renderers shared by more than one view.

Everything that has to keep up with a live run is wrapped in :func:`live`, a
fragment that refreshes only itself. The old dashboard slept a second at the
bottom of the script and called ``st.rerun()``, re-running the whole page — and
with it a full scan of ``missions/``, a reload of the selected contract, and a
re-parse of the entire ledger — once per second for the length of a mission.
Now the run's live regions refresh and nothing else does.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Callable

import streamlit as st

from ui import cache, runs, state, theme

AVATARS = {"chatgpt": "🟢", "gemini": "🔵", "claude": "🟠"}

# How much transcript is drawn before the Founder has to ask for the rest. A run
# can reach several hundred entries; redrawing all of them every refresh tick is
# the one thing that would make fragments as slow as the full rerun they replace.
TRANSCRIPT_WINDOW = 30


def live(render: Callable[[], None], *, running: bool) -> None:
    """Render inside a fragment that self-refreshes only while a run is live."""
    interval = state.get("refresh_seconds", 1.0) if running else None
    st.fragment(run_every=interval)(render)()


def runtime_state() -> tuple[str, str, bool]:
    """``(state_key, label, is_live)`` for the header pill and status cards."""
    if st.session_state.launch_pending:
        return "waiting", "starting", True
    if state.is_running():
        pending, _ = state.checkpoint_state()
        if pending:
            return "waiting", "awaiting decision", True
        return "running", "running", True
    if st.session_state.launch_error:
        return "failed", "launch failed", False
    if st.session_state.user_stopped:
        return "stopped", "stopped", False
    if st.session_state.run_complete:
        return "complete", "complete", False
    return "idle", "idle", False


def run_label() -> str:
    record = st.session_state.run_record
    if record is None:
        return "NO ACTIVE MISSION"
    return f"MISSION {record.label}"


def short_time(iso: str) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso[:16]


def since(iso: str) -> str:
    """A short "how long ago", or an em dash when there is nothing to say."""
    if not iso:
        return "—"
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return "—"
    delta = (datetime.now(moment.tzinfo) - moment).total_seconds()
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #


def status_cards() -> None:
    """The headline numbers: runtime state, seated agents, turns, rounds."""
    key, label, _ = runtime_state()
    entries = state.ledger_entries()
    summary = runs.summarise(entries)
    record = st.session_state.run_record
    theme.cards(
        [
            ("RUNTIME", label.upper(), key),
            ("AGENTS", str(len(summary["agents"])), key if summary["agents"] else "idle"),
            ("TURNS", str(summary["turns"]), "idle"),
            ("ROUNDS", str(summary["rounds"]), "idle"),
            ("MISSION", record.label if record else "—", "idle"),
        ]
    )


# --------------------------------------------------------------------------- #
# Governance
# --------------------------------------------------------------------------- #


def governance_panel(*, show_summary: bool = True) -> None:
    """The checkpoint decision board — the one control that blocks a live run.

    Every button is an ``on_click`` callback, so a click sends exactly one key
    to the Conductor's stdin. Sending from the page body, as the old dashboard
    did, let one click write the key twice.
    """
    pending, summary = state.checkpoint_state()
    running = state.is_running()

    if not running:
        st.caption("Idle — start a run to receive governance checkpoints here.")
        return
    if not pending:
        st.caption("The Conductor is working. No checkpoint is pending.")
        return

    with st.container(border=True):
        st.markdown("**Your decision is required**")
        if show_summary and summary:
            with st.expander("Checkpoint summary from the Conductor", expanded=False):
                st.code("\n".join(summary[-40:]), language="text")
        choices = [
            ("C", "Continue", "Run another discussion round", "primary"),
            ("V", "Converged", "Peers agree — proceed to final synthesis", "secondary"),
            ("E", "Escalate", "Deadlock — escalate the decision", "secondary"),
            ("T", "Terminate", "Stop the mission immediately", "secondary"),
            ("F", "Fully automate", "Auto-continue every remaining checkpoint", "secondary"),
        ]
        for key, label, helptext, kind in choices:
            st.button(
                label,
                key=f"ckpt_{key}",
                width="stretch",
                type=kind,
                help=helptext,
                on_click=state.send_decision,
                args=(key, f"Decision: {label.upper()}"),
            )
        st.caption("Edit Prompt ([P]) is a terminal-only control for now.")


def checkpoint_history(entries: list[dict]) -> None:
    decisions = [e for e in entries if e.get("entry_type") == "checkpoint"]
    if not decisions:
        st.caption("No checkpoint decisions recorded for this run.")
        return
    for entry in decisions:
        st.markdown(
            f"- **Round {entry.get('round_number', '—')}** · "
            f"`{short_time(entry.get('timestamp', ''))}` — {entry.get('content', '')}"
        )


# --------------------------------------------------------------------------- #
# Agents
# --------------------------------------------------------------------------- #


def agent_grid(seats: list[dict], entries: list[dict], *, detailed: bool = False) -> None:
    """One card per seated participant, with its identity and live statistics.

    A participant is not always a model: a contract may seat an *agent* — a
    configured worker built around a model, with its own standing brief and
    role. The card states which it is, and which model backs it, because
    "Gemini" and "Research Agent (Gemini)" are different things to reason about
    and the old card could not tell them apart.
    """
    if not seats:
        st.caption("This contract declares no participants that could be read.")
        return
    columns = st.columns(len(seats))
    for column, seat in zip(columns, seats):
        stats = runs.agent_stats(entries, seat["id"])
        active = stats["turns"] > 0
        kind = (seat.get("type") or "model").lower()
        rows = [
            ("Type", kind.capitalize()),
            ("Model", seat.get("engine", "—")),
            ("Turns", str(stats["turns"])),
            ("Last spoke", since(stats["last_at"])),
        ]
        if detailed:
            rows += [
                ("Mean reply", f"{stats['mean_chars']:,} chars" if stats["turns"] else "—"),
                (
                    "Mean turn",
                    f"{stats['mean_seconds']}s" if stats["mean_seconds"] else "—",
                ),
                ("Last phase", stats["last_phase"] or "—"),
                ("Transport", seat.get("transport", "—")),
                ("Standing brief", "yes" if seat.get("standing_brief") else "none"),
            ]
        body = "".join(
            f'<div class="cp-agent-row"><span>{html.escape(k)}</span><b>{html.escape(v)}</b></div>'
            for k, v in rows
        )
        dot = theme.STATE_COLOURS["running" if active else "idle"]
        column.markdown(
            '<div class="cp-agent">'
            '<div class="cp-agent-head">'
            f'<span class="cp-dot" style="background:{dot}"></span>'
            f'<span class="cp-agent-name">{html.escape(seat["display_name"])}</span>'
            "</div>"
            f'<div class="cp-agent-role">{html.escape(seat["role"])}'
            f'{" · injected peer" if seat.get("injected") else ""}</div>'
            f'<div class="cp-agent-rows">{body}</div>'
            "</div>",
            unsafe_allow_html=True,
        )


# --------------------------------------------------------------------------- #
# Execution shape
# --------------------------------------------------------------------------- #


def interaction_note(name: str, support: str) -> str:
    """One line describing an interaction and how far it has been proven."""
    described = {
        "sequential": "one participant at a time, each seeing what was said before it",
        "parallel": "all participants prompted together, none seeing another's "
        "answer from this round",
    }.get(name, "")
    suffix = {
        "implemented": "",
        "experimental": " — experimental: implemented and unit-tested, not yet "
        "evidenced by a live browser run",
    }.get(support, f" — {support}")
    return f"**{name}** — {described}{suffix}" if described else f"**{name}**{suffix}"


def execution_order(phase: dict, *, last_speaker: str = "") -> None:
    """Draw the phase's real execution topology — never a prettier one.

    A sequential phase is drawn as a chain, a parallel phase as a fan into the
    round boundary. The runtime decides which; this only reports it. Drawing a
    parallel topology over a sequential phase would misdescribe the run, which
    is worse than drawing nothing.
    """
    members = [str(p) for p in (phase.get("participants") or [])]
    if not members:
        st.caption("This phase declares no participants.")
        return
    interaction = phase.get("interaction") or "sequential"

    def _mark(name: str) -> str:
        # Drawn inside a code block, so the marker has to be plain text —
        # markdown emphasis would render as literal asterisks.
        return f"{name.upper()} (last)" if name == last_speaker else name.upper()

    if interaction == "parallel":
        st.markdown(
            f"`{interaction}` — every participant is prompted from the same "
            "round-start state; the replies are collected afterwards."
        )
        lines = [f"{_mark(m)}  ─┐" for m in members[:-1]]
        lines.append(f"{_mark(members[-1])}  ─┴─→  round complete")
        st.code("\n".join(lines), language="text")
        return

    st.markdown(f"`{interaction}` — each turn sees the turns before it.")
    st.code("  →  ".join(_mark(m) for m in members), language="text")


def mission_shape(shape: dict) -> None:
    """Meeting type, workflow, and the interactions the contract declares."""
    if not shape:
        st.caption("This contract does not load, so its shape cannot be read.")
        return
    rows = [
        ("Meeting type", shape.get("meeting_type") or "—"),
        ("Format", shape.get("format") or "—"),
        ("Workflow", shape.get("workflow") or "none"),
        ("Interaction", ", ".join(shape.get("interactions") or []) or "sequential"),
    ]
    stages = shape.get("stages") or []
    if stages:
        rows.append(("Stages", " → ".join(dict.fromkeys(stages))))
    theme.key_values(rows)
    support = shape.get("interaction_support") or {}
    for name in shape.get("interactions") or []:
        st.caption(interaction_note(name, support.get(name, "unknown")))


# --------------------------------------------------------------------------- #
# Transcript and console
# --------------------------------------------------------------------------- #


def transcript(entries: list[dict], *, scope: str, window: int | None = TRANSCRIPT_WINDOW) -> None:
    """The multi-agent conversation, as the Founder would read it.

    Prompts stay collapsed; responses render as chat turns. Only the tail is
    drawn unless the whole transcript is asked for, because this is redrawn on
    every refresh tick of a live run.
    """
    if not entries:
        st.caption("No transcript yet. Start a run, or open a past run in History.")
        return

    shown = entries
    if window is not None and len(entries) > window:
        if st.toggle(
            f"Show all {len(entries)} entries (currently showing the last {window})",
            key=f"tr_all_{scope}",
            value=False,
        ):
            shown = entries
        else:
            shown = entries[-window:]

    for entry in shown:
        etype = entry.get("entry_type")
        content = entry.get("content", "")
        pid = entry.get("participant_id")
        speaker = pid.upper() if pid else "SYSTEM"

        if etype == "prompt":
            with st.expander(f"Prompt sent to {speaker} ({len(content):,} chars)"):
                st.code(content, language="markdown")
        elif etype == "response":
            avatar = AVATARS.get((pid or "").lower(), "🤖")
            with st.chat_message(speaker, avatar=avatar):
                meta = (
                    f"Phase {entry.get('phase_id', '—')} · "
                    f"Round {entry.get('round_number', '—')} · "
                    f"Role {entry.get('role', '—')}"
                )
                duration = entry.get("duration_seconds")
                if isinstance(duration, (int, float)):
                    meta += f" · {duration:.0f}s"
                st.markdown(f"**{speaker}**  \n*{meta}*")
                st.markdown(content)
        elif etype == "checkpoint":
            st.info(f"Checkpoint decision — {content}")
        elif etype == "system":
            st.caption(content)


def console(*, lines: int = 400, key: str = "cp_console") -> None:
    """Raw runtime output. Technical detail, kept technical."""
    with st.container(key=key):
        log = st.session_state.logs
        if not log:
            st.caption("No console output yet.")
            return
        st.text_area(
            "Runtime output",
            value="\n".join(log[-lines:]),
            height=420,
            disabled=True,
            label_visibility="collapsed",
            key=f"{key}_text",
        )
        st.caption(f"{len(log):,} lines captured · showing the last {min(lines, len(log)):,}.")


# --------------------------------------------------------------------------- #
# Deliverables
# --------------------------------------------------------------------------- #


def deliverables(mission_path: Path, run_dir: Path | None, *, scope: str) -> None:
    """Each declared final-answer file, by its subject, with download and copy.

    A declared output that has not been written yet is listed as pending rather
    than hidden, so the panel always reflects what the mission promises.
    """
    outputs = cache.declared_outputs(mission_path)
    if not outputs:
        st.caption("This mission declares no output files.")
        return
    if run_dir is None:
        for spec in outputs:
            st.markdown(f"**{spec['title']}** — `{spec['filename']}`")
            if spec["description"]:
                st.caption(spec["description"])
        st.caption("No run selected yet — these are the files a run will produce.")
        return

    for spec in outputs:
        path = Path(run_dir) / spec["filename"]
        st.markdown(f"**{spec['title']}**")
        if spec["description"]:
            st.caption(spec["description"])
        if not path.is_file():
            st.caption(f"Pending — `{spec['filename']}` has not been produced yet.")
            st.divider()
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            st.warning(f"Could not read `{spec['filename']}`: {exc}")
            st.divider()
            continue
        st.download_button(
            "Download",
            data=content,
            file_name=Path(spec["filename"]).name,
            mime="text/markdown",
            width="stretch",
            key=f"dl_{scope}_{spec['filename']}",
        )
        with st.expander("View / copy text"):
            st.code(content, language="markdown")  # st.code carries a copy button
        st.divider()


def artifact_downloads(run_dir: Path | None, *, scope: str) -> None:
    """Every run artifact on disk — the evidence behind the deliverables."""
    if run_dir is None:
        st.caption("No run selected.")
        return
    found = runs.run_artifacts(run_dir)
    if not found:
        st.caption("This run directory holds no artifacts yet.")
        return
    columns = st.columns(min(len(found), 3))
    for index, path in enumerate(found):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        columns[index % len(columns)].download_button(
            path.name,
            data=data,
            file_name=path.name,
            width="stretch",
            key=f"art_{scope}_{path.name}",
        )
