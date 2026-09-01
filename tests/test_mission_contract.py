"""Focused tests for the Mission Layer (mission_contract.py).

The contract is pure immutable data, so these tests pin exactly the two
properties the rest of the system depends on:

1. shallow immutability — the contract cannot drift once loaded, and
2. read-only lookups — participant/phase resolution behaves predictably.

Semantic validation (cross-references, required fields) is the loader's job and
is tested separately in Step 4.
"""

from __future__ import annotations

import dataclasses

import pytest

from frelan.mission_contract import (
    AssignedEngine,
    Capability,
    ExecutionPhase,
    GovernancePolicy,
    Mission,
    OutputDefinition,
    Participant,
)


def _mission() -> Mission:
    return Mission(
        id="m1",
        name="Test Mission",
        objective="Decide whether to adopt X.",
        participants=(
            Participant(
                id="chatgpt",
                display_name="ChatGPT",
                assigned_engine=AssignedEngine(
                    role="proposer",
                    required_capabilities=("reasoning",),
                    transport_provider="browser",
                    execution_engine="chatgpt",
                ),
            ),
            Participant(
                id="gemini",
                display_name="Gemini",
                assigned_engine=AssignedEngine(
                    role="critic",
                    required_capabilities=(),
                    transport_provider="browser",
                    execution_engine="gemini",
                ),
            ),
        ),
        capabilities=(Capability(id="reasoning", description="step-by-step"),),
        phases=(
            ExecutionPhase(
                id="debate",
                name="Debate",
                objective="Argue for and against X.",
                participant_ids=("chatgpt", "gemini"),
            ),
        ),
        governance=GovernancePolicy(checkpoint_interval=2),
        outputs=(
            OutputDefinition(
                id="rec",
                title="Recommendation",
                description="final recommendation",
                filename="recommendation.md",
            ),
        ),
    )


def test_contract_attribute_reassignment_is_blocked():
    mission = _mission()
    with pytest.raises(dataclasses.FrozenInstanceError):
        mission.objective = "changed"  # type: ignore[misc]


def test_collection_fields_are_tuples_not_lists():
    # A list here would be a silent mutability hole; assert the type explicitly.
    mission = _mission()
    assert isinstance(mission.participants, tuple)
    assert isinstance(mission.phases, tuple)
    assert isinstance(
        mission.participants[0].assigned_engine.required_capabilities, tuple
    )


def test_participant_lookup_returns_matching_participant():
    mission = _mission()
    assert mission.participant("gemini").assigned_engine.role == "critic"


def test_participant_lookup_raises_on_unknown_id():
    mission = _mission()
    with pytest.raises(KeyError):
        mission.participant("claude")


def test_phase_lookup_returns_matching_phase():
    mission = _mission()
    assert mission.phase("debate").name == "Debate"


def test_phase_lookup_raises_on_unknown_id():
    mission = _mission()
    with pytest.raises(KeyError):
        mission.phase("nope")
