"""Visual language for the control plane: CSS, header, and section furniture.

The dashboard is a console for a multi-agent runtime, so it is styled like one:
a dark ground, one bronze accent taken from the FRELAN mark, monospaced numbers,
and status carried by a labelled pill rather than by colour alone.

Styling hooks are Streamlit's own keyed-container classes (``st.container(key=…)``
renders a ``.st-key-<key>`` class), not scraped DOM structure. That is the one
selector Streamlit treats as public, so a version bump changes the theme's
appearance at worst, never its correctness.
"""

from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

ASSETS_DIR = Path("assets")

BRONZE = "#C08A52"
BRONZE_DIM = "#945F3C"
INK = "#0B0E13"
PANEL = "#141922"
PANEL_2 = "#1B2230"
LINE = "#2A3342"
TEXT = "#E4E7EC"
MUTED = "#8A94A6"

# One colour per runtime state, used for the pill and for card accents.
STATE_COLOURS = {
    "idle": MUTED,
    "running": "#3FB950",
    "waiting": "#E3B341",
    "complete": "#58A6FF",
    "stopped": "#D29922",
    "failed": "#F85149",
}

_CSS = """
<style>
:root {
  --cp-bronze: %(bronze)s;
  --cp-ink: %(ink)s;
  --cp-panel: %(panel)s;
  --cp-panel-2: %(panel2)s;
  --cp-line: %(line)s;
  --cp-text: %(text)s;
  --cp-muted: %(muted)s;
}

/* Give the main column room and pull it up under the sticky header. */
.block-container { padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1500px; }

/* ---------------------------------------------------------------- header -- */
.st-key-cp_header {
  position: sticky; top: 0; z-index: 99;
  background: linear-gradient(180deg, var(--cp-ink) 82%%, rgba(11,14,19,0));
  border-bottom: 1px solid var(--cp-line);
  padding: 0.35rem 0.2rem 0.5rem 0.2rem;
  margin-bottom: 1.1rem;
}
.cp-brand { display: flex; align-items: center; gap: 0.7rem; line-height: 1.1; }
.cp-brand-mark {
  font-weight: 700; letter-spacing: 0.24em; color: var(--cp-bronze);
  font-size: 0.92rem;
}
.cp-brand-rule { width: 1px; height: 20px; background: var(--cp-line); }
.cp-brand-title {
  font-weight: 600; letter-spacing: 0.16em; color: var(--cp-text);
  font-size: 0.92rem;
}
.cp-runline {
  display: flex; align-items: center; justify-content: flex-end;
  gap: 0.6rem; font-size: 0.86rem;
}
.cp-runid {
  font-family: ui-monospace, "SFMono-Regular", "Cascadia Mono", monospace;
  color: var(--cp-text); letter-spacing: 0.08em;
}
.cp-runsep { color: var(--cp-line); }
.cp-pill {
  display: inline-flex; align-items: center; gap: 0.4rem;
  padding: 0.16rem 0.62rem; border-radius: 999px;
  border: 1px solid var(--cp-line); background: var(--cp-panel);
  font-size: 0.74rem; font-weight: 600; letter-spacing: 0.13em;
  text-transform: uppercase; white-space: nowrap;
}
.cp-dot { width: 7px; height: 7px; border-radius: 50%%; display: inline-block; }
.cp-dot-live { animation: cp-breathe 1.6s ease-in-out infinite; }
@keyframes cp-breathe { 0%%,100%% { opacity: 1 } 50%% { opacity: 0.35 } }

/* --------------------------------------------------------------- sidebar -- */
[data-testid="stSidebar"] { border-right: 1px solid var(--cp-line); }
[data-testid="stSidebar"] .block-container { padding-top: 1.1rem; }
.st-key-cp_plate {
  background: #F5F1EA; border-radius: 10px; padding: 0.85rem 1rem;
  margin-bottom: 0.4rem;
}
/* The keyed container *is* the vertical block, and Streamlit sets it to
   align-items:start. The image's own wrappers size to the image, so centring
   has to be overridden here rather than on the <img>. */
.st-key-cp_plate { align-items: center; }
.st-key-cp_plate img { max-width: 100%%; height: auto; display: block; }
.cp-navgroup {
  color: var(--cp-muted); font-size: 0.66rem; font-weight: 700;
  letter-spacing: 0.2em; text-transform: uppercase;
  margin: 1.05rem 0 0.3rem 0.35rem;
}
/* Nav rail: flat by default, bronze bar on the active view. */
.st-key-cp_nav .stButton > button {
  justify-content: flex-start; text-align: left;
  border: 0; border-left: 2px solid transparent; border-radius: 4px;
  padding: 0.3rem 0.6rem; font-size: 0.9rem; color: var(--cp-muted);
  background: transparent; font-weight: 500;
}
.st-key-cp_nav .stButton > button:hover {
  color: var(--cp-text); background: var(--cp-panel-2);
}
.st-key-cp_nav .stButton > button[kind="primary"] {
  color: var(--cp-text); background: var(--cp-panel-2);
  border-left: 2px solid var(--cp-bronze); font-weight: 600;
}

/* -------------------------------------------------------------- sections -- */
.cp-section {
  display: flex; align-items: center; gap: 0.75rem;
  margin: 1.5rem 0 0.55rem 0;
}
.cp-section-title {
  color: var(--cp-muted); font-size: 0.7rem; font-weight: 700;
  letter-spacing: 0.2em; text-transform: uppercase; white-space: nowrap;
}
.cp-section-rule { flex: 1; height: 1px; background: var(--cp-line); }
.cp-note { color: var(--cp-muted); font-size: 0.82rem; margin: -0.15rem 0 0.6rem 0; }

/* ----------------------------------------------------------- stat cards -- */
.cp-cards { display: flex; gap: 0.8rem; flex-wrap: wrap; }
.cp-card {
  flex: 1 1 150px; min-width: 140px;
  background: var(--cp-panel); border: 1px solid var(--cp-line);
  border-top: 2px solid var(--cp-line);
  border-radius: 8px; padding: 0.75rem 0.9rem;
}
.cp-card-label {
  color: var(--cp-muted); font-size: 0.64rem; font-weight: 700;
  letter-spacing: 0.16em; text-transform: uppercase;
}
.cp-card-value {
  font-family: ui-monospace, "SFMono-Regular", "Cascadia Mono", monospace;
  font-size: 1.5rem; font-weight: 600; color: var(--cp-text);
  line-height: 1.5; overflow-wrap: anywhere;
}
.cp-card-sub { color: var(--cp-muted); font-size: 0.74rem; }

/* ---------------------------------------------------------- agent cards -- */
.cp-agent {
  background: var(--cp-panel); border: 1px solid var(--cp-line);
  border-radius: 8px; padding: 0.85rem 0.95rem; height: 100%%;
}
.cp-agent-head { display: flex; align-items: center; gap: 0.5rem; }
.cp-agent-name {
  font-weight: 600; color: var(--cp-text); font-size: 0.95rem;
  letter-spacing: 0.02em;
}
.cp-agent-role {
  color: var(--cp-muted); font-size: 0.72rem; letter-spacing: 0.12em;
  text-transform: uppercase; margin-top: 0.15rem;
}
.cp-agent-rows { margin-top: 0.65rem; display: grid; gap: 0.22rem; }
.cp-agent-row {
  display: flex; justify-content: space-between; gap: 0.6rem;
  font-size: 0.8rem; color: var(--cp-muted);
}
.cp-agent-row b {
  font-family: ui-monospace, "SFMono-Regular", "Cascadia Mono", monospace;
  color: var(--cp-text); font-weight: 600;
}

/* --------------------------------------------------------------- tables -- */
.cp-kv { display: grid; gap: 0.28rem; margin: 0.2rem 0 0.5rem 0; }
.cp-kv div { display: flex; gap: 0.75rem; font-size: 0.84rem; }
.cp-kv span:first-child { color: var(--cp-muted); min-width: 190px; }
.cp-kv span:last-child { color: var(--cp-text); overflow-wrap: anywhere; }

/* Console text area: fixed-width, so wrapped runtime output stays readable. */
.st-key-cp_console textarea {
  font-family: ui-monospace, "SFMono-Regular", "Cascadia Mono", monospace !important;
  font-size: 0.78rem !important; line-height: 1.45 !important;
  background: #06080C !important; color: #9FD5F5 !important;
}
</style>
""" % {
    "bronze": BRONZE,
    "ink": INK,
    "panel": PANEL,
    "panel2": PANEL_2,
    "line": LINE,
    "text": TEXT,
    "muted": MUTED,
}


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def logo_path() -> str | None:
    for name in ("frelan-logo.png", "frelan-logo.jpg", "frelan-logo.svg"):
        candidate = ASSETS_DIR / name
        if candidate.is_file():
            return str(candidate)
    return None


