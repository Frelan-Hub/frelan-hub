"""Transport Layer — the transport port.

``Transport`` is a structural ``Protocol``: the interpreter depends on *this*
interface only, never on a concrete adapter. Any object providing these three
methods is a valid transport, so a future API/MCP adapter needs no interpreter
change and no inheritance — it just has to match the shape.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from frelan.enums import CheckpointDecision
from frelan.mission_contract import Participant


@runtime_checkable
class Transport(Protocol):
    """Moves information between the interpreter and an execution engine."""

    def deliver_prompt(self, participant: Participant, prompt: str) -> None:
        """Hand a rendered prompt to the given participant (e.g. show + copy)."""
        ...

    def collect_response(self, participant: Participant) -> str:
        """Return the participant's response as a single string."""
        ...

    def ask_checkpoint(self, summary: str) -> CheckpointDecision:
        """Show the checkpoint summary and return the Founder's decision."""
        ...
