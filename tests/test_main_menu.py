"""Menu-from-directory-scan (MISSION-LIBRARY-RESOLUTION.md §7 change #1)."""

from __future__ import annotations

from pathlib import Path

from main import _discover_meeting_types, _prompt_meeting_type, _FIXTURE_FILES


def test_discovery_finds_real_templates_and_excludes_fixtures() -> None:
    found = _discover_meeting_types()
    names = {path.name for _label, path, _group in found}

    # app_planning is a real runnable template; it must be discovered.
    assert "app_planning.yaml" in names
    # workspace_preparation is a library meeting type, not a hidden stage.
    assert "workspace_preparation.yaml" in names
    # Dev fixtures are excluded from the library listing (§6).
    assert names.isdisjoint(_FIXTURE_FILES)
    # Every discovered entry is (label, Path, group).
    assert all(isinstance(p, Path) and label for label, p, _g in found)


def test_prompt_selects_by_index_and_keeps_default_on_blank() -> None:
    templates = _discover_meeting_types()
    assert templates, "expected at least one runnable template in missions/"

    # Choosing "1" returns the first discovered template's path; ask_claude off.
    replies = iter(["1"])
    path, include_claude = _prompt_meeting_type(
        input_fn=lambda _prompt: next(replies), ask_claude=False
    )
    assert path == templates[0][1]
    assert include_claude is False

    # Blank / out-of-range keeps the default (None) rather than crashing.
    for bad in ("", "0", "999", "abc"):
        p, _ = _prompt_meeting_type(
            input_fn=lambda _prompt, _b=bad: _b, ask_claude=False
        )
        assert p is None


# -- custom meeting types (Founder-authored contracts) ---------------------

import shutil

import main as entrypoint


def _shipped_contract() -> Path:
    """Locate a real shipped contract by name, wherever its category folder is.

    Templates move between ``candidates/`` and their category folder as they are
    promoted (MISSION-LIBRARY-RESOLUTION.md §10), so the path is found rather
    than hardcoded — otherwise every promotion breaks these tests.
    """
    return next(Path("missions").rglob("general_inquiry.yaml"))


def _library_copy(dest: Path, name: str = "custom_one.yaml") -> Path:
    """A real, valid contract copied to ``dest`` — no hand-rolled YAML."""
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / name
    shutil.copy(_shipped_contract(), target)
    return target


def test_general_inquiry_is_a_listed_meeting_type() -> None:
    names = {path.name for _label, path, _group in _discover_meeting_types()}
    assert "general_inquiry.yaml" in names


def test_discovery_lists_subfolder_contracts_tagged_by_folder(tmp_path: Path) -> None:
    shutil.copy(_shipped_contract(), tmp_path / "root_one.yaml")
    _library_copy(tmp_path / "custom")
    # Stage folders hold contracts that are not meeting types.
    _library_copy(tmp_path / "pre-planning", "stage.yaml")

    found = _discover_meeting_types(tmp_path)
    by_file = {path.name: group for _label, path, group in found}

    assert by_file["root_one.yaml"] == ""
    assert by_file["custom_one.yaml"] == "custom"
    assert "stage.yaml" not in by_file  # excluded stage directory


def test_discovery_skips_invalid_contracts_without_crashing(tmp_path: Path) -> None:
    _library_copy(tmp_path / "custom")
    (tmp_path / "custom" / "broken.yaml").write_text("id: only-an-id\n", encoding="utf-8")

    names = {path.name for _label, path, _group in _discover_meeting_types(tmp_path)}
    assert names == {"custom_one.yaml"}


def test_menu_accepts_a_typed_path_to_a_custom_contract(tmp_path: Path) -> None:
    target = _library_copy(tmp_path)
    path, _claude = _prompt_meeting_type(
        input_fn=lambda _prompt: str(target), ask_claude=False
    )
    assert path == target


def test_menu_resolves_a_bare_custom_name_and_rejects_a_bad_one(
    tmp_path: Path, monkeypatch
) -> None:
    target = _library_copy(tmp_path)
    monkeypatch.setattr(entrypoint, "CUSTOM_MISSIONS_DIR", tmp_path)

    # "custom_one" -> missions/custom/custom_one.yaml
    assert entrypoint._custom_meeting_path("custom_one") == target
    # A nonexistent name keeps the default rather than raising.
    assert entrypoint._custom_meeting_path("no_such_meeting") is None


def test_menu_reports_an_invalid_typed_contract_and_keeps_default(
    tmp_path: Path,
) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("id: only-an-id\n", encoding="utf-8")
    path, _claude = _prompt_meeting_type(
        input_fn=lambda _prompt: str(broken), ask_claude=False
    )
    assert path is None
