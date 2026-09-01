"""Reference files are inlined into every prompt, so the set must be bounded.

Without a budget each harvested artifact or downloaded file permanently
enlarged every later turn — the compounding that pushed prompts past composer
limits and produced hundreds of overflow spills.
"""

from __future__ import annotations

from frelan.enums import LedgerEntryType
from frelan.ledger import Ledger
from frelan.mission_instance import MissionInstance
from frelan.mission_interpreter import (
    MAX_SHARED_ARTIFACT_CHARS,
    MissionInterpreter,
)
from frelan.prompt_renderer import (
    MAX_EMBED_FILE_CHARS,
    REFERENCE_FILES_CHAR_BUDGET,
    SYNTHESIS_CONTEXT_CHAR_BUDGET,
    render_synthesis_prompt,
    render_turn_prompt,
)
from frelan.enums import CheckpointDecision


def _instance(make_mission, files=None, images=None):
    instance = MissionInstance(mission=make_mission(), ledger=Ledger())
    if files is not None:
        instance.context["injected_files"] = files
    if images is not None:
        instance.context["injected_images"] = images
    return instance


# -- per-file cap -----------------------------------------------------------


def test_a_single_oversized_file_is_truncated(make_mission):
    huge = "x" * (MAX_EMBED_FILE_CHARS * 3)
    prompt = render_turn_prompt(_instance(make_mission, files={"big.txt": huge}))

    assert "truncated for context length" in prompt
    assert len(prompt) < len(huge)


def test_a_small_file_is_inlined_whole(make_mission):
    prompt = render_turn_prompt(
        _instance(make_mission, files={"notes.txt": "CREATE TABLE t (id int);"})
    )
    assert "CREATE TABLE t (id int);" in prompt


# -- total budget and eviction ---------------------------------------------


def test_files_beyond_the_total_budget_are_named_not_inlined(make_mission):
    body = "y" * MAX_EMBED_FILE_CHARS
    count = (REFERENCE_FILES_CHAR_BUDGET // MAX_EMBED_FILE_CHARS) + 3
    files = {f"file{i}.txt": body for i in range(count)}

    prompt = render_turn_prompt(_instance(make_mission, files=files))

    assert "not inlined to fit the context budget" in prompt
    # The budget is a real ceiling, not advice.
    assert prompt.count("### File:") < count


def test_the_founders_first_file_survives_eviction(make_mission):
    """Insertion order is priority: startup references outrank later artifacts."""
    files = {"founder_brief.md": "THE BRIEF"}
    for i in range(20):
        files[f"generated_{i}.md"] = "z" * MAX_EMBED_FILE_CHARS

    prompt = render_turn_prompt(_instance(make_mission, files=files))

    assert "THE BRIEF" in prompt
    assert "founder_brief.md" in prompt


def test_omitted_files_are_announced_by_name(make_mission):
    files = {"kept.md": "k" * MAX_EMBED_FILE_CHARS}
    for i in range(20):
        files[f"dropped_{i}.md"] = "d" * MAX_EMBED_FILE_CHARS

    prompt = render_turn_prompt(_instance(make_mission, files=files))

    assert "dropped_19.md" in prompt  # named in the omission notice


def test_prompt_size_stops_growing_once_the_budget_is_reached(make_mission):
    body = "q" * MAX_EMBED_FILE_CHARS
    ten = {f"f{i}.txt": body for i in range(10)}
    fifty = {f"f{i}.txt": body for i in range(50)}

    small = render_turn_prompt(_instance(make_mission, files=ten))
    large = render_turn_prompt(_instance(make_mission, files=fifty))

    # Five times the files must not mean five times the prompt.
    assert len(large) < len(small) * 2


# -- the synthesis prompt is bounded too ------------------------------------


def test_synthesis_applies_the_same_reference_budget(make_mission):
    body = "s" * MAX_EMBED_FILE_CHARS
    files = {f"f{i}.txt": body for i in range(30)}

    prompt = render_synthesis_prompt(_instance(make_mission, files=files))

    assert "not inlined to fit the context budget" in prompt


def test_synthesis_gets_a_wider_discussion_window_than_a_turn(make_mission):
    instance = _instance(make_mission)
    for i in range(40):
        instance.ledger.append(
            LedgerEntryType.RESPONSE, "w" * 4_000, participant_id="chatgpt"
        )

    turn = render_turn_prompt(instance)
    synthesis = render_synthesis_prompt(instance)

    assert len(synthesis) > len(turn)
    assert len(synthesis) < SYNTHESIS_CONTEXT_CHAR_BUDGET * 2


def test_synthesis_heading_does_not_promise_more_than_it_sends(make_mission):
    instance = _instance(make_mission)
    for i in range(40):
        instance.ledger.append(
            LedgerEntryType.RESPONSE, "w" * 8_000, participant_id="chatgpt"
        )

    prompt = render_synthesis_prompt(instance)

    assert "## Discussion transcript" in prompt
    assert "## Full discussion" not in prompt
    assert "omitted to fit the context budget" in prompt


# -- the producer side ------------------------------------------------------


class _OneShotTransport:
    def __init__(self, response):
        self._response = response
        self._used = False
        self.injected_files = None

    def deliver_prompt(self, participant, prompt):
        pass

    def collect_response(self, participant):
        if self._used:
            return "done"
        self._used = True
        return self._response

    def ask_checkpoint(self, summary):
        return CheckpointDecision.CONTINUE


def test_harvested_artifact_is_written_whole_but_shared_capped(
    tmp_path, make_mission
):
    body = "L" * (MAX_SHARED_ARTIFACT_CHARS * 2)
    response = f"```python\n# filename: big.py\n{body}\n```\n"
    instance = MissionInstance(mission=make_mission(gov_max_rounds=1), ledger=Ledger())

    MissionInterpreter(_OneShotTransport(response), artifact_dir=tmp_path).run(instance)

    on_disk = (tmp_path / "big.py").read_text(encoding="utf-8")
    shared = instance.context["injected_files"][str(tmp_path / "big.py")]

    assert len(on_disk) > MAX_SHARED_ARTIFACT_CHARS  # complete file preserved
    assert len(shared) < len(on_disk)  # inlined copy capped
    assert "truncated for context" in shared
    assert "big.py" in shared  # points at the complete file
