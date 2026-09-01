"""Shared test fixtures.

A single ``make_mission`` factory keeps the contract-building boilerplate in one
place, so the instance / renderer / interpreter tests can each build the small
variant they need (different checkpoint cadence, phase caps, etc.) without
repeating the full nested structure.
"""

from __future__ import annotations

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


def _participants() -> tuple[Participant, Participant]:
    return (
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
    )


@pytest.fixture
def make_mission():
    def _make(
        *,
        checkpoint_interval: int = 2,
        gov_max_rounds: int | None = None,
        phases: tuple[ExecutionPhase, ...] | None = None,
        outputs: tuple[OutputDefinition, ...] | None = None,
    ) -> Mission:
        if phases is None:
            phases = (
                ExecutionPhase(
                    id="debate",
                    name="Debate",
                    objective="Argue for and against X.",
                    participant_ids=("chatgpt", "gemini"),
                ),
            )
        return Mission(
            id="m1",
            name="Test Mission",
            objective="Decide whether to adopt X.",
            participants=_participants(),
            capabilities=(Capability(id="reasoning", description="step-by-step"),),
            phases=phases,
            governance=GovernancePolicy(
                checkpoint_interval=checkpoint_interval,
                max_rounds=gov_max_rounds,
            ),
            outputs=outputs
            if outputs is not None
            else (
                OutputDefinition(
                    id="rec",
                    title="Recommendation",
                    description="final recommendation",
                    filename="recommendation.md",
                ),
            ),
        )

    return _make
