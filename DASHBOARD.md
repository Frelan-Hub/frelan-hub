# The Dashboard — AI-Conductor B Control Plane

The dashboard is a **control plane over the runtime**, not a second runtime. A
run started from it is the same `main.py` subprocess a Founder would start in a
terminal, given an explicit `-o`. It changes no contract, no prompt, and no
transport behaviour.

```bash
run_ui.bat
```

Or directly:

```bash
uv run streamlit run streamlit_app.py
```

`.streamlit/config.toml` sets `headless = true`, so a server started by other
means never seizes a browser tab. `run_ui.bat` overrides that setting on the
command line, because a launcher started by a human is exactly the case that
should open one — Streamlit opens that tab itself (at `http://localhost:8501`, or the next free port if 8501 is taken), once the
server is actually accepting requests. `run_ui.bat` also pulls any upstream
changes before starting; set `AICB_NO_UPDATE=1` to skip that.

---

## 1. Layout

A persistent header, a navigation rail, and one view at a time.

```
┌──────────────────────────────────────────────────────────────┐
│ FRELAN │ AI-CONDUCTOR B          MISSION #0043 · ● RUNNING  ⚙ │
├───────────────┬──────────────────────────────────────────────┤
│ MISSION       │  MISSION STATUS                              │
│   Overview    │  ┌────────┐┌────────┐┌───────┐┌────────┐     │
│   Setup       │  │RUNTIME ││ AGENTS ││ TURNS ││ ROUNDS │     │
│   Agents      │  └────────┘└────────┘└───────┘└────────┘     │
│               │  CURRENT MISSION                             │
│ RUN           │  ┌──────────────────────────────────────┐    │
│   Execution   │  │ objective …          [ Start mission ]│    │
│   Governance  │  └──────────────────────────────────────┘    │
│   Outputs     │  GOVERNANCE · LIVE AGENTS · DELIVERABLES      │
│ SYSTEM        │  ▸ Live meeting workspace                    │
│   Logs        │  ▸ Technical logs                            │
│   History     │                                              │
└───────────────┴──────────────────────────────────────────────┘
```

**The header is on every view.** It carries the run identifier and the runtime
state, because the one question a control plane must never make you navigate to
answer is "what is running right now, and which run is it". The gear holds the
runtime connection: Chrome CDP URL, output root, and the live-refresh interval.

---

## 2. The views

| View | Answers |
|---|---|
| **Overview** | What is the state of the mission right now? Status cards, the mission and its Start control, the mission's *shape* (meeting type, interaction, workflow), where the run currently is (phase, stage, interaction, last speaker), the pending governance decision, live agents, deliverables, then the transcript and console as collapsed panels. |
| **Setup** | Which meeting type, what interaction each phase uses, which workflow (if any), what objective, what reference files and images, what run configuration. Shows the exact command line the run will use. Reference context sits directly under the objective it supports rather than in a view of its own; uploads are written to `inputs/` immediately, so they survive navigating away. |
| **Agents** | Who is seated — a **participant** is either a MODEL seated as itself or an AGENT built around one. Name, type, model, role, capabilities, transport, standing brief, plus each engine's live turn statistics and transport limits. |
| **Execution** | Where the run is (phase, stage, interaction, who is working), the real execution topology, the mission timeline, and the full live transcript. |
| **Governance** | The checkpoint decision board, the contract's declared policy and phases, the decisions recorded so far, and the run's `evidence.json`. Interaction is listed with each phase for completeness and labelled as *not* governance. |
| **Outputs** | The declared final-answer files, with download and copy; the provenance of those files (meeting type, workflow, interaction, synthesiser, the phases behind them); plus every artifact on disk. |
| **Logs** | Raw console output from the Conductor subprocess, downloadable. Deliberately technical, and kept that way. |
| **History** | Every run under the output root — those launched here and those launched from a terminal — each labelled with what it actually was: meeting type, workflow, interaction, models. Open one to inspect its artifacts without disturbing the run that is executing. |

The transcript is **state-aware**: it opens itself while a mission is running or
has just finished, and folds away when idle. The console stays collapsed — it is
diagnostic detail, kept as diagnostic detail.

**Eight views, and eight is the number.** Interaction, models, capabilities and
workflows were integrated into the views that already answer the question they
belong to, rather than added as pages of their own. A concept nobody can find
because it has its own tab is worse served than one shown where it is needed.

### Two rules the views obey

1. **Never draw a topology the runtime is not executing.** A sequential phase is
   drawn as `A → B → C`; a parallel phase as a fan into the round boundary. The
   drawing is read from the phase's declared `interaction`, and
   `tests/test_ui_dashboard.py` asserts a sequential contract is never drawn as
   parallel. For a parallel phase there is no "next speaker" and none is shown —
   the whole round is in flight.
