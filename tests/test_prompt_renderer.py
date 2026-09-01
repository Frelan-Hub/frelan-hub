"""Focused tests for the Prompt Renderer (prompt_renderer.py).

Prompts are the interpreter's only outward-facing text, so these tests pin the
must-haves: the right participant is addressed in the right role, phase guidance
is present, and prior responses flow into the next turn's context.
"""

from __future__ import annotations

from frelan.enums import LedgerEntryType
from frelan.ledger import Ledger
from frelan.mission_instance import MissionInstance
from frelan.prompt_renderer import (
    MAX_EMBED_RESPONSE_CHARS,
    render_checkpoint_summary,
    render_synthesis_prompt,
    render_turn_prompt,
)


def _instance(mission) -> MissionInstance:
    return MissionInstance(mission=mission, ledger=Ledger())


def test_turn_prompt_includes_scoring_on_last_phase_when_gated(make_mission):
    from dataclasses import replace

    mission = replace(make_mission(), metadata={"peer_scoring": "true"})
    prompt = render_turn_prompt(_instance(mission))
    assert "## Contribution Scoring" in prompt
    assert "`gemini`" in prompt  # the other peer is named as a target
    assert "Never score" in prompt
    assert "```frelan-scores" in prompt


def test_turn_prompt_omits_scoring_without_metadata_gate(make_mission):
    prompt = render_turn_prompt(_instance(make_mission()))
    assert "Contribution Scoring" not in prompt


def test_turn_prompt_addresses_current_participant_in_role(make_mission):
    inst = _instance(make_mission())
    prompt = render_turn_prompt(inst)
    assert "ChatGPT" in prompt  # display_name of participant at turn 0
    assert "proposer" in prompt  # its assigned role
    assert "Decide whether to adopt X." in prompt  # mission objective


def test_turn_prompt_opens_when_no_prior_responses(make_mission):
    inst = _instance(make_mission())
    assert "opening the discussion" in render_turn_prompt(inst)


def test_recent_exchange_truncates_oversized_responses(make_mission):
    inst = _instance(make_mission())
    inst.ledger.append(
        LedgerEntryType.RESPONSE, "X" * 9_000, participant_id="chatgpt", role="peer"
    )
    prompt = render_turn_prompt(inst)
    assert "truncated for context length" in prompt
    assert "X" * (MAX_EMBED_RESPONSE_CHARS + 1) not in prompt  # per-response cap


def test_recent_exchange_budget_drops_oldest_first(make_mission):
    inst = _instance(make_mission())
    for i in range(10):
        inst.ledger.append(
            LedgerEntryType.RESPONSE,
            f"resp-{i} " + "Y" * 7_000,
            participant_id="gemini",
            role="peer",
        )
    prompt = render_turn_prompt(inst)
    assert "resp-9" in prompt  # newest always survives
    assert "resp-0" not in prompt  # oldest dropped by the char budget
    assert "omitted to fit the context budget" in prompt


def test_turn_prompt_includes_prior_responses(make_mission):
    inst = _instance(make_mission())
    inst.ledger.append(
        LedgerEntryType.RESPONSE,
        "I propose adopting X because it scales.",
        participant_id="chatgpt",
        role="proposer",
    )
    inst.advance_turn()  # now it's gemini's turn
    prompt = render_turn_prompt(inst)
    assert "I propose adopting X because it scales." in prompt
    assert "Gemini" in prompt  # the new current participant


def test_checkpoint_summary_includes_rounds_and_convergence_note(make_mission):
    inst = _instance(make_mission())
    inst.advance_turn(); inst.advance_turn()  # one round done
    summary = render_checkpoint_summary(inst)
    assert "Rounds completed:** 1" in summary


def test_synthesis_prompt_requests_a_recommendation(make_mission):
    inst = _instance(make_mission())
    prompt = render_synthesis_prompt(inst)
    assert "final recommendation" in prompt.lower()
    assert "Final Synthesis" in prompt


def test_turn_prompt_renders_topic_override_and_prompt_inject(make_mission):
    inst = _instance(make_mission())
    inst.context["topic_override"] = "My Custom Architecture Topic"
    inst.context["prompt_inject"] = "Focus purely on biophilic sustainability."

    prompt = render_turn_prompt(inst)
    assert "My Custom Architecture Topic" in prompt
    assert "Custom Instructions / Context Override" in prompt
    assert "Focus purely on biophilic sustainability." in prompt

    # Verify synthesis prompt also gets the topic override
    synth = render_synthesis_prompt(inst)
    assert "My Custom Architecture Topic" in synth


def test_turn_prompt_renders_injected_files_and_images(make_mission):
    inst = _instance(make_mission())
    inst.context["injected_files"] = {
        "schema.sql": "CREATE TABLE users (id INT);"
    }
    inst.context["injected_images"] = [
        "facade.jpg",
        "https://example.com/drawing.png"
    ]

    prompt = render_turn_prompt(inst)
    assert "## Reference Files" in prompt
    assert "schema.sql" in prompt
    assert "CREATE TABLE users (id INT);" in prompt
    assert "## Reference Images" in prompt
    assert "facade.jpg" in prompt
    assert "https://example.com/drawing.png" in prompt

    # Verify synthesis prompt also gets files and images
    synth = render_synthesis_prompt(inst)
    assert "## Reference Files" in synth
    assert "schema.sql" in synth
    assert "CREATE TABLE users (id INT);" in synth
    assert "## Reference Images" in synth
    assert "facade.jpg" in synth
