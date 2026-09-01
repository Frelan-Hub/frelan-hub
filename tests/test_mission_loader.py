"""Focused tests for the Mission Loader (mission_loader.py).

The loader is the trust boundary, so these tests cover both directions: a valid
contract builds a correct ``Mission``, and each documented validation rule
rejects a malformed contract. Because the loader collects *all* errors, one test
asserts that multiple problems are reported together.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from frelan.mission_loader import MissionValidationError, load_mission

EXAMPLE = Path(__file__).resolve().parent.parent / "missions" / "frelan_debate.yaml"
MISSIONS_DIR = EXAMPLE.parent

# The meeting-type template library: every template must load AND be equitable.
# Derived from the menu scan rather than listed by hand — a hardcoded list goes
# stale the moment a template is authored, promoted, or retired, and the whole
# point is that the library grows by dropping in a .yaml file.
def _meeting_templates() -> list[Path]:
    from main import _discover_meeting_types

    return [path for _label, path, _group in _discover_meeting_types()]


@pytest.mark.parametrize("path", _meeting_templates(), ids=lambda p: p.name)
def test_meeting_template_loads_and_is_equitable(path):
    mission = load_mission(path)

    engines = [p.assigned_engine for p in mission.participants]
    # Symmetric peers: one shared role, identical capability lists — no engine
    # permanently owns proposer/critic duties. The role string is also rendered
    # into every prompt, so a mission where the peers announce different roles
    # is unequal in the transcript itself.
    assert len({e.role for e in engines}) == 1
    assert len({e.required_capabilities for e in engines}) == 1
    # Turn order rotates between phases — no engine permanently anchors.
    assert len({phase.participant_ids for phase in mission.phases}) >= 2
    # Claude injection draws its capabilities from whatever the mission
    # declares (`main._inject_claude_peer`); with none of these declared it
    # would join with an empty capability list.
    declared = {c.id for c in mission.capabilities}
    assert declared & {"reasoning.strategic", "critique", "reasoning"}


def _valid_contract() -> dict:
    return {
        "id": "m1",
        "name": "Test",
        "objective": "Decide X.",
        "capabilities": [{"id": "reasoning", "description": "step by step"}],
        "participants": [
            {
                "id": "chatgpt",
                "display_name": "ChatGPT",
                "assigned_engine": {
                    "role": "proposer",
                    "required_capabilities": ["reasoning"],
                    "transport_provider": "browser",
                    "execution_engine": "chatgpt",
                },
            }
        ],
        "phases": [
            {
                "id": "debate",
                "name": "Debate",
                "objective": "Argue.",
                "participant_ids": ["chatgpt"],
            }
        ],
        "governance": {"checkpoint_interval": 1, "max_rounds": 2},
        "outputs": [
            {
                "id": "rec",
                "title": "Rec",
                "description": "final",
                "filename": "recommendation.md",
            }
        ],
    }


def _write_json(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "mission.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_loads_bundled_example_yaml():
    mission = load_mission(EXAMPLE)
    assert mission.id == "frelan-debate-001"
    assert [p.id for p in mission.participants] == ["chatgpt", "gemini"]
    # assigned_engine separation survives the round-trip:
    gemini = mission.participant("gemini").assigned_engine
    assert gemini.transport_provider == "browser"
    assert gemini.execution_engine == "gemini"
    assert "critique" in gemini.required_capabilities


def test_loads_valid_json(tmp_path):
    mission = load_mission(_write_json(tmp_path, _valid_contract()))
    assert mission.governance.checkpoint_interval == 1
    assert isinstance(mission.phases, tuple)


def test_unsupported_extension_is_rejected(tmp_path):
    path = tmp_path / "mission.txt"
    path.write_text("nope", encoding="utf-8")
    with pytest.raises(MissionValidationError):
        load_mission(path)


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(MissionValidationError):
        load_mission(tmp_path / "nope.yaml")


def test_undeclared_capability_reference_is_rejected(tmp_path):
    contract = _valid_contract()
    contract["participants"][0]["assigned_engine"]["required_capabilities"] = ["ghost"]
    with pytest.raises(MissionValidationError) as exc:
        load_mission(_write_json(tmp_path, contract))
    assert any("ghost" in e for e in exc.value.errors)


def test_undeclared_participant_reference_is_rejected(tmp_path):
    contract = _valid_contract()
    contract["phases"][0]["participant_ids"] = ["chatgpt", "ghost"]
    with pytest.raises(MissionValidationError) as exc:
        load_mission(_write_json(tmp_path, contract))
    assert any("ghost" in e for e in exc.value.errors)


def test_bad_checkpoint_interval_is_rejected(tmp_path):
    contract = _valid_contract()
    contract["governance"]["checkpoint_interval"] = 0
    with pytest.raises(MissionValidationError) as exc:
        load_mission(_write_json(tmp_path, contract))
    assert any("checkpoint_interval" in e for e in exc.value.errors)


def test_duplicate_participant_ids_are_rejected(tmp_path):
    contract = _valid_contract()
    contract["participants"].append(dict(contract["participants"][0]))
    with pytest.raises(MissionValidationError) as exc:
        load_mission(_write_json(tmp_path, contract))
    assert any("duplicate participant" in e for e in exc.value.errors)


@pytest.mark.parametrize("bad", ["two", 0, -1, True, 1.5])
def test_bad_phase_max_rounds_is_rejected(tmp_path, bad):
    """Caught at load, not mid-mission.

    An unvalidated value survived loading and only failed inside
    ``is_phase_complete``'s comparison, after the run's browser work was spent.
    """
    contract = _valid_contract()
    contract["phases"][0]["max_rounds"] = bad
    with pytest.raises(MissionValidationError) as exc:
        load_mission(_write_json(tmp_path, contract))
    assert any("max_rounds" in e for e in exc.value.errors)


@pytest.mark.parametrize("value", [None, 1, 5])
def test_valid_phase_max_rounds_is_accepted(tmp_path, value):
    contract = _valid_contract()
    contract["phases"][0]["max_rounds"] = value
    assert load_mission(_write_json(tmp_path, contract)).phases[0].max_rounds == value


@pytest.mark.parametrize(
    "filename",
    [
        "../escape.md",
        "sub/report.md",
        "C:/Windows/report.md",
        "nested\\report.md",
        "..",
    ],
)
def test_output_filename_that_escapes_the_run_directory_is_rejected(tmp_path, filename):
    contract = _valid_contract()
    contract["outputs"][0]["filename"] = filename
    with pytest.raises(MissionValidationError) as exc:
        load_mission(_write_json(tmp_path, contract))
    assert any("filename" in e for e in exc.value.errors)


def test_every_bundled_contract_declares_a_bare_output_filename():
    """The whole library must satisfy the rule, not just hand-written cases."""
    for path in sorted(MISSIONS_DIR.rglob("*.y*ml")):
        try:
            mission = load_mission(path)
        except MissionValidationError:
            continue  # dev fixtures are allowed to be invalid; they never run
        for output in mission.outputs:
            assert output.filename == Path(output.filename).name
            assert ".." not in output.filename


def test_multiple_errors_reported_together(tmp_path):
    contract = _valid_contract()
    del contract["governance"]  # error 1: missing top-level key
    contract["participants"] = []  # error 2: no participants
    with pytest.raises(MissionValidationError) as exc:
        load_mission(_write_json(tmp_path, contract))
    assert len(exc.value.errors) >= 2
