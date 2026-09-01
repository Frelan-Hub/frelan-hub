"""Lightweight integration checks for the Browser transport (transport/browser.py).

Per the agreed test strategy the interactive transport gets light checks only:
that multi-line paste assembles correctly, the checkpoint menu maps keys and
re-prompts on bad input, and clipboard failure degrades gracefully. All I/O is
scripted, so nothing here touches a real terminal or clipboard.
"""

from __future__ import annotations

from frelan.enums import CheckpointDecision
from frelan.mission_contract import AssignedEngine, Participant
from frelan.transport.base import Transport
from frelan.transport.browser import (
    BrowserTransport,
    looks_like_local_path,
    split_path_list,
)


def test_split_path_list_comma_separated_bare_paths():
    assert split_path_list("inputs/a.sql, inputs/notes.txt") == [
        "inputs/a.sql",
        "inputs/notes.txt",
    ]


def test_split_path_list_quoted_paths_separated_by_spaces():
    # Exactly the input shape that previously collapsed into one broken entry.
    raw = (
        '"C:\\inputs\\Screenshot 2026-06-25 123410.jpg" '
        '"C:\\inputs\\Screenshot 2026-06-25 122040.jpg"'
    )
    assert split_path_list(raw) == [
        "C:\\inputs\\Screenshot 2026-06-25 123410.jpg",
        "C:\\inputs\\Screenshot 2026-06-25 122040.jpg",
    ]


def test_split_path_list_mixed_quoted_and_bare():
    assert split_path_list('"C:\\refs\\plan v2.jpg", inputs/site.png') == [
        "C:\\refs\\plan v2.jpg",
        "inputs/site.png",
    ]


def test_split_path_list_single_unquoted_path_with_spaces():
    raw = "C:\\inputs\\Screenshot 2026-06-25 123410.jpg"
    assert split_path_list(raw) == [raw]


def test_looks_like_local_path_classification():
    assert looks_like_local_path("C:\\inputs\\missing.jpg") is True
    assert looks_like_local_path("inputs/facade.jpg") is True
    assert looks_like_local_path("photo.png") is True
    assert looks_like_local_path("a modern facade with glass/steel") is False
    assert looks_like_local_path("https://example.com/x.jpg") is False


def test_deliver_prompt_warns_when_paste_exceeds_safe_threshold():
    outputs: list[str] = []
    transport = BrowserTransport(
        output_fn=outputs.append, clipboard_copy=lambda _t: True
    )
    transport.deliver_prompt(_participant(), "X" * 9_500)  # over ChatGPT's 9k line
    assert any("[ADVISORY]" in line for line in outputs)


def test_deliver_prompt_stays_quiet_for_small_prompts():
    outputs: list[str] = []
    transport = BrowserTransport(
        output_fn=outputs.append, clipboard_copy=lambda _t: True
    )
    transport.deliver_prompt(_participant(), "short prompt")
    assert not any("[ADVISORY]" in line for line in outputs)


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


def _transport(lines: list[str], *, clipboard_ok: bool = True):
    outputs: list[str] = []
    transport = BrowserTransport(
        input_fn=_ScriptedInput(lines),
        output_fn=outputs.append,
        clipboard_copy=lambda _t: clipboard_ok,
    )
    return transport, outputs


def test_browser_transport_satisfies_protocol():
    transport, _ = _transport([])
    assert isinstance(transport, Transport)


def test_collect_response_assembles_multiline_until_sentinel():
    transport, _ = _transport(["line one", "line two", "END", "ignored"])
    assert transport.collect_response(_participant()) == "line one\nline two"


def test_collect_response_handles_eof():
    transport, _ = _transport(["only line"])  # no sentinel, input runs out
    assert transport.collect_response(_participant()) == "only line"


def test_ask_checkpoint_maps_key_and_reprompts_on_bad_input():
    transport, outputs = _transport(["x", "V"])  # invalid, then Converged
    assert transport.ask_checkpoint("summary") is CheckpointDecision.CONVERGED
    assert any("one of C, V, E, T" in line for line in outputs)


def test_deliver_prompt_notes_clipboard_failure():
    transport, outputs = _transport([], clipboard_ok=False)
    transport.deliver_prompt(_participant(), "the prompt body")
    joined = "\n".join(outputs)
    assert "the prompt body" in joined
    assert "clipboard unavailable" in joined


