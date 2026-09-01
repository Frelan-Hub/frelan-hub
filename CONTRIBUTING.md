# Contributing to AI-Conductor B Runtime

Thank you for your interest in contributing to AI-Conductor B Runtime.

## Development Setup

### Prerequisites

- Python 3.12 or 3.13
- Google Chrome (if testing automated browser transport via Chrome DevTools Protocol)

### Environment Setup

With [uv](https://docs.astral.sh/uv/) — recommended, and what the Windows installer uses:

```bash
uv sync --extra dev              # Windows shortcut: install.bat dev
uv run playwright install chromium
```

Prefix commands with `uv run` (`uv run python -m pytest`) or activate `.venv` as usual.

Or with plain `pip`:

1. Clone the repository and create a virtual environment:
   ```bash
   python -m venv .venv
   ```

2. Activate the virtual environment:
   - **Windows (PowerShell)**: `.venv\Scripts\Activate.ps1`
   - **macOS / Linux**: `source .venv/bin/activate`

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   pip install pytest
   playwright install chromium
   ```

### Dependencies

`pyproject.toml` is canonical and `uv.lock` pins the resolution. `requirements.txt` is **generated** — never edit it by hand. After changing a dependency:

```bash
uv lock
uv export --no-hashes --no-dev --format requirements-txt > requirements.txt
```

## Running Tests

Run the test suite using `pytest`:

```bash
# Run all tests
python -m pytest

# Run a specific test file
python -m pytest tests/test_mission_interpreter.py
```

## Contribution Guidelines

- **Preserve Core Contracts**: The Mission Contract schema, wire sentinels (`BEGIN-OUTPUT:` / `END-OUTPUT:`), append-only ledger structure, and transport protocol boundaries are stable interfaces.
- **Maintain Validation Integrity**: Ensure contract validation rules and fail-closed error handling remain strict.
- **Verify with Tests**: Add or update unit tests in `tests/` to cover new behaviors or fixes, and verify that the full test suite passes before proposing changes.
- **Keep Changes Minimal**: Prefer simple, decoupled implementations over unnecessary abstractions or framework complexity.
