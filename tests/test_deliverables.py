"""Declared synthesiser and multi-output fan-out.

Two accidents are corrected here: the synthesiser was whichever participant
happened to be listed first, and only the first declared output was ever
written — so a contract declaring three deliverables produced one.
"""

from __future__ import annotations

import pytest

from frelan.deliverables import (
    BEGIN_SENTINEL,
    END_SENTINEL,
    ensure_wrapped,
    render_output_instructions,
    render_output_request,
    split_outputs,
)
from frelan.enums import CheckpointDecision, RuntimeStatus
from frelan.ledger import Ledger
from frelan.mission_contract import GovernancePolicy, OutputDefinition
from frelan.mission_instance import MissionInstance
from frelan.mission_interpreter import MissionInterpreter
from frelan.prompt_renderer import render_synthesis_prompt

import main as entrypoint


def _outputs(*ids: str) -> tuple[OutputDefinition, ...]:
    return tuple(
        OutputDefinition(
            id=i, title=i.replace("_", " ").title(), description=f"the {i}",
            filename=f"{i}.md",
        )
        for i in ids
    )


def _section(output_id: str, body: str) -> str:
    return f"{BEGIN_SENTINEL}: {output_id}\n{body}\n{END_SENTINEL}: {output_id}\n"


# -- splitting --------------------------------------------------------------


def test_each_declared_section_is_extracted():
    outputs = _outputs("report", "plan")
    text = "Preamble.\n" + _section("report", "REPORT BODY") + _section("plan", "PLAN BODY")

    assert split_outputs(text, outputs) == {"report": "REPORT BODY", "plan": "PLAN BODY"}


def test_sections_survive_a_missing_trailing_id_on_end():
    outputs = _outputs("report")
    text = f"{BEGIN_SENTINEL}: report\nBODY\n{END_SENTINEL}\n"

    assert split_outputs(text, outputs) == {"report": "BODY"}


def test_ids_match_case_insensitively():
    text = _section("REPORT", "BODY")
    assert split_outputs(text, _outputs("report")) == {"report": "BODY"}


def test_undeclared_sections_do_not_become_deliverables():
    """A model inventing a section name must not create a file."""
    text = _section("report", "GOOD") + _section("invented", "BAD")

    assert split_outputs(text, _outputs("report")) == {"report": "GOOD"}


def test_empty_sections_are_dropped():
    assert split_outputs(_section("report", "   "), _outputs("report")) == {}


def test_the_first_section_for_an_id_wins():
    text = _section("report", "FIRST") + _section("report", "SECOND")
    assert split_outputs(text, _outputs("report")) == {"report": "FIRST"}


def test_plain_prose_yields_nothing():
    assert split_outputs("Just a recommendation, no sentinels.", _outputs("a")) == {}


def test_indented_sentinels_still_parse():
    """innerText flattens indentation, but a pasted response may keep it."""
    text = f"   {BEGIN_SENTINEL}: report\nBODY\n   {END_SENTINEL}: report\n"
    assert split_outputs(text, _outputs("report")) == {"report": "BODY"}


# -- prompt instructions ----------------------------------------------------


def test_single_output_missions_get_no_extra_ceremony():
    assert render_output_instructions(_outputs("only")) == []


def test_multi_output_missions_are_told_the_exact_format():
    lines = "\n".join(render_output_instructions(_outputs("report", "plan")))

    assert f"{BEGIN_SENTINEL}: report" in lines
    assert f"{BEGIN_SENTINEL}: plan" in lines
    assert "no code" in lines  # fences are stripped by innerText


def test_synthesis_prompt_carries_the_deliverable_instructions(make_mission):
    from dataclasses import replace

    mission = replace(make_mission(), outputs=_outputs("report", "plan"))
    instance = MissionInstance(mission=mission, ledger=Ledger())

    prompt = render_synthesis_prompt(instance)

    assert "Required deliverables" in prompt
    assert f"{BEGIN_SENTINEL}: report" in prompt
    assert f"{BEGIN_SENTINEL}: plan" in prompt


def test_synthesis_prompt_stays_plain_for_a_single_output(make_mission):
    instance = MissionInstance(mission=make_mission(), ledger=Ledger())

    assert "Required deliverables" not in render_synthesis_prompt(instance)


# -- declared synthesiser ---------------------------------------------------


class _NamingTransport:
    """Records which participant was asked to synthesise."""

    def __init__(self):
        self.synthesis_participant = None
        self._seen_synthesis = False

    def deliver_prompt(self, participant, prompt):
        if "Final Synthesis" in prompt:
            self.synthesis_participant = participant.id
            self._seen_synthesis = True

    def collect_response(self, participant):
        return "SYNTHESIS" if self._seen_synthesis else "turn response"

    def ask_checkpoint(self, summary):
        return CheckpointDecision.CONTINUE


def _run(mission):
    instance = MissionInstance(mission=mission, ledger=Ledger())
    transport = _NamingTransport()
    MissionInterpreter(transport).run(instance)
    return instance, transport


def test_synthesiser_defaults_to_the_first_participant(make_mission):
    mission = make_mission(gov_max_rounds=1)
    _, transport = _run(mission)
    assert transport.synthesis_participant == mission.participants[0].id


def test_declared_synthesiser_is_used_instead_of_list_position(make_mission):
    from dataclasses import replace

    mission = make_mission(gov_max_rounds=1)
    mission = replace(
        mission,
        governance=replace(mission.governance, synthesiser="gemini"),
    )

    _, transport = _run(mission)

    assert transport.synthesis_participant == "gemini"
    assert mission.participants[0].id == "chatgpt"  # not the first in the list


