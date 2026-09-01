"""The eight control-plane views.

The information architecture is Overview → Setup → Agents → Execution →
Governance → Outputs, with Logs and History as system views. Reference files
are part of Setup: they belong with the objective they support, not in a view
of their own. Each
view answers one question, and the header answers "what is running" everywhere,
so no view has to.

Blocks that must keep up with a live run are named module-level functions passed
to :func:`ui.components.live`. That is deliberate rather than incidental:
Streamlit identifies a fragment by its function, so a lambda would give several
unrelated live regions the same identity.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from frelan.transport.adapters import get_adapter
from ui import cache, components, launcher, library, runs, state, theme

PAGES = (
    "Overview",
    "Setup",
    "Agents",
    "Execution",
    "Governance",
    "Outputs",
    "Logs",
    "History",
)

# Grouped for the nav rail. The grouping is the story the rail tells: configure
# a mission, watch it run, then keep what it produced.
NAV_GROUPS = (
    ("Mission", ("Overview", "Setup", "Agents")),
    ("Run", ("Execution", "Governance", "Outputs")),
    ("System", ("Logs", "History")),
)


def selected_mission() -> Path:
    """The contract the dashboard is currently pointed at."""
    types = cache.meeting_types()
    key = state.get("meeting_key") or next(iter(types), "")
    if key not in types:
        key = next(iter(types), "")
    return types[key][1] if key else Path("missions/frelan_debate.yaml")


def _viewing_note() -> None:
    """Say plainly when a view is showing a past run rather than the live one."""
    if st.session_state.view_dir is None:
        return
    st.info(
        f"Viewing a past run: `{st.session_state.view_dir}`. "
        "The active run keeps executing in the background.",
        icon=":material/history:",
    )
    st.button(
        "Follow the active run instead",
        key=f"follow_{st.session_state.page}",
        on_click=state.set_view_dir,
        args=(None,),
    )


# --------------------------------------------------------------------------- #
# Live regions
# --------------------------------------------------------------------------- #


def live_header_state() -> None:
    """Header pill and status, refreshed on its own tick.

    Also owns the one full-app rerun a run causes: when the Conductor exits, the
    page body holds stale controls (a Start button still disabled, a mission
    panel still claiming to be running), and only an app-scoped rerun can fix
    that. One rerun per run, rather than the old one per second.
    """
    was_running = st.session_state.is_running
    state.drain_logs()
    running = state.is_running()
    key, label, is_live = components.runtime_state()
    st.markdown(
        '<div class="cp-runline">'
        f'<span class="cp-runid">{components.run_label()}</span>'
        '<span class="cp-runsep">·</span>'
        f"{theme.pill(key, label, live=is_live)}"
        "</div>",
        unsafe_allow_html=True,
    )
    if was_running and not running:
        st.rerun(scope="app")


def live_status_cards() -> None:
    state.drain_logs()
    components.status_cards()


def live_governance() -> None:
    state.drain_logs()
    components.governance_panel()


def live_agents() -> None:
    state.drain_logs()
    seats = cache.roster(selected_mission(), claude_peer=bool(state.get("claude_peer")))
    components.agent_grid(seats, state.ledger_entries())


def live_agents_detailed() -> None:
    state.drain_logs()
    seats = cache.roster(selected_mission(), claude_peer=bool(state.get("claude_peer")))
    components.agent_grid(seats, state.ledger_entries(), detailed=True)


def live_deliverables() -> None:
    components.deliverables(selected_mission(), state.view_dir(), scope="main")


def live_transcript() -> None:
    state.drain_logs()
    components.transcript(state.ledger_entries(), scope="main")


def live_console() -> None:
    state.drain_logs()
    components.console()


def live_timeline() -> None:
    state.drain_logs()
    _timeline(state.ledger_entries())


def live_position() -> None:
    """Where the run is now: phase, stage, interaction, who is working.

    Reads the contract for the phase structure and the ledger for what has
    actually happened, and says which is which. The runtime decides who speaks;
    this reports it.
    """
    state.drain_logs()
    shape = cache.shape(selected_mission())
    if not shape:
        st.caption("This contract does not load, so its execution shape cannot be read.")
        return
    entries = state.ledger_entries()
    spot = runs.position(entries, shape["phases"])

    theme.cards(
        [
            ("PHASE", spot["phase_name"] or spot["phase_id"] or "—",
             "running" if state.is_running() else "idle"),
            ("STAGE", spot["stage"] or "—", "idle"),
            ("INTERACTION", spot["interaction"], "idle"),
            ("ROUND", str(spot["round"] or "—"), "idle"),
            ("LAST SPEAKER", (spot["last_speaker"] or "—").upper(), "idle"),
        ]
    )
    if spot["working"]:
        st.caption(
            "This phase runs in parallel: "
            + ", ".join(w.upper() for w in spot["working"])
            + " are prompted together, so there is no single next speaker."
        )
    elif spot["next_speaker"]:
        who = spot["next_speaker"].upper()
        st.caption(
            f"Expected next in this phase's declared turn order: **{who}**. "
            "The runtime decides; this is read from the contract."
        )
    components.execution_order(
        {"participants": spot["participants"], "interaction": spot["interaction"]},
        last_speaker=spot["last_speaker"],
    )


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #


def overview() -> None:
    running = state.is_running()
    _viewing_note()

    theme.section("Mission status")
    components.live(live_status_cards, running=running)

    theme.section("Current mission")
    _current_mission()

    theme.section("Mission shape", "What this meeting is, and how its participants work.")
    components.mission_shape(cache.shape(selected_mission()))

    theme.section("Where the run is")
    components.live(live_position, running=running)

    theme.section("Governance")
    components.live(live_governance, running=running)

    theme.section("Live agents")
    components.live(live_agents, running=running)

    theme.section("Deliverables")
    components.live(live_deliverables, running=running)

    theme.section("Live meeting workspace")
    # State-aware: the transcript is what a Founder watches during a mission, so
    # it opens itself while one is running and folds away again when it is not.
    with st.expander(
        "Multi-agent conversation", expanded=running or st.session_state.run_complete
    ):
        components.live(live_transcript, running=running)

    theme.section("Technical logs")
    with st.expander("Console output and diagnostics", expanded=False):
        components.live(live_console, running=running)


def _current_mission() -> None:
    """The mission about to run, or the one running, and the controls for it."""
    path = selected_mission()
    brief = cache.brief(path)
    running = state.is_running()
    record = st.session_state.run_record

    with st.container(border=True):
        left, right = st.columns([3, 1.15])
        with left:
            st.markdown(f"### {cache.mission_name(path)}")
            topic = (state.get("topic") or "").strip()
            objective = topic or brief.get("objective", "")
            if objective:
                shown, truncated = _split(objective)
                st.write(shown)
                if truncated:
                    with st.expander("Full objective"):
                        st.write(objective)
            else:
                st.caption("This contract declares no objective that could be read.")
            if topic:
                st.caption("Objective overridden for this run (set on Setup).")

            # The toggle can still hold True from an earlier mission choice, so
            # the summary reads the contract too: this line must describe the
            # run that will actually start, not the widget.
            options = [
                "Claude seated as a third peer"
                if state.get("claude_peer") and library.claude_peer_allowed(path)
                else "Two peers",
                "Auto-pilot: every checkpoint continues"
                if state.get("auto_pilot")
                else "Checkpoints require your decision",
            ]
            files = state.get("inject_files", []) + state.get("inject_images", [])
            if files:
                options.append(f"{len(files)} reference file(s) injected")
            st.caption(" · ".join(options))

        with right:
            st.button(
                "Start mission",
                key="start_main",
                type="primary",
                width="stretch",
                disabled=not launcher.can_start(),
                on_click=launcher.start,
            )
            st.button(
                "Stop",
                key="stop_main",
                width="stretch",
                disabled=not running,
                on_click=launcher.stop,
            )
            if record is not None:
                st.caption(f"Run {record.label} · started {components.short_time(record.started_at)}")
                st.caption(f"`{record.run_dir}`")

        if st.session_state.launch_error:
            st.error(st.session_state.launch_error)
        elif st.session_state.run_complete:
            st.success(
                f"Run finished. Artifacts are in `{state.view_dir()}` — see Outputs."
            )
        elif st.session_state.user_stopped:
            st.warning("The last run was stopped before it finished.")


def _split(objective: str) -> tuple[str, bool]:
    from ui.library import split_objective

    return split_objective(objective)


# --------------------------------------------------------------------------- #
# Setup
# --------------------------------------------------------------------------- #


def setup() -> None:
    types = cache.meeting_types()
    keys = list(types)
    if not keys:
        st.error("No runnable mission contracts were found in `missions/`.")
        return
    current = state.get("meeting_key") or keys[0]
    if current not in types:
        current = keys[0]

    theme.section("Meeting type", "Every runnable contract in the Mission Library.")
    st.selectbox(
        "Meeting type",
        options=keys,
        index=keys.index(current),
        format_func=lambda k: types[k][0],
        key="w_meeting",
        on_change=state.persist("w_meeting", "meeting_key"),
        label_visibility="collapsed",
        disabled=state.is_running(),
    )
    path = types[state.get("meeting_key") or current][1]

    brief = cache.brief(path)
    if not brief:
        st.warning("This contract does not load, so it cannot be run.")
    else:
        rows = []
        if brief.get("summary"):
            rows.append(("What it's for", brief["summary"]))
        if brief.get("format"):
            rows.append(("Format", brief["format"]))
        rows.append(("Contract", str(path)))
        theme.key_values(rows)
        objective = brief.get("objective", "")
        if objective:
            shown, truncated = _split(objective)
            st.caption(f"**Goal** — {shown}")
            if truncated:
                with st.expander("Full goal"):
                    st.caption(objective)

    theme.section(
        "Interaction",
        "How this contract's participants work together. Declared per phase by "
        "the contract, which is the authority — not a run-time switch.",
    )
    shape = cache.shape(path)
    if not shape:
        st.caption("This contract does not load, so its interactions cannot be read.")
    else:
        for phase in shape["phases"]:
            stage = f" · stage `{phase['stage']}`" if phase["stage"] else ""
            st.markdown(
                f"- **{phase['name']}** — interaction `{phase['interaction']}`"
                f" · context `{phase['context']}`{stage}"
            )
        support = shape["interaction_support"]
        for name in shape["interactions"]:
            st.caption(components.interaction_note(name, support.get(name, "unknown")))
        with st.expander("Interactions this runtime can execute"):
            for name, status in library.interaction_catalogue():
                st.markdown(f"- {components.interaction_note(name, status)}")
            st.caption(
                "Only these are listed, because only these are implemented. "
                "Relay, debate, critique, validation gates, delegation and "
                "pipelines are described in CONCEPTUAL-MODEL.md with their "
                "status; the ones that are contract-expressible today are "
                "written as phase instructions, not as execution modes."
            )

    theme.section(
        "Workflow",
        "The multi-stage composition this contract belongs to, if it declares one.",
    )
    if not shape:
        st.caption("Unavailable — the contract does not load.")
    elif shape["workflow"]:
        stages = " → ".join(dict.fromkeys(shape["stages"])) or "no stages labelled"
        theme.key_values([("Workflow", shape["workflow"]), ("Stages", stages)])
        st.caption(
            "A workflow is composed inside one contract, as labelled phases. "
            "There is no workflow engine and no cross-mission chaining."
        )
    else:
        st.caption(
            "None. This contract declares no workflow — its phases are the "
            "whole composition. Workflow is optional and never required."
        )

    theme.section("Objective override", "Leave blank to use the contract's own objective.")
    st.text_area(
        "Custom main topic",
        value=state.get("topic", ""),
        key="w_topic",
        on_change=state.persist("w_topic", "topic"),
        label_visibility="collapsed",
        placeholder="Ask the peers a specific question, or restate the objective for this run…",
        height=120,
    )

    theme.section(
        "Reference context",
        "Documents and images to hand the peers with this objective. Injected "
        "into the mission context and inlined into the prompts once.",
    )
    _reference_inputs()
    st.caption(
        "A reference file is inlined into a prompt once and referred to by name "
        "afterwards, within the renderer's reference budget. Anything the budget "
        "drops is announced in the prompt rather than silently omitted."
    )

    theme.section("Run configuration")
    st.toggle(
        "Auto-pilot — continue through every checkpoint without asking",
        value=bool(state.get("auto_pilot")),
        key="w_auto",
        on_change=state.persist("w_auto", "auto_pilot"),
    )
    st.caption(
        "The peer roster is set on Agents; the Chrome connection under the gear "
        "in the header."
    )

    theme.section("Launch")
    st.code(
        " ".join(
            launcher.build_args(path, Path(state.get("output_root", "outputs")) / "run-<new>", state.cfg())
        ),
        language="bash",
    )
    st.caption("The run directory is allocated at launch, so no run overwrites another.")
    st.button(
        "Start mission",
        key="start_setup",
        type="primary",
        disabled=not launcher.can_start(),
        on_click=launcher.start,
    )
    if st.session_state.launch_error:
        st.error(st.session_state.launch_error)


# --------------------------------------------------------------------------- #
# Reference context (part of Setup)
# --------------------------------------------------------------------------- #


def _reference_inputs() -> None:
    """Reference uploader plus the list of what is already injected."""
    uploaded = st.file_uploader(
        "Upload reference files",
        accept_multiple_files=True,
        key="w_uploads",
        label_visibility="collapsed",
    )
    if uploaded:
        files, images = state.store_uploads(uploaded)
        # Stored on upload, not at launch: an upload has to survive the Founder
        # navigating to another view, and the uploader's own state does not.
        merged_files = list(dict.fromkeys(state.get("inject_files", []) + files))
        merged_images = list(dict.fromkeys(state.get("inject_images", []) + images))
        if merged_files != state.get("inject_files") or merged_images != state.get("inject_images"):
            state.put("inject_files", merged_files)
            state.put("inject_images", merged_images)
            st.rerun()

    _injected_list("inject_files", "Documents")
    _injected_list("inject_images", "Images")

    if not state.get("inject_files") and not state.get("inject_images"):
        st.caption("Nothing injected. The peers will work from the objective alone.")


def _injected_list(key: str, title: str) -> None:
    items = state.get(key, [])
    if not items:
        return
    st.markdown(f"**{title}**")
    for index, item in enumerate(items):
        row, action = st.columns([6, 1])
        exists = Path(item).is_file()
        row.markdown(f"`{item}`" if exists else f"`{item}` — missing on disk")
        action.button(
            "Remove",
            key=f"rm_{key}_{index}",
            width="stretch",
            on_click=_remove_injected,
            args=(key, item),
        )


def _remove_injected(key: str, item: str) -> None:
    state.put(key, [i for i in state.get(key, []) if i != item])


# --------------------------------------------------------------------------- #
# Agents
# --------------------------------------------------------------------------- #


def agents() -> None:
    path = selected_mission()
    running = state.is_running()

    theme.section(
        "Participants",
        "Who takes part. A participant is either a MODEL seated as itself or an "
        "AGENT — a configured worker built around a model, with its own "
        "standing brief. Both are peers here.",
    )
    # A contract may declare that a third peer would break it — the asymmetric
    # Red/Blue review is the case: injection adds a peer to EVERY phase, so
    # Claude would argue both sides. The toggle is disabled rather than left
    # enabled and refused at launch, so the reason is visible while choosing.
    claude_allowed = library.claude_peer_allowed(path)
    st.toggle(
        "Seat Claude as a third peer",
        value=bool(state.get("claude_peer")) and claude_allowed,
        key="w_claude",
        on_change=state.persist("w_claude", "claude_peer"),
        disabled=running or not claude_allowed,
        help="Claude joins every phase with the same peer role as the other engines."
        if claude_allowed
        else "This meeting type separates the peers by role in each phase. A third "
        "peer would join every phase and collapse that separation, so the runtime "
        "refuses it. Use a symmetric meeting type for three peers.",
    )
    if not claude_allowed:
        st.caption(
            "This meeting type runs with two peers. It declares "
            "`claude_peer: unsupported`."
        )
    components.live(live_agents_detailed, running=running)

    theme.section("Transport limits", "Read from the browser adapters — the authority on these numbers.")
    seats = cache.roster(path, claude_peer=bool(state.get("claude_peer")))
    rows = []
    for seat in seats:
        adapter = get_adapter(seat["engine"])
        if adapter is None:
            rows.append((seat["display_name"], "no browser adapter for this engine"))
            continue
        budget = adapter.chat_budget_chars
        rows.append(
            (
                seat["display_name"],
                f"composer {adapter.max_inline_chars:,} chars · "
                + (f"conversation {budget:,} chars" if budget else "no conversation budget"),
            )
        )
    theme.key_values(rows)
    st.caption(
        "A prompt over the composer limit goes down the delivery ladder: verified "
        "attachment, then chunked inline messages, then a truncated head and tail."
    )

    theme.section(
        "Identity",
        "Name, type, model, and role — kept separate on purpose. A role is a "
        "responsibility, not a model: the same model can hold different roles, "
        "and the same role can be held by different models.",
    )
    for seat in seats:
        kind = (seat.get("type") or "model").lower()
        rows = [
            ("Participant", seat["display_name"]),
            ("Type", kind.capitalize()),
            ("Model", seat["engine"]),
            ("Role", seat["role"]),
            ("Capabilities", ", ".join(seat["capabilities"]) or "none declared"),
            ("Transport", seat["transport"]),
        ]
        if seat.get("injected"):
            rows.append(("Seated by", "--claude (injected into every phase)"))
        theme.key_values(rows)
        if seat.get("standing_brief"):
            with st.expander(f"Standing brief — {seat['display_name']}"):
                st.write(seat["standing_brief"])
        st.divider()

    st.caption(
        "Two participants backed by the SAME model share one browser "
        "conversation: the transport finds a tab by engine, not by participant. "
        "Seat one participant per engine until that changes."
    )


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #


def execution() -> None:
    running = state.is_running()
    _viewing_note()

    theme.section("Current position", "Phase, stage, interaction, and who is working.")
    components.live(live_position, running=running)

    theme.section("Timeline")
    components.live(live_timeline, running=running)

    theme.section("Live transcript")
    components.live(live_transcript, running=running)


def _timeline(entries: list[dict]) -> None:
    if not entries:
        st.caption("Nothing has executed yet.")
        return
    summary = runs.summarise(entries)
    theme.cards(
        [
            ("PHASE", summary["phase"] or "—", "running" if state.is_running() else "idle"),
            ("ROUND", str(summary["rounds"]), "idle"),
            ("TURNS", str(summary["turns"]), "idle"),
            ("CHECKPOINTS", str(summary["checkpoints"]), "idle"),
            ("LAST TURN", components.since(summary["last_at"]), "idle"),
        ]
    )
    # How each phase actually ran, so a round is never drawn with an arrow chain
    # it did not execute. Read from the contract the dashboard is pointed at;
    # unknown phases fall back to naming the speakers without a topology claim.
    declared = {
        ph["id"]: ph for ph in (cache.shape(selected_mission()).get("phases") or [])
    }
    ordered: dict[tuple[str, object], list[dict]] = {}
    for entry in runs.turn_entries(entries):
        ordered.setdefault(
            (entry.get("phase_id") or "—", entry.get("round_number")), []
        ).append(entry)
    st.markdown("")
    for (phase, round_number), turns in ordered.items():
        names = [(t.get("participant_id") or "?").upper() for t in turns]
        spec = declared.get(phase, {})
        interaction = spec.get("interaction", "")
        stage = f" · {spec['stage']}" if spec.get("stage") else ""
        if interaction == "parallel":
            speakers = " + ".join(names) + "  (together)"
        else:
            speakers = " → ".join(names)
        st.markdown(f"**{phase}{stage} · round {round_number}** — {speakers}")


# --------------------------------------------------------------------------- #
# Governance
# --------------------------------------------------------------------------- #


def governance() -> None:
    running = state.is_running()
    path = selected_mission()
    _viewing_note()

    theme.section("Checkpoint")
    components.live(live_governance, running=running)

    theme.section("Policy", "Declared by the contract; the runtime never invents one.")
    policy = cache.governance(path)
    if not policy:
        st.caption("This contract does not load, so its policy cannot be read.")
    else:
        rows = [
            ("Checkpoint interval", f"every {policy['checkpoint_interval']} round(s)"),
            ("Maximum rounds", str(policy["max_rounds"] or "not capped")),
            ("Synthesiser", policy["synthesiser"] or "first participant"),
        ]
        if policy.get("convergence_note"):
            rows.append(("Convergence", policy["convergence_note"]))
        if policy.get("escalation_note"):
            rows.append(("Escalation", policy["escalation_note"]))
        theme.key_values(rows)
        with st.expander("Phases"):
            for phase in policy["phases"]:
                cap = f" · max {phase['max_rounds']} round(s)" if phase["max_rounds"] else ""
                stage = f" · stage: {phase['stage']}" if phase.get("stage") else ""
                st.markdown(
                    f"**{phase['name']}** (`{phase['id']}`){cap}  \n"
                    f"{phase['objective']}  \n"
                    f"*Participants: {', '.join(phase['participants'])} · "
                    f"context: {phase['context']} · "
                    f"interaction: {phase.get('interaction', 'sequential')}{stage}*"
                )
        st.caption(
            "Interaction is listed here for completeness but is not governance: "
            "it is how the participants work, while governance is how the "
            "mission is controlled. Changing an interaction changes neither the "
            "checkpoint cadence nor any decision available to you."
        )

    theme.section("Decisions recorded")
    components.checkpoint_history(state.ledger_entries())

    theme.section("Evidence")
    target = state.view_dir()
    evidence = runs.read_json(Path(target) / "evidence.json") if target else None
    if not evidence:
        st.caption("No `evidence.json` for this run yet. It is written when a run ends.")
        return
    rows = [
        ("Mission", str(evidence.get("mission_name", "—"))),
        ("Meeting type", str(evidence.get("meeting_type", "—"))),
        ("Status", str(evidence.get("status", "—"))),
        ("Peer scoring", "captured" if evidence.get("participants") else "none"),
    ]
    if "founder_rating" in evidence:
        rows.append(("Founder rating", str(evidence["founder_rating"])))
    theme.key_values(rows)
    with st.expander("Full evidence record"):
        st.json(evidence)


# --------------------------------------------------------------------------- #
# Outputs
# --------------------------------------------------------------------------- #


def outputs() -> None:
    running = state.is_running()
    _viewing_note()

    theme.section("Declared deliverables", "The final-answer files this mission promises.")
    components.live(live_deliverables, running=running)

    theme.section(
        "Provenance",
        "Who produced the deliverables, and out of what.",
    )
    _output_provenance()

    theme.section("Run artifacts", "The evidence behind the deliverables.")
    components.artifact_downloads(state.view_dir(), scope="outputs")

    target = state.view_dir()
    if target:
        st.caption(f"Run directory — `{target}`")


def _output_provenance() -> None:
    """Who wrote the deliverables, from the run's own record where it exists.

    A finished run states its own shape in ``metadata.json``; that is preferred
    over the contract the dashboard currently points at, which may be a
    different meeting type entirely. Nothing is invented: a run that has not
    written its metadata yet says so.
    """
    recorded = runs.run_shape(state.view_dir())
    shape = recorded or cache.shape(selected_mission())
    if not shape:
        st.caption("No provenance available — no run selected and no contract loaded.")
        return

    synthesiser = shape.get("synthesiser", "")
    by_id = {p.get("id"): p for p in (shape.get("participants") or [])}
    producer = by_id.get(synthesiser, {})

    rows = [("Source", "this run's own record" if recorded else "the selected contract")]
    if shape.get("meeting_type"):
        rows.append(("Meeting type", shape["meeting_type"]))
    if shape.get("workflow"):
        rows.append(("Workflow", shape["workflow"]))
    if shape.get("interactions"):
        rows.append(("Interaction", ", ".join(shape["interactions"])))
    if synthesiser:
        detail = ", ".join(
            part
            for part in (producer.get("model"), producer.get("role"))
            if part
        )
        rows.append(
            (
                "Synthesiser",
                f"{producer.get('display_name') or synthesiser}"
                + (f" ({detail})" if detail else ""),
            )
        )
    theme.key_values(rows)

    phases = shape.get("phases") or []
    if phases:
        st.caption(
            "Phases behind the deliverables: "
            + " → ".join(
                f"{ph.get('stage') or ph.get('id')}" for ph in phases
            )
        )
    if not recorded:
        st.caption(
            "Read from the selected contract: no run is open, or the open run "
            "has not written its `metadata.json` yet. A finished run's own "
            "record is preferred, because it may be a different meeting type."
        )


# --------------------------------------------------------------------------- #
# Logs
# --------------------------------------------------------------------------- #


def logs() -> None:
    running = state.is_running()
    theme.section("Runtime console", "Raw output from the Conductor subprocess.")
    components.live(live_console, running=running)

    body = "\n".join(st.session_state.logs)
    st.download_button(
        "Download this session's console log",
        data=body or "(no output)",
        file_name="conductor-console.log",
        mime="text/plain",
        disabled=not body,
    )
    st.caption(
        "This is the dashboard's capture of the current session. The run's own "
        "permanent record is `ledger.jsonl` in the run directory."
    )


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #


def history() -> None:
    root = Path(state.get("output_root", "outputs"))
    theme.section("Previous runs", f"Every run recorded under `{root}`.")
    records = runs.list_runs(root)
    if not records:
        st.caption("No runs yet.")
        return

    active = state.active_dir()
    for index, record in enumerate(records[:60]):
        with st.container(border=True):
            left, middle, right = st.columns([2, 4, 1.4])
            is_active = active is not None and record.path == active
            left.markdown(f"**{record.label}**")
            left.markdown(
                theme.pill(
                    _history_state(record.status),
                    record.status,
                    live=is_active and state.is_running(),
                ),
                unsafe_allow_html=True,
            )
            middle.markdown(f"**{record.mission_name or 'Unnamed mission'}**")
            middle.caption(
                f"Started {components.short_time(record.started_at)} · "
                f"`{record.run_dir}`"
            )
            # Only what the run itself recorded. A run from before these fields
            # existed shows nothing here rather than a reconstructed guess —
            # comparing experiments is the whole point of this list.
            facts = []
            if record.meeting_type:
                facts.append(f"type {record.meeting_type}")
            if record.workflow:
                facts.append(f"workflow {record.workflow}")
            if record.interactions:
                facts.append("interaction " + ", ".join(record.interactions))
            if record.models:
                facts.append("models " + ", ".join(record.models))
            if facts:
                middle.caption(" · ".join(facts))
            if record.topic:
                middle.caption(f"Objective override: {record.topic[:160]}")
            right.button(
                "Open",
                key=f"open_{index}",
                width="stretch",
                on_click=_open_run,
                args=(record.run_dir,),
            )
    if len(records) > 60:
        st.caption(f"Showing the 60 most recent of {len(records)} runs.")


def _history_state(status: str) -> str:
    return {
        runs.STATUS_RUNNING: "running",
        runs.STATUS_COMPLETED: "complete",
        runs.STATUS_STOPPED: "stopped",
        runs.STATUS_FAILED: "failed",
    }.get(status, "idle")


def _open_run(run_dir: str) -> None:
    state.set_view_dir(run_dir)
    st.session_state.page = "Outputs"


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #

RENDERERS = {
    "Overview": overview,
    "Setup": setup,
    "Agents": agents,
    "Execution": execution,
    "Governance": governance,
    "Outputs": outputs,
    "Logs": logs,
    "History": history,
}


def render(page: str) -> None:
    RENDERERS.get(page, overview)()
