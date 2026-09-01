"""Cached contract reads.

Every one of these was previously performed on every rerun — which, with a
one-second refresh loop, meant loading and validating every YAML in
``missions/`` once per second for the length of a mission.

Each entry is keyed on the contract file's modification time, so editing a
template still shows up immediately, exactly as the uncached version did. The
cache removes repeated work, not freshness. ``meeting_type_map`` is keyed on a
signature of the whole ``missions/`` tree for the same reason: adding a template
must still be "drop a .yaml in the folder", with no restart.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from main import MISSIONS_DIR
from ui import library

_TTL = 60


def _mtime(path: Path | str) -> float:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


def _library_signature() -> tuple[int, float]:
    """``(file count, newest mtime)`` over the mission tree.

    Cheap to compute and sufficient: adding, removing, or editing a template all
    move one of the two, so the menu rebuilds without a restart.
    """
    files = list(Path(MISSIONS_DIR).rglob("*.y*ml"))
    newest = max((f.stat().st_mtime for f in files), default=0.0)
    return len(files), newest


@st.cache_data(ttl=_TTL, show_spinner=False)
def _meeting_type_map(_signature: tuple[int, float]) -> dict[str, tuple[str, str]]:
    return {k: (label, str(path)) for k, (label, path) in library.meeting_type_map().items()}


def meeting_types() -> dict[str, tuple[str, Path]]:
    raw = _meeting_type_map(_library_signature())
    return {k: (label, Path(path)) for k, (label, path) in raw.items()}


@st.cache_data(ttl=_TTL, show_spinner=False)
def _brief(path: str, _mtime_key: float) -> dict[str, str]:
    return library.brief(Path(path))


def brief(path: Path) -> dict[str, str]:
    return _brief(str(path), _mtime(path))


@st.cache_data(ttl=_TTL, show_spinner=False)
def _declared_outputs(path: str, _mtime_key: float) -> list[dict]:
    return library.declared_outputs(Path(path))


def declared_outputs(path: Path) -> list[dict]:
    return _declared_outputs(str(path), _mtime(path))


@st.cache_data(ttl=_TTL, show_spinner=False)
def _roster(path: str, claude_peer: bool, _mtime_key: float) -> list[dict]:
    return library.roster(Path(path), claude_peer=claude_peer)


def roster(path: Path, *, claude_peer: bool) -> list[dict]:
    return _roster(str(path), claude_peer, _mtime(path))


@st.cache_data(ttl=_TTL, show_spinner=False)
def _governance(path: str, _mtime_key: float) -> dict:
    return library.governance(Path(path))


def governance(path: Path) -> dict:
    return _governance(str(path), _mtime(path))


@st.cache_data(ttl=_TTL, show_spinner=False)
def _shape(path: str, _mtime_key: float) -> dict:
    return library.shape(Path(path))


def shape(path: Path) -> dict:
    return _shape(str(path), _mtime(path))


@st.cache_data(ttl=_TTL, show_spinner=False)
def _mission_name(path: str, _mtime_key: float) -> str:
    return library.mission_name(Path(path))


def mission_name(path: Path) -> str:
    return _mission_name(str(path), _mtime(path))
