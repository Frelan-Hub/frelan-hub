"""Tests for artifact harvesting — the only path that writes a filename chosen
by a participant's response to disk.

Two things are asserted here: that a declared file is written, shared, and
contained inside the run directory; and that a name which would land outside it
is refused loudly rather than written, mangled, or silently dropped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from frelan.enums import CheckpointDecision, LedgerEntryType
from frelan.ledger import Ledger
from frelan.mission_instance import MissionInstance
from frelan.mission_interpreter import MissionInterpreter, resolve_artifact_path


class RecordingTransport:
    """Replies with one canned response per turn, then only empty ones."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.injected_files: dict[str, str] | None = None

    def deliver_prompt(self, participant, prompt: str) -> None:
        pass

    def collect_response(self, participant) -> str:
        return self._responses.pop(0) if self._responses else "done"

    def ask_checkpoint(self, summary: str) -> CheckpointDecision:
        return CheckpointDecision.CONTINUE


def _block(filename: str, body: str = "print('hi')", lang: str = "python") -> str:
    return f"```{lang}\n# filename: {filename}\n{body}\n```\n"


def _run(tmp_path: Path, make_mission, response: str) -> MissionInstance:
    mission = make_mission(gov_max_rounds=1)
    instance = MissionInstance(mission=mission, ledger=Ledger())
    transport = RecordingTransport([response])
    MissionInterpreter(transport, artifact_dir=tmp_path).run(instance)
    return instance


def _system_log(instance: MissionInstance) -> str:
    return "\n".join(
        e.content
        for e in instance.ledger.entries
        if e.entry_type is LedgerEntryType.SYSTEM
    )


# -- resolve_artifact_path (the containment decision itself) ----------------


@pytest.mark.parametrize(
    "filename",
    [
        "C:/Windows/Temp/pwn.txt",
        "C:\\Users\\pwn.txt",
        "../pwn.txt",
        "../../pwn.txt",
        "sub/../../pwn.txt",
        "/",
        "",
        "   ",
    ],
)
def test_names_that_escape_the_run_directory_are_refused(tmp_path, filename):
    assert resolve_artifact_path(tmp_path, filename) is None


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("report.md", "report.md"),
        ("sub/report.md", "sub/report.md"),
        ("charette/registers.md", "charette/registers.md"),
        ("/etc/passwd", "etc/passwd"),  # leading slash is stripped, not escaping
        ("nested\\win.md", "nested/win.md"),
    ],
)
def test_contained_names_resolve_under_the_run_directory(tmp_path, filename, expected):
    resolved = resolve_artifact_path(tmp_path, filename)
    assert resolved == (tmp_path / expected).resolve()
    assert resolved.is_relative_to(tmp_path.resolve())


# -- harvesting end to end --------------------------------------------------


def test_declared_file_is_written_and_shared_as_reference(tmp_path, make_mission):
    instance = _run(tmp_path, make_mission, _block("schema.sql", "SELECT 1;", "sql"))

    written = tmp_path / "schema.sql"
    assert written.read_text(encoding="utf-8") == "# filename: schema.sql\nSELECT 1;"
    assert str(written) in instance.context["injected_files"]
    assert "[HARVESTED ARTIFACT]" in _system_log(instance)


def test_harvest_honours_the_run_directory_not_a_hardcoded_outputs(
    tmp_path, make_mission, monkeypatch
):
    """`-o` must govern harvested artifacts too.

    Regression guard: the harvester wrote to a literal ``outputs/`` relative to
    the process CWD, so a run with a custom output directory scattered its
    artifacts somewhere else entirely.
    """
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    run_dir = tmp_path / "run-dir"

    _run(run_dir, make_mission, _block("artifact.py"))

    assert (run_dir / "artifact.py").is_file()
    assert not (cwd / "outputs").exists()


def test_nested_declared_path_is_written_under_the_run_directory(
    tmp_path, make_mission
):
    _run(tmp_path, make_mission, _block("charette/registers.md", "# Registers", "md"))
    assert (tmp_path / "charette" / "registers.md").is_file()


def test_escaping_name_writes_nothing_and_is_recorded(tmp_path, make_mission):
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    instance = _run(run_dir, make_mission, _block("../escaped.md"))

    assert not (tmp_path / "escaped.md").exists()
    assert list(run_dir.iterdir()) == []
    log = _system_log(instance)
    assert "[REFUSED ARTIFACT]" in log
    assert "[HARVESTED ARTIFACT]" not in log
    assert "injected_files" not in instance.context


def test_drive_absolute_name_is_refused_rather_than_misfiled(tmp_path, make_mission):
    """Previously this wrote a junk file literally named ``C``.

    The filename regex stopped at the colon, so ``C:/Windows/x`` was captured as
    ``C`` and written as an extensionless file inside the output directory —
    contained, but silently wrong. It is now refused explicitly.
    """
    instance = _run(tmp_path, make_mission, _block("C:/Windows/Temp/pwn.txt"))

    assert not (tmp_path / "C").exists()
    assert list(tmp_path.iterdir()) == []
    assert "[REFUSED ARTIFACT]" in _system_log(instance)


def test_blocks_without_a_filename_comment_are_ignored(tmp_path, make_mission):
    instance = _run(tmp_path, make_mission, "```python\nprint('no name')\n```\n")

    assert list(tmp_path.iterdir()) == []
    assert "injected_files" not in instance.context


def test_filename_comment_below_the_scan_window_is_ignored(tmp_path, make_mission):
    body = "\n".join(["a = 1"] * 6 + ["# filename: late.py"])
    _run(tmp_path, make_mission, f"```python\n{body}\n```\n")

    assert not (tmp_path / "late.py").exists()
