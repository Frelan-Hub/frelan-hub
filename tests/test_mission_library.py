"""Mission Library integrity — every shipped contract loads and is annotated.

The menu scan skips a contract that fails to load, by design: one malformed
template must not crash the menu (``_discover_meeting_types``). The cost of that
is silence — a broken template simply stops appearing. These tests are the
counterweight, and they are the only enforcement the library has: promotion,
categories, and formats are conventions, not mechanisms
(MISSION-LIBRARY-RESOLUTION.md §8.1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from main import _discover_meeting_types
from frelan.mission_loader import load_mission

MISSIONS = Path("missions")

# `frelan_mission_contract_v2.yaml` is a legacy schema SAMPLE, not a contract:
# it fails validation on eight counts (no `capabilities`, no `phases`, malformed
# `assigned_engine`). Nothing loads it — it is excluded from the menu by name
# and is not the default mission — so it has never broken a run. It is exempted
# here rather than quietly passing, and the exemption is the record of it.
_NOT_RUNNABLE = frozenset({"frelan_mission_contract_v2.yaml"})


def _all_contracts() -> list[Path]:
    return sorted(MISSIONS.rglob("*.y*ml"))


def _runnable_contracts() -> list[Path]:
    return [p for p in _all_contracts() if p.name not in _NOT_RUNNABLE]


def test_the_library_is_not_empty() -> None:
    assert _all_contracts(), "no mission contracts found under missions/"


@pytest.mark.parametrize("path", _runnable_contracts(), ids=lambda p: str(p))
def test_every_shipped_contract_loads(path: Path) -> None:
    """A contract that does not load vanishes from the menu without a word."""
    load_mission(path)


def test_the_exempt_sample_stays_out_of_the_menu() -> None:
    """It cannot load, so it must never be offered as a meeting type."""
    listed = {path.name for _label, path, _group in _discover_meeting_types()}
    assert not (listed & _NOT_RUNNABLE)


def test_no_contract_is_silently_missing_from_the_menu() -> None:
    """Listed == on disk, minus the fixtures and the excluded stage folders."""
    from main import _EXCLUDED_MISSION_DIRS, _FIXTURE_FILES

    expected = {
        p.resolve()
        for p in _all_contracts()
        if p.name not in _FIXTURE_FILES
        and not (set(p.relative_to(MISSIONS).parts[:-1]) & _EXCLUDED_MISSION_DIRS)
        # The scan is one level deep; anything deeper is not a menu entry.
        and len(p.relative_to(MISSIONS).parts) <= 2
    }
    listed = {path.resolve() for _label, path, _group in _discover_meeting_types()}
    assert listed == expected


@pytest.mark.parametrize(
    "path", [p for _l, p, _g in _discover_meeting_types()], ids=lambda p: p.name
)
def test_every_menu_template_is_annotated(path: Path) -> None:
    """Template anatomy per MISSION-LIBRARY-RESOLUTION.md §10.

    ``group`` is deliberately absent: it is derived from the folder by the scan,
    and declaring it as well would duplicate the truth.

    ``summary`` is what the menu shows beside the template. Without it a new
    meeting type is offered to the Founder with no way to tell what it is for,
    so it is required here rather than left to the author's diligence.
    """
    metadata = load_mission(path).metadata
    for key in ("meeting_type", "format", "template_version", "summary"):
        assert metadata.get(key), f"{path} is missing metadata.{key}"


@pytest.mark.parametrize(
    "path", [p for _l, p, _g in _discover_meeting_types()], ids=lambda p: p.name
)
def test_independent_phases_declare_and_instruct_independence(path: Path) -> None:
    """``context: none`` and the instruction that justifies it travel together.

    A phase whose instructions forbid referencing the peers, but which still
    carries their answers, leaks silently — the failure the context policy was
    added to fix. The reverse (withholding without saying so) strands the peer.
    """
    for phase in load_mission(path).phases:
        forbids = "do not reference the other peer" in phase.instructions.lower()
        if forbids:
            assert phase.context == "none", (
                f"{path}:{phase.id} forbids referencing the peers but still "
                f"carries them (context={phase.context!r})"
            )
