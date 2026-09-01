"""Lightweight integration and fallback checks for the Playwright Automated transport."""

from __future__ import annotations

from frelan.enums import CheckpointDecision
from frelan.mission_contract import AssignedEngine, Participant
from frelan.transport.base import Transport
from frelan.transport.adapters import get_adapter
from frelan.transport.playwright_auto import (
    PlaywrightAutomatedTransport,
    _chip_needles,
    _effective_inline_limit,
    _is_web_lag,
    _needs_rollover,
    _render_overflow_stub,
    _safe_artifact_name,
    _split_upload_state,
)


class _ScriptedInput:
    """Returns queued lines; raises EOFError when exhausted (mimics Ctrl-Z/D)."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def __call__(self, _prompt: str = "") -> str:
        if not self._lines:
            raise EOFError
        return self._lines.pop(0)


def _participant() -> Participant:
    return Participant(
        id="chatgpt",
        display_name="ChatGPT",
        assigned_engine=AssignedEngine(
            role="proposer",
            required_capabilities=(),
            transport_provider="browser",
            execution_engine="chatgpt",
        ),
    )


def test_playwright_transport_satisfies_protocol():
    outputs: list[str] = []
    # Using an invalid port so it immediately fails and falls back
    transport = PlaywrightAutomatedTransport(
        cdp_url="http://localhost:9999",
        input_fn=_ScriptedInput([]),
        output_fn=outputs.append,
    )
    assert isinstance(transport, Transport)
    assert any("WARNING: Could not connect to Chrome" in line for line in outputs)


def test_playwright_transport_falls_back_to_manual_input_on_collect_response():
    outputs: list[str] = []
    # Test that when not connected, it falls back to BrowserTransport collect_response
    transport = PlaywrightAutomatedTransport(
        cdp_url="http://localhost:9999",
        input_fn=_ScriptedInput(["manually pasted", "END"]),
        output_fn=outputs.append,
    )

    response = transport.collect_response(_participant())
    assert response == "manually pasted"


def test_playwright_transport_falls_back_to_manual_input_on_deliver_prompt():
    outputs: list[str] = []
    transport = PlaywrightAutomatedTransport(
        cdp_url="http://localhost:9999",
        input_fn=_ScriptedInput([]),
        output_fn=outputs.append,
    )

    transport.deliver_prompt(_participant(), "some custom prompt")
    joined = "\n".join(outputs)
    assert "AUTOMATED PROMPT FOR" in joined
    assert "Could not locate active browser tab" in joined
    assert "some custom prompt" in joined


def test_playwright_transport_auto_checkpoint():
    outputs: list[str] = []
    transport = PlaywrightAutomatedTransport(
        cdp_url="http://localhost:9999",
        input_fn=_ScriptedInput([]),  # empty, would crash if manual input was requested
        output_fn=outputs.append,
        auto=True,
    )
    decision = transport.ask_checkpoint("some summary")
    assert decision is CheckpointDecision.CONTINUE
    joined = "\n".join(outputs)
    assert "Autonomous mode" in joined
    assert "Automatically choosing CONTINUE" in joined


def test_no_extract_js_uses_playwright_only_pseudo_selectors():
    # extract_js runs in the raw browser via page.evaluate, where ':has-text()'
    # (a Playwright pseudo) throws SyntaxError and aborts the whole extractor.
    for engine in ("chatgpt", "gemini", "claude"):
        assert ":has-text" not in get_adapter(engine).extract_js, engine


def test_get_adapter_maps_known_engines_and_rejects_unknown():
    assert get_adapter("chatgpt").engine_key == "chatgpt"
    assert get_adapter("gemini-advanced").engine_key == "gemini"
    assert get_adapter("claude").engine_key == "claude"
    assert get_adapter("unknown") is None


def test_split_upload_state_uploads_new_and_unloads_removed():
    desired = {"C:/refs/a.txt", "C:/refs/b.txt"}
    tracked = {"C:/refs/b.txt", "C:/refs/old.txt"}

    to_upload, stale = _split_upload_state(desired, tracked)

    assert to_upload == ["C:/refs/a.txt"]  # only the new file is uploaded
    assert stale == {"C:/refs/old.txt"}  # the removed file is unloaded


def test_split_upload_state_reupload_after_remove_and_readd():
    path = "C:/refs/a.txt"
    tracked = {path}

    # Removed via the P menu: file leaves the desired set -> becomes stale.
    to_upload, stale = _split_upload_state(set(), tracked)
    assert to_upload == []
    assert stale == {path}
    tracked -= stale

    # Re-added later: it must be uploaded again, not skipped as "already sent".
    to_upload, stale = _split_upload_state({path}, tracked)
    assert to_upload == [path]
    assert stale == set()


def test_chip_needles_truncates_long_names_for_chip_matching():
    needles = _chip_needles(
        ["C:/inputs/Screenshot 2026-06-25 122040.jpg", "C:/refs/a.txt"]
    )
    # Long stems are truncated to 12 chars (chips ellipsize long filenames).
    assert needles == ["screenshot 2", "a"]


def test_safe_artifact_name_prefers_link_text_filename():
    assert _safe_artifact_name("report.csv", "https://x/download?id=1") == "report.csv"


def test_safe_artifact_name_falls_back_to_url_path_for_generic_labels():
    name = _safe_artifact_name(
        "Download", "https://files.oaiusercontent.com/files/abc/plan%20v2.xlsx"
    )
    assert name == "plan v2.xlsx"


def test_safe_artifact_name_caps_length_for_data_uris():
    name = _safe_artifact_name("", "data:image/png;base64," + "A" * 5000)
    assert len(name) <= 90  # never produces an unwritable giant filename


def test_safe_artifact_name_sanitizes_and_defaults_extension():
    name = _safe_artifact_name("../..\\evil<script>", "blob:https://chatgpt.com/123")
    assert "/" not in name and "\\" not in name and "<" not in name
    assert "." in name  # always carries an extension


def test_is_web_lag_ignores_a_busy_page_past_threshold():
    # Claude thinking >35s: no text yet, but Stop button visible (busy) -> NOT lag.
    # Reloading here is the bug that re-fired the prompt three times.
    assert _is_web_lag(has_started=False, busy=True, elapsed=120, threshold_seconds=35) is False


def test_is_web_lag_reloads_a_quiet_stuck_page_past_threshold():
    # Genuine lag: no text AND not generating past the threshold -> reload.
    assert _is_web_lag(has_started=False, busy=False, elapsed=40, threshold_seconds=35) is True


def test_is_web_lag_holds_within_threshold_and_once_text_appears():
    assert _is_web_lag(has_started=False, busy=False, elapsed=10, threshold_seconds=35) is False
    assert _is_web_lag(has_started=True, busy=False, elapsed=99, threshold_seconds=35) is False


class _FakeAdapter:
    """Minimal adapter double for spill/rollover tests (no real browser)."""

    engine_key = "chatgpt"
    max_inline_chars = 100
    chat_budget_chars = None
    new_chat_url = "https://example.com/new"
    limit_error_js = "() => false"
    file_input_selectors: list[str] = []
    attach_menu: dict[str, list[str]] = {"open": [], "item": []}

    def __init__(self) -> None:
        self.delivered: list[str] = []

    def deliver_prompt(self, page, prompt, output_fn, wait_until_enabled) -> None:
        self.delivered.append(prompt)


class _FakePage:
    def __init__(self) -> None:
        self.gotos: list[str] = []

    def goto(self, url: str) -> None:
        self.gotos.append(url)

    def wait_for_load_state(self, state: str) -> None:
        pass

    def wait_for_timeout(self, ms: int) -> None:
        pass


def _offline_transport(outputs: list[str]) -> PlaywrightAutomatedTransport:
    return PlaywrightAutomatedTransport(
        cdp_url="http://localhost:9999",  # invalid port: no browser needed
        input_fn=_ScriptedInput([]),
        output_fn=outputs.append,
    )


def test_effective_inline_limit_static_without_budget():
    assert _effective_inline_limit(9_000, cumulative=1_000_000, budget=None) == 9_000


def test_effective_inline_limit_shrinks_with_budget_and_floors():
    # Half the budget consumed -> half the cap.
    assert _effective_inline_limit(12_000, cumulative=150_000, budget=300_000) == 6_000
    # Budget exhausted -> never below the floor (a stub always fits).
    assert _effective_inline_limit(12_000, cumulative=300_000, budget=300_000) == 3_000


def test_needs_rollover_only_on_budgeted_platforms():
    assert _needs_rollover(cumulative=999_999, next_size=999_999, budget=None) is False
    assert _needs_rollover(cumulative=100_000, next_size=5_000, budget=300_000) is False
    # 10% margin: rolls over before the budget is fully spent.
    assert _needs_rollover(cumulative=265_000, next_size=6_000, budget=300_000) is True


def test_render_overflow_stub_names_file_and_participant():
    stub = _render_overflow_stub("prompt_overflow_claude_1.md", "Claude")
    assert "prompt_overflow_claude_1.md" in stub
    assert "Claude" in stub
    assert len(stub) < 3_000  # the stub itself always fits inline


def test_deliver_with_spill_inline_when_under_limit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    transport = _offline_transport([])
    adapter = _FakeAdapter()

    delivered = transport._deliver_with_spill(None, _participant(), adapter, "short", limit=100)

    assert delivered == "short"
    assert adapter.delivered == ["short"]
    assert not (tmp_path / "outputs").exists()  # no overflow file for inline sends


def test_deliver_with_spill_writes_overflow_file_and_sends_stub(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    transport = _offline_transport([])
    adapter = _FakeAdapter()
    transport._attach_one_shot = lambda page, a, path: True  # upload verified

    prompt = "X" * 500
    delivered = transport._deliver_with_spill(None, _participant(), adapter, prompt, limit=100)

    files = list((tmp_path / "outputs").glob("prompt_overflow_chatgpt_*.md"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == prompt  # full fidelity travels as .md
    assert delivered != prompt and files[0].name in delivered  # stub was typed instead
    assert adapter.delivered == [delivered]
    # Never registered as a reference file — injected_files gets inlined into
    # every future prompt, which would compound prompt sizes round over round.
    assert transport.injected_files is None


def test_deliver_with_spill_falls_back_inline_when_attach_unverified(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    transport = _offline_transport([])
    adapter = _FakeAdapter()
    transport._attach_one_shot = lambda page, a, path: False  # chips never appeared

    # Over the requested limit but under the adapter's composer cap: the
    # full prompt still goes inline — a possibly-rejected send beats a
    # silently dropped turn.
    prompt = "Y" * 80
    delivered = transport._deliver_with_spill(None, _participant(), adapter, prompt, limit=50)

    assert delivered == prompt
    assert adapter.delivered == [prompt]


def test_deliver_with_spill_degrades_inline_when_attach_fails_on_huge_prompt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    transport = _offline_transport([])
    adapter = _FakeAdapter()
    adapter.max_inline_chars = 2_000  # instance override for realistic head+tail math
    transport._attach_one_shot = lambda page, a, path: False

    # Way over the composer cap (the 134k-char incident, scaled down): typing
    # it all inline wedges the run, so the middle is dropped instead.
    prompt = "H" * 1_500 + "M" * 1_500 + "T" * 1_500
    delivered = transport._deliver_with_spill(None, _participant(), adapter, prompt, limit=0)

    assert delivered != prompt
    assert len(delivered) < len(prompt)
    assert "CONTEXT OMITTED" in delivered
    assert delivered.startswith("HHHH")  # mission header survives
    assert delivered.endswith("TTTT")  # 'Your turn' directives survive
    # The full prompt is still preserved on disk for the ledger/debugging.
    assert list((tmp_path / "outputs").glob("prompt_overflow_chatgpt_*.md"))


def test_ensure_sent_retries_enter_until_composer_empties():
    # A force-click on a disabled send button sends nothing (live 2026-07-12):
    # the text stays in the composer. _ensure_sent must keep re-submitting
    # until the composer empties, then stop.
    transport = _offline_transport([])
    adapter = _FakeAdapter()
    stub = _render_overflow_stub("prompt_overflow_chatgpt_1.md", "ChatGPT")

    class _Keyboard:
        def __init__(self) -> None:
            self.pressed: list[str] = []

        def press(self, key: str) -> None:
            self.pressed.append(key)

    class _StuckPage(_FakePage):
        """Composer keeps the text until two retry Enters have landed."""

        def __init__(self) -> None:
            super().__init__()
            self.keyboard = _Keyboard()

        def evaluate(self, js: str, arg=None):
            if js == adapter.limit_error_js:
                return False
            return "" if len(self.keyboard.pressed) >= 2 else stub

    page = _StuckPage()
    transport._ensure_sent(page, adapter, stub)
    assert page.keyboard.pressed == ["Enter", "Enter"]


def test_ensure_sent_returns_immediately_when_composer_empty():
    transport = _offline_transport([])
    adapter = _FakeAdapter()

    class _Keyboard:
        def __init__(self) -> None:
            self.pressed: list[str] = []

        def press(self, key: str) -> None:
            self.pressed.append(key)

    class _SentPage(_FakePage):
        def __init__(self) -> None:
            super().__init__()
            self.keyboard = _Keyboard()

        def evaluate(self, js: str, arg=None):
            return ""  # composer already empty -> message went out

    page = _SentPage()
    transport._ensure_sent(page, adapter, "some delivered prompt")
    assert page.keyboard.pressed == []


def test_rollover_resets_only_that_participants_state():
    transport = _offline_transport([])
    adapter = _FakeAdapter()
    page = _FakePage()
    transport._uploaded_attachments = {"chatgpt": {"C:/a.txt"}, "gemini": {"C:/b.txt"}}
    transport._last_responses = {"chatgpt": "old reply", "gemini": "keep"}
    transport._conversation_chars = {"chatgpt": 250_000, "gemini": 42}

    transport._rollover_conversation(page, _participant(), adapter)

    assert page.gotos == ["https://example.com/new"]
    assert "chatgpt" not in transport._uploaded_attachments  # re-uploads on next sync
    assert "chatgpt" not in transport._last_responses  # stale baseline cleared
    assert transport._conversation_chars["chatgpt"] == 0
    # Other participants untouched.
    assert transport._uploaded_attachments["gemini"] == {"C:/b.txt"}
    assert transport._last_responses["gemini"] == "keep"
    assert transport._conversation_chars["gemini"] == 42


def test_refresh_policy_overrides_merge_over_defaults():
    transport = PlaywrightAutomatedTransport(
        cdp_url="http://localhost:9999",
        input_fn=_ScriptedInput([]),
        output_fn=lambda _line: None,
        refresh_policy={"stalled_refresh_seconds": 45},
    )
    # Overridden field wins; unspecified fields keep engine-agnostic defaults.
    assert transport._refresh_policy["stalled_refresh_seconds"] == 45
    assert transport._refresh_policy["max_refreshes_per_turn"] == 2
    assert transport._refresh_policy["lag_seconds"] == 35
    assert transport._refresh_policy["max_redeliveries_per_turn"] == 2


def test_limits_for_prefers_mission_metadata_overrides():
    transport = PlaywrightAutomatedTransport(
        cdp_url="http://localhost:9999",
        input_fn=_ScriptedInput([]),
        output_fn=lambda _line: None,
        limit_overrides={"chatgpt": {"max_inline_chars": 5_000}},
    )
    adapter = _FakeAdapter()

    assert transport._limits_for(adapter) == (5_000, None)  # override wins, default kept


def test_every_adapter_declares_blocker_detection():
    # Quota walls, login walls, and verification challenges are human-only
    # blockers: detected and surfaced, never auto-solved.
    for engine in ("chatgpt", "gemini", "claude"):
        js = get_adapter(engine).blocker_js
        assert "reached your limit" in js, engine
        assert "verify you are human" in js, engine
        assert ":has-text" not in js, engine  # valid CSS only in evaluate


def test_chatgpt_extractor_resumes_paused_generations():
    # Long outputs pause at the token cap with a "Continue generating" button;
    # without clicking it only half the response would be harvested.
    js = get_adapter("chatgpt").extract_js
    assert "Continue generating" in js
    assert "continueClicked" in js


def test_every_adapter_reports_message_count():
    # msgCount is the strong "new reply exists" signal: after a lag
    # re-delivery the text baseline can equal the finished response (observed
    # live 2026-07-11 on ChatGPT), but the message count still increments.
    for engine in ("chatgpt", "gemini", "claude"):
        assert "msgCount" in get_adapter(engine).extract_js, engine


def test_gemini_busy_probe_excludes_aria_busy():
    # Gemini keeps aria-busy="true" on the completed markdown panel FOREVER
    # (observed live 2026-07-11). Probing it held busy=true after every reply
    # and burned the full artifact grace (~3 min) on every Gemini turn.
    assert '[aria-busy="true"]' not in get_adapter("gemini").extract_js
    # Real busy signals must survive the fix.
    assert "stopBtn" in get_adapter("gemini").extract_js
    assert "imgsLoading" in get_adapter("gemini").extract_js


def test_every_adapter_declares_composer_limits():
    for engine in ("chatgpt", "gemini", "claude"):
        adapter = get_adapter(engine)
        assert adapter.max_inline_chars > 0, engine
        assert adapter.new_chat_url.startswith("https://"), engine
        assert adapter.chat_budget_chars is None or adapter.chat_budget_chars > adapter.max_inline_chars, engine
        # Same page.evaluate constraint as extract_js: valid CSS only.
        assert ":has-text" not in adapter.limit_error_js, engine
    # Claude is the only platform whose whole conversation is resent per turn.
    assert get_adapter("claude").chat_budget_chars is not None
    assert get_adapter("chatgpt").chat_budget_chars is None


def test_playwright_transport_supports_f_to_enable_auto():
    outputs: list[str] = []
    transport = PlaywrightAutomatedTransport(
        cdp_url="http://localhost:9999",
        input_fn=_ScriptedInput(["F"]),  # User inputs F
        output_fn=outputs.append,
        auto=False,
    )

    # 1. First checkpoint - user inputs F
    decision = transport.ask_checkpoint("checkpoint summary 1")
    assert decision is CheckpointDecision.CONTINUE
    assert transport.auto is True

    # 2. Second checkpoint - automatically resolved to CONTINUE
    decision2 = transport.ask_checkpoint("checkpoint summary 2")
    assert decision2 is CheckpointDecision.CONTINUE

    joined = "\n".join(outputs)
    assert "Switching to fully automated mode" in joined
    assert "Automatically choosing CONTINUE" in joined
