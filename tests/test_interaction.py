"""Interaction patterns — how a phase's participants work together.

The one claim worth testing hardest is that ``parallel`` is genuinely parallel
and not a relabelled sequential round. Two properties prove it, and both are
asserted below rather than described:

1. **Every prompt is delivered before any reply is collected.** That ordering
   IS the concurrency: ``deliver_prompt`` submits and returns, ``collect_response``
   is what waits, so overlapping the waits is what makes the engines generate at
   the same time.
2. **No participant's prompt carries another's answer from that round.** Every
   prompt is rendered from the same round-start ledger, so this holds by
   construction and cannot drift back into cross-talk.

The rest guards what must NOT change: round accounting, ledger order, and the
independence of one participant's failure from the others'.
"""

from __future__ import annotations

import pytest

from frelan.enums import (
    DEFAULT_INTERACTION,
    DEFAULT_PARTICIPANT_TYPE,
    INTERACTION_SUPPORT,
    CheckpointDecision,
    Interaction,
    LedgerEntryType,
    ParticipantType,
    RuntimeStatus,
)
from frelan.ledger import Ledger
from frelan.mission_contract import (
    AssignedEngine,
    ExecutionPhase,
    Participant,
)
from frelan.mission_instance import MissionInstance
from frelan.mission_interpreter import MissionInterpreter
from frelan.mission_loader import MissionValidationError, load_mission
from frelan.prompt_renderer import (
    PARTICIPANT_BRIEF_CHAR_BUDGET,
    render_turn_prompt,
)


# --------------------------------------------------------------------------- #
# A transport that records the exact order of every call
# --------------------------------------------------------------------------- #


class RecordingTransport:
    """Scripted transport that logs the call sequence, not just the payloads.

    ``calls`` is the whole point: a parallel round must show every ``deliver``
    before the first ``collect``, and a sequential round must alternate. That
    distinction is invisible to a transport that only records what it was given.
    """

    def __init__(self, responses, decisions=(), fail_deliver=(), fail_collect=()):
        self._responses = dict(responses) if isinstance(responses, dict) else None
        self._queue = None if self._responses else iter(responses)
        self._decisions = iter(decisions)
        self._fail_deliver = set(fail_deliver)
        self._fail_collect = set(fail_collect)
        self.calls: list[tuple[str, str]] = []
        self.delivered: list[tuple[str, str]] = []

    def deliver_prompt(self, participant, prompt):
        self.calls.append(("deliver", participant.id))
        self.delivered.append((participant.id, prompt))
        if participant.id in self._fail_deliver:
            # Fails once, then recovers — a tab that is gone for the whole run
            # would also break the final synthesis, which is a sequential turn
            # and has never had failure isolation. The isolation under test is
            # the parallel round's.
            self._fail_deliver.discard(participant.id)
            raise RuntimeError("browser tab is gone")

    def collect_response(self, participant):
        self.calls.append(("collect", participant.id))
        if participant.id in self._fail_collect:
            raise TimeoutError("no reply within the transport timeout")
        if self._responses is not None:
            replies = self._responses[participant.id]
            return replies.pop(0) if isinstance(replies, list) else replies
        return next(self._queue)

    def ask_checkpoint(self, summary):
        return next(self._decisions)


def _run(mission, transport) -> MissionInstance:
    instance = MissionInstance(mission=mission, ledger=Ledger())
    MissionInterpreter(transport).run(instance)
    return instance


def _phase(interaction: str, **kwargs) -> ExecutionPhase:
    return ExecutionPhase(
        id="work",
        name="Work",
        objective="Do the thing.",
        participant_ids=("chatgpt", "gemini"),
        interaction=interaction,
        max_rounds=1,
        **kwargs,
    )


def _responses(instance: MissionInstance) -> list:
    return [
        e
        for e in instance.ledger.entries
        if e.entry_type is LedgerEntryType.RESPONSE
    ]


def _phase_turns(instance: MissionInstance) -> dict[str, str]:
    """``{participant_id: reply}`` for discussion turns only.

    The final synthesis is a RESPONSE too, and it goes to a participant that has
    already spoken — folding it in silently overwrote the turn under test.
    """
    return {
        e.participant_id: e.content
        for e in _responses(instance)
        if e.role != "synthesiser"
    }


