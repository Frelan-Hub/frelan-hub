"""The control plane itself: navigation, guarded controls, checkpoint detection.

Two of these guard defects that were live in the old single-page dashboard and
would have been invisible in review: a Start button that could launch a second
Conductor against the same Chrome session, and a checkpoint panel that went
blind as soon as the runtime printed anything after its menu.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from ui import launcher, state, views


def _app(page: str = "Overview") -> AppTest:
    app = AppTest.from_file("streamlit_app.py", default_timeout=120).run()
    app.session_state.page = page
    return app.run()


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #


def test_the_rail_offers_every_view_exactly_once():
    app = _app()
    labels = [b.label for b in app.sidebar.button]
    assert labels == list(views.PAGES)
    grouped = [page for _group, pages in views.NAV_GROUPS for page in pages]
    assert grouped == list(views.PAGES), "a view is missing from the rail's groups"


@pytest.mark.parametrize("page", views.PAGES)
def test_every_view_renders_without_error(page):
    app = _app(page)
    assert not app.exception, f"{page} raised: {[e.value for e in app.exception]}"


def test_clicking_a_rail_item_switches_the_view():
    app = _app()
    app.sidebar.button(key="nav_Outputs").click().run()
    assert app.session_state.page == "Outputs"
    assert not app.exception


def test_configuration_survives_switching_views():
    """Streamlit drops the state of any widget it did not render this run. A
    setting typed on Setup must not vanish when the Founder opens Agents."""
    app = _app("Setup")
    app.text_area(key="w_topic").set_value("A specific question for the peers").run()
    assert app.session_state.cfg["topic"] == "A specific question for the peers"

    app.session_state.page = "Agents"
    app.run()
    app.session_state.page = "Setup"
    app.run()
    assert app.session_state.cfg["topic"] == "A specific question for the peers"
    assert app.text_area(key="w_topic").value == "A specific question for the peers"


# --------------------------------------------------------------------------- #
# Guarded controls
# --------------------------------------------------------------------------- #


def test_start_is_refused_while_a_launch_is_already_in_flight():
    """The guard is claimed before the subprocess is spawned, so a second event
    arriving mid-launch is refused rather than queued."""
    app = _app()
    app.session_state.launch_pending = True
    app.run()
    start = app.button(key="start_main")
    assert start.disabled, "Start must be disabled while a launch is in flight"


def test_stop_is_offered_only_when_something_is_running():
    app = _app()
    assert app.button(key="stop_main").disabled


def test_the_command_line_carries_the_founders_configuration():
    cfg = {
        "cdp_url": "http://localhost:9999",
        "claude_peer": True,
        "auto_pilot": True,
        "topic": "  a topic  ",
        "inject_files": ["inputs/a.txt"],
        "inject_images": ["inputs/b.png"],
    }
    args = launcher.build_args("missions/x.yaml", "outputs/run-1", cfg)
    assert args[1:4] == ["main.py", "missions/x.yaml", "-o"]
    assert "--claude" in args and "--auto" in args
    assert args[args.index("--topic") + 1] == "a topic", "the topic is trimmed"
    assert args[args.index("--cdp-url") + 1] == "http://localhost:9999"
    assert args[args.index("--inject-files") + 1] == "inputs/a.txt"
    assert args[args.index("--inject-images") + 1] == "inputs/b.png"


def test_an_unconfigured_run_stays_minimal():
    args = launcher.build_args("missions/x.yaml", "outputs/run-1", {})
    for flag in ("--claude", "--auto", "--topic", "--inject-files", "--inject-images"):
        assert flag not in args


# --------------------------------------------------------------------------- #
# Checkpoint detection
# --------------------------------------------------------------------------- #

MENU = (
    "Checkpoint — choose:  [C] Continue   [V] Converged   [E] Escalate   "
    "[T] Terminate   [F] Fully Automate   [P] Edit Prompt"
)


def test_a_pending_checkpoint_is_detected_with_its_summary():
    pending, summary = state.scan_checkpoint(
        ["Round 2 complete.", "Peers still disagree on scope.", MENU]
    )
    assert pending
    assert summary == ["Round 2 complete.", "Peers still disagree on scope."]


def test_a_checkpoint_is_cleared_once_a_decision_is_sent():
    pending, _ = state.scan_checkpoint([MENU, "[USER ACTION] Decision: CONTINUE"])
    assert not pending


def test_the_next_checkpoint_is_detected_after_an_earlier_one_was_answered():
    """The old rule looked at the last few lines only, so it went false as soon
    as the runtime printed anything, and true again for an answered menu."""
    logs = [
        MENU,
        "[USER ACTION] Decision: CONTINUE",
        "Round 3 running…",
        "Round 3 complete.",
        MENU,
    ]
    pending, summary = state.scan_checkpoint(logs)
    assert pending
    assert summary == ["Round 3 running…", "Round 3 complete."]


def test_intervening_output_does_not_hide_a_waiting_checkpoint():
    logs = [MENU] + [f"line {i}" for i in range(20)]
    # Output after the menu means the runtime is not blocked on it any more only
    # when a decision was sent; otherwise it is still waiting.
    pending, _ = state.scan_checkpoint(logs)
    assert pending


def test_no_checkpoint_means_no_panel():
    assert state.scan_checkpoint(["starting up", "round 1"]) == (False, [])
    assert state.scan_checkpoint([]) == (False, [])


# --------------------------------------------------------------------------- #
# The concepts, as the eight views present them
# --------------------------------------------------------------------------- #


def test_the_rail_gained_no_new_pages():
    """The information architecture is preserved: concepts were integrated into
    the existing views, not scattered across new ones."""
    assert views.PAGES == (
        "Overview",
        "Setup",
        "Agents",
        "Execution",
        "Governance",
        "Outputs",
        "Logs",
        "History",
    )


def _select(app, contract: str):
    """Point the dashboard at a specific contract by its library key."""
    from ui import cache

    types = cache.meeting_types()
    key = next(k for k, (_label, path) in types.items() if path.name == contract)
    app.session_state.cfg["meeting_key"] = key
    return app.run()


@pytest.mark.parametrize(
    "contract", ["research_architect_build.yaml", "general_inquiry.yaml"]
)
@pytest.mark.parametrize("page", views.PAGES)
def test_every_view_renders_for_both_shapes_of_contract(page, contract):
    """A parallel, staged, workflow contract must not break any view."""
    app = _select(_app(page), contract)
    assert not app.exception, f"{page}/{contract} raised: {[e.value for e in app.exception]}"


def test_setup_names_only_the_interactions_the_runtime_can_execute():
    from frelan.enums import INTERACTION_SUPPORT

    app = _select(_app("Setup"), "research_architect_build.yaml")
    body = " ".join(m.value for m in app.markdown) + " ".join(
        c.value for c in app.caption
    )
    for name in INTERACTION_SUPPORT:
        assert name in body
    for unimplemented in ("relay", "validation_gate", "delegation"):
        # Named in the deferred list is fine; offered as a phase interaction is
        # not. Neither appears as an interaction the contract declares.
        assert f"interaction `{unimplemented}`" not in body


def test_setup_labels_an_experimental_interaction_as_experimental():
    app = _select(_app("Setup"), "research_architect_build.yaml")
    text = " ".join(c.value for c in app.caption)
    assert "parallel" in text and "experimental" in text


def test_a_sequential_contract_is_never_drawn_as_parallel():
    """The UI must represent actual runtime behaviour."""
    app = _select(_app("Execution"), "general_inquiry.yaml")
    code = " ".join(c.value for c in app.code)
    assert "→" in code
    assert "round complete" not in code, "a parallel fan was drawn over a sequential phase"


def test_a_parallel_contract_is_drawn_as_parallel():
    app = _select(_app("Execution"), "research_architect_build.yaml")
    code = " ".join(c.value for c in app.code)
    assert "round complete" in code


def test_agents_states_what_each_participant_is_and_what_backs_it():
    app = _select(_app("Agents"), "research_architect_build.yaml")
    rendered = " ".join(m.value for m in app.markdown)
    assert "Participants" in rendered
    for expected in ("Model", "Role", "Capabilities", "Type"):
        assert expected in rendered
