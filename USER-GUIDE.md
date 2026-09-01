# AI-Conductor B Runtime — Comprehensive Application Manual

Welcome to the **AI-Conductor B Runtime** (browser engine, formerly FRELAN Mission Interpreter MVP), a generic Python runtime that executes immutable, declarative Mission Contracts (YAML or JSON) and moderates structured, multi-round discussions between browser-based AI models (ChatGPT, Gemini, and optionally Claude).

This manual covers installation, every run mode, the meeting-type menu, equitable role design, composer-limit safety, output measurement, mission authoring, and troubleshooting. For a one-page cheat sheet, see [QUICK-REFERENCE.md](QUICK-REFERENCE.md). For the mission file schema, see [MISSION-CONTRACT.md](MISSION-CONTRACT.md). For the underlying conceptual model, see [CONCEPTUAL-MODEL.md](CONCEPTUAL-MODEL.md).

---

## Table of Contents

1. [Core Concept & Philosophy](#1-core-concept--philosophy)
2. [Prerequisites & Local Setup](#2-prerequisites--local-setup)
3. [Launching Chrome with Remote Debugging](#3-launching-chrome-with-remote-debugging)
4. [Quick Start](#4-quick-start)
5. [The Meeting-Type Menu](#5-the-meeting-type-menu)
6. [CLI Reference](#6-cli-reference)
7. [Startup Topic, File & Image Injection](#7-startup-topic-file--image-injection)
8. [Execution Transports](#8-execution-transports)
9. [Composer Limits, Overflow Attachments & Auto-Rollover](#9-composer-limits-overflow-attachments--auto-rollover)
10. [Participants, Roles, Interaction & Workflow](#10-participants-roles-interaction--workflow)
11. [Governance Checkpoints](#11-governance-checkpoints)
12. [Output Measurement: Peer Scoring & Evidence](#12-output-measurement-peer-scoring--evidence)
13. [Saved Outputs Reference](#13-saved-outputs-reference)
14. [Authoring Custom Missions](#14-authoring-custom-missions)
15. [Testing](#15-testing)
16. [Troubleshooting](#16-troubleshooting)
17. [Related Documents](#17-related-documents)

---

## 1. Core Concept & Philosophy

FRELAN is an **engine-agnostic moderation run-loop**, built on four strictly separated layers:

| Layer | Responsibility | Key files |
|---|---|---|
| **Mission** | Declares *what* the discussion is — participants, roles, phases, checkpoint cadence, outputs. Immutable, declarative. | `frelan/mission_contract.py` |
| **Interpretation** | The generic runtime — loads/validates the contract, tracks mutable run state, renders prompts, appends to the ledger, steps the run loop. Knows nothing about a specific mission, model, or transport. | `mission_loader.py`, `mission_instance.py`, `prompt_renderer.py`, `ledger.py`, `mission_interpreter.py`, `evidence.py` |
| **Transport** | Moves prompts/responses to and from the actual browser tabs. | `transport/playwright_auto.py` (automated), `transport/browser.py` (manual) |
| **Execution** | The AI models themselves — external, interchangeable, and (by design) never permanently favored. | ChatGPT, Gemini, Claude in your browser |

Two governing principles shape everything below:

- **Models do not own permanent roles.** Every meeting template assigns the *same* `peer_analyst` role and capabilities to every engine; who proposes, critiques, or speaks first/last rotates by phase (§10). This reflects the core orchestration principle: *"Models do not own permanent roles. Capabilities determine eligibility."* Evidence informs which models you keep over the long term; it never gates a run.
- **Missions produce evidence; they never assume strength.** Output quality is not assumed from a model's reputation — it is measured every run via reciprocal peer scoring and objective metrics (§12), so that "which model is actually strong at this kind of work" becomes a question you can answer from data instead of guessing.

---

## 2. Prerequisites & Local Setup

Requires **Python 3.12 or 3.13**.

### Step 1 — Virtual environment

```powershell
# Windows PowerShell
python -m venv .venv
.venv\Scripts\Activate.ps1

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

### Step 2 — Install dependencies

```bash
pip install -r requirements.txt
```

Core dependencies (from `pyproject.toml`): `PyYAML`, `pyperclip`, `playwright`. Dev/test: `pytest`.

### Step 3 — Install Playwright's browser driver

```bash
playwright install chromium
```

---

## 3. Launching Chrome with Remote Debugging

The default automated mode connects to your **already-open, already-authenticated** Chrome via the DevTools Protocol (CDP). This means FRELAN uses your existing ChatGPT Plus / Gemini Advanced / Claude Pro sessions for free and never trips bot detection — because it isn't a bot browser, it's *your* browser.

Chrome **must** be started with remote debugging enabled, on port `9223` by default, before you run FRELAN.

**Windows** — use the provided script:
```powershell
.\launch_chrome_debug.bat
```
or manually:
```powershell
Start-Process "chrome.exe" -ArgumentList "--remote-debugging-port=9223 --user-data-dir=`"$env:TEMP\ai-conductor-b-chrome-profile`""
```

**macOS / Linux:**
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9223 --user-data-dir="/tmp/chrome-profile"
```

> ⚠️ Log into [chatgpt.com](https://chatgpt.com/), [gemini.google.com](https://gemini.google.com/), and (if you plan to include Claude) [claude.ai](https://claude.ai/) inside this specific browser window, and keep tabs to each open, before launching the interpreter.

---

## 4. Quick Start

Three ways to launch, all from the repository root:

```bash
# Interactive — shows the meeting-type menu (§5), then topic/file injection (§7)
python main.py

# Windows convenience launchers (open a new terminal, then pause on exit)
.\run_frelan.bat            # same as: python main.py
.\run_frelan_claude.bat     # same as: python main.py --claude
```

Everything from here — meeting type, engines included, topic, files — is chosen interactively unless you pass explicit flags (§6).

---

## 5. The Meeting-Type Menu

Running `python main.py` **without an explicit mission path**, in an interactive terminal, opens a menu **before** anything else runs:

```
======================================================================
                AI-CONDUCTOR B RUNTIME — MEETING TYPE
======================================================================
  1) [candidates] Adversarial Collaboration
  2) [candidates] Brainstorm
  3) [candidates] Decision Trade-off (ADR)
  4) [candidates] Document Review
  5) [candidates] Frontier Architecture Charette
  6) [candidates] PRD & Build Blueprint
  7) [candidates] Parallel Lenses
  8) [candidates] Premortem
  9) [candidates] Red Team / Blue Team Review
 10) [candidates] Research Deep-Dive
 11) [candidates] Website / Design Review
 12) [candidates] Workspace Preparation
 13) [custom] My Custom Meeting
 14) [distill] General Inquiry
 15) [shape] App Pre-Planning (Architecture & Schema)
(Press Enter to keep the default legacy debate)
(Custom meeting type: type its path, or drop the .yaml in missions/custom/ to list it above)

Meeting Type [1-15 or path]:
>
```

**The list is a scan of `missions/`, not a fixed table.** Entries are sorted by
group then name, so the numbers move when a template is added or removed —
read the labels, don't memorise the digits. Because `candidates` sorts before
`distill` and `shape`, the unproven templates currently list *above* the two
promoted ones; that ordering corrects itself as candidates are promoted into
their category folders. Files that fail contract validation
are skipped silently rather than breaking the menu, and the two development
fixtures (`frelan_debate.yaml`, `frelan_mission_contract_v2.yaml`) are excluded
from the listing.

A template's folder is its **category**, and the menu shows it as the `[group]`
tag. `candidates/` holds templates that are runnable but not yet proven; three
recorded runs plus positive Founder feedback promote one into its category
folder ([missions/LIBRARY.md](missions/LIBRARY.md)).

| Template file | Structure | Best for |
|---|---|---|
| `missions/distill/general_inquiry.yaml` | Independent answers (fact vs. inference labelled) → reconcile → one consolidated answer | **General inquiries** — a question that needs a good answer, not a design or a decision. 4 rounds, expected to converge early |
| `missions/shape/app_planning.yaml` | Requirements → independent architecture & schema proposals → cross-challenge (risk/scaling/data-integrity) → consolidated build plan | Pre-implementation planning for an app or service |
| `missions/candidates/prd_blueprint.yaml` | Product framing → independent scope & user stories → independent technical proposals → cross-challenge → blueprint merge, then **four separate synthesis turns** | Producing the full build document set for an app or tool: `prd.md`, `technical-blueprint.md`, `build-plan.md`, and `agent-brief.md` — the brief written to be pasted straight into a coding agent. Use this when the output is meant to be *built from*, not read once |
| `missions/candidates/brainstorm.yaml` | Independent divergence (≥12 ideas each, unseen) → clustering into named themes → agreed criteria then ranking → idea backlog | Opening a subject up before deciding anything. The entry-point meeting type |
| `missions/candidates/premortem.yaml` | "It failed twelve months from now, here is how" written independently → root causes with early signals → blind spots → risk register | Stress-testing a plan you are already committed to |
| `missions/candidates/document_review.yaml` | Independent severity-graded critique → reconciliation (accept / reject / re-grade) → consolidated findings | Reviewing a document, spec, or design you supply as a Reference File |
| `missions/candidates/red_blue_review.yaml` | Red attacks → Blue defends → **duties swap** → Red attacks the repairs → Blue defends → joint findings | Hardening an artifact that must withstand attack. **Cannot take a third peer** — Claude would join both sides, so the contract declares `claude_peer: unsupported` and the runtime refuses it |
| `missions/candidates/tradeoff_adr.yaml` | Options (incl. "change nothing") → weighted criteria agreed first → blind independent scoring → reconcile divergences ≥ 2 points → ADR | Making a decision you need to be able to defend later |
| `missions/candidates/adversarial_collaboration.yaml` | Opposing positions staked blind → steelman and concede → **agree the decisive test and its decision rule in advance** → joint report | A genuinely contested question where you want the disagreement narrowed and testable, not smoothed over |
| `missions/candidates/parallel_lenses.yaml` | Facts → risks → benefits → alternatives → assessment, both peers on the same lens per phase | Examining something from every side without letting one side dominate early |
| `missions/candidates/research_deepdive.yaml` | Independent findings with resolvable citations → cross-examination of sources and reasoning → decision brief | Investigating a question and needing a sourced, challenged answer |
| `missions/candidates/website_design.yaml` | Concept directions → design/UX critique → content/IA & stack review → direction recommendation | Website, landing page, or product UX work — pairs well with reference-image injection |
| `missions/candidates/frontier_architecture_charette.yaml` | Long-form multi-phase architecture charette | Deep architecture work with a large round budget |
| `missions/candidates/workspace_preparation.yaml` | Workspace/environment preparation planning | Preparing a workspace or toolchain before a build |
| Enter | Fixed proposer/critic roles (legacy `frelan_debate.yaml`) | Back-compat only; kept for reference, **not** peer-equitable (see §10) |

`strategy_debate` is **retired** — `tradeoff_adr` replaces it with a stricter
choreography (weighted criteria agreed before any option is scored, then blind
scoring). The file stays on disk as `missions/strategy_debate.yaml.retired`,
which the menu scan does not match.

### General Inquiry vs. Research Deep-Dive

Both answer questions; they differ in weight. **General Inquiry** is 2 phases /
4 rounds and is meant to end at the first checkpoint once the peers agree —
use it for anything you would otherwise ask a single model. **Research
Deep-Dive** is 3 phases / 10 rounds with a dedicated cross-examination of
sources — use it when the answer must survive an adversarial pass and arrive as
a decision brief. If a General Inquiry keeps running to its round cap, that is
the signal to re-run the question as a Deep-Dive.

### Custom meeting types

A meeting type is just a Mission Contract on disk, so adding one is a file
operation, not a code change:

1. Copy `missions/custom/TEMPLATE.yaml.example` to
   `missions/custom/<your_name>.yaml`.
2. Edit the phases, participants, rounds, and outputs (schema:
   [MISSION-CONTRACT.md](MISSION-CONTRACT.md); proven phase choreographies:
   [missions/FORMATS.md](missions/FORMATS.md) Part A).
3. Validate it:
   ```bash
   python -c "from frelan.mission_loader import load_mission; print(load_mission('missions/custom/my_meeting.yaml').name)"
   ```
4. Run `python main.py` — it appears in the menu as `[custom] <name>`.

Subfolders of `missions/` are scanned one level deep and tagged with the folder
name (`missions/pre-planning/` is excluded: it holds the `--discover` stage, not
a meeting type). A contract kept outside the library can be run by typing its
path at the menu prompt — a bare name is also looked up under
`missions/custom/`. A typed contract is validated at the menu, so a typo or a
malformed file is reported there instead of aborting the run several prompts
later.

**Author a new meeting type only when the *choreography* differs.** Changing the
subject, adding reference files, or adding instructions is *session briefing* —
supply it at startup (§7) on top of a shipped template. There is no contract
generator in the runtime and no "custom mode" flag: a custom session and a
standard session run the same file through the same code path
([missions/FORMATS.md](missions/FORMATS.md) §B.7, §B.10).

After picking a type, you're asked:

```
Include Claude as a third peer? [y/N]
```

Answering `y` adds Claude to **every** phase of the chosen template as a full, equal peer — same `peer_analyst` role as ChatGPT and Gemini — speaking **after** both of them each turn. This has the same effect as passing `--claude` on the command line, and if you already passed that flag the question is skipped.

**The template defines *structure*; you define the *subject*.** Immediately after the meeting type is chosen, the Topic Injection prompt (§7) lets you set what's actually being discussed — the templates ship with a generic default objective, not a fixed topic.

Passing an explicit mission path (`python main.py missions/my_mission.yaml`) or running non-interactively (scripts, CI, the `.bat` launchers piping input) **skips the menu entirely** — this keeps existing automation and both `.bat` files behaving exactly as before this feature existed.

---

## 6. CLI Reference

```bash
python main.py [mission] [-o OUTPUT_DIR] [--manual] [--auto]
                [--cdp-url URL] [--claude | --review | --high-complexity]
```

| Argument | Default | Meaning |
|---|---|---|
| `mission` (positional) | `missions/frelan_debate.yaml` | Path to a `.yaml`/`.yml`/`.json` mission contract. Supplying this explicitly skips the meeting-type menu. |
| `-o`, `--output-dir` | a fresh `outputs/run-<UTC-timestamp>/` | Directory for `ledger.md`, `checkpoints.md`, `recommendation.md`, `metadata.json`, `evidence.json`. Omit it and every run gets its own directory, so history is preserved; `outputs/.last-run` records the newest for a bare `--resume`. Given explicitly, the path is **used verbatim and overwritten**. |
| `--resume` | off | Continue an interrupted mission from `<output-dir>/ledger.jsonl`. Every turn is autosaved there as it completes, so a crash or Ctrl+C never loses finished rounds; resume replays them (never re-runs them) and continues at the exact next turn. |
| `--manual` | off | Use the clipboard/terminal transport instead of browser automation (§8B). |
| `--auto` | off | Fully autonomous: answers Continue at every checkpoint until natural completion or convergence. |
| `--cdp-url` | `http://localhost:9223` | Chrome DevTools Protocol endpoint. Change if you launched Chrome on a different debug port. |
| `--claude`, `--review`, `--high-complexity` | off | Three names for the same switch: inject Claude as a full peer (§10) without going through the interactive menu. Useful for scripted/non-interactive runs. |

Examples:
```bash
python main.py --auto                                    # legacy debate, unattended
python main.py missions/shape/app_planning.yaml --claude        # explicit template + Claude, no menu
python main.py missions/my_mission.yaml -o outputs/run2   # custom mission + custom output dir
python main.py --cdp-url http://localhost:9222            # different Chrome debug port
python main.py --manual                                   # no browser automation at all
```

---

## 7. Startup Topic, File & Image Injection

Immediately after the meeting type is resolved, if running interactively, FRELAN prompts for three optional overrides:

1. **Custom Topic/Objective** — free text that replaces the mission's default `objective` for this run only. Press Enter to keep the template default.
2. **Reference Files** — comma-separated paths (quote paths containing spaces). Behavior depends on file type:
   - Plain text/code → read and **inlined** directly into every participant's prompt as a Markdown code block.
   - Recognized image extensions (`.jpg/.jpeg/.png/.webp/.gif/.bmp/.svg`) → automatically redirected into Reference Images instead.
   - Binary (PDF, DOCX, ZIP, etc., detected by a null-byte sniff) → registered for automated browser upload rather than inlined.
3. **Reference Images** — comma-separated image paths (queued for multi-modal browser upload) or free-text descriptions/URLs (passed through as context, not uploaded). A path that looks like a file but doesn't exist prints a loud warning rather than silently becoming a text description.

Any of the three can also be added, replaced, or cleared **mid-run** via the checkpoint `P` sub-menu (§11).

Non-interactive runs (no TTY on stdin) skip all startup prompts and simply run the mission as declared.

---

## 8. Execution Transports

### A. Automated Browser Mode (default)

`PlaywrightAutomatedTransport` drives your already-open Chrome over CDP. On every turn:

1. **Tab lookup** — finds the active tab for the current participant's `execution_engine` (`chatgpt.com`, `gemini.google.com`, or `claude.ai`).
2. **Attachment sync** — uploads/removes reference files and images to match the current injection state.
3. **Composer-limit check** — decides whether the prompt fits inline or must spill to an attached file (§9) *before* typing anything.
4. **Prompt delivery** — types the prompt into the composer and clicks Send (or the platform's keyboard shortcut).
5. **Streaming detection** — polls the DOM until the response stabilizes (the "Stop" button reverts, text stops growing).
6. **Harvesting** — extracts the final text, any generated images/file links, and any fenced code blocks tagged with a filename comment (auto-saved under `outputs/` and re-injected as a reference file for subsequent turns).
7. **Limit-error recovery** (§9) — if the platform rejects the message as too long, automatically retries as an attachment, then (Claude only) rolls over to a fresh chat.

### B. Manual Clipboard Mode (`--manual`)

No browser automation. Each turn:
1. The prompt is printed to the terminal **and** copied to your clipboard.
2. You paste it into the model's chat yourself.
3. You paste the model's reply back into the terminal.
4. Type `END` on its own line (or Ctrl+Z/Ctrl+D) to submit a multi-line response.

If a prompt is larger than the target platform's safe paste size, an `[ADVISORY]` line warns you before you paste — the browser itself may auto-convert a large paste into an attachment or reject it outright; consider saving the prompt to a file and attaching it manually.

---

## 9. Composer Limits, Overflow Attachments & Auto-Rollover

Browser chat boxes have real, mostly-undocumented size limits, and `insert_text` (how the automated transport types) bypasses some platforms' own "convert big pastes to an attachment" safety net. FRELAN compensates so a long mission **never halts** on a rejected message — every participant still receives 100% of the content; only the *delivery mechanism* changes.

| Engine | Safe inline chars | Conversation budget | Over budget → |
|---|---|---|---|
| ChatGPT | 9,000 | — (limit is per-message only) | n/a |
| Gemini | 18,000 | — (limit is per-message only) | n/a |
| Claude | 12,000 (shrinks as the chat fills) | ~300,000 chars | **Auto-rollover** to a fresh chat |

**Prompts stay small in the first place.** Each participant talks in its own persistent conversation, and that conversation is the memory: it already holds every prompt it was sent, its own answers, and any file it was given. A turn prompt therefore carries only the *difference* — responses appended since that participant last spoke, plus any reference file it has not seen. A file is inlined once and referenced by name afterwards. The effect is that prompt size stops growing with the discussion: on the 2026-08-19 run prompts grew 1,490 → 35,243 chars over ten turns and eight exceeded the composer; the same transcript now renders flat at 1.5k–7k with none over. A phase can override how much it carries via `phases[].context` ([MISSION-CONTRACT.md](MISSION-CONTRACT.md) §3.3).

**Over the inline limit** — usually just the final synthesis, the one turn that must see the whole mission — delivery walks a ladder, each rung losing more than the one above:

1. **Attachment + short stub.** The full prompt is written to `outputs/prompt_overflow_<participant>_<timestamp>.md` and attached, with a stub telling the model to read it. Used **only when the upload chip is verified as newly added** — every overflow filename shares a truncated head, so a stale chip from the previous turn used to pass this check for a file that never uploaded.
2. **Chunked inline delivery.** The prompt is sent as several composer messages, parts 1..N−1 marked "do not respond yet", the last marked FINAL. Nothing is lost and nothing has to upload successfully.
3. **Truncated inline.** Head and tail kept, the middle dropped behind an explicit marker. Last resort.

**A refusal is not an answer.** If a model replies that it cannot read the attached file, that is a *delivery failure*: the prompt is re-delivered truncated once rather than recorded as the turn. Before this, such refusals were banked as responses — on the 2026-08-19 run three ChatGPT turns were lost that way, including the synthesis, whose refusal was written straight into `recommendation.md`.

**"Message too long" rejection:** detected automatically (a visible error banner), and recovered in order: retry as an attachment → (Claude only, if still rejected) roll over to a brand-new chat → fall back to the manual transport as a last resort. Capped at two recovery attempts per turn so a false-positive detection can't loop forever.

**Auto-rollover (Claude only):** because Claude resends its entire conversation history on every turn, a long-running mission will eventually approach that per-conversation budget. When it does, FRELAN opens a fresh Claude chat and re-uploads the current reference files automatically. A fresh chat remembers nothing, so that participant is also flagged for a **full re-brief** on its next turn — it receives the whole bounded discussion window and its reference files inlined again, instead of a delta into a conversation with no history. Continuity survives the rollover; without the flag, that engine would have been quietly running on partial context for the rest of the mission.

**Tuning per mission:** override any of these defaults via mission `metadata` (see [MISSION-CONTRACT.md](MISSION-CONTRACT.md) §2), e.g.:
```yaml
metadata:
  claude_chat_budget_chars: "250000"
  chatgpt_max_inline_chars: "8000"
```
Invalid override values are ignored with a console warning — they never halt the mission.

---

## 10. Participants, Roles, Interaction & Workflow

### 10.1 Equitable roles and turn order

All four meeting templates (§5, options 1–4) are built to a strict equity pattern, so no engine is structurally favored:

- **Symmetric roles.** Every participant — ChatGPT, Gemini, and Claude if included — is assigned the identical `peer_analyst` role and the identical `required_capabilities` list. No engine is hard-wired as "the proposer" or "the critic."
- **Rotating duties.** Each phase's instructions assign who proposes, who challenges, and who defends — and that assignment swaps between phases, so both engines take every duty over the course of a run.
- **Alternating turn order.** Odd phases run `[chatgpt, gemini]`; even phases run `[gemini, chatgpt]` (Claude, when included, is always appended **last** in every phase — after both, never first, by design). No engine permanently opens or permanently gets the last word.
- **Claude is a full peer, not a special reviewer.** Earlier versions of this project injected Claude only into the final phase as an "independent reviewer." That has been replaced: when included, Claude joins *every* phase with the same peer role and capabilities as the others.

The **legacy debate** (`missions/frelan_debate.yaml`, menu option 5) intentionally keeps its original fixed `proposer`/`critic` roles for back-compatibility — use it only when you specifically want that older shape; prefer option 4 (`strategy_debate.yaml`) for equitable general-purpose debates.

### 10.2 Who takes part: models and agents

A **participant** is whoever takes part in a mission. It is one of two things,
and the contract says which:

- **Model** (`type: model`, the default) — the engine seated as itself. ChatGPT
  taking part as ChatGPT.
- **Agent** (`type: agent`) — a configured worker built *around* a model, with
  its own **standing brief** (`instructions`) that is added to every one of its
  prompts.

The difference from a phase's instructions matters: a phase's instructions apply
to everyone in that phase; a participant's standing brief applies to that
participant in every phase. That is what makes an agent an agent rather than a
model with a different display name.

You do not need agents. Every shipped template seats plain models, and `type`
defaults to `model`, so a contract that says nothing about it means what it
always meant.

A **role** (`peer_analyst`, `critic`, `builder` …) is a responsibility, and it is
independent of the model. The same model can hold different roles in different
missions, and the same role can be held by different models.

> **One conversation per engine.** The browser transport finds a tab by engine,
> so two participants backed by the *same* `execution_engine` would share one
> browser conversation. The contract allows it; the transport cannot yet keep
> them apart. Seat one participant per engine until that changes.

### 10.3 How they work together: interaction

A phase declares `interaction` — how its participants work together. This is a
different question from `context` (how much of the discussion each prompt
carries) and from governance (how the mission is controlled).

| Value | What happens | Status |
|---|---|---|
| `sequential` *(default)* | one participant at a time; each turn sees the turns before it in that round | implemented |
| `parallel` | every prompt is built from the same round-start state and sent before any reply is read, so the engines think at the same time and none sees another's answer from that round | **experimental** |

Two things follow from a parallel round, by construction rather than by asking
the models nicely: the engines genuinely generate at the same time, and none of
them can see another's answer from that round. Replies are recorded in the order
the contract lists the participants — not the order they finish — so a run stays
comparable with itself. If one engine fails to receive its prompt or to answer,
that is recorded and the round carries on without it.

**`context: none` is not the same thing.** It withholds the other participants'
answers while still running one turn after another. The two settings are
independent, and a contract may use either, both, or neither.

**Experimental means:** implemented, unit-tested, and dry-run end to end — but
not yet measured against a live browser. What is unknown is how the chat UIs
behave in a background tab while another tab is being driven. See
[CONCEPTUAL-MODEL.md](CONCEPTUAL-MODEL.md) §7.

Any other pattern you may have heard named — relay, debate, critique, validation
gates, delegation, pipelines — is either already achievable by writing phase
instructions, or deliberately deferred. Their status is listed in
CONCEPTUAL-MODEL.md §6. An interaction the runtime cannot execute is refused
when the contract loads, naming what is supported; it is never quietly run as
something else.

### 10.4 Composing stages: workflow

A workflow is a multi-stage composition — for example Research → Architecture →
Build → Validation. It is expressed **inside one contract**, as data:

```yaml
metadata:
  workflow: research-architect-build   # names the workflow

phases:
  - id: research
    stage: research                    # which stage this phase belongs to
    interaction: parallel
    ...
```

Neither field changes how anything executes. The runtime records them in
`metadata.json` and displays them, and that is all — which is why you can invent
your own stage names freely.

There is **no workflow engine** and no cross-mission chaining. To carry one
mission's result into the next, run the first, then inject its deliverable as a
reference file for the second.

Worked example: `missions/candidates/research_architect_build.yaml`.

---

## 11. Governance Checkpoints

Checkpoints appear at the round interval declared in the contract's `governance.checkpoint_interval` (all four new templates use `1` — a checkpoint after every round). At each checkpoint, the moderator summarizes the discussion and asks you to choose:

| Key | Decision | Effect |
|:---:|---|---|
| `C` | Continue | Proceed to the next round. |
| `V` | Converged | End the discussion now; a final synthesis is generated. |
| `E` | Escalate | Critical deadlock — end immediately, **no** synthesis. |
| `T` | Terminate | Abandon the mission immediately. |
| `F` | Fully Automate | Continue now, then auto-continue through all future checkpoints to natural completion. |
| `P` | Edit Prompt | Open the sub-menu below. |

### The `P` sub-menu

1. Change the main topic/objective on the fly.
2. Inject custom instructions for the upcoming turn (steer a specific participant).
3. Manage/inject reference files.
4. Manage/inject reference images.
5. Clear all overrides back to the mission's defaults.

After editing, you return to the `C`/`V`/`E`/`T`/`F` choice.

---

## 12. Output Measurement: Peer Scoring & Evidence

Structured phases and rotating duties make the *process* fair, but say nothing about *output quality* — and quality is never assumed here. All four meeting templates set `peer_scoring: "true"` in their metadata, which activates two independent, deterministic measurements:

### Reciprocal peer scoring

In the mission's **final phase only**, each participant is instructed to end its response with one scoring block per *other* peer in the phase (never itself):

````
```frelan-scores
target: <participant_id>
evidence_quality: <1-5>
reasoning_depth: <1-5>
actionability: <1-5>
responsiveness: <1-5>
justification: <one line>
```
````

The parser (`frelan/evidence.py`) is tolerant of loose formatting (clamps out-of-range scores, skips malformed values) but **hard-enforces one governance rule**: a self-score is always discarded, even if a model emits one against the instruction.

### Objective metrics

Independently of any score, every run also records deterministic facts straight from the ledger, per participant: turns taken, total/mean response length, citation count (URLs and `[n]`-style markers), and artifacts harvested (files the model produced that were auto-saved and re-injected).

### Where it goes

- **`outputs/evidence.json`** — full per-run detail: every score, every scorer, every metric.
- **`evidence-log.jsonl`** (project root, **not** inside `outputs/`, so it survives the output-dir overwrite) — one compact JSON line appended per mission: timestamp, mission id, meeting type, status, and per-participant score means/turns/citations.

Over enough runs, `evidence-log.jsonl` becomes an empirical record of which engine actually performs well at which meeting type — the evidence that a future capability-routing layer (§14) would consume. **This project deliberately stops at producing evidence.** Nothing here auto-updates trust, confidence, or routing — reviewing the log and deciding what it means is a human governance activity, not something the mission runtime does to itself.

The legacy debate template does not set `peer_scoring`, so it runs exactly as before — `evidence.json` is still written (with empty scores and just the objective metrics), keeping every mission's outputs consistently shaped.

---

## 13. Saved Outputs Reference

Written to the configured output directory (`outputs/` by default) at natural or early completion:

| File | Contents |
|---|---|
| `recommendation.md` | Final synthesized recommendation — only produced on Converge or natural completion. A mission declaring several outputs writes **one file per declared output instead** (e.g. `prd.md`, `technical-blueprint.md`, …), each produced by its own synthesis turn. |
| `checkpoints.md` | Every checkpoint reached and the decision made. |
| `ledger.md` | Full append-only transcript: every prompt, response, checkpoint, and system event (including harvested-artifact and limit-recovery notices). |
| `metadata.json` | Run telemetry: mission id, phases, rounds, turns, checkpoints, `peer_scoring` flag, start/end timestamps. |
| `evidence.json` | Per-participant peer scores + objective metrics (§12). |
| `prompt_overflow_*.md` | Any prompt that exceeded a platform's composer limit, delivered as an attachment instead of typed inline (§9). |

At the **project root** (survives output-dir overwrites):

| File | Contents |
|---|---|
| `evidence-log.jsonl` | One appended line per mission run — the cumulative evidence trail (§12). |

> ⚠️ `outputs/` (or whatever `-o` you passed) is **overwritten every run**. Pass a fresh `-o <folder>` to keep a run's artifacts around; `evidence-log.jsonl` is the one artifact designed to survive regardless.

---

## 14. Authoring Custom Missions

Any of the four templates in `missions/` is a good starting point — copy one and edit. The full schema (required top-level keys, capability taxonomy conventions, validation rules) lives in [MISSION-CONTRACT.md](MISSION-CONTRACT.md); this section covers what a manual author actually needs to touch:

- **`capabilities`** — declare the capability IDs your participants require (free-form strings; the equitable templates use taxonomy-style dotted IDs like `reasoning.strategic`, `research.web`).
- **`participants`** — give every engine you want in the room the *same* `role` and `required_capabilities` if you want equity (§10); vary `participant_ids` order per phase to rotate who speaks first.
- **`phases`** — each phase's `instructions` is where duty-rotation actually lives (who proposes vs. challenges this round).
- **`phases[].context`** (optional) — how much of the discussion this phase's prompts carry: `auto` (default), `none`, `delta`, or `full`. Use `none` for a genuinely independent phase — one whose `instructions` say "do not reference the other peer". Before this field existed, such a phase was handed the very answers it was told to ignore. See [MISSION-CONTRACT.md](MISSION-CONTRACT.md) §3.3.
- **`governance.checkpoint_interval`** — how often you're asked to intervene; `1` for tight control, higher for a longer autonomous run.
- **`metadata`** (all optional, all strings) — `meeting_type` (label only), `summary` (one or two sentences on what the meeting type is *for*, shown under the meeting-type menu beside the objective), `peer_scoring: "true"` to enable §12, and any `<engine>_max_inline_chars` / `<engine>_chat_budget_chars` override from §9.
- **`outputs`** — at minimum one entry; its `filename`/`title` become the deliverable's name and heading. Declare **two or more** and the runtime runs one synthesis turn per output, writing one file each — that is how a document *set* is produced, and it is the only way past the one-reply ceiling on a single deliverable. When you do, put each document's section skeleton in its `description` (as a literal `|` YAML block): the synthesis prompt carries the description, never the phase instructions.

`load_mission()` validates the whole contract up front and reports **every** problem at once (not just the first) — fix a bad contract in one pass rather than iterating error-by-error.

`missions/frelan_mission_contract_v2.yaml` is kept as a **design sketch only** — it does not conform to the schema above and will not load; it predates and partially inspired the equitable-template design in §10.

---

## 15. Testing

```bash
python -m pytest                              # full suite
python -m pytest tests/test_ledger.py         # one file
python -m pytest tests/test_ledger.py::test_name -v   # one test
```

Tests mirror modules one-to-one under `tests/`, with shared fixtures in `tests/conftest.py`. `pyproject.toml` sets `pythonpath = ["."]`, so no editable install is required.

---

## 16. Troubleshooting

**"Could not connect to Chrome via CDP"**
1. Confirm Chrome was launched with `--remote-debugging-port=9223` (§3).
2. Close any other running Chrome instance that might be holding the port.
3. Check the port matches — pass `--cdp-url http://localhost:<port>` if you used a non-default one.

**"Could not locate active browser tab"**
Make sure you have an open, logged-in tab to `chatgpt.com`, `gemini.google.com/app`, and (if included) `claude.ai` — the transport matches tabs by domain in the URL.

**"Send button did not enable in time"**
Large attachments sometimes take a few seconds to finish uploading. FRELAN waits for the Send button to re-enable before clicking; if it times out anyway, it warns and continues (or you can click Send yourself in the browser).

**A message was rejected as "too long"**
This should self-heal (§9) — the transport retries as an attachment and, on Claude, rolls over to a fresh chat if the whole conversation is full. If you see this rejection persist, the platform's actual wording may not match the detector's known phrases; check `outputs/ledger.md` for the `[LIMIT ERROR]`/`[ROLLOVER]` system entries and consider tightening the relevant `metadata` override (§9).

**A turn seems wedged — response visible in the browser but the terminal keeps "Waiting…"**
The transport now does automatically what a manual F5 does: after ~30 seconds of zero harvest progress it refreshes the tab **without re-delivering the prompt** (capped at 2 refreshes per turn, logged as `[REFRESH]`). This is distinct from the web-lag path, which reloads *and re-sends* when the message appears never to have been sent at all.

**The mission was interrupted (crash, Ctrl+C, machine restart)**
Nothing is lost. Every completed turn was already persisted to `<output-dir>/ledger.jsonl` the moment it finished. Run `python main.py --resume` (add `-o <dir>` if the run used a custom output directory): the mission, topic, reference files, and Claude-inclusion are restored from the record, completed rounds are replayed into context (never re-sent to the models), and execution continues at the exact next speaker. Browser side needs nothing special — prompts are self-contained, so resuming into fresh chats works the same way rollover does.

**Peer scores look suspiciously uniform (e.g., every score is a 4)**
Reciprocal scoring is a rubric prompt, not a guaranteed calibration — models can cluster scores. Treat `evidence-log.jsonl` as a *starting* signal across many runs, not a verdict from one. If clustering is a persistent problem, tighten the scoring instructions in `frelan/prompt_renderer.py` to demand differentiation across axes.

---

## 17. Related Documents

| Document | Purpose |
|---|---|
| [QUICK-REFERENCE.md](QUICK-REFERENCE.md) | One-page cheat sheet: commands, checkpoint keys, meeting types, output files. |
| [DASHBOARD.md](DASHBOARD.md) | The web dashboard: its eight views, run IDs and history, governance from the browser. |
| [CONCEPTUAL-MODEL.md](CONCEPTUAL-MODEL.md) | Model, Agent, Participant, Role, Capability, Interaction, Workflow, Governance — what each word means here, and the status of every interaction pattern (implemented / experimental / deferred). |
| [MISSION-CONTRACT.md](MISSION-CONTRACT.md) | The full mission file schema and validation rules. |
