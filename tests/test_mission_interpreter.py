"""Focused tests for the Mission Interpreter (mission_interpreter.py).

This is the architectural centrepiece, so it is driven end-to-end through a
scripted ``FakeTransport``: no real I/O, fully deterministic. The scenarios pin
the control-flow contract — turn order, checkpoint cadence, each Founder
decision, and the synthesis-on-success rule.
"""

from __future__ import annotations

from frelan.deliverables import split_outputs
from frelan.enums import CheckpointDecision, LedgerEntryType, RuntimeStatus
from frelan.ledger import Ledger
from frelan.mission_contract import ExecutionPhase, OutputDefinition
from frelan.mission_instance import MissionInstance
from frelan.mission_interpreter import MissionInterpreter


class FakeTransport:
    """Scripted transport: returns queued responses and checkpoint decisions."""

    def __init__(self, responses, decisions=()):
        self._responses = iter(responses)
        self._decisions = iter(decisions)
        self.delivered: list[tuple[str, str]] = []

    def deliver_prompt(self, participant, prompt):
        self.delivered.append((participant.id, prompt))

    def collect_response(self, participant):
        return next(self._responses)

    def ask_checkpoint(self, summary):
        return next(self._decisions)


def _run(mission, transport) -> MissionInstance:
    instance = MissionInstance(mission=mission, ledger=Ledger())
    MissionInterpreter(transport).run(instance)
    return instance


def _responses(ledger: Ledger):
    return [e for e in ledger.entries if e.entry_type is LedgerEntryType.RESPONSE]


def test_terminate_at_checkpoint_stops_without_synthesis(make_mission):
    mission = make_mission(checkpoint_interval=1)  # checkpoint after each round
    transport = FakeTransport(
        responses=["chatgpt says A", "gemini says B"],
        decisions=[CheckpointDecision.TERMINATE],
    )
    inst = _run(mission, transport)

    assert inst.status is RuntimeStatus.TERMINATED
    assert "final_recommendation" not in inst.context
    assert [pid for pid, _ in transport.delivered] == ["chatgpt", "gemini"]


def test_converged_triggers_synthesis_turn(make_mission):
    mission = make_mission(checkpoint_interval=1)
    transport = FakeTransport(
        responses=["A", "B", "THE RECOMMENDATION"],  # 3rd is the synthesis
        decisions=[CheckpointDecision.CONVERGED],
    )
    inst = _run(mission, transport)

    assert inst.status is RuntimeStatus.CONVERGED
    assert inst.context["final_recommendation"] == "THE RECOMMENDATION"
    # a synthesis turn was delivered to the first participant
    assert transport.delivered[-1][0] == "chatgpt"
    assert any(e.role == "synthesiser" for e in _responses(inst.ledger))


def test_turns_recorded_in_declared_order_with_roles(make_mission):
    mission = make_mission(checkpoint_interval=1)
    transport = FakeTransport(
        responses=["A", "B"], decisions=[CheckpointDecision.TERMINATE]
    )
    inst = _run(mission, transport)

    responses = _responses(inst.ledger)
    assert [e.participant_id for e in responses] == ["chatgpt", "gemini"]
    assert [e.role for e in responses] == ["proposer", "critic"]


def test_continue_then_terminate_spans_two_rounds(make_mission):
    mission = make_mission(checkpoint_interval=1, gov_max_rounds=5)
    transport = FakeTransport(
        responses=["A", "B", "C", "D"],  # two full rounds
        decisions=[CheckpointDecision.CONTINUE, CheckpointDecision.TERMINATE],
    )
    inst = _run(mission, transport)

    assert inst.status is RuntimeStatus.TERMINATED
    assert len(inst.ledger.checkpoint_summaries()) == 2
    assert len(transport.delivered) == 4


def test_natural_completion_by_phase_cap_produces_recommendation(make_mission):
    phases = (
        ExecutionPhase(
            id="only",
            name="Only Phase",
            objective="one round then done",
            participant_ids=("chatgpt", "gemini"),
            max_rounds=1,
        ),
    )
    mission = make_mission(checkpoint_interval=5, phases=phases)  # no checkpoint hit
    transport = FakeTransport(responses=["A", "B", "RECOMMENDATION"])
    inst = _run(mission, transport)

    assert inst.status is RuntimeStatus.COMPLETED
    assert inst.context["final_recommendation"] == "RECOMMENDATION"


def test_global_round_cap_completes_mission(make_mission):
    mission = make_mission(checkpoint_interval=5, gov_max_rounds=1)
    transport = FakeTransport(responses=["A", "B", "RECOMMENDATION"])
    inst = _run(mission, transport)

    assert inst.status is RuntimeStatus.COMPLETED
    assert inst.context["final_recommendation"] == "RECOMMENDATION"


# -- per-output synthesis ---------------------------------------------------
#
# A browser reply is the ceiling on a deliverable, so a mission declaring N
# documents gets N synthesis turns rather than one turn carrying all N. The
# replies are joined and parsed exactly as one multi-section reply would be.


def _outputs(*ids) -> tuple[OutputDefinition, ...]:
    return tuple(
        OutputDefinition(
            id=i, title=i.upper(), description=f"the {i}", filename=f"{i}.md"
        )
        for i in ids
    )


