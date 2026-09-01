"""Delta prompting and send-once references (context steps 2 and 3).

The browser chat is the context store: each engine's conversation already holds
its prior prompts, its own answers, and any file it was given. Re-sending all of
it every turn is what grew prompts 1.5k -> 35k on the 2026-08-19 run. A prompt
now carries only the difference.
"""

from __future__ import annotations

from frelan.enums import LedgerEntryType
from frelan.ledger import Ledger
from frelan.mission_instance import MissionInstance
from frelan.prompt_renderer import (
    _delta_exchange,
    _unseen_responses,
    render_turn_prompt,
)


def _instance(mission) -> MissionInstance:
    return MissionInstance(mission=mission, ledger=Ledger())


def _turn(inst: MissionInstance, pid: str, prompt: str, answer: str) -> None:
    """Record a completed turn the way the interpreter does (prompt, then response)."""
    inst.ledger.append(LedgerEntryType.PROMPT, prompt, participant_id=pid, role="peer")
    inst.ledger.append(LedgerEntryType.RESPONSE, answer, participant_id=pid, role="peer")


def test_first_turn_gets_the_full_window(make_mission):
    inst = _instance(make_mission())
    _turn(inst, "gemini", "p", "gemini opening position")

    prompt = render_turn_prompt(inst)  # chatgpt has not spoken yet
    assert "## Discussion so far" in prompt
    assert "gemini opening position" in prompt


def test_later_turns_get_only_what_is_new(make_mission):
    inst = _instance(make_mission())
    _turn(inst, "chatgpt", "prompt-1", "chatgpt round one")
    _turn(inst, "gemini", "prompt-2", "gemini round one")

    prompt = render_turn_prompt(inst)  # chatgpt again

    assert "## Since your last turn" in prompt
    assert "gemini round one" in prompt          # new to chatgpt
    assert "chatgpt round one" not in prompt     # its own answer, already in its chat
    assert "above in this same conversation" in prompt


def test_own_responses_are_never_echoed_back(make_mission):
    inst = _instance(make_mission())
    _turn(inst, "chatgpt", "p1", "mine-1")
    _turn(inst, "gemini", "p2", "theirs-1")
    _turn(inst, "chatgpt", "p3", "mine-2")

    unseen = _unseen_responses(inst, "chatgpt")
    assert [e.content for e in unseen] == []
    assert "Nothing new since your last turn" in _delta_exchange(inst, "chatgpt")


def test_prompt_size_is_flat_across_many_rounds(make_mission):
    inst = _instance(make_mission())
    sizes = []
    for i in range(12):
        prompt = render_turn_prompt(inst)
        sizes.append(len(prompt))
        pid = inst.current_participant().id
        _turn(inst, pid, prompt, f"answer {i} " + "z" * 4_000)
        inst.advance_turn()

    steady = sizes[4:]
    assert max(steady) - min(steady) < 500, f"prompt size drifting: {sizes}"
    assert max(sizes) < 9_000


def test_reference_file_is_inlined_once_then_referenced(make_mission):
    inst = _instance(make_mission())
    inst.context["injected_files"] = {"inputs/schema.sql": "CREATE TABLE demo (id INT);"}

    first = render_turn_prompt(inst)
    assert "CREATE TABLE demo" in first  # body delivered on the first turn

    _turn(inst, "chatgpt", first, "noted")
    _turn(inst, "gemini", "p", "also noted")

    second = render_turn_prompt(inst)  # chatgpt's next turn
    assert "CREATE TABLE demo" not in second
    assert "schema.sql" in second
    assert "Already provided to you earlier in this conversation" in second


def test_a_file_added_mid_run_is_inlined_on_its_first_appearance(make_mission):
    inst = _instance(make_mission())
    inst.context["injected_files"] = {"inputs/a.md": "ALPHA BODY"}
    first = render_turn_prompt(inst)
    _turn(inst, "chatgpt", first, "ok")
    _turn(inst, "gemini", "p", "ok")

    # A harvested artifact appears later in the run.
    inst.context["injected_files"]["outputs/b.md"] = "BRAVO BODY"
    second = render_turn_prompt(inst)

    assert "BRAVO BODY" in second   # new file: inlined once
    assert "ALPHA BODY" not in second  # old file: referenced by name


def test_rollover_forces_a_full_rebrief(make_mission):
    """A fresh chat remembers nothing, so a delta would strand that engine."""
    inst = _instance(make_mission())
    _turn(inst, "chatgpt", "p1", "chatgpt round one")
    _turn(inst, "gemini", "p2", "gemini round one")

    # Without a reset, chatgpt's next turn is a delta.
    assert "## Since your last turn" in render_turn_prompt(inst)

    # The transport rolled chatgpt's conversation over; the interpreter synced it.
    inst.context["context_reset"] = {"chatgpt"}
    rebrief = render_turn_prompt(inst)

    assert "## Discussion so far" in rebrief
    assert "chatgpt round one" in rebrief  # its own history is gone, so resend it
    assert "gemini round one" in rebrief


def test_rollover_also_re_inlines_reference_files(make_mission):
    inst = _instance(make_mission())
    inst.context["injected_files"] = {"inputs/schema.sql": "CREATE TABLE demo (id INT);"}
    first = render_turn_prompt(inst)
    _turn(inst, "chatgpt", first, "ok")
    _turn(inst, "gemini", "p", "ok")

    assert "CREATE TABLE demo" not in render_turn_prompt(inst)

    inst.context["context_reset"] = {"chatgpt"}
    assert "CREATE TABLE demo" in render_turn_prompt(inst)
