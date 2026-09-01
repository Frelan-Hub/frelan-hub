from __future__ import annotations

import queue
import threading
import time

from frelan.enums import CheckpointDecision
from frelan.transport.streamlit_transport import StreamlitUIBridge, StreamlitTransport
from tests.test_browser_transport import _participant


def test_bridge_logs_queueing():
    bridge = StreamlitUIBridge()
    bridge.push_log("test log")
    assert bridge.logs_queue.get_nowait() == "test log"


def test_bridge_checkpoint_active_toggle():
    bridge = StreamlitUIBridge()
    assert not bridge.checkpoint_active
    assert bridge.checkpoint_summary is None

    bridge.set_checkpoint_active("test summary")
    assert bridge.checkpoint_active
    assert bridge.checkpoint_summary == "test summary"

    bridge.clear_checkpoint_active()
    assert not bridge.checkpoint_active
    assert bridge.checkpoint_summary is None


def test_streamlit_transport_ask_checkpoint_auto():
    bridge = StreamlitUIBridge()
    # If auto is True, ask_checkpoint should instantly return CONTINUE without blocking
    transport = StreamlitTransport(bridge=bridge, auto=True)
    decision = transport.ask_checkpoint("summary")
    assert decision == CheckpointDecision.CONTINUE


def test_streamlit_transport_ask_checkpoint_manual_interactive():
    bridge = StreamlitUIBridge()
    transport = StreamlitTransport(bridge=bridge, auto=False)

    results: queue.Queue[CheckpointDecision] = queue.Queue()

    def run_ask():
        res = transport.ask_checkpoint("test manual checkpoint")
        results.put(res)

    t = threading.Thread(target=run_ask, daemon=True)
    t.start()

    # Wait for the bridge to mark checkpoint as active
    for _ in range(100):
        if bridge.checkpoint_active:
            break
        time.sleep(0.01)

    assert bridge.checkpoint_active
    assert bridge.checkpoint_summary == "test manual checkpoint"

    # Push decision from the "UI"
    bridge.checkpoint_decision_queue.put(CheckpointDecision.CONVERGED)

    # Get the return decision
    decision = results.get(timeout=2.0)
    assert decision == CheckpointDecision.CONVERGED
    assert not bridge.checkpoint_active
