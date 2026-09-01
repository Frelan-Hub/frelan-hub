from __future__ import annotations

import queue
import threading
from typing import TYPE_CHECKING

from frelan.enums import CheckpointDecision
from frelan.transport.playwright_auto import PlaywrightAutomatedTransport

if TYPE_CHECKING:
    from frelan.mission_contract import Participant


class StreamlitUIBridge:
    """Thread-safe state and queue container for communication between
    the Streamlit UI and the background Conductor thread.
    """
    def __init__(self) -> None:
        self.logs_queue: queue.Queue[str] = queue.Queue()
        self.input_queue: queue.Queue[str] = queue.Queue()
        self.checkpoint_decision_queue: queue.Queue[CheckpointDecision | str] = queue.Queue()

        self.checkpoint_summary: str | None = None
        self.checkpoint_active = False
        self._lock = threading.Lock()

    def push_log(self, text: str) -> None:
        self.logs_queue.put(text)

    def get_input(self) -> str:
        # Blocks until the Streamlit UI thread pushes input
        return self.input_queue.get()

    def set_checkpoint_active(self, summary: str) -> None:
        with self._lock:
            self.checkpoint_summary = summary
            self.checkpoint_active = True

    def clear_checkpoint_active(self) -> None:
        with self._lock:
            self.checkpoint_summary = None
            self.checkpoint_active = False

    def get_checkpoint_decision(self) -> CheckpointDecision | str:
        # Blocks until the Streamlit UI thread pushes a checkpoint decision
        return self.checkpoint_decision_queue.get()


class StreamlitTransport:
    """A thread-safe bridge transport for the Streamlit UI.

    It delegates actual browser interactions to PlaywrightAutomatedTransport (or
    BrowserTransport), but diverts human prompt/checkpoint decisions to thread-safe
    queues that the Streamlit frontend can interact with.
    """

    def __init__(
        self,
        bridge: StreamlitUIBridge,
        cdp_url: str = "http://localhost:9223",
        auto: bool = False,
        limit_overrides: dict[str, dict[str, int]] | None = None,
        refresh_policy: dict[str, int] | None = None,
    ) -> None:
        self.bridge = bridge
        self.auto = auto

        # Initialize the underlying automated transport
        # We hook into its output_fn to stream console prints to the UI!
        self.underlying = PlaywrightAutomatedTransport(
            cdp_url=cdp_url,
            input_fn=self._streamlit_input_fn,
            output_fn=self._streamlit_output_fn,
            auto=auto,
            limit_overrides=limit_overrides,
            refresh_policy=refresh_policy,
        )

    # Allow custom topic override, prompt injects etc. to be accessed by the interpreter
    @property
    def topic_override(self) -> str | None:
        return self.underlying._fallback_transport.topic_override

    @topic_override.setter
    def topic_override(self, value: str | None) -> None:
        self.underlying._fallback_transport.topic_override = value

    @property
    def prompt_inject(self) -> str | None:
        return self.underlying._fallback_transport.prompt_inject

    @prompt_inject.setter
    def prompt_inject(self, value: str | None) -> None:
        self.underlying._fallback_transport.prompt_inject = value

    @property
    def injected_files(self) -> dict[str, str] | None:
        return self.underlying._fallback_transport.injected_files

    @injected_files.setter
    def injected_files(self, value: dict[str, str] | None) -> None:
        self.underlying._fallback_transport.injected_files = value

    @property
    def injected_images(self) -> list[str] | None:
        return self.underlying._fallback_transport.injected_images

    @injected_images.setter
    def injected_images(self, value: list[str] | None) -> None:
        self.underlying._fallback_transport.injected_images = value

    def _streamlit_output_fn(self, text: str) -> None:
        """Stream output prints to the UI bridge."""
        self.bridge.push_log(text)

    def _streamlit_input_fn(self, prompt_text: str) -> str:
        """Collect input from the UI bridge instead of the terminal."""
        self.bridge.push_log(f"[PROMPT] {prompt_text}")
        return self.bridge.get_input()

    def deliver_prompt(self, participant: Participant, prompt: str) -> None:
        # Notify the UI about prompt delivery
        self.bridge.push_log(f"Delivering prompt to {participant.display_name}...")
        self.underlying.deliver_prompt(participant, prompt)

    def collect_response(self, participant: Participant) -> str:
        self.bridge.push_log(f"Waiting for {participant.display_name}'s response...")
        return self.underlying.collect_response(participant)

    def ask_checkpoint(self, summary: str) -> CheckpointDecision:
        if self.auto or self.underlying.auto:
            self.bridge.push_log("Autonomous mode: Automatically continuing...")
            return CheckpointDecision.CONTINUE

        self.bridge.push_log("[CHECKPOINT REACHED]")
        # Signal Streamlit that a checkpoint is active
        self.bridge.set_checkpoint_active(summary)

        # Wait for the user's decision from the UI
        decision = self.bridge.get_checkpoint_decision()

        # Clean up the active checkpoint state
        self.bridge.clear_checkpoint_active()

        if decision == "F":
            self.auto = True
            self.underlying.auto = True
            return CheckpointDecision.CONTINUE

        return decision
