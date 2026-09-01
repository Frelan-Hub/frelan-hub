"""Focused tests for evidence collection (evidence.py).

The parser is the trust boundary for model-formatted score blocks, so it gets
the tolerance cases (clamping, malformed values, missing targets). The
collector test pins the governance rules: self-scores carry no evidence, and
objective metrics come straight from the ledger.
"""

from __future__ import annotations

from frelan.enums import LedgerEntryType
from frelan.evidence import collect_evidence, parse_score_blocks
from frelan.ledger import Ledger
from frelan.mission_instance import MissionInstance


def _score_block(target: str, value: int = 4) -> str:
    return (
        "```frelan-scores\n"
        f"target: {target}\n"
        f"evidence_quality: {value}\n"
        "justification: solid work\n"
        "```"
    )


def test_parse_score_blocks_extracts_clamps_and_skips_malformed():
    text = (
        "Some prose first.\n"
        "```frelan-scores\n"
        "target: gemini\n"
        "evidence_quality: 7\n"
        "reasoning_depth: abc\n"
        "actionability: 3\n"
        "justification: good sourcing\n"
        "```\n"
    )
    scores = parse_score_blocks(text)
    assert len(scores) == 1
    assert scores[0]["target"] == "gemini"
    assert scores[0]["evidence_quality"] == 5  # clamped from 7
    assert "reasoning_depth" not in scores[0]  # malformed value skipped
    assert scores[0]["actionability"] == 3


def test_parse_score_blocks_accepts_fenceless_dom_text():
    # innerText extraction strips markdown fences: a real completed run parsed
    # ZERO scores while the blocks were plainly visible on screen. Bare
    # 'target:' key-value runs must parse too, without double-counting fenced.
    dom_text = (
        "Contribution Scoring\n"
        "Code snippet\n"          # UI label Gemini renders above code blocks
        "target: chatgpt\n"
        "evidence_quality: 5\n"
        "reasoning_depth: 5\n"
        "justification: strong normalization push\n"
        "Some trailing prose.\n"
        "target: claude\n"
        "actionability: 3\n"
    )
    scores = parse_score_blocks(dom_text)
    assert [s["target"] for s in scores] == ["chatgpt", "claude"]
    assert scores[0]["evidence_quality"] == 5
    assert scores[1]["actionability"] == 3
    # Fenced text still parses exactly once (no fenced+bare duplicate).
    assert len(parse_score_blocks(_score_block("gemini", 4))) == 1


def test_parse_score_blocks_drops_untargeted_or_scoreless_blocks():
    no_target = "```frelan-scores\nevidence_quality: 4\n```"
    no_scores = "```frelan-scores\ntarget: gemini\njustification: nice\n```"
    assert parse_score_blocks(no_target) == []
    assert parse_score_blocks(no_scores) == []
    assert parse_score_blocks("no blocks at all") == []


def test_collect_evidence_reciprocal_scores_and_metrics(make_mission):
    instance = MissionInstance(mission=make_mission(), ledger=Ledger())
    instance.ledger.append(
        LedgerEntryType.RESPONSE,
        "Findings with a source https://example.com and marker [1].\n"
        + _score_block("gemini", 4)
        + "\n"
        + _score_block("chatgpt", 5),  # self-score -> must be discarded
        participant_id="chatgpt",
        role="peer_analyst",
    )
    instance.ledger.append(
        LedgerEntryType.RESPONSE,
        "Counterpoints.\n" + _score_block("chatgpt", 3),
        participant_id="gemini",
        role="peer_analyst",
    )

    evidence = collect_evidence(instance)

    chatgpt = evidence["participants"]["chatgpt"]
    gemini = evidence["participants"]["gemini"]
    # Reciprocal scores survived; the self-score did not.
    assert gemini["scores_received"]["by_scorer"]["chatgpt"]["evidence_quality"] == 4
    assert chatgpt["scores_received"]["by_scorer"]["gemini"]["evidence_quality"] == 3
    assert "chatgpt" not in chatgpt["scores_received"]["by_scorer"]
    assert gemini["scores_received"]["means"]["evidence_quality"] == 4
    # Objective metrics are deterministic ledger facts.
    assert chatgpt["metrics"]["turns"] == 1
    assert chatgpt["metrics"]["citations"] == 2  # one URL + one [1] marker
    assert chatgpt["engine"] == "chatgpt"
    assert evidence["mission_id"] == "m1"


def test_turn_latency_is_reported_only_from_measured_turns(make_mission):
    """Unmeasured turns must not be averaged in as zero seconds.

    Latency cannot be recovered from ledger timestamps — a turn's PROMPT and
    RESPONSE are both appended after the response arrives, microseconds apart —
    so it is captured explicitly and may simply be absent on older records.
    """
    instance = MissionInstance(mission=make_mission(), ledger=Ledger())
    instance.ledger.append(
        LedgerEntryType.RESPONSE, "a", participant_id="chatgpt", duration_seconds=10.0
    )
    instance.ledger.append(
        LedgerEntryType.RESPONSE, "b", participant_id="chatgpt", duration_seconds=20.0
    )
    instance.ledger.append(  # unmeasured (e.g. restored from an older record)
        LedgerEntryType.RESPONSE, "c", participant_id="chatgpt"
    )

    metrics = collect_evidence(instance)["participants"]["chatgpt"]["metrics"]

    assert metrics["turns"] == 3
    assert metrics["timed_turns"] == 2
    assert metrics["mean_turn_seconds"] == 15.0
    assert metrics["total_turn_seconds"] == 30.0


def test_latency_is_absent_rather_than_zero_when_nothing_was_measured(make_mission):
    instance = MissionInstance(mission=make_mission(), ledger=Ledger())
    instance.ledger.append(LedgerEntryType.RESPONSE, "a", participant_id="chatgpt")

    metrics = collect_evidence(instance)["participants"]["chatgpt"]["metrics"]

    assert metrics["timed_turns"] == 0
    assert metrics["mean_turn_seconds"] is None
    assert metrics["total_turn_seconds"] is None


def test_interpreter_records_how_long_each_turn_took(make_mission, monkeypatch):
    """The interpreter must measure around the transport call itself."""
    from frelan.enums import CheckpointDecision, RuntimeStatus
    from frelan.mission_interpreter import MissionInterpreter

    clock = iter([100.0, 107.5, 200.0, 203.25])
    monkeypatch.setattr("frelan.mission_interpreter.time.monotonic", lambda: next(clock))

    class SlowTransport:
        def deliver_prompt(self, participant, prompt):
            pass

        def collect_response(self, participant):
            return "answered"

        def ask_checkpoint(self, summary):
            return CheckpointDecision.CONTINUE

    instance = MissionInstance(mission=make_mission(gov_max_rounds=1), ledger=Ledger())
    MissionInterpreter(SlowTransport()).run(instance)

    durations = [
        e.duration_seconds
        for e in instance.ledger.entries
        if e.entry_type is LedgerEntryType.RESPONSE and e.role != "synthesiser"
    ]
    assert durations == [7.5, 3.25]
    assert instance.status is RuntimeStatus.COMPLETED
