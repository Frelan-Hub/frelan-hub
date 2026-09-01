"""Overflow delivery must fail safe, never bank a failed delivery as a turn.

Evidence: the 2026-08-19 general_inquiry run. Three ChatGPT turns were recorded
as responses that only said the attached prompt_overflow_*.md was unreadable —
including the final synthesis, which wrote that refusal into recommendation.md.
Two defects made that possible and both are covered here:

1. chip verification matched a *stale* chip, because every overflow filename
   shares the truncated head "prompt_overf";
2. a refusal-to-read was returned as the turn's response.
"""

from __future__ import annotations

import pytest

from frelan.mission_contract import AssignedEngine, Participant
from frelan.transport.playwright_auto import (
    PlaywrightAutomatedTransport,
    _chip_needles,
    _looks_like_attachment_refusal,
)

REFUSALS = [
    "I still don't have access to the contents of prompt_overflow_chatgpt_1787121788.md "
    "in the conversation context, so I can't truthfully say I've read it completely.",
    "I can do that, but the file prompt_overflow_chatgpt_1787121839.md is not actually "
    "available to me in the accessible attachments/context for this turn. Please attach it.",
    "The attachment prompt_overflow_gemini_1787121757.md is not accessible to me; "
    "please re-attach the file or paste its contents.",
]


@pytest.mark.parametrize("text", REFUSALS)
def test_real_refusals_are_recognised(text: str) -> None:
    filename = next(w.strip(".,;") for w in text.split() if w.startswith("prompt_overflow"))
    assert _looks_like_attachment_refusal(text, filename) is True


def test_real_answer_is_not_mistaken_for_a_refusal() -> None:
    answer = (
        "Interpretation: I read the mission as asking where New Zealand stands on AI "
        "adoption. Core finding: baseline consumer adoption is near saturation while "
        "operational integration remains under 15%. " + ("Evidence follows. " * 200)
    )
    assert _looks_like_attachment_refusal(answer, "prompt_overflow_chatgpt_1.md") is False


def test_refusal_must_name_the_file() -> None:
    # Inability alone is not a delivery failure — a model may simply not know.
    assert _looks_like_attachment_refusal(
        "I don't have access to real-time data on that topic.",
        "prompt_overflow_chatgpt_1.md",
    ) is False


def test_long_response_mentioning_the_file_is_still_a_response() -> None:
    body = "prompt_overflow_chatgpt_1.md cannot read " + ("substantive analysis " * 300)
    assert _looks_like_attachment_refusal(body, "prompt_overflow_chatgpt_1.md") is False


def test_overflow_chip_needles_collide_across_turns() -> None:
    """Documents WHY baseline-diff verification is required."""
    a = _chip_needles(["/x/prompt_overflow_chatgpt_1787121788.md"])
    b = _chip_needles(["/x/prompt_overflow_chatgpt_1787121839.md"])
    assert a == b, "needles are indistinguishable, so presence alone proves nothing"


class _FakePage:
    """Composer whose text is scripted per evaluate() call."""

    def __init__(self, texts: list[str]) -> None:
        self._texts = texts

    def evaluate(self, _js, *args):
        return self._texts.pop(0) if len(self._texts) > 1 else self._texts[0]

    def wait_for_timeout(self, _ms):  # pragma: no cover - timing only
        return None


def _transport() -> PlaywrightAutomatedTransport:
    return PlaywrightAutomatedTransport.__new__(PlaywrightAutomatedTransport)


def test_stale_chip_alone_does_not_verify_an_upload() -> None:
    t = _transport()
    t._output = lambda *_a, **_k: None
    stale = "prompt_overflow_chatgpt_1787121788.md"
    page = _FakePage([stale, stale, stale])

    # Baseline already contains a matching chip and nothing new arrives.
    assert (
        t._attachments_visible(
            page, ["/x/prompt_overflow_chatgpt_1787121839.md"], wait_seconds=0, baseline=stale
        )
        is not True
    )


def test_a_newly_added_chip_verifies() -> None:
    t = _transport()
    t._output = lambda *_a, **_k: None
    stale = "prompt_overflow_chatgpt_1787121788.md"
    page = _FakePage([stale + " prompt_overflow_chatgpt_1787121839.md"])

    assert (
        t._attachments_visible(
            page, ["/x/prompt_overflow_chatgpt_1787121839.md"], wait_seconds=1, baseline=stale
        )
        is True
    )


class _FakeAdapter:
    engine_key = "chatgpt"
    max_inline_chars = 9_000
    chat_budget_chars = None
    extract_js = "() => ({})"

    def __init__(self) -> None:
        self.delivered: list[str] = []

    def deliver_prompt(self, _page, prompt, _out, _wait):
        self.delivered.append(prompt)


def _participant() -> Participant:
    return Participant(
        id="chatgpt",
        display_name="ChatGPT",
        assigned_engine=AssignedEngine(
            role="peer_analyst",
            required_capabilities=(),
            transport_provider="browser",
            execution_engine="chatgpt",
        ),
    )


def _retry_transport(adapter: _FakeAdapter) -> PlaywrightAutomatedTransport:
    t = _transport()
    t._output = lambda *_a, **_k: None
    t._pending_spill = {}
    t._spill_redeliveries = {}
    t._last_responses = {}
    t._baseline_msg_counts = {}
    t._limit_overrides = {}
    t._clear_composer = lambda _page: None
    t._ensure_sent = lambda *_a, **_k: None
    return t


def test_refusal_triggers_one_truncated_redelivery() -> None:
    adapter = _FakeAdapter()
    t = _retry_transport(adapter)
    participant = _participant()
    full = "FULL PROMPT " * 2_000  # 24k chars, over the 9k cap
    t._pending_spill[participant.id] = (full, "prompt_overflow_chatgpt_1.md")
    t.collect_response = lambda _p: "the real answer"

    result = t._retry_failed_spill(
        _FakePage(["{}"]),
        participant,
        adapter,
        "I don't have access to prompt_overflow_chatgpt_1.md; please attach it.",
    )

    assert result == "the real answer"
    assert len(adapter.delivered) == 1
    assert len(adapter.delivered[0]) <= adapter.max_inline_chars
    assert t._spill_redeliveries[participant.id] == 1


def test_second_refusal_is_not_retried_again() -> None:
    adapter = _FakeAdapter()
    t = _retry_transport(adapter)
    participant = _participant()
    t._pending_spill[participant.id] = ("x" * 20_000, "prompt_overflow_chatgpt_1.md")
    t._spill_redeliveries[participant.id] = 1

    result = t._retry_failed_spill(
        _FakePage(["{}"]),
        participant,
        adapter,
        "Still cannot read prompt_overflow_chatgpt_1.md.",
    )

    assert result is None  # the turn stands; no infinite argument with the model
    assert adapter.delivered == []


def test_a_real_answer_after_a_spill_is_left_alone() -> None:
    adapter = _FakeAdapter()
    t = _retry_transport(adapter)
    participant = _participant()
    t._pending_spill[participant.id] = ("x" * 20_000, "prompt_overflow_chatgpt_1.md")

    assert t._retry_failed_spill(
        _FakePage(["{}"]), participant, adapter, "Interpretation: here is my analysis..."
    ) is None
    assert adapter.delivered == []