def _wrapped(output_id: str, body: str) -> str:
    return f"BEGIN-OUTPUT: {output_id}\n{body}\nEND-OUTPUT: {output_id}"


def _synthesis_prompts(transport, participant_id="chatgpt"):
    """The prompts delivered after the discussion turns ended."""
    return [p for pid, p in transport.delivered if pid == participant_id][-4:]


def test_each_declared_output_gets_its_own_synthesis_turn(make_mission):
    outputs = _outputs("prd", "blueprint", "plan", "brief")
    mission = make_mission(checkpoint_interval=1, outputs=outputs)
    transport = FakeTransport(
        responses=[
            "A",
            "B",
            _wrapped("prd", "the requirements"),
            _wrapped("blueprint", "the architecture"),
            _wrapped("plan", "the phases"),
            _wrapped("brief", "the agent brief"),
        ],
        decisions=[CheckpointDecision.CONVERGED],
    )
    inst = _run(mission, transport)

    synthesis = [e for e in _responses(inst.ledger) if e.role == "synthesiser"]
    assert len(synthesis) == 4
    assert {e.participant_id for e in synthesis} == {"chatgpt"}

    sections = split_outputs(inst.context["final_recommendation"], outputs)
    assert sections == {
        "prd": "the requirements",
        "blueprint": "the architecture",
        "plan": "the phases",
        "brief": "the agent brief",
    }


def test_only_the_first_synthesis_turn_carries_the_transcript(make_mission):
    """The rest are short: that conversation already holds the discussion.

    This is what keeps four synthesis prompts off the attachment ladder — one
    oversized prompt is an accepted cost, four is not.
    """
    outputs = _outputs("prd", "blueprint")
    mission = make_mission(checkpoint_interval=1, outputs=outputs)
    discussion = "a real discussion turn. " * 250  # ~6k chars, as they run live
    transport = FakeTransport(
        responses=[
            discussion,
            discussion,
            _wrapped("prd", "x"),
            _wrapped("blueprint", "y"),
        ],
        decisions=[CheckpointDecision.CONVERGED],
    )
    _run(mission, transport)

    first, second = [p for pid, p in transport.delivered if pid == "chatgpt"][-2:]
    assert "## Discussion transcript" in first
    assert "## Discussion transcript" not in second
    assert len(first) > 10_000  # over ChatGPT's composer; an accepted cost once
    assert len(second) < 9_000  # ...but the turns after it stay inline
    # Each turn names exactly one deliverable, and the later one knows what
    # already exists so the documents stay consistent.
    assert "Deliverable 1 of 2" in first and "Deliverable 2 of 2" not in first
    assert "Deliverable 2 of 2" in second
    assert "Already written in this run: PRD" in second


def test_a_reply_without_sentinels_is_wrapped_and_the_repair_recorded(make_mission):
    """Losing a whole document to a formatting slip is the worse failure."""
    outputs = _outputs("prd", "blueprint")
    mission = make_mission(checkpoint_interval=1, outputs=outputs)
    transport = FakeTransport(
        responses=["A", "B", "a PRD with no sentinels", _wrapped("blueprint", "y")],
        decisions=[CheckpointDecision.CONVERGED],
    )
    inst = _run(mission, transport)

    sections = split_outputs(inst.context["final_recommendation"], outputs)
    assert sections["prd"] == "a PRD with no sentinels"
    system = "\n".join(
        e.content for e in inst.ledger.entries if e.entry_type is LedgerEntryType.SYSTEM
    )
    assert "[DELIVERABLE]" in system and "prd" in system


def test_a_single_output_mission_still_runs_exactly_one_synthesis_turn(make_mission):
    """The legacy path is untouched — no sentinels, no per-output ceremony."""
    mission = make_mission(checkpoint_interval=1)
    transport = FakeTransport(
        responses=["A", "B", "THE RECOMMENDATION"],
        decisions=[CheckpointDecision.CONVERGED],
    )
    inst = _run(mission, transport)

    synthesis = [e for e in _responses(inst.ledger) if e.role == "synthesiser"]
    assert len(synthesis) == 1
    assert inst.context["final_recommendation"] == "THE RECOMMENDATION"
    assert "BEGIN-OUTPUT" not in transport.delivered[-1][1]


def test_interpreter_handles_turn_level_prompt_editing(make_mission):
    mission = make_mission(checkpoint_interval=1)

    class TurnEditingTransport(FakeTransport):
        def __init__(self, responses, decisions=()):
            super().__init__(responses, decisions)
            self.collect_count = 0

        def collect_response(self, participant):
            self.collect_count += 1
            if self.collect_count == 1:
                # First collect call, simulate user choosing [P] to edit prompt
                self.topic_override = "Brand New Custom Topic"
                self.prompt_inject = "Direct focus on performance tests."
                return "__EDIT_PROMPT__"
            # Second collect call, return the actual response
            return "This is response with edited context."

    transport = TurnEditingTransport(
        responses=["never called", "THE RECOMMENDATION"],
        decisions=[CheckpointDecision.CONVERGED]
    )
    inst = _run(mission, transport)

    assert transport.collect_count == 4
    # Verify the second delivered prompt contains our edits!
    last_delivered_prompt = transport.delivered[1][1]
    assert "Brand New Custom Topic" in last_delivered_prompt
    assert "Direct focus on performance tests." in last_delivered_prompt
