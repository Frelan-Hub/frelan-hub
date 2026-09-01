"""The contract-declared restriction on injecting Claude as an extra peer.

``--claude`` appends a peer to EVERY phase. For a symmetric meeting type that is
exactly the intent; for an asymmetric one — Red/Blue, where each phase seats a
single peer holding one duty — it collapses the separation the contract exists
to express. ``metadata.claude_peer: "unsupported"`` is how a contract says so.

These assert the restriction is carried by the metadata rather than by a mission
id, because the point of the key is that the *next* asymmetric template gets the
same guarantee without a runtime change (MISSION-LIBRARY-RESOLUTION.md §8.4).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import main as entrypoint
from frelan.mission_loader import load_mission
from ui import launcher, library

RED_BLUE = Path("missions/candidates/red_blue_review.yaml")
SYMMETRIC = Path("missions/candidates/document_review.yaml")


@pytest.fixture
def restricted(make_mission):
    """The shared fixture mission, but declaring the restriction."""
    return replace(
        make_mission(), metadata={"claude_peer": entrypoint.CLAUDE_PEER_UNSUPPORTED}
    )


# --------------------------------------------------------------------------- #
# The predicate
# --------------------------------------------------------------------------- #


def test_a_contract_that_says_nothing_permits_the_peer(make_mission):
    """Every contract written before the key existed keeps its old meaning."""
    assert entrypoint.claude_peer_supported(make_mission())


def test_the_declared_restriction_is_read(restricted):
    assert not entrypoint.claude_peer_supported(restricted)


@pytest.mark.parametrize("declared", ["UNSUPPORTED", " unsupported ", "Unsupported"])
def test_the_restriction_survives_case_and_whitespace(make_mission, declared):
    """The loader coerces metadata to strings; a template may format it freely."""
    mission = replace(make_mission(), metadata={"claude_peer": declared})
    assert not entrypoint.claude_peer_supported(mission)


def test_any_other_value_permits_the_peer(make_mission):
    """Only the one word restricts. A typo must not silently disable a feature."""
    mission = replace(make_mission(), metadata={"claude_peer": "supported"})
    assert entrypoint.claude_peer_supported(mission)


# --------------------------------------------------------------------------- #
# Runtime injection
# --------------------------------------------------------------------------- #


def test_injection_is_refused_for_a_restricted_contract(restricted):
    result = entrypoint._inject_claude_peer(restricted)
    assert [p.id for p in result.participants] == ["chatgpt", "gemini"]
    for phase in result.phases:
        assert "claude" not in phase.participant_ids


def test_injection_still_works_for_an_unrestricted_contract(make_mission):
    """The existing behaviour, unchanged — the guard must be the exception."""
    result = entrypoint._inject_claude_peer(make_mission())
    assert [p.id for p in result.participants] == ["chatgpt", "gemini", "claude"]
    for phase in result.phases:
        assert phase.participant_ids[-1] == "claude"


def test_the_refusal_is_reported_and_clears_the_flag(restricted, capsys):
    """The Founder is told, and the run metadata records two peers, not three.

    ``claude_injected`` is written to the run's metadata and read back by
    ``--resume``; leaving it True after a refusal would both misreport the run
    and make the resume retry an injection that cannot happen.
    """
    include_claude = True
    if include_claude and not entrypoint.claude_peer_supported(restricted):
        print(
            entrypoint._CLAUDE_PEER_REFUSED.format(
                name=restricted.name, value=entrypoint.CLAUDE_PEER_UNSUPPORTED
            )
        )
        include_claude = False
    assert include_claude is False
    assert "REFUSED" in capsys.readouterr().out


def _menu_with(only: Path, tmp_path: Path, answers: list[str]):
    """Run the meeting-type menu over a library holding exactly one template."""
    library_dir = tmp_path / "missions"
    library_dir.mkdir()
    (library_dir / only.name).write_text(only.read_text(encoding="utf-8"), encoding="utf-8")
    asked: list[str] = []

    def _input(prompt: str) -> str:
        asked.append(prompt)
        return answers[len(asked) - 1]

    path, include_claude = entrypoint._prompt_meeting_type(
        input_fn=_input, missions_dir=library_dir
    )
    return path, include_claude, asked


def test_the_menu_does_not_offer_a_peer_the_contract_forbids(tmp_path, capsys):
    """Asking a question the runtime must then refuse is the defect, not the fix."""
    path, include_claude, asked = _menu_with(RED_BLUE, tmp_path, ["1", "y"])
    assert path is not None and path.name == RED_BLUE.name
    assert include_claude is False
    assert len(asked) == 1, "only the meeting-type question was asked"
    assert "third peer" in capsys.readouterr().out


def test_the_menu_still_offers_the_peer_where_it_is_allowed(tmp_path):
    _path, include_claude, asked = _menu_with(SYMMETRIC, tmp_path, ["1", "y"])
    assert include_claude is True
    assert len(asked) == 2, "meeting type, then the peer question"


# --------------------------------------------------------------------------- #
# The shipped library
# --------------------------------------------------------------------------- #


def test_red_blue_declares_the_restriction_in_metadata_not_only_in_a_comment():
    """The regression this key exists for: the loader strips YAML comments."""
    mission = load_mission(RED_BLUE)
    assert mission.metadata.get("claude_peer") == entrypoint.CLAUDE_PEER_UNSUPPORTED
    assert not entrypoint.claude_peer_supported(mission)


def test_red_blue_is_the_only_restricted_template():
    """A guard against restricting a template by accident."""
    restricted_paths = [
        path
        for _label, path, _group in entrypoint._discover_meeting_types()
        if not entrypoint._claude_peer_supported_at(path)
    ]
    assert restricted_paths == [RED_BLUE]


# --------------------------------------------------------------------------- #
# The dashboard
# --------------------------------------------------------------------------- #


def test_the_dashboard_agrees_with_the_runtime():
    assert library.claude_peer_allowed(RED_BLUE) is False
    assert library.claude_peer_allowed(SYMMETRIC) is True


def test_an_unloadable_contract_is_not_treated_as_restricted(tmp_path):
    """Setup reports a broken contract; this predicate must not also block on it."""
    assert library.claude_peer_allowed(tmp_path / "does-not-exist.yaml") is True


def test_the_roster_omits_a_seat_the_run_cannot_deliver():
    seats = library.roster(RED_BLUE, claude_peer=True)
    assert seats, "the contract loads"
    assert not any(s["id"].lower() == "claude" for s in seats)


def test_the_roster_still_shows_the_injected_seat_where_it_is_allowed():
    seats = library.roster(SYMMETRIC, claude_peer=True)
    injected = [s for s in seats if s["id"].lower() == "claude"]
    assert len(injected) == 1
    assert injected[0]["injected"] is True


def test_the_launch_command_omits_the_flag_for_a_restricted_contract():
    args = launcher.build_args(RED_BLUE, Path("outputs/run-1"), {"claude_peer": True})
    assert "--claude" not in args


def test_the_launch_command_keeps_the_flag_where_it_is_allowed():
    args = launcher.build_args(SYMMETRIC, Path("outputs/run-1"), {"claude_peer": True})
    assert "--claude" in args