# --------------------------------------------------------------------------- #
# Vocabulary — one spelling, no drift
# --------------------------------------------------------------------------- #


def test_the_contract_defaults_match_the_runtime_vocabulary():
    """The Mission Layer restates these literals rather than importing enums.

    That keeps the contract free of runtime dependencies, at the price of two
    values living in two files. This is the guard on that price.
    """
    assert ExecutionPhase(
        id="p", name="P", objective="o", participant_ids=("chatgpt",)
    ).interaction == DEFAULT_INTERACTION
    assert Participant(
        id="x",
        display_name="X",
        assigned_engine=AssignedEngine("r", (), "browser", "chatgpt"),
    ).type == DEFAULT_PARTICIPANT_TYPE


def test_every_declared_interaction_has_a_support_status():
    assert set(INTERACTION_SUPPORT) == {i.value for i in Interaction}
    assert set(INTERACTION_SUPPORT.values()) <= {"implemented", "experimental"}


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _contract(**overrides) -> dict:
    raw = {
        "id": "m1",
        "name": "Test",
        "objective": "Decide X.",
        "capabilities": [{"id": "reasoning", "description": "step by step"}],
        "participants": [
            {
                "id": "chatgpt",
                "display_name": "ChatGPT",
                "assigned_engine": {
                    "role": "peer",
                    "required_capabilities": ["reasoning"],
                    "transport_provider": "browser",
                    "execution_engine": "chatgpt",
                },
            }
        ],
        "phases": [
            {
                "id": "p1",
                "name": "P1",
                "objective": "Argue.",
                "participant_ids": ["chatgpt"],
            }
        ],
        "governance": {"checkpoint_interval": 1},
        "outputs": [],
    }
    raw.update(overrides)
    return raw


def _write(tmp_path, raw) -> "object":
    import yaml

    path = tmp_path / "m.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_an_unspecified_interaction_is_sequential(tmp_path):
    """A contract written before the field existed means what it always meant."""
    mission = load_mission(_write(tmp_path, _contract()))
    assert mission.phases[0].interaction == Interaction.SEQUENTIAL.value
    assert mission.phases[0].stage == ""


def test_parallel_is_accepted(tmp_path):
    raw = _contract()
    raw["phases"][0]["interaction"] = "parallel"
    raw["phases"][0]["stage"] = "research"
    mission = load_mission(_write(tmp_path, raw))
    assert mission.phases[0].interaction == Interaction.PARALLEL.value
    assert mission.phases[0].stage == "research"


def test_an_unsupported_interaction_fails_clearly(tmp_path):
    """The failure has to name what IS supported, or the author is left guessing."""
    raw = _contract()
    raw["phases"][0]["interaction"] = "relay"
    with pytest.raises(MissionValidationError) as exc:
        load_mission(_write(tmp_path, raw))
    message = str(exc.value)
    assert "interaction" in message
    assert "relay" in message
    assert "sequential" in message and "parallel" in message


def test_an_unknown_participant_type_fails_clearly(tmp_path):
    raw = _contract()
    raw["participants"][0]["type"] = "daemon"
    with pytest.raises(MissionValidationError) as exc:
        load_mission(_write(tmp_path, raw))
    assert "type must be one of" in str(exc.value)


def test_participant_instructions_and_stage_must_be_strings(tmp_path):
    raw = _contract()
    raw["participants"][0]["instructions"] = ["not", "a", "string"]
    raw["phases"][0]["stage"] = 3
    with pytest.raises(MissionValidationError) as exc:
        load_mission(_write(tmp_path, raw))
    assert "instructions must be a string" in str(exc.value)
    assert "stage must be a string" in str(exc.value)


def test_an_agent_participant_loads_with_its_standing_brief(tmp_path):
    raw = _contract()
    raw["participants"][0].update(
        {"type": "agent", "instructions": "You are the research agent."}
    )
    participant = load_mission(_write(tmp_path, raw)).participants[0]
    assert participant.type == ParticipantType.AGENT.value
    assert participant.instructions == "You are the research agent."