def sidebar_logo() -> None:
    """The FRELAN mark, on a light plate.

    The mark is bronze over dark grey on transparency; dropped straight onto the
    console's near-black ground its wordmark disappears. The plate keeps the
    brand's own colours instead of inverting them.

    The plate is a keyed container rather than a ``<div>`` opened in one markdown
    call and closed in another: Streamlit sanitises each markdown block on its
    own and auto-closes it, so the opening tag never reaches the image and the
    plate renders as an empty box above the logo.
    """
    path = logo_path()
    if not path:
        return
    with st.sidebar:
        with st.container(key="cp_plate"):
            if Path(path).suffix.lower() == ".svg":
                try:
                    st.markdown(
                        Path(path).read_text(encoding="utf-8"), unsafe_allow_html=True
                    )
                except OSError:
                    pass
            else:
                st.image(path, width=170)


def pill(state: str, label: str | None = None, *, live: bool = False) -> str:
    """HTML for a labelled status pill. Colour supports the word; never replaces it."""
    colour = STATE_COLOURS.get(state, MUTED)
    text = html.escape(label or state).upper()
    dot_class = "cp-dot cp-dot-live" if live else "cp-dot"
    return (
        f'<span class="cp-pill">'
        f'<span class="{dot_class}" style="background:{colour}"></span>{text}</span>'
    )