def test_unknown_declared_synthesiser_falls_back_rather_than_losing_the_run(
    make_mission,
):
    from dataclasses import replace

    mission = make_mission(gov_max_rounds=1)
    mission = replace(
        mission, governance=replace(mission.governance, synthesiser="ghost")
    )

    instance, transport = _run(mission)

    assert transport.synthesis_participant == mission.participants[0].id
    assert instance.status is RuntimeStatus.COMPLETED


def test_loader_rejects_a_synthesiser_that_is_not_a_participant(tmp_path):
    import json

    from frelan.mission_loader import MissionValidationError, load_mission
    from tests.test_mission_loader import _valid_contract

    contract = _valid_contract()
    contract["governance"]["synthesiser"] = "ghost"
    path = tmp_path / "m.json"
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(MissionValidationError) as exc:
        load_mission(path)
    assert any("synthesiser" in e for e in exc.value.errors)


# -- writing the fan-out ----------------------------------------------------


def _completed(make_mission, outputs, recommendation):
    from dataclasses import replace

    mission = replace(make_mission(), outputs=outputs)
    instance = MissionInstance(mission=mission, ledger=Ledger())
    instance.status = RuntimeStatus.COMPLETED
    instance.context["final_recommendation"] = recommendation
    return instance


def test_every_declared_output_is_written(tmp_path, make_mission):
    outputs = _outputs("report", "plan", "risks")
    recommendation = (
        _section("report", "THE REPORT")
        + _section("plan", "THE PLAN")
        + _section("risks", "THE RISKS")
    )
    instance = _completed(make_mission, outputs, recommendation)

    entrypoint.write_outputs(instance, tmp_path, evidence_log=tmp_path / "ev.jsonl")

    assert "THE REPORT" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "THE PLAN" in (tmp_path / "plan.md").read_text(encoding="utf-8")
    assert "THE RISKS" in (tmp_path / "risks.md").read_text(encoding="utf-8")


def test_a_missing_section_is_reported_not_silently_skipped(
    tmp_path, make_mission, capsys
):
    outputs = _outputs("report", "plan")
    instance = _completed(make_mission, outputs, _section("report", "ONLY THIS"))

    entrypoint.write_outputs(instance, tmp_path, evidence_log=tmp_path / "ev.jsonl")

    assert (tmp_path / "report.md").is_file()
    assert not (tmp_path / "plan.md").exists()
    assert "was not produced" in capsys.readouterr().out


def test_single_output_mission_behaves_exactly_as_before(tmp_path, make_mission):
    instance = _completed(
        make_mission, _outputs("recommendation"), "A plain recommendation."
    )

    entrypoint.write_outputs(instance, tmp_path, evidence_log=tmp_path / "ev.jsonl")

    text = (tmp_path / "recommendation.md").read_text(encoding="utf-8")
    assert "A plain recommendation." in text


def test_multi_output_without_sections_falls_back_to_the_primary(
    tmp_path, make_mission, capsys
):
    outputs = _outputs("report", "plan")
    instance = _completed(make_mission, outputs, "Model ignored the format entirely.")

    entrypoint.write_outputs(instance, tmp_path, evidence_log=tmp_path / "ev.jsonl")

    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Model ignored the format entirely." in text
    assert "carried no deliverable sections" in capsys.readouterr().out


# -- per-output requests (one deliverable per synthesis turn) ----------------


def test_a_single_output_request_names_only_that_deliverable():
    outputs = _outputs("prd", "blueprint", "plan")
    lines = "\n".join(render_output_request(outputs[1], 1, 3, ("Prd",)))

    assert "Deliverable 2 of 3" in lines
    assert f"{BEGIN_SENTINEL}: blueprint" in lines
    assert f"{END_SENTINEL}: blueprint" in lines
    # The other deliverables are not requested this turn...
    assert "prd\n" not in lines and "BEGIN-OUTPUT: plan" not in lines
    # ...but what already exists is named, so the documents stay consistent.
    assert "Already written in this run: Prd" in lines
    # The filename is stated because the reply is written verbatim to it.
    assert "blueprint.md" in lines


def test_the_first_request_has_nothing_written_before_it():
    outputs = _outputs("prd", "blueprint")
    lines = "\n".join(render_output_request(outputs[0], 0, 2))

    assert "Deliverable 1 of 2" in lines
    assert "Already written" not in lines


def test_a_correctly_sentinelled_reply_is_left_alone():
    output = _outputs("prd")[0]
    reply = _section("prd", "THE PRD")

    text, wrapped = ensure_wrapped(reply, output)

    assert text == reply
    assert wrapped is False


def test_a_reply_missing_its_sentinels_is_wrapped_rather_than_lost():
    output = _outputs("prd")[0]

    text, wrapped = ensure_wrapped("# Product Requirements\n\nBody.", output)

    assert wrapped is True
    assert split_outputs(text, (output,)) == {
        "prd": "# Product Requirements\n\nBody."
    }


def test_a_reply_carrying_a_different_output_id_is_still_wrapped():
    """A section for some other deliverable does not make this one present."""
    output = _outputs("prd")[0]

    text, wrapped = ensure_wrapped(_section("blueprint", "WRONG"), output)

    assert wrapped is True
    assert split_outputs(text, (output,))["prd"].startswith(BEGIN_SENTINEL)
