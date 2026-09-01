"""The Mission Library, as the dashboard reads it.

The menu is the same filesystem scan the CLI menu uses, so these assert on the
library itself rather than on rendered text: a template that documents itself
badly is a library defect, not a rendering one.
"""

from __future__ import annotations

import pytest

from main import DEFAULT_MISSION, _discover_meeting_types
from ui import library


def test_the_menu_offers_every_discovered_template_plus_the_legacy_default():
    types = library.meeting_type_map()
    discovered = _discover_meeting_types()
    assert len(types) == len(discovered) + 1
    assert any(path == DEFAULT_MISSION for _label, path in types.values())


def test_a_grouped_template_is_tagged_with_its_category():
    types = library.meeting_type_map()
    grouped = [label for label, _p in types.values() if label.startswith("[")]
    assert grouped, "category folders should tag their templates"


def test_a_short_objective_is_shown_whole():
    text = "Answer the question posed by the Founder."
    assert library.split_objective(text) == (text, False)


def test_a_long_objective_is_cut_at_a_word_boundary():
    text = "word " * 200
    shown, truncated = library.split_objective(text)
    assert truncated
    assert shown.endswith("…")
    assert not shown.rstrip("…").endswith(" "), "cut at a word, not mid-space"
    assert len(shown) <= library.OBJECTIVE_INLINE_CHARS + 1


@pytest.mark.parametrize(
    "path", [p for _l, p, _g in _discover_meeting_types()], ids=lambda p: p.name
)
def test_every_template_has_a_brief_worth_showing(path):
    """A summary only as informative as the template's name is not a summary."""
    brief = library.brief(path)
    assert brief, f"{path} did not load"
    assert len(brief["summary"]) >= 40, f"{path}: metadata.summary is too thin"
    assert "\n" not in brief["summary"], f"{path}: summary must render on one line"
    assert brief["objective"], f"{path} has no objective to show as its goal"


@pytest.mark.parametrize(
    "path", [p for _l, p, _g in _discover_meeting_types()], ids=lambda p: p.name
)
def test_every_template_declares_at_least_one_deliverable(path):
    outputs = library.declared_outputs(path)
    assert outputs, f"{path} promises no final-answer file"
    for spec in outputs:
        assert spec["filename"], f"{path}: an output with no filename cannot be written"


def test_the_roster_reflects_the_run_that_will_actually_happen():
    """``--claude`` seats Claude in every phase, so the roster must show it."""
    path = DEFAULT_MISSION
    without = library.roster(path, claude_peer=False)
    with_claude = library.roster(path, claude_peer=True)
    ids = {seat["id"].lower() for seat in with_claude}
    assert "claude" in ids
    assert len(with_claude) == len(without) + 1
    injected = next(s for s in with_claude if s["id"].lower() == "claude")
    assert injected["injected"] is True


def test_governance_is_read_from_the_contract():
    policy = library.governance(DEFAULT_MISSION)
    assert policy["checkpoint_interval"] >= 1
    assert policy["phases"], "a mission with no phases cannot be run"
    assert policy["synthesiser"], "synthesis falls back to the first participant"


# --------------------------------------------------------------------------- #
# Mission shape — what the dashboard is allowed to claim
# --------------------------------------------------------------------------- #


def test_the_shape_reports_the_interactions_the_contract_declares():
    from pathlib import Path

    shape = library.shape(Path("missions/candidates/research_architect_build.yaml"))
    assert shape["workflow"] == "research-architect-build"
    assert shape["interactions"] == ["parallel", "sequential"]
    assert shape["stages"] == ["research", "architecture", "build", "validation"]
    research = next(ph for ph in shape["phases"] if ph["id"] == "research")
    assert research["interaction"] == "parallel"
    assert research["context"] == "none", (
        "parallel and context isolation are separate decisions; this template "
        "makes both, and the shape must report both"
    )


def test_a_sequential_contract_reports_no_workflow_and_no_stages():
    """Absence is reported as absence, not filled in with a plausible default."""
    from pathlib import Path

    shape = library.shape(Path("missions/distill/general_inquiry.yaml"))
    assert shape["workflow"] == ""
    assert shape["stages"] == []
    assert shape["interactions"] == ["sequential"]


def test_the_catalogue_lists_only_interactions_the_runtime_can_execute():
    """The dashboard must not offer a pattern the loader would refuse."""
    from frelan.enums import INTERACTION_SUPPORT

    catalogue = dict(library.interaction_catalogue())
    assert catalogue == INTERACTION_SUPPORT
    assert "relay" not in catalogue and "validation_gate" not in catalogue


def test_a_broken_contract_yields_an_empty_shape(tmp_path):
    bad = tmp_path / "broken.yaml"
    bad.write_text("id: only-an-id\n", encoding="utf-8")
    assert library.shape(bad) == {}


def test_the_roster_states_what_each_participant_is():
    from pathlib import Path

    seats = library.roster(
        Path("missions/candidates/research_architect_build.yaml"), claude_peer=False
    )
    assert {s["type"] for s in seats} == {"model"}
    assert {s["engine"] for s in seats} == {"chatgpt", "gemini"}
    assert all(s["standing_brief"] == "" for s in seats)


def test_the_shape_names_what_backs_the_synthesiser():
    """The provenance panel needs the model and role, not just the id."""
    from pathlib import Path

    shape = library.shape(Path("missions/candidates/research_architect_build.yaml"))
    producer = next(p for p in shape["participants"] if p["id"] == shape["synthesiser"])
    assert producer["model"] == "chatgpt"
    assert producer["role"] == "peer_analyst"
    assert producer["type"] == "model"


def test_the_contract_shape_and_a_runs_shape_agree_on_their_keys():
    """Outputs reads whichever is available, so the two must not diverge."""
    from pathlib import Path

    from main import _mission_shape
    from frelan.mission_loader import load_mission

    path = Path("missions/candidates/research_architect_build.yaml")
    from_run = _mission_shape(load_mission(path))
    from_contract = library.shape(path)
    shared = {"meeting_type", "workflow", "interactions", "stages", "phases",
              "participants"}
    assert shared <= set(from_run) and shared <= set(from_contract)
    for key in ("meeting_type", "workflow", "interactions", "stages"):
        assert from_run[key] == from_contract[key]
    assert [p["id"] for p in from_run["participants"]] == [
        p["id"] for p in from_contract["participants"]
    ]
