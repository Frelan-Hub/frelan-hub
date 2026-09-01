"""Shared runtime enumerations.

These describe *states and choices* that arise during execution. They belong to
the Interpretation Layer (they are engine mechanics, not mission data), and are
deliberately kept out of the immutable Mission Contract.
"""

from enum import Enum


class RuntimeStatus(Enum):
    """Lifecycle status of a MissionInstance."""

    INITIALIZED = "initialized"
    RUNNING = "running"
    CONVERGED = "converged"
    ESCALATED = "escalated"
    TERMINATED = "terminated"
    COMPLETED = "completed"

    @property
    def is_terminal(self) -> bool:
        """True once the mission has stopped and no further turns will run."""
        return self in _TERMINAL_STATUSES


_TERMINAL_STATUSES = frozenset(
    {
        RuntimeStatus.CONVERGED,
        RuntimeStatus.ESCALATED,
        RuntimeStatus.TERMINATED,
        RuntimeStatus.COMPLETED,
    }
)


class CheckpointDecision(Enum):
    """The Founder's decision at a checkpoint.

    Presented at the CLI as [C] Continue, [V] Converged, [E] Escalate,
    [T] Terminate. The Founder decides during the MVP; the interpreter never
    infers consensus on its own.
    """

    CONTINUE = "continue"
    CONVERGED = "converged"
    ESCALATE = "escalate"
    TERMINATE = "terminate"


class LedgerEntryType(Enum):
    """Category of a single ledger entry."""

    PROMPT = "prompt"
    RESPONSE = "response"
    CHECKPOINT = "checkpoint"
    SYSTEM = "system"


class Interaction(Enum):
    """How the participants of one phase work together — the execution pattern.

    Deliberately separate from ``phases[].context`` (how much of the discussion
    a prompt carries) and from governance (how the mission is controlled).
    Context isolation is not parallelism: a ``context: none`` phase still runs
    one participant after another.

    - ``SEQUENTIAL`` — each participant speaks in turn and sees what the ones
      before it said in this round. The historical behaviour and the default.
    - ``PARALLEL`` — every participant's prompt is rendered from the same
      round-start state, all prompts are delivered, and only then are the
      replies collected. The engines therefore generate at the same time and
      none of them can see another's answer from this round.
    """

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


#: How far each interaction has actually been proven. Read by the loader (the
#: valid set), the interpreter (dispatch) and the dashboard (what it may claim),
#: so there is one spelling of the vocabulary rather than three.
#:
#: ``experimental`` means implemented and unit-tested, but not yet evidenced by
#: a live browser run. The dashboard must label it as such rather than present
#: it as proven.
INTERACTION_SUPPORT: dict[str, str] = {
    Interaction.SEQUENTIAL.value: "implemented",
    Interaction.PARALLEL.value: "experimental",
}

DEFAULT_INTERACTION = Interaction.SEQUENTIAL.value


class ParticipantType(Enum):
    """What a participant *is*, as declared by the contract.

    - ``MODEL`` — the execution engine seated as itself (ChatGPT as ChatGPT).
    - ``AGENT`` — a configured worker built around an engine: same engine, but
      its own identity, standing brief, and role.

    Declarative only. The interpreter never branches on it, and a mission is
    free to seat models and agents side by side.
    """

    MODEL = "model"
    AGENT = "agent"


DEFAULT_PARTICIPANT_TYPE = ParticipantType.MODEL.value
