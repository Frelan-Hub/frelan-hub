"""Transport Layer.

Transports move information between the interpreter and the execution engines.
The interpreter depends only on the ``Transport`` protocol in ``base``; concrete
adapters (``BrowserTransport`` for the MVP) live beside it and can be added
without touching the interpreter.
"""

from frelan.transport.base import Transport
from frelan.transport.browser import BrowserTransport

__all__ = ["Transport", "BrowserTransport"]
