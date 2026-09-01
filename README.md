# AI-Conductor B Runtime

> **A declarative, engine-agnostic runtime for moderating structured multi-round discussions between browser-based AI models.**

AI-Conductor B Runtime is a generic Python runtime that executes immutable, declarative Mission Contracts (YAML or JSON) to orchestrate structured, multi-phase deliberations among browser-based AI models (such as ChatGPT, Gemini, and Claude).

The runtime drives models inside authenticated browser sessions or via manual clipboard interaction. It provides deterministic transcript recording, prompt-budget management, human-in-the-loop governance checkpoints, multi-document deliverable synthesis, and empirical peer-scoring evidence collection without requiring direct API tokens or closed orchestration frameworks.

---

## Learn AI-Conductor B

- 📖 [User Guide](USER-GUIDE.md) — detailed installation, configuration, and usage
- 🎥 Video Tutorial — Coming Soon
- 🧪 [Testing](#10-testing) — run and verify the test suite
- 🏗️ [Architecture](#3-core-architecture) — understand the runtime design
- 📋 [Mission Contracts](MISSION-CONTRACT.md) — understand how missions are defined
- 🤝 [Contributing](CONTRIBUTING.md) — improve the project
- 🔐 [Security](SECURITY.md) — report security vulnerabilities

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Key Capabilities](#2-key-capabilities)
3. [Core Architecture](#3-core-architecture)
4. [Major Components](#4-major-components)
5. [Mission & Turn Execution Model](#5-mission--turn-execution-model)
6. [Browser & Transport Model](#6-browser--transport-model)
7. [Mission Examples & Library](#7-mission-examples--library)
8. [Installation & Prerequisites](#8-installation--prerequisites)
9. [Basic Usage](#9-basic-usage)
10. [Testing](#10-testing)
11. [Repository Structure](#11-repository-structure)
12. [Current Status & Maturity](#12-current-status--maturity)
13. [Known Limitations](#13-known-limitations)
14. [License](#14-license)

---

## 1. Project Overview

AI-Conductor B Runtime coordinates multi-model collaboration by separating **what** a discussion is (the declarative contract) from **how** it is executed (the generic runtime and transport layers).

### Highlights

- **Engine-Agnostic Collaboration**: Seats models (ChatGPT, Gemini, Claude) as equal peers with rotating roles and turn orders.
- **No Direct API Token Requirement**: Drives models through active, authenticated browser sessions via Chrome DevTools Protocol (CDP) or manual clipboard mode.
- **Delta Prompting & Context Budgets**: Prevents context explosion by sending only turn diffs across persistent chats while enforcing platform-specific character budgets.
- **Human-in-the-Loop Governance**: Scheduled checkpoints allow operators to steer, edit context, fully automate, converge, escalate, or terminate missions.
- **Multi-Output Deliverable Synthesis**: Runs dedicated synthesis turns per declared deliverable to avoid single-response token truncation.
- **Deterministic Ledger & Resumption**: Append-only logging (`ledger.jsonl`) allows instant mission replay and crash recovery.
- **Empirical Evidence Collection**: Gathers reciprocal peer scoring and objective metrics (citations, latency, response sizes) across runs.

---

## 2. Key Capabilities

| Capability | Description |
|---|---|
| **Declarative Mission Contracts** | Missions are defined entirely in YAML/JSON specifying participants, roles, required capabilities, phases, turn orders, context policies, governance intervals, and deliverables. |
| **Equitable Peer Choreography** | Contracts rotate duties (proposer, critic, synthesizer) and alternate turn orders across phases so no model is permanently privileged. |
| **Delivery Ladder** | Long prompts walk a fallback ladder: inline typing &rarr; verified file attachment &rarr; chunked sequential delivery &rarr; head/tail truncation. |
| **Attachment Refusal Recovery** | Detects model refusals to read attached overflow files and automatically re-delivers content truncated inline. |
| **Automatic Chat Rollover** | Monitors conversation character budgets (e.g., Claude's per-conversation limits), opening a fresh chat and providing a full re-brief automatically. |
| **Dual Control Planes** | Run missions through an interactive terminal CLI (`main.py`) or a Streamlit web control plane (`streamlit_app.py`). |

---

## 3. Core Architecture

The runtime adheres to four strictly separated layers with inward-only dependencies:

```
┌─────────────────────────────────────────────────────────────┐
│ 1. MISSION LAYER (Immutable Contracts)                      │
│    frelan/mission_contract.py · YAML/JSON Specifications    │
└──────────────────────────────┬──────────────────────────────┘
                               │ loaded & validated by
┌──────────────────────────────▼──────────────────────────────┐
│ 2. INTERPRETATION LAYER (Generic Runtime Engine)            │
│    mission_loader · mission_instance · prompt_renderer      │
│    mission_interpreter · ledger · deliverables · evidence   │
└──────────────────────────────┬──────────────────────────────┘
                               │ dispatches via Transport Protocol
┌──────────────────────────────▼──────────────────────────────┐
│ 3. TRANSPORT LAYER (Delivery & Harvesting)                  │
│    transport/playwright_auto.py · transport/browser.py      │
│    transport/adapters.py                                    │
└──────────────────────────────┬──────────────────────────────┘
                               │ interacts with
┌──────────────────────────────▼──────────────────────────────┐
│ 4. EXECUTION LAYER (External Models in Browser / Manual)    │
│    ChatGPT · Gemini · Claude                                │
└─────────────────────────────────────────────────────────────┘
```

### Architectural Rules

1. **Inner layers never know outer implementations**: The Mission and Interpretation layers have no dependency on browser automation or specific model providers.
2. **Single Composition Root**: `main.py` is the only module that instantiates concrete transports and resolves filesystem output destinations.
3. **Contracts are frozen; implementations are replaceable**: Contract schemas, wire sentinels, ledger formats, and directory containment rules are stable interfaces. Adapters, prompt rendering strategies, and budgets can change freely.
4. **Operational Simplicity First**: Structural simplicity takes precedence over complex routing abstractions, while strict correctness controls (fail-closed validation, path containment, refusal handling) remain non-negotiable.

---

## 4. Major Components

### Core Modules (`frelan/`)

- **`mission_contract.py`**: Frozen dataclasses representing immutable mission contracts (`MissionContract`, `Participant`, `Phase`, `GovernanceConfig`, `OutputConfig`).
- **`mission_loader.py`**: Parses YAML/JSON contracts, enforces structural validation, and validates capability/participant references.
- **`mission_instance.py`**: Manages mutable runtime state (current phase, turn index, context overrides, checkpoint records).
- **`prompt_renderer.py`**: Renders dynamic prompts, injects reference files/images, calculates ledger diffs (`_unseen_responses`), and enforces character budgets.
- **`mission_interpreter.py`**: The central run loop. Dispatches turn execution, coordinates checkpoint evaluations, and triggers synthesis turns.
- **`ledger.py`**: Append-only log persisting every prompt, response, checkpoint, and system event in Markdown (`ledger.md`) and JSON Lines (`ledger.jsonl`).
- **`deliverables.py`**: Handles deliverable formatting and extraction using plain-text `BEGIN-OUTPUT:` and `END-OUTPUT:` sentinels, with automatic recovery for formatting slips.
- **`evidence.py` & `report.py`**: Parses reciprocal peer scoring rubrics (````frelan-scores````), calculates objective response metrics, and aggregates summary statistics into `evidence-log.jsonl`.
- **`discovery.py`**: Optional read-only pre-flight inspection for participant capabilities and environment readiness.

### Transport Adapters (`frelan/transport/`)

- **`base.py`**: Abstract `Transport` protocol defining `deliver_prompt`, `collect_response`, and lifecycle hooks.
- **`playwright_auto.py`**: Automated browser transport driving Chrome tabs over CDP using Playwright. Implements tab switching, streaming detection, delivery ladder fallback, limit-error recovery, and chat rollover.
- **`browser.py`**: Manual clipboard transport that copies prompts to the system clipboard and accepts responses pasted into the terminal.
- **`adapters.py`**: Browser DOM selectors, per-engine composer character ceilings, and streaming stability heuristics for supported model web interfaces.

### Web Control Plane (`ui/` & `streamlit_app.py`)

A multi-view Streamlit dashboard that runs peer to `main.py`, providing:
- **Overview**: Live mission status, active speaker, and quick controls.
- **Setup**: Meeting type selection, topic overrides, file/image attachments, and generated CLI command previews.
- **Agents**: Live roster of seated participants, engine assignments, capabilities, and token/character limits.
- **Execution**: Visual execution topology, live event stream, and real-time ledger transcript.
- **Governance**: Checkpoint decision board and historical intervention logs.
- **Outputs**: Rendered deliverables and file downloads with full generation provenance.
- **Logs**: Downloadable technical execution logs from the runtime subprocess.
- **History**: Historical run registry with parameter comparisons and artifact inspection.

---

## 5. Mission & Turn Execution Model

### Conceptual Model

AI-Conductor B Runtime separates orchestration into distinct concepts:

| Concept | Description | Location in Contract |
|---|---|---|
| **Model** | The underlying intelligence engine (e.g., `chatgpt`, `gemini`, `claude`). | `assigned_engine.execution_engine` |
| **Agent** | A configured worker wrapping a model with a standing brief. | `type: agent` + `instructions` |
| **Participant** | An entity seated in the mission (model or agent). | `participants[]` |
| **Role** | The responsibility held by a participant (e.g., `peer_analyst`, `critic`). | `assigned_engine.role` |
| **Capability** | Declared capability requirements (e.g., `research.web`, `architecture`). | `capabilities[]` |
| **Interaction** | How participants collaborate within a phase (`sequential` or `parallel`). | `phases[].interaction` |
| **Workflow** | Declarative multi-stage composition (e.g., `research-architect-build`). | `metadata.workflow` + `phases[].stage` |
| **Governance** | Human oversight rules and checkpoint intervals. | `governance` |

### Turn Execution Flow

```
[Start Mission]
       │
       ▼
[For Each Phase in Contract]
       │
       ├─► Determine Interaction Mode
       │     ├─► Sequential: Render Turn Prompt ──► Deliver ──► Wait/Collect Response
       │     │                                                 (Repeats per participant)
       │     └─► Parallel:   Render All Prompts ──► Deliver All ──► Collect All Responses
       │
       ├─► Record Turn(s) to ledger.md & ledger.jsonl
       │
       ├─► Checkpoint Interval Reached?
       │     ├─► YES ──► Prompt Governance: [C]ontinue / [V]converged / [E]scalate / [T]erminate
       │     └─► NO  ──► Continue
       │
       ▼
[Convergence / Natural Completion]
       │
       ▼
[Synthesis Turn(s)]
       ├─► Single Output: 1 synthesis turn ──► recommendation.md
       └─► Multi-Output:  1 synthesis turn per declared output ──► <output_filename>.md
       │
       ▼
[Write Final Artifacts & Append evidence-log.jsonl]
```

### Phase Context Policies (`phases[].context`)

- `auto` *(default)*: Sends the full bounded discussion window on a participant's first turn; sends only new turns (deltas) thereafter.
- `none`: Sends no previous discussion context (used for blind/independent divergence).
- `delta`: Sends only new responses since the last turn, even on the first turn.
- `full`: Re-sends the entire bounded discussion window on every turn.

---

## 6. Browser & Transport Model

### Automated CDP Transport (`PlaywrightAutomatedTransport`)

Automated mode attaches to an existing, already-authenticated Chrome browser session via Chrome DevTools Protocol (`http://localhost:9223` by default).

```
┌──────────────────────────────────────────────────────────┐
│ Chrome Browser (Remote Debugging Port 9223)              │
│ ┌────────────────┐ ┌────────────────┐ ┌────────────────┐ │
│ │  chatgpt.com   │ │gemini.google.com││   claude.ai    │ │
│ └───────▲────────┘ └───────▲────────┘ └───────▲────────┘ │
└─────────┼──────────────────┼──────────────────┼──────────┘
          └──────────────────┼──────────────────┘
                             │ CDP Connection
┌────────────────────────────┴─────────────────────────────┐
│ PlaywrightAutomatedTransport                             │
│  - Tab Matching by Domain                                │
│  - Delivery Ladder (Inline / Attachment / Chunk / Trunc) │
│  - Response Streaming & DOM Stabilization Check          │
│  - Attachment Refusal Detection & Auto-Retry             │
│  - Session Rollover Management (Claude)                  │
└──────────────────────────────────────────────────────────┘
```

#### Safe Composer Limits & Delivery Ladder

Browser chat composers have character thresholds. The transport dynamically manages prompt sizing:

| Platform | Safe Inline Limit | Conversation Budget | Action on Limit |
|---|---|---|---|
| **ChatGPT** | ~9,000 chars | Per-message | Spills to verified attachment or chunking |
| **Gemini** | ~18,000 chars | Per-message | Spills to verified attachment or chunking |
| **Claude** | ~12,000 chars | ~300,000 chars | Auto-rollover to fresh chat with full re-brief |

When a prompt exceeds the safe inline limit:
1. **Attachment + Stub**: Saves prompt to `outputs/prompt_overflow_*.md` and uploads it as an attachment (only when the upload chip is verified new).
2. **Chunked Delivery**: Sends prompt across sequential messages marked "do not respond yet".
3. **Truncated Inline**: Falls back to keeping head and tail with an explicit omission notice.

---

## 7. Mission Examples & Library

Meeting templates are stored as YAML contracts in `missions/`. Shipped templates use equitable peer choreographies:

| Template | Path | Pattern | Use Case |
|---|---|---|---|
| **General Inquiry** | `missions/distill/general_inquiry.yaml` | Distill | Fast, lightweight 2-phase inquiry: independent answers &rarr; reconciliation. |
| **App Pre-Planning** | `missions/shape/app_planning.yaml` | Shape | Requirements &rarr; schema/architecture proposals &rarr; cross-challenge &rarr; build plan. |
| **PRD & Build Blueprint** | `missions/candidates/prd_blueprint.yaml` | Multi-Output | Product framing &rarr; stories &rarr; technical proposals &rarr; blueprint merge. Produces `prd.md`, `technical-blueprint.md`, `build-plan.md`, and `agent-brief.md`. |
| **Brainstorm** | `missions/candidates/brainstorm.yaml` | Diverge/Rank | Independent divergence &rarr; clustering &rarr; ranking &rarr; backlog. |
| **Premortem** | `missions/candidates/premortem.yaml` | Risk Analysis | Assume failure &rarr; root cause analysis &rarr; blind spots &rarr; risk register. |
| **Document Review** | `missions/candidates/document_review.yaml` | Review | Independent severity-graded critique &rarr; reconciliation &rarr; findings report. |
| **Red/Blue Review** | `missions/candidates/red_blue_review.yaml` | Adversarial | Red attacks &rarr; Blue defends &rarr; duties swap &rarr; joint hardening findings. |
| **Trade-Off ADR** | `missions/candidates/tradeoff_adr.yaml` | Decision | Weighted criteria &rarr; blind scoring &rarr; divergence reconciliation &rarr; ADR. |
| **Adversarial Collaboration**| `missions/candidates/adversarial_collaboration.yaml`| Contested | Opposing positions &rarr; steelmanning &rarr; advance test agreement &rarr; joint report. |
| **Research & Build** | `missions/candidates/research_architect_build.yaml` | Multi-Stage | Parallel research &rarr; sequential architecture &rarr; build specification. |

### Adding Custom Meetings

Create a YAML contract in `missions/custom/<your_meeting>.yaml`. The runtime scans `missions/` on startup and lists custom files automatically without code modifications.

---

## 8. Installation & Prerequisites

### Prerequisites

- **Google Chrome** (for automated browser mode)
- **Python 3.12 or 3.13** — not needed on Windows, where `install.bat` provides it

### Windows — One-Click Install

```powershell
git clone https://github.com/Frelan-Hub/frelan-hub.git
cd frelan-hub
.\install.bat
```

`install.bat` needs nothing installed beforehand, Python included. It installs [uv](https://docs.astral.sh/uv/), resolves the environment from `uv.lock`, fetches the Playwright Chromium driver, and creates an **AI-Conductor B** shortcut on the Desktop and Start Menu. Running it again is safe.

**Updates run themselves.** `run_ui.bat` — and the shortcut that points at it — pulls upstream changes and re-syncs dependencies on each start. No Git, no network, or local edits in the way all leave the checkout untouched and start the app regardless. Set `AICB_NO_UPDATE=1` to skip.

### macOS / Linux, or a Manual Windows Setup

```bash
git clone https://github.com/Frelan-Hub/frelan-hub.git
cd frelan-hub

# With uv (recommended — manages the Python version too)
uv sync
uv run playwright install chromium

# Or with plain pip
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

`pyproject.toml` is the canonical dependency declaration and `uv.lock` pins the resolution; `requirements.txt` is generated from the lock for the pip path.

### Launch Chrome with Remote Debugging

Before running in automated mode, launch Chrome with remote debugging on port `9223`:

**Windows:**
```powershell
.\launch_chrome_debug.bat
```

or manually:
```powershell
Start-Process "chrome.exe" -ArgumentList "--remote-debugging-port=9223 --user-data-dir=`"$env:TEMP\ai-conductor-b-chrome-profile`""
```

**macOS:**
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9223 --user-data-dir="/tmp/chrome-profile"
```

**Linux:**
```bash
google-chrome --remote-debugging-port=9223 --user-data-dir="/tmp/chrome-profile"
```

> **Important**: In the opened Chrome window, navigate to and log in to [chatgpt.com](https://chatgpt.com), [gemini.google.com](https://gemini.google.com), and [claude.ai](https://claude.ai) (if using Claude), keeping the tabs open.

---

## 9. Basic Usage

### Interactive CLI

Launch the interactive meeting-type selector and topic injection prompt:

```bash
python main.py
```

### Common CLI Options

```bash
# Run fully autonomously (proceeds through all checkpoints automatically)
python main.py --auto

# Run a specific mission contract directly
python main.py missions/shape/app_planning.yaml

# Include Claude as an equal third peer in every phase
python main.py missions/shape/app_planning.yaml --claude

# Specify an explicit output directory
python main.py missions/shape/app_planning.yaml -o outputs/my-run

# Resume an interrupted or crashed run
python main.py --resume

# Manual clipboard mode (no browser automation)
python main.py --manual

# Inject topic and reference files directly via CLI
python main.py --topic "Design an event-driven payment service" --inject-files "docs/requirements.md,docs/schema.sql"

# Generate a summary report of cumulative evidence
python main.py --report

# Prune prompt overflow scratch files older than 14 days
python main.py --prune-spills 14
```

### Launch Web Dashboard

On Windows, use the **AI-Conductor B** shortcut, or:

```powershell
.\run_ui.bat
```

This updates the checkout, syncs dependencies, and opens `http://localhost:8501`. Elsewhere, or to skip the update step:

```bash
uv run streamlit run streamlit_app.py
```

### Windows Launchers

| Script | Does |
|---|---|
| `install.bat` | One-shot install; safe to re-run. `install.bat dev` adds test dependencies. |
| `run_ui.bat` | Updates, then starts the dashboard. Target of the Desktop shortcut. |
| `launch_chrome_debug.bat` | Starts Chrome on the CDP port, with sign-in instructions. |
| `run_frelan.bat` | `python main.py`, with any extra flags passed through. |
| `run_frelan_claude.bat` | `python main.py --claude`, with any extra flags passed through. |

---

## 10. Testing

The repository uses `pytest`. Tests mirror modules one-to-one under `tests/`:

```bash
# Run all unit and integration tests
python -m pytest

# Run a specific test module
python -m pytest tests/test_mission_interpreter.py

# Run a single test with verbose output
python -m pytest tests/test_interaction.py -k "test_parallel" -v
```

---

## 11. Repository Structure

```
ai-conductor-b-runtime/
├── contracts/                     # Transport & capability contracts
│   ├── communication-contract.yaml
│   └── peer-capability-brief.md
├── frelan/                        # Core runtime package
│   ├── transport/                 # Browser & manual transport adapters
│   │   ├── adapters.py            # DOM selectors & platform limits
│   │   ├── base.py                # Transport protocol definition
│   │   ├── browser.py             # Manual clipboard transport
│   │   ├── playwright_auto.py     # Automated CDP Playwright transport
│   │   └── streamlit_transport.py # UI bridge transport
│   ├── deliverables.py            # Deliverable sentinel parser
│   ├── discovery.py               # Pre-flight environment inspector
│   ├── enums.py                   # Enums (Interaction, Status, Decisions)
│   ├── evidence.py                # Peer scoring & metrics collector
│   ├── ledger.py                  # Append-only transcript engine
│   ├── mission_contract.py        # Immutable contract dataclasses
│   ├── mission_instance.py        # Mutable runtime state
│   ├── mission_interpreter.py     # Core execution run-loop
│   ├── mission_loader.py          # Contract parser & validator
│   ├── prompt_adapter.py          # Prompt tuning & formatting
│   ├── prompt_renderer.py         # Delta rendering & context budgeting
│   └── report.py                  # Evidence log aggregator
├── missions/                      # Mission contract library
│   ├── candidates/                # Candidate / unproven meeting templates
│   ├── custom/                    # User-authored custom meeting contracts
│   ├── distill/                   # Promoted distillation templates
│   ├── shape/                     # Promoted architecture & planning templates
│   ├── FORMATS.md                 # Facilitation pattern documentation
│   └── LIBRARY.md                 # Template promotion register
├── scripts/                       # Launcher support (not run directly)
│   ├── _env.bat                   # Repo root + interpreter resolution
│   ├── _update.bat                # Best-effort git pull & dependency sync
│   └── new-shortcuts.ps1          # Desktop / Start Menu shortcut creation
├── tests/                         # Test suite
├── ui/                            # Streamlit dashboard modules
├── install.bat                    # One-click Windows installer
├── launch_chrome_debug.bat        # Chrome with the CDP debugging port
├── main.py                        # CLI entry point & composition root
├── pyproject.toml                 # Canonical dependencies & pytest config
├── requirements.txt               # Generated from uv.lock, for the pip path
├── run_frelan.bat                 # CLI launcher
├── run_frelan_claude.bat          # CLI launcher, Claude included
├── run_ui.bat                     # Dashboard launcher (updates, then starts)
├── streamlit_app.py               # Web dashboard entry point
└── uv.lock                        # Pinned dependency resolution
```

---

## 12. Current Status & Maturity

- **Implemented & Validated**:
  - Sequential execution loop with dynamic context diffing (`phases[].context`).
  - Automated CDP transport across ChatGPT, Gemini, and Claude with attachment fallback and refusal recovery.
  - Multi-document deliverable synthesis with sentinel parsing.
  - Governance checkpoints and mid-run prompt/topic injection.
  - Full resumability from `ledger.jsonl`.
  - Reciprocal peer scoring and evidence logging.
  - Multi-view Streamlit dashboard control plane.
- **Experimental**:
  - `phases[].interaction: parallel` concurrent generation is implemented, unit-tested, and verified against scripted transports; live browser A/B benchmarks across background tabs remain ongoing.
- **Intentionally Deferred**:
  - Autonomous model-driven validation gates, runtime cross-mission pipeline schedulers, and automatic dynamic model-routing engines are omitted in favor of explicit contract definitions and operator governance.

---

## 13. Known Limitations

1. **One Conversation Per Engine**: The automated browser transport addresses tabs by domain (`chatgpt.com`, `gemini.google.com`, `claude.ai`). Seating two participants on the same engine causes them to share a browser conversation thread.
2. **Web DOM Selector Drift**: Automated mode relies on web UI selectors. Frontend changes by provider platforms may require updates in `frelan/transport/adapters.py`.
3. **Background Tab Throttling**: In parallel interaction mode, browser tab streaming performance depends on the operating system's background tab throttling policies.
4. **Manual Mission Chaining**: Multi-mission workflows (feeding deliverable A into mission B) are currently composed by passing output files into startup file injection rather than via automated execution pipelines.

---

## 14. License

AI-Conductor B Runtime is licensed under the [Apache License, Version 2.0](LICENSE).

- **Copyright**: Copyright 2026 FRELAN
- **Origin**: Originally created by FRELAN.
- **Terms**: Contributions and derivative works are permitted under the terms of the Apache License, Version 2.0. See the [LICENSE](LICENSE) file for full details.
