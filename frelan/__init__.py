"""AI-Conductor B Runtime (browser engine; formerly FRELAN Mission Interpreter).

A generic runtime for declarative Mission Contracts. The package is organised
into the architecture's four layers:

- Mission Layer         -> mission_contract.py (immutable, declarative data)
- Interpretation Layer  -> mission_loader.py, mission_instance.py,
                           prompt_renderer.py, ledger.py, mission_interpreter.py
- Transport Layer       -> transport/ (moves information; MVP = human via browser)
- Execution Layer       -> the AI models themselves (external to this package)

The interpreter contains execution mechanics only. It knows nothing about any
specific mission, and nothing about any specific model or transport.
"""

__version__ = "0.1.0"