def test_a_role_is_independent_of_the_model(tmp_path):
    """The same model may hold different roles; a role is a responsibility.

    Two participants backed by one engine is a *contract*-level fact and must
    load. Whether the browser transport can drive two conversations on one
    engine is a separate, transport-level limitation, recorded in
    CONCEPTUAL-MODEL.md rather than enforced here.
    """
    raw = _contract()
    raw["participants"].append(
        {
            "id": "builder",
            "display_name": "Builder Agent",
            "type": "agent",
            "instructions": "You build what the architect settled.",
            "assigned_engine": {
                "role": "builder",
                "required_capabilities": ["reasoning"],
                "transport_provider": "browser",
                "execution_engine": "chatgpt",  # same model, different role
            },
        }
    )
    raw["phases"][0]["participant_ids"] = ["chatgpt", "builder"]
    mission = load_mission(_write(tmp_path, raw))
    roles = {p.assigned_engine.role for p in mission.participants}
    models = {p.assigned_engine.execution_engine for p in mission.participants}
    assert roles == {"peer", "builder"}
    assert models == {"chatgpt"}


def test_capability_is_independent_of_interaction(tmp_path):
    """Changing how participants work must not change what they can do."""
    sequential = load_mission(_write(tmp_path, _contract()))
    raw = _contract()
    raw["phases"][0]["interaction"] = "parallel"
    parallel = load_mission(_write(tmp_path, raw))
    assert (
        sequential.participants[0].assigned_engine.required_capabilities
        == parallel.participants[0].assigned_engine.required_capabilities
    )
    assert sequential.capabilities == parallel.capabilities


def test_every_shipped_contract_still_declares_a_runnable_interaction():
    """Backward compatibility over the whole library, not a sampled contract."""
    from main import _discover_meeting_types

    for _label, path, _group in _discover_meeting_types():
        for phase in load_mission(path).phases:
            assert phase.interaction in INTERACTION_SUPPORT


# --------------------------------------------------------------------------- #
# Sequential — unchanged behaviour
# --------------------------------------------------------------------------- #


def test_a_sequential_round_alternates_deliver_and_collect(make_mission):
    mission = make_mission(checkpoint_interval=1, phases=(_phase("sequential"),))
    transport = RecordingTransport(
        responses=["A", "B", "SYNTHESIS"], decisions=[CheckpointDecision.CONVERGED]
    )
    _run(mission, transport)
    assert transport.calls[:4] == [
        ("deliver", "chatgpt"),
        ("collect", "chatgpt"),
        ("deliver", "gemini"),
        ("collect", "gemini"),
    ]


def test_an_unspecified_interaction_runs_exactly_as_before(make_mission):
    """The default path must be the historical path, call for call."""
    plain = ExecutionPhase(
        id="work", name="Work", objective="Do it.",
        participant_ids=("chatgpt", "gemini"), max_rounds=1,
    )
    mission = make_mission(checkpoint_interval=1, phases=(plain,))
    transport = RecordingTransport(
        responses=["A", "B", "S"], decisions=[CheckpointDecision.CONVERGED]
    )
    inst = _run(mission, transport)
    assert transport.calls[0] == ("deliver", "chatgpt")
    assert transport.calls[1] == ("collect", "chatgpt")
    assert inst.status is RuntimeStatus.CONVERGED


# --------------------------------------------------------------------------- #
# Parallel — the concurrency claim
# --------------------------------------------------------------------------- #


def test_a_parallel_round_delivers_everything_before_collecting_anything(make_mission):
    """This ordering IS the concurrency — assert it, do not describe it."""
    mission = make_mission(checkpoint_interval=1, phases=(_phase("parallel"),))
    transport = RecordingTransport(
        responses=["A", "B", "SYNTHESIS"], decisions=[CheckpointDecision.CONVERGED]
    )
    _run(mission, transport)

    round_calls = transport.calls[:4]
    assert round_calls == [
        ("deliver", "chatgpt"),
        ("deliver", "gemini"),
        ("collect", "chatgpt"),
        ("collect", "gemini"),
    ]
    first_collect = next(i for i, (kind, _) in enumerate(transport.calls) if kind == "collect")
    delivers_before = [c for c in transport.calls[:first_collect] if c[0] == "deliver"]
    assert len(delivers_before) == 2, "an engine was still waiting to be asked"


