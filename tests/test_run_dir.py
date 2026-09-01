"""Run-directory resolution, the resume pointer, and spill pruning.

Runs used to overwrite a single ``outputs/`` directory, so only the most recent
one survived and no two runs could be compared. Each run now gets its own
timestamped directory; the whole risk in that change is ``--resume`` no longer
knowing where the previous run went, so most of these tests are about the
pointer that keeps it findable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import main as entrypoint


def _args(**overrides):
    base = entrypoint._parse_args([])
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


# -- fresh runs -------------------------------------------------------------


def test_fresh_run_gets_a_timestamped_directory_and_writes_the_pointer(tmp_path):
    run_dir = entrypoint._resolve_run_dir(_args(), root=tmp_path)

    assert run_dir.parent == tmp_path
    assert run_dir.name.startswith(entrypoint.RUN_DIR_PREFIX)
    assert run_dir.is_dir()
    pointer = tmp_path / entrypoint.LAST_RUN_POINTER
    assert Path(pointer.read_text(encoding="utf-8").strip()) == run_dir


def test_two_runs_never_share_a_directory(tmp_path):
    """Including two started inside the same second."""
    first = entrypoint._resolve_run_dir(_args(), root=tmp_path)
    second = entrypoint._resolve_run_dir(_args(), root=tmp_path)

    assert first != second
    assert second.is_dir()


def test_new_run_dir_uses_a_utc_stamp():
    when = datetime(2026, 7, 13, 14, 22, 30, tzinfo=timezone.utc)
    assert (
        entrypoint._new_run_dir(Path("outputs"), now=when).name
        == "run-20260713T142230Z"
    )


# -- explicit -o keeps working unchanged ------------------------------------


def test_explicit_output_dir_is_used_verbatim(tmp_path):
    """Streamlit and the .bat launchers pass -o; they must be unaffected."""
    target = tmp_path / "explicit"

    resolved = entrypoint._resolve_run_dir(_args(output_dir=target), root=tmp_path)

    assert resolved == target
    assert not (tmp_path / entrypoint.LAST_RUN_POINTER).exists()


def test_explicit_output_dir_is_used_verbatim_on_resume(tmp_path):
    target = tmp_path / "explicit"
    target.mkdir()

    resolved = entrypoint._resolve_run_dir(
        _args(output_dir=target, resume=True), root=tmp_path
    )

    assert resolved == target


# -- resume via the pointer -------------------------------------------------


def test_resume_without_o_follows_the_pointer(tmp_path):
    original = entrypoint._resolve_run_dir(_args(), root=tmp_path)

    resumed = entrypoint._resolve_run_dir(_args(resume=True), root=tmp_path)

    assert resumed == original


def test_resume_without_a_pointer_fails_loudly(tmp_path):
    with pytest.raises(SystemExit) as exc:
        entrypoint._resolve_run_dir(_args(resume=True), root=tmp_path)
    assert "Nothing to resume" in str(exc.value)


def test_resume_fails_loudly_when_the_pointed_run_is_gone(tmp_path):
    run_dir = entrypoint._resolve_run_dir(_args(), root=tmp_path)
    run_dir.rmdir()

    with pytest.raises(SystemExit) as exc:
        entrypoint._resolve_run_dir(_args(resume=True), root=tmp_path)
    assert "no longer" in str(exc.value)


def test_a_fresh_run_does_not_disturb_an_earlier_run_directory(tmp_path):
    first = entrypoint._resolve_run_dir(_args(), root=tmp_path)
    (first / "ledger.md").write_text("evidence", encoding="utf-8")

    entrypoint._resolve_run_dir(_args(), root=tmp_path)

    assert (first / "ledger.md").read_text(encoding="utf-8") == "evidence"


# -- spill pruning ----------------------------------------------------------


def _aged_file(path: Path, days: float) -> Path:
    import os
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * 100, encoding="utf-8")
    when = time.time() - days * 86_400
    os.utime(path, (when, when))
    return path


def test_prune_removes_only_old_spills(tmp_path):
    old = _aged_file(tmp_path / "run-a" / "prompt_overflow_chatgpt_1.md", days=30)
    recent = _aged_file(tmp_path / "run-a" / "prompt_overflow_gemini_2.md", days=1)

    removed, freed = entrypoint._prune_spills(tmp_path, days=14)

    assert removed == 1
    assert freed == 100
    assert not old.exists()
    assert recent.exists()


@pytest.mark.parametrize(
    "filename",
    [
        "ledger.md",
        "ledger.jsonl",
        "recommendation.md",
        "metadata.json",
        "evidence.json",
        "checkpoints.md",
        "harvested_artifact.py",
    ],
)
def test_prune_never_touches_run_evidence(tmp_path, filename):
    kept = _aged_file(tmp_path / "run-a" / filename, days=365)

    entrypoint._prune_spills(tmp_path, days=14)

    assert kept.exists()


def test_prune_spills_reports_and_exits_without_running_a_mission(tmp_path, capsys):
    _aged_file(tmp_path / "run-a" / "prompt_overflow_chatgpt_1.md", days=30)

    assert entrypoint.main(["--prune-spills", "14", "-o", str(tmp_path)]) == 0

    assert "Removed 1 spill file(s)" in capsys.readouterr().out


# -- end to end through main() ---------------------------------------------


class _StubTransport:
    """Answers every turn instantly so main() can be exercised offline."""

    def __init__(self, *_a, **_kw):
        self.topic_override = None
        self.prompt_inject = None
        self.injected_files = None
        self.injected_images = None
        self.auto = True

    def deliver_prompt(self, participant, prompt):
        pass

    def collect_response(self, participant):
        return f"Response from {participant.id}."

    def ask_checkpoint(self, summary):
        from frelan.enums import CheckpointDecision

        return CheckpointDecision.CONTINUE

    def close(self):
        pass


@pytest.fixture
def offline_run(monkeypatch, tmp_path):
    """Run main() with no browser, no tty, and an isolated outputs root."""
    monkeypatch.setattr(entrypoint, "_make_transport", lambda *a, **kw: _StubTransport())
    monkeypatch.setattr(entrypoint, "DEFAULT_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(entrypoint, "EVIDENCE_LOG", tmp_path / "evidence-log.jsonl")
    monkeypatch.setattr(entrypoint.sys.stdin, "isatty", lambda: False)
    return tmp_path


def _run_dirs(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.name.startswith(entrypoint.RUN_DIR_PREFIX))


def test_main_writes_each_run_to_its_own_directory(offline_run):
    mission = str(Path("missions") / "frelan_debate.yaml")

    assert entrypoint.main([mission, "--auto"]) == 0
    assert entrypoint.main([mission, "--auto"]) == 0

    dirs = _run_dirs(offline_run)
    assert len(dirs) == 2
    for run_dir in dirs:
        assert (run_dir / "ledger.md").is_file()
        assert (run_dir / "metadata.json").is_file()


def test_invalid_mission_leaves_no_run_directory_behind(offline_run):
    """A mistyped mission path must not litter the outputs root.

    The run directory is created only once the contract validates; resolving it
    earlier left an empty directory and a stale pointer behind every typo.
    """
    assert entrypoint.main([str(offline_run / "nope.yaml")]) == 2

    assert _run_dirs(offline_run) == []
    assert not (offline_run / entrypoint.LAST_RUN_POINTER).exists()


def test_main_resume_after_a_completed_run_targets_the_same_directory(offline_run):
    mission = str(Path("missions") / "frelan_debate.yaml")
    assert entrypoint.main([mission, "--auto"]) == 0
    original = _run_dirs(offline_run)[0]

    assert entrypoint.main(["--resume", "--auto"]) == 0

    # Resume reused the pointed-at run instead of starting a third directory.
    assert _run_dirs(offline_run) == [original]


# -- the evidence log points back at the transcript -------------------------


def test_evidence_log_line_records_the_run_directory(tmp_path, make_mission):
    from frelan.enums import RuntimeStatus
    from frelan.ledger import Ledger
    from frelan.mission_instance import MissionInstance

    instance = MissionInstance(mission=make_mission(), ledger=Ledger())
    instance.status = RuntimeStatus.COMPLETED
    run_dir = tmp_path / "run-x"
    log = tmp_path / "evidence-log.jsonl"

    entrypoint.write_outputs(instance, run_dir, evidence_log=log)

    line = json.loads(log.read_text(encoding="utf-8").strip())
    assert line["run_dir"] == str(run_dir)
    assert line["objective"] == instance.mission.objective
