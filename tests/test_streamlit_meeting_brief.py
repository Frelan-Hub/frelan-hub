"""The meeting-type brief, now on the Setup view.

The menu offers fifteen templates by name alone, which says nothing about what
any of them is for. Setup answers that from the contract itself —
``metadata.summary`` (when to reach for it), ``objective`` (what it drives at)
and ``metadata.format`` (its phase skeleton). These tests render the real app
headlessly, because the failure they guard against is a rendering one: a long
objective silently swallowing the panel, or the fallback swallowing the text.
"""

from __future__ import annotations

import pathlib

from html import unescape

from streamlit.testing.v1 import AppTest

from ui import library

# Streamlit 1.62 resolves a relative AppTest path against the *calling file*
# rather than the working directory, which put the entry point in tests/.
# An absolute path is correct under either behaviour.
_APP = pathlib.Path(__file__).resolve().parent.parent / "streamlit_app.py"


def _setup_view() -> AppTest:
    app = AppTest.from_file(str(_APP), default_timeout=120).run()
    app.session_state.page = "Setup"
    return app.run()


def _brief_for_key(app: AppTest, key: str) -> dict[str, str]:
    return library.brief(library.meeting_type_map()[key][1])


def test_setup_says_what_the_selected_meeting_type_is_for() -> None:
    app = _setup_view()
    assert not app.exception
    # The brief renders as an escaped HTML key/value block, so compare on the
    # unescaped text rather than on the markup.
    rendered = unescape(" ".join(m.value for m in app.markdown))
    captions = [c.value for c in app.caption]
    assert "What it's for" in rendered, "the summary must be shown beside the menu"
    assert "Format" in rendered, "the phase skeleton must be named"
    assert any(c.startswith("**Goal**") for c in captions), "the objective must be shown"


def test_a_long_objective_is_cut_at_a_word_and_kept_one_click_away() -> None:
    """Truncation must never be the whole story — the full text stays reachable."""
    app = _setup_view()
    types = library.meeting_type_map()
    longest = max(types, key=lambda k: len(library.brief(types[k][1]).get("objective", "")))
    app = app.selectbox(key="w_meeting").set_value(types[longest][0]).run()

    assert app.session_state.cfg["meeting_key"] == longest, (
        "the selection must persist into cfg, or it is lost on the next view"
    )
    goal = next(c.value for c in app.caption if c.value.startswith("**Goal**"))
    assert goal.endswith("…"), "a long objective should be visibly cut, not clipped"
    assert not goal.rstrip("…").endswith(" "), "cut at a word boundary, not mid-space"
    assert "Full goal" in [e.label for e in app.expander], (
        "the untruncated objective must stay reachable"
    )


def test_a_short_objective_is_shown_whole() -> None:
    """The expander is for long objectives only; a short goal must not be hidden."""
    app = _setup_view()
    key = app.session_state.cfg["meeting_key"]
    objective = _brief_for_key(app, key)["objective"]
    if len(objective) > library.OBJECTIVE_INLINE_CHARS:
        types = library.meeting_type_map()
        shortest = min(
            types, key=lambda k: len(library.brief(types[k][1]).get("objective", "") or "z" * 999)
        )
        app = app.selectbox(key="w_meeting").set_value(types[shortest][0]).run()
        objective = _brief_for_key(app, shortest)["objective"]

    goal = next(c.value for c in app.caption if c.value.startswith("**Goal**"))
    assert objective in goal
    assert "Full goal" not in [e.label for e in app.expander]