def test_a_parallel_prompt_never_carries_a_peer_answer_from_that_round(make_mission):
    mission = make_mission(checkpoint_interval=1, phases=(_phase("parallel"),))
    transport = RecordingTransport(
        responses=["CHATGPT-SECRET-ANSWER", "GEMINI-SECRET-ANSWER", "S"],
        decisions=[CheckpointDecision.CONVERGED],
    )
    _run(mission, transport)

    round_prompts = dict(transport.delivered[:2])
    assert "GEMINI-SECRET-ANSWER" not in round_prompts["chatgpt"]
    assert "CHATGPT-SECRET-ANSWER" not in round_prompts["gemini"]


def test_a_sequential_prompt_does_carry_the_previous_answer(make_mission):
    """The contrast that makes the previous test meaningful."""
    mission = make_mission(checkpoint_interval=1, phases=(_phase("sequential"),))
    transport = RecordingTransport(
        responses=["CHATGPT-SECRET-ANSWER", "B", "S"],
        decisions=[CheckpointDecision.CONVERGED],
    )
    _run(mission, transport)
    assert "CHATGPT-SECRET-ANSWER" in dict(transport.delivered[:2])["gemini"]


def test_a_parallel_round_records_in_declaration_order(make_mission):
    """Whoever finishes first, the ledger reads the same. Runs stay comparable."""
    mission = make_mission(checkpoint_interval=1, phases=(_phase("parallel"),))
    transport = RecordingTransport(
        responses={"chatgpt": ["A"], "gemini": ["B"], "__synth__": []},
        decisions=[CheckpointDecision.CONVERGED],
    )
    # The synthesis turn goes to the first participant; give it a reply.
    transport._responses["chatgpt"].append("SYNTHESIS")
    inst = _run(mission, transport)

    turns = [(e.participant_id, e.content) for e in _responses(inst)]
    assert turns[:2] == [("chatgpt", "A"), ("gemini", "B")]


def test_a_parallel_round_keeps_the_same_round_accounting(make_mission):
    """Rounds and checkpoints must not depend on how a round was executed."""
    results = {}
    for interaction in ("sequential", "parallel"):
        mission = make_mission(
            checkpoint_interval=1,
            phases=(
                ExecutionPhase(
                    id="work", name="Work", objective="Do it.",
                    participant_ids=("chatgpt", "gemini"),
                    interaction=interaction, max_rounds=2,
                ),
            ),
        )
        transport = RecordingTransport(
            responses=["A", "B", "C", "D", "S"],
            decisions=[CheckpointDecision.CONTINUE, CheckpointDecision.CONVERGED],
        )
        inst = _run(mission, transport)
        results[interaction] = (
            inst.rounds_completed,
            len(inst.checkpoint_history),
            len(_responses(inst)),
            inst.status,
        )
    assert results["parallel"] == results["sequential"]


# --------------------------------------------------------------------------- #
# Parallel — independent failure
# --------------------------------------------------------------------------- #


def test_a_failed_delivery_does_not_cost_the_round(make_mission):
    mission = make_mission(checkpoint_interval=1, phases=(_phase("parallel"),))
    transport = RecordingTransport(
        responses={"chatgpt": ["SYNTHESIS"], "gemini": ["B"]},
        decisions=[CheckpointDecision.CONVERGED],
        fail_deliver=["chatgpt"],
    )
    inst = _run(mission, transport)

    turns = _phase_turns(inst)
    assert "[NO RESPONSE]" in turns["chatgpt"]
    assert turns["gemini"] == "B"          # the other engine completed normally
    assert inst.rounds_completed == 1      # the round still completed
    notes = [
        e.content
        for e in inst.ledger.entries
        if e.entry_type is LedgerEntryType.SYSTEM and "PARALLEL FAILURE" in e.content
    ]
    assert notes and "ChatGPT" in notes[0]