2. **Never present an unimplemented pattern as functional.** The interaction
   list comes from `frelan.enums.INTERACTION_SUPPORT`, the same table the loader
   validates against, so the dashboard cannot offer something the runtime would
   refuse. `parallel` is labelled **experimental** wherever it appears, because
   it has no live browser run behind it yet (CONCEPTUAL-MODEL.md §7).

Interaction is **not** a run-time switch. It is declared per phase by the
contract, which is the authority; Setup displays it and does not override it.

History and Outputs read a finished run's **own** `metadata.json` in preference
to whatever contract the dashboard currently points at — which may be a
different meeting type entirely. A run recorded before these fields existed
shows nothing rather than a reconstructed guess: the list exists for
evidence-based comparison, which a plausible-looking invention would poison.

---

## 3. Runs, identity, and persistence

Each run started from the dashboard gets:

- **its own directory** — `outputs/run-<UTC-timestamp>/`, allocated with the
  runtime's own naming helper and passed as an explicit `-o`. Nothing is ever
  deleted to make room for a new run;
- **a run ID** — a monotonic integer shown as `#0043`, allocated from an
  append-only registry at `outputs/.runs.jsonl` so it survives an app restart;
- **an entry in the resume pointer** — `outputs/.last-run` is updated, so
  `python main.py --resume` with no `-o` resumes a run started from the
  dashboard.

`outputs/.runs.jsonl` is append-only: a status change appends a superseding
record rather than rewriting the original. **History** folds the registry
together with a scan of `run-*` directories, so a run started in a terminal —
which has no registry entry, and so no `#` number — is still listed, read from
its own `metadata.json`.

---

## 4. Governance from the dashboard

When the Conductor blocks on a checkpoint, the decision board appears on
**Overview** and **Governance** with the same five choices the terminal offers:
Continue, Converged, Escalate, Terminate, Fully automate. Each writes exactly one
key to the subprocess's stdin.

`[P] Edit Prompt` is still terminal-only. Mid-run topic, instruction, and file
overrides go through the terminal checkpoint sub-menu.

A pending checkpoint is detected by scanning the captured console output
backwards for the runtime's own menu line and checking that no decision was sent
after it — so a checkpoint stays visible however much the runtime prints, and an
answered one never reappears.

---

## 5. Refresh behaviour

Only live regions refresh, and only while a run is live. Each is a Streamlit
fragment on its own timer (default one second, adjustable under the gear); the
page body is not re-executed, so the mission library is not rescanned and the
ledger is not re-parsed on every tick. The ledger is read **incrementally** —
only bytes appended since the last read, stopping at the last complete line so a
half-written record is picked up on the next pass rather than skipped.

One full-page rerun happens per run, when the Conductor exits, so the controls
stop claiming the mission is still running.

---

## 6. Code layout

`streamlit_app.py` wires the control plane and nothing else. Everything it wires
lives in `ui/`:

| Module | Role |
|---|---|
| `ui/runs.py` | Run registry, run IDs, incremental ledger reads, artifact discovery. **No Streamlit import** — this is the part worth testing. |
| `ui/library.py` | Meeting-type menu, contract briefs, roster, governance policy. Also Streamlit-free. |
| `ui/cache.py` | Cached contract reads, keyed on file modification time so an edited template still shows up without a restart. |
| `ui/state.py` | Session state, configuration persistence, console-log drain, checkpoint detection. |
| `ui/theme.py` | CSS, header, status pill, section furniture. |
| `ui/launcher.py` | Starting and stopping the Conductor subprocess. |
| `ui/components.py` | Renderers shared by more than one view. |
| `ui/views.py` | The eight views. |

Two rules keep this honest:

- **Configuration lives in `st.session_state.cfg`, never in a widget key.**
  Streamlit discards the state of any widget it did not render on the current
  run, so with eight views a setting typed on Setup would vanish the moment
  Agents opened. Widgets mirror into `cfg` through an `on_change` callback and
  the launcher reads `cfg`.
- **Start, Stop, and every checkpoint decision are `on_click` callbacks.** A
  callback fires once per click. Running these in the page body — and calling
  `st.rerun()` from inside them — is what allowed one click to launch two
  Conductors against the same Chrome session, or write a decision key twice.

---

## 7. Related documents

| Document | Contents |
|---|---|
| [USER-GUIDE.md](USER-GUIDE.md) | The full manual: setup, run modes, meeting types, checkpoints, outputs. |
| [QUICK-REFERENCE.md](QUICK-REFERENCE.md) | One-page cheat sheet. |
| [MISSION-CONTRACT.md](MISSION-CONTRACT.md) | The mission file schema. |
| [CONCEPTUAL-MODEL.md](CONCEPTUAL-MODEL.md) | Model / Agent / Participant / Role / Capability / Interaction / Workflow / Governance, and the status of every interaction pattern. |