def test_ask_checkpoint_auto_returns_continue():
    outputs = []
    transport = BrowserTransport(
        input_fn=_ScriptedInput([]),  # No inputs provided, would crash if it tried to read input
        output_fn=outputs.append,
        auto=True,
    )
    decision = transport.ask_checkpoint("some summary")
    assert decision is CheckpointDecision.CONTINUE
    joined = "\n".join(outputs)
    assert "Autonomous mode" in joined
    assert "Automatically choosing CONTINUE" in joined


def test_ask_checkpoint_supports_f_to_enable_auto():
    outputs = []
    transport = BrowserTransport(
        input_fn=_ScriptedInput(["F"]),  # User selects [F]
        output_fn=outputs.append,
        auto=False,  # starts as interactive/manual
    )

    # 1. First checkpoint - user selects F
    decision = transport.ask_checkpoint("checkpoint summary 1")
    assert decision is CheckpointDecision.CONTINUE
    assert transport.auto is True  # auto mode should now be enabled dynamically!

    # 2. Second checkpoint - should automatically continue WITHOUT requesting input
    # If it tries to read input, it will raise EOFError because _ScriptedInput is empty.
    decision2 = transport.ask_checkpoint("checkpoint summary 2")
    assert decision2 is CheckpointDecision.CONTINUE

    joined = "\n".join(outputs)
    assert "Switching to fully automated mode" in joined
    assert "Automatically choosing CONTINUE" in joined


def test_browser_transport_initializes_with_injected_files_and_images():
    in_files = {"notes.txt": "Some note content."}
    in_images = ["diagram.png"]
    transport = BrowserTransport(
        injected_files=in_files,
        injected_images=in_images,
    )
    assert transport.injected_files == in_files
    assert transport.injected_images == in_images


def test_browser_transport_ask_checkpoint_p_options():
    # Test sub-choices of option P:
    # Option 1: Set main topic
    transport, _ = _transport(["P", "1", "New Topic", "6", "C"])
    assert transport.ask_checkpoint("summary") is CheckpointDecision.CONTINUE
    assert transport.topic_override == "New Topic"

    # Option 2: Set custom instructions
    transport, _ = _transport(["P", "2", "New Instruction", "6", "C"])
    assert transport.ask_checkpoint("summary") is CheckpointDecision.CONTINUE
    assert transport.prompt_inject == "New Instruction"

    # Option 5: Clear all
    transport, _ = _transport(["P", "5", "6", "C"])
    transport.topic_override = "Some Topic"
    transport.prompt_inject = "Some Instruction"
    transport.injected_files = {"a.txt": "a"}
    transport.injected_images = ["img"]
    assert transport.ask_checkpoint("summary") is CheckpointDecision.CONTINUE
    assert transport.topic_override is None
    assert transport.prompt_inject is None
    assert transport.injected_files is None
    assert transport.injected_images is None


def test_browser_transport_ask_checkpoint_p_file_and_image_management():
    # Option 3 A: Add file
    # We will inject "requirements.txt" which exists in the workspace root
    transport, _ = _transport(["P", "3", "A", "requirements.txt", "6", "C"])
    assert transport.ask_checkpoint("summary") is CheckpointDecision.CONTINUE
    assert transport.injected_files is not None
    assert "requirements.txt" in transport.injected_files
    assert len(transport.injected_files["requirements.txt"]) > 0

    # Option 3 D: Delete file
    transport, _ = _transport(["P", "3", "D", "requirements.txt", "6", "C"])
    transport.injected_files = {"requirements.txt": "some content"}
    assert transport.ask_checkpoint("summary") is CheckpointDecision.CONTINUE
    assert transport.injected_files is None

    # Option 4 A: Add image — free-text descriptions are accepted
    transport, _ = _transport(["P", "4", "A", "a modern glass facade at dusk", "6", "C"])
    assert transport.ask_checkpoint("summary") is CheckpointDecision.CONTINUE
    assert transport.injected_images == ["a modern glass facade at dusk"]

    # Option 4 A: Add image — a missing path-like file is warned about and
    # skipped, never silently registered as a text "description".
    transport, outputs = _transport(["P", "4", "A", "facade_rendering.jpg", "6", "C"])
    assert transport.ask_checkpoint("summary") is CheckpointDecision.CONTINUE
    assert transport.injected_images is None
    assert any("Image file not found" in line for line in outputs)

    # Option 4 D: Delete image
    transport, _ = _transport(["P", "4", "D", "1", "6", "C"])
    transport.injected_images = ["facade_rendering.jpg"]
    assert transport.ask_checkpoint("summary") is CheckpointDecision.CONTINUE
    assert transport.injected_images is None