def test_a_collection_timeout_does_not_cost_the_round(make_mission):
    """A transport timeout is one engine's failure, not the round's."""
    mission = make_mission(checkpoint_interval=1, phases=(_phase("parallel"),))
    transport = RecordingTransport(
        responses={"chatgpt": ["A", "SYNTHESIS"], "gemini": []},
        decisions=[CheckpointDecision.CONVERGED],
        fail_collect=["gemini"],
    )
    inst = _run(mission, transport)

    turns = _phase_turns(inst)
    assert turns["chatgpt"] == "A"
    assert "[NO RESPONSE]" in turns["gemini"]
    assert inst.status is RuntimeStatus.CONVERGED


def test_an_abandoned_parallel_turn_reruns_only_that_participant(make_mission):
    """``__EDIT_PROMPT__`` is not a reply; the rest of the round is already sent."""
    mission = make_mission(checkpoint_interval=1, phases=(_phase("parallel"),))
    transport = RecordingTransport(
        responses={
            "chatgpt": ["__EDIT_PROMPT__", "A-AFTER-EDIT", "SYNTHESIS"],
            "gemini": ["B"],
        },
        decisions=[CheckpointDecision.CONVERGED],
    )
    inst = _run(mission, transport)

    turns = _phase_turns(inst)
    assert turns["chatgpt"] == "A-AFTER-EDIT"
    assert turns["gemini"] == "B"
    # Only chatgpt was re-delivered; gemini was asked exactly once this round.
    assert [pid for pid, _ in transport.delivered].count("gemini") == 1


def test_a_parallel_round_announces_itself_in_the_ledger(make_mission):
    mission = make_mission(checkpoint_interval=1, phases=(_phase("parallel"),))
    transport = RecordingTransport(
        responses=["A", "B", "S"], decisions=[CheckpointDecision.CONVERGED]
    )
    inst = _run(mission, transport)
    notes = [
        e.content
        for e in inst.ledger.entries
        if e.entry_type is LedgerEntryType.SYSTEM and e.content.startswith("[PARALLEL]")
    ]
    assert notes and "chatgpt, gemini" in notes[0]


def test_a_parallel_phase_resumed_midround_finishes_only_what_is_left(make_mission):
    """A resume landing mid-round must not re-ask an engine already on disk."""
    mission = make_mission(checkpoint_interval=1, phases=(_phase("parallel"),))
    instance = MissionInstance(mission=mission, ledger=Ledger())
    instance.turn_index = 1  # chatgpt's turn is already recorded
    transport = RecordingTransport(
        responses={"gemini": ["B"], "chatgpt": ["SYNTHESIS"]},
        decisions=[CheckpointDecision.CONVERGED],
    )
    MissionInterpreter(transport).run(instance)

    round_deliveries = [pid for pid, _ in transport.delivered][:1]
    assert round_deliveries == ["gemini"]


# --------------------------------------------------------------------------- #
# Standing briefs — what makes an agent an agent
# --------------------------------------------------------------------------- #


def test_a_standing_brief_is_rendered_into_the_prompt(make_mission):
    mission = make_mission()
    briefed = Participant(
        id="chatgpt",
        display_name="ChatGPT",
        type="agent",
        instructions="Always cite a source for every claim.",
        assigned_engine=mission.participants[0].assigned_engine,
    )
    instance = MissionInstance(mission=mission, ledger=Ledger())
    prompt = render_turn_prompt(instance, briefed)
    assert "## Your standing brief" in prompt
    assert "Always cite a source for every claim." in prompt


def test_no_standing_brief_means_no_section(make_mission):
    mission = make_mission()
    instance = MissionInstance(mission=mission, ledger=Ledger())
    assert "## Your standing brief" not in render_turn_prompt(instance)


def test_a_long_standing_brief_is_capped_and_says_so(make_mission):
    mission = make_mission()
    briefed = Participant(
        id="chatgpt",
        display_name="ChatGPT",
        type="agent",
        instructions="x" * (PARTICIPANT_BRIEF_CHAR_BUDGET * 3),
        assigned_engine=mission.participants[0].assigned_engine,
    )
    instance = MissionInstance(mission=mission, ledger=Ledger())
    prompt = render_turn_prompt(instance, briefed)
    assert "truncated for context length" in prompt
    assert len(prompt) < PARTICIPANT_BRIEF_CHAR_BUDGET * 3
