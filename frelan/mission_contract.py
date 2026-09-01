"""Mission Layer — the immutable, declarative Mission Contract.

This module *implements* the canonical specification in ``MISSION-CONTRACT.md``;
it does not define it. When the two disagree, the specification is authoritative
and this module has a bug.

This module contains ONLY data. It describes *what* a mission is: its identity,
objective, participants, capabilities, execution phases, governance policy, and
expected outputs. It contains no execution mechanics and no mission-specific
behaviour.

Immutability is enforced structurally:

- every type is a ``frozen`` dataclass (attributes cannot be reassigned), and
- every collection field is a ``tuple`` (never a ``list``), so nested contents
  cannot be mutated either.

Runtime state that *does* change during a run lives in the separate
``MissionInstance`` (see mission_instance.py). The contract must never change
once loaded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class Capability:
    """A named ability a participant may be required to provide."""

    id: str
    description: str


@dataclass(frozen=True, slots=True)
class AssignedEngine:
    """A participant's binding, separating four independent concerns.

    - ``role`` and ``required_capabilities`` are Mission-Layer concerns (what the
      participant is *for*).
    - ``transport_provider`` is a Transport-Layer selector (*how* it is reached,
      e.g. ``"browser"`` now, ``"openai_api"`` later).
    - ``execution_engine`` is an Execution-Layer selector (*which* model runs,
      e.g. ``"chatgpt"``, ``"gemini"``).

    The interpreter reads these values only to render prompts and route through
    the transport; it never branches on them. That separation is what keeps
    every engine interchangeable and every transport addable without touching
    the interpreter.
    """

    role: str
    required_capabilities: tuple[str, ...]
    transport_provider: str
    execution_engine: str


@dataclass(frozen=True, slots=True)
class Participant:
    """An entity taking part in the mission.

    Identity (``id``, ``display_name``) is kept separate from the binding
    (``assigned_engine``) so that re-assigning a participant to a different
    transport or engine is a change to data, not to identity.

    ``type`` says what this participant *is*: ``"model"`` — the execution engine
    seated as itself — or ``"agent"`` — a configured worker built around an
    engine, with its own identity, standing brief, and role. It is declarative:
    the interpreter never branches on it, and a mission may seat models and
    agents side by side. Defaults to ``"model"``, so a contract written before
    the field existed means exactly what it always meant.

    ``instructions`` is that participant's **standing brief** — guidance that
    applies to every one of its turns, as distinct from a phase's instructions,
    which apply to everyone in the phase. It is what makes an agent an agent
    rather than a renamed model.

    The literal defaults below intentionally repeat ``frelan.enums``' vocabulary
    rather than importing it: the Mission Layer is pure data and must not depend
    on runtime enumerations. ``tests/test_interaction.py`` asserts the two never
    drift.
    """

    id: str
    display_name: str
    assigned_engine: AssignedEngine
    type: str = "model"
    instructions: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionPhase:
    """One declarative phase of the discussion.

    The ordered ``participant_ids`` together with the round structure *are* the
    discussion strategy — expressed as data, not as Strategy-pattern classes.
    ``max_rounds`` optionally caps this phase; ``None`` means the phase is only
    bounded by governance and the Founder's checkpoint decisions.

    ``context`` declares how much of the discussion this phase's prompts carry:

    - ``"auto"`` (default) — full window on a participant's first turn, then
      only what is new since its last turn. Each engine's own conversation
      holds the rest.
    - ``"none"`` — no discussion window at all. For genuinely independent
      phases, whose instructions forbid referencing the other peers: sending
      their answers anyway both wastes the composer and undercuts the phase.
    - ``"delta"`` — only what is new, even on a first turn.
    - ``"full"`` — the whole bounded window every turn.

    It is declarative context policy, not execution logic: the interpreter never
    branches on it; the renderer reads it while building the prompt.

    ``interaction`` declares *how* this phase's participants work together —
    the execution pattern, which is a different question from how much context
    they carry and from how the mission is governed:

    - ``"sequential"`` (default) — one participant at a time, each seeing what
      the ones before it said in this round.
    - ``"parallel"`` — every prompt is rendered from the same round-start state
      and delivered before any reply is collected, so the engines generate at
      the same time and none sees another's answer from this round.

    ``context: "none"`` is NOT parallelism: it withholds the other participants'
    answers while still running one turn after another. The two fields are
    orthogonal and may be combined freely.

    ``stage`` is an optional workflow-stage label (``"research"``,
    ``"architecture"``, ``"build"``, ``"validation"``, or anything else a
    contract wants). It carries no execution meaning at all — it exists so a
    multi-stage mission can say which stage a phase belongs to, and so the
    dashboard and the run metadata can report it. No vocabulary is enforced:
    hard-coding a stage list would hard-code one workflow into the runtime.
    """

    id: str
    name: str
    objective: str
    participant_ids: tuple[str, ...]
    instructions: str = ""
    max_rounds: int | None = None
    context: str = "auto"
    interaction: str = "sequential"
    stage: str = ""


@dataclass(frozen=True, slots=True)
class GovernancePolicy:
    """Declarative governance: when to checkpoint and how the mission may end.

    ``checkpoint_interval`` is expressed in completed rounds: after every N
    rounds the interpreter presents the checkpoint menu to the Founder.

    ``synthesiser`` names the participant that writes the final synthesis. It is
    declared rather than inferred so the duty is a contract decision, not an
    accident of who happens to be listed first — which silently gave the role to
    the same engine in every mission. ``None`` keeps the historical behaviour.
    """

    checkpoint_interval: int
    max_rounds: int | None = None
    convergence_note: str = ""
    escalation_note: str = ""
    synthesiser: str | None = None


@dataclass(frozen=True, slots=True)
class OutputDefinition:
    """A declared expected output artifact (rendered to Markdown at the end)."""

    id: str
    title: str
    description: str
    filename: str


@dataclass(frozen=True, slots=True)
class Mission:
    """The complete immutable Mission Contract.

    This is the "program" the interpreter runs. It is assembled once by the
    loader and never mutated thereafter.
    """

    id: str
    name: str
    objective: str
    participants: tuple[Participant, ...]
    capabilities: tuple[Capability, ...]
    phases: tuple[ExecutionPhase, ...]
    governance: GovernancePolicy
    outputs: tuple[OutputDefinition, ...]
    metadata: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def participant(self, participant_id: str) -> Participant:
        """Return the participant with ``participant_id``.

        Raises ``KeyError`` if no participant matches. This is a read-only
        lookup over immutable data; it holds no runtime state.
        """
        for candidate in self.participants:
            if candidate.id == participant_id:
                return candidate
        raise KeyError(f"unknown participant id: {participant_id!r}")

    def phase(self, phase_id: str) -> ExecutionPhase:
        """Return the phase with ``phase_id``; raises ``KeyError`` if absent."""
        for candidate in self.phases:
            if candidate.id == phase_id:
                return candidate
        raise KeyError(f"unknown phase id: {phase_id!r}")