def header():
    """The persistent top bar. Returns ``(status_column, settings_column)``.

    The brand is drawn here; the run identifier and runtime state are left to
    the caller, because they have to refresh on their own tick. They ride on
    every view because the one question a control plane must never make you
    navigate to answer is "what is running right now, and which run is it".
    """
    bar = st.container(key="cp_header")
    with bar:
        left, right, gear = st.columns([5, 5, 0.9], vertical_alignment="center")
        left.markdown(
            '<div class="cp-brand">'
            '<span class="cp-brand-mark">FRELAN</span>'
            '<span class="cp-brand-rule"></span>'
            '<span class="cp-brand-title">AI-CONDUCTOR B</span>'
            "</div>",
            unsafe_allow_html=True,
        )
    return right, gear


def section(title: str, note: str | None = None) -> None:
    st.markdown(
        f'<div class="cp-section"><span class="cp-section-title">{html.escape(title)}</span>'
        '<span class="cp-section-rule"></span></div>',
        unsafe_allow_html=True,
    )
    if note:
        st.markdown(f'<div class="cp-note">{html.escape(note)}</div>', unsafe_allow_html=True)


def cards(items: list[tuple[str, str, str]]) -> None:
    """A row of stat cards, each ``(label, value, accent-state)``."""
    blocks = []
    for label, value, state in items:
        colour = STATE_COLOURS.get(state, LINE)
        blocks.append(
            f'<div class="cp-card" style="border-top-color:{colour}">'
            f'<div class="cp-card-label">{html.escape(label)}</div>'
            f'<div class="cp-card-value">{html.escape(str(value))}</div>'
            "</div>"
        )
    st.markdown(f'<div class="cp-cards">{"".join(blocks)}</div>', unsafe_allow_html=True)


def key_values(rows: list[tuple[str, str]]) -> None:
    body = "".join(
        f"<div><span>{html.escape(k)}</span><span>{html.escape(str(v))}</span></div>"
        for k, v in rows
    )
    st.markdown(f'<div class="cp-kv">{body}</div>', unsafe_allow_html=True)
