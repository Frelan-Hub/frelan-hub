"""AI-Conductor B Runtime — control-plane dashboard.

This module wires the control plane together and nothing else: page setup, the
persistent header, the navigation rail, and dispatch to one view. It is to
``ui/`` what ``main.py`` is to ``frelan/`` — the only place that names concrete
pieces, kept deliberately thin so any one of them can be replaced without
touching the rest.

Run it with ``streamlit run streamlit_app.py`` (or ``run_ui.bat``).
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from ui import cache, components, state, theme, views

st.set_page_config(
    page_title="AI-Conductor B Runtime",
    page_icon="🎛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

theme.inject_css()
state.init()

# The default meeting type is resolved once the library has been scanned, not
# baked into the defaults: the library is a folder of YAML, and what is in it
# is only knowable at runtime.
_types = cache.meeting_types()
if not state.get("meeting_key") and _types:
    state.put("meeting_key", next(iter(_types)))

Path("inputs").mkdir(exist_ok=True)
Path(state.get("output_root", "outputs")).mkdir(exist_ok=True)

running = state.is_running()

# ----------------------------------------------------------------- header -- #
status_column, gear_column = theme.header()
with status_column:
    components.live(views.live_header_state, running=running)
with gear_column:
    with st.popover("⚙", width="stretch"):
        st.markdown("**Runtime connection**")
        st.text_input(
            "Chrome CDP URL",
            value=state.get("cdp_url"),
            key="w_cdp",
            on_change=state.persist("w_cdp", "cdp_url"),
            help="The already-running Chrome with remote debugging enabled.",
        )
        st.text_input(
            "Output root",
            value=state.get("output_root"),
            key="w_root",
            on_change=state.persist("w_root", "output_root"),
            help="Each run is written to its own run-<timestamp> directory below this.",
        )
        st.slider(
            "Live refresh (seconds)",
            min_value=0.5,
            max_value=5.0,
            step=0.5,
            value=float(state.get("refresh_seconds", 1.0)),
            key="w_refresh",
            on_change=state.persist("w_refresh", "refresh_seconds"),
            help="How often the live regions refresh. Only live regions refresh, "
            "never the whole page.",
        )

# -------------------------------------------------------------- nav rail -- #
theme.sidebar_logo()
with st.sidebar:
    rail = st.container(key="cp_nav")
    with rail:
        for group, pages in views.NAV_GROUPS:
            st.markdown(f'<div class="cp-navgroup">{group}</div>', unsafe_allow_html=True)
            for page in pages:
                st.button(
                    page,
                    key=f"nav_{page}",
                    width="stretch",
                    type="primary" if st.session_state.page == page else "tertiary",
                    on_click=state.go(page),
                )

# ------------------------------------------------------------------ view -- #
views.render(st.session_state.page)
