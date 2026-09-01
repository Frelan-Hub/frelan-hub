# AI-Conductor B Runtime — Quick Reference

One-page cheat sheet. For the full manual see [`USER-GUIDE.md`](USER-GUIDE.md);
for the mission file format see [`MISSION-CONTRACT.md`](MISSION-CONTRACT.md).

> Run all commands from the project root.

---

## Commands

**Run the default mission** (`missions/frelan_debate.yaml`)

```bash
python main.py
```

**Run the default mission in fully autonomous mode** (automatically proceeds through all checkpoints to natural completion)

```bash
python main.py --auto
```

**Run a specific mission**

```bash
python main.py missions/my_mission.yaml
```

**Specify an output directory** (an explicit path is used verbatim)

```bash
python main.py missions/my_mission.yaml -o outputs/run2
```

**Resume an interrupted mission** (crash or Ctrl+C — completed rounds are never lost or re-run)

```bash
python main.py --resume                 # follows outputs/.last-run
python main.py --resume -o outputs/run2 # if the run used an explicit -o
```

**Add Claude as an equal third peer** (joins every phase; `--review` and `--high-complexity` are aliases)

```bash
python main.py --claude
```

**Manual clipboard mode** (no browser automation — paste prompts yourself, end each reply with `END`)

```bash
python main.py --manual
```

**Point at a different Chrome debugging port**

```bash
python main.py --cdp-url http://localhost:9223
```

**Inject a topic and reference material without the interactive prompts**

```bash
python main.py --topic "Design a clinic in Cebu" --inject-files "inputs/brief.md, inputs/site.txt"
python main.py --inject-images "inputs/facade.jpg"
```

**Run the optional read-only Capability Discovery stage first** (installs nothing; you approve before launch)

```bash
python main.py --discover
```

**Summarise the accumulated evidence log** (read-only; runs no mission)

```bash
python main.py --report
```

**Delete old transport overflow scratch** (default 14 days; never touches ledgers, deliverables, evidence, or harvested artifacts)

```bash
python main.py --prune-spills 14
```

---

## Where output goes

Each run without an explicit `-o` gets its own directory:

```
outputs/
  .last-run                  <- pointer to the most recent run
  run-20260801T024010Z/      <- one directory per run; history is kept
    ledger.md  ledger.jsonl  checkpoints.md
    metadata.json  evidence.json
    <one file per declared output>
```

`--resume` with no `-o` follows `.last-run`. An explicit `-o` is always used
verbatim, so existing launchers and the Streamlit UI are unaffected.

---

## Meeting Types

Running `python main.py` **without a mission path** in an interactive shell
opens the meeting-type menu before anything else. The menu is a scan of
`missions/`, so the numbering shifts as templates are added — pick by name:

The folder a template sits in is its **category**, shown as the `[group]` tag.
`candidates/` entries are runnable but unproven — they are how a template earns
promotion ([missions/LIBRARY.md](missions/LIBRARY.md)).

| Template | Use for |
|----------|---------|
| `missions/distill/general_inquiry.yaml` | **General inquiries.** Independent answers → reconcile → one consolidated answer. 4 rounds; the light-weight default for "answer this" rather than "design this" |
| `missions/shape/app_planning.yaml` | App pre-planning: requirements → architecture & schema proposals → cross-challenge → build plan |
| `missions/candidates/prd_blueprint.yaml` | **Build documentation for an app or tool.** Product framing → independent scope & stories → independent technical proposals → cross-challenge → blueprint merge. Produces **four** files — `prd.md`, `technical-blueprint.md`, `build-plan.md`, `agent-brief.md` — the last of which is written to be pasted into a coding agent. Four synthesis turns, so allow extra time at convergence |
| `missions/candidates/brainstorm.yaml` | Ideas: independent divergence → clustering → criteria & ranking → idea backlog |
| `missions/candidates/premortem.yaml` | Risk: assume it already failed → root causes → blind spots → risk register |
| `missions/candidates/document_review.yaml` | Review a supplied document or design: independent critique → reconcile → severity-ordered findings |
| `missions/candidates/red_blue_review.yaml` | Harden a supplied artifact: Red attacks, Blue defends, duties swap, joint findings. **Cannot seat a third peer** — it declares `claude_peer: unsupported`, and the runtime refuses the injection |
| `missions/candidates/tradeoff_adr.yaml` | Decisions: options → weighted criteria → blind scoring → reconcile → ADR |
| `missions/candidates/adversarial_collaboration.yaml` | Contested questions: opposing positions → steelman → agree the decisive test in advance → joint report |
| `missions/candidates/parallel_lenses.yaml` | One subject, one shared lens per phase: facts → risks → benefits → alternatives → assessment |
| `missions/candidates/research_deepdive.yaml` | Research: independent sourced findings → cross-examination → decision brief |
| `missions/candidates/website_design.yaml` | Website/UX: concept directions → design critique → content/IA & stack → direction |
| `missions/candidates/frontier_architecture_charette.yaml` | Long-form architecture charette |
| `missions/candidates/workspace_preparation.yaml` | Workspace/environment preparation planning |
| Enter | `missions/frelan_debate.yaml` — legacy debate (unchanged, back-compat default) |

Retired: `strategy_debate` — absorbed by `tradeoff_adr`, kept on disk as
`missions/strategy_debate.yaml.retired` (the scan does not match it).

**Custom meeting types.** Drop a valid contract in `missions/custom/` and it is
listed automatically as `[custom] <name>` — no code change, no registration.
Copy [`missions/custom/TEMPLATE.yaml.example`](missions/custom/TEMPLATE.yaml.example)
to start; see [`missions/custom/README.md`](missions/custom/README.md). At the
menu prompt you can also type a **path** (or a bare name under
`missions/custom/`) instead of a number, for a contract kept outside the
library. An invalid contract is skipped by the menu rather than crashing it;
validate with:

```bash
python -c "from frelan.mission_loader import load_mission; print(load_mission('missions/custom/my_meeting.yaml').name)"
```

Subfolders of `missions/` are scanned one level deep and tagged with the folder
name; `missions/pre-planning/` is excluded because it holds the `--discover`
stage, which is not a meeting type.

- All templates are **equitable**: both engines share the same `peer_analyst`
  role and capabilities, duties rotate per phase, and turn order alternates so
  no engine always speaks first or last.
- The menu then asks **"Include Claude as a third peer?"** — `y` adds Claude to
  every phase as an equal peer, speaking after ChatGPT and Gemini (same effect
  as the `--claude` flag, which skips the question).
- The template defines the *structure*; the Topic Injection prompt that follows
  sets the *subject*. Passing an explicit mission path skips the menu entirely.
  Most "custom" runs need only a topic + reference files on a shipped
  template — author a new meeting type when the *choreography* differs.
- `missions/frelan_mission_contract_v2.yaml` is a design sketch, not a runnable
  contract.
- **Output measurement:** every shipped template sets `peer_scoring: "true"`, so
  in the final phase each peer scores the others (1–5: evidence quality,
  reasoning depth, actionability, responsiveness; never itself). Scores +
  objective metrics (turns, citations, artifacts) land in
  `outputs/evidence.json`, and one summary line per run is appended to
  `evidence-log.jsonl` at the project root — over time this shows which engine
  actually performs per meeting type, instead of assuming model strength.

---

## Interaction, Roles, and Workflow

Full definitions in [CONCEPTUAL-MODEL.md](CONCEPTUAL-MODEL.md).

| Word | Contract field | One line |
|---|---|---|
| Model | `assigned_engine.execution_engine` | the intelligence engine |
| Agent | `participants[].type: agent` + `instructions` | a configured worker around a model |
| Participant | `participants[]` | who takes part — a model or an agent |
| Role | `assigned_engine.role` | its responsibility; independent of the model |
| Capability | `required_capabilities` | what it can do |
| Interaction | `phases[].interaction` | how the participants work together |
| Workflow | `metadata.workflow` + `phases[].stage` | how stages compose (data only) |
| Governance | `governance{}` | how the mission is controlled |

**Interactions the runtime can execute:**

| Value | Behaviour | Status |
|---|---|---|
| `sequential` *(default)* | one turn at a time, each seeing the ones before it | implemented |
| `parallel` | all prompts delivered before any reply is collected — the engines generate at the same time and none sees another's answer from that round | **experimental** (unit-tested; no live browser run yet) |

`context: none` is context isolation, **not** parallelism. The two fields are
independent.

Anything else — relay, debate, critique, validation gates, delegation,
pipelines — is either already expressible as phase instructions or deferred.
Their status is listed in CONCEPTUAL-MODEL.md §6. An unsupported value fails at
load time, naming what is supported.

Worked example: `missions/candidates/research_architect_build.yaml`.

## Mission Workflow

```
Start
  ↓
Prompt generated
  ↓
Prompt copied to clipboard
  ↓
Paste into AI (the model named in "PROMPT FOR:")
  ↓
Copy response
  ↓
Paste into terminal
  ↓
Type END on its own line
  ↓
(at intervals) Checkpoint  →  C / V / E / T
  ↓
Repeat
  ↓
Outputs generated
```

> **Note:** A checkpoint does not appear after every turn — only at the round
> intervals set by the mission's `checkpoint_interval`.

---

## Checkpoint Keys

| Key | Meaning   | Ends mission? | Recommendation? |
|-----|-----------|---------------|-----------------|
| `C` | Continue  | No            | —               |
| `V` | Converged | Yes           | Yes             |
| `E` | Escalate  | Yes           | No              |
| `T` | Terminate | Yes           | No              |
| `F` | Fully Automate | No       | At completion   |
| `P` | Edit Prompt | No          | —               |

* **`F` (Fully Automate):** Dynamically switches the session to fully autonomous mode. It answers `CONTINUE` for the current checkpoint and automatically bypasses all subsequent checkpoints to run the mission to its natural completion, where the recommendation is generated.
* **`P` (Edit Prompt):** Opens an interactive prompt-editing sub-menu. Allows you to:
  1. **Change the main topic** (Mission Objective) on the fly.
  2. **Inject custom instructions** / context override for the upcoming turn.
  3. **Manage/inject reference files** (reads files like code/text and embeds them as markdown code blocks).
  4. **Manage/inject reference images** (embeds multi-modal design rendering/file image context).
  5. **Clear all custom overrides** (reverts topic, custom instructions, files, and images to default).
  Once completed, the menu returns to the checkpoint selection so you can choose how to proceed (`C`, `V`, `E`, `T`, `F`).

Input is case-insensitive; any other key re-prompts.

---

## Dynamic Startup Topic, File, & Image Injection

When launched in an interactive shell, the interpreter prompts you at startup to configure:
1. **Custom Topic/Objective:** Overrides the default mission contract topic.
2. **Reference Files:** Prompts for comma-separated file paths. Reads and structures them as formatted markdown attachments in all participants' prompts.
3. **Reference Images/URLs:** Prompts for comma-separated image paths or descriptions to provide multi-modal reference context.

This gives you absolute control over the debate's design guidelines, architectural parameters, and background context before execution begins!

---

## Composer Limits, Overflow & Rollover (automated mode)

Prompts are typed into each browser's chat box, which has undocumented size
limits. The transport keeps the cycle from ever halting:

| Engine  | Safe inline chars | Conversation budget | Over budget → |
|---------|-------------------|---------------------|----------------|
| ChatGPT | 9,000             | — (per-message)     | n/a            |
| Gemini  | 18,000            | — (per-message)     | n/a            |
| Claude  | 12,000 (shrinks as the chat grows) | ~300,000 chars | auto-rollover to a fresh chat |

- **Prompts carry the difference, not the discussion.** Each engine's own
  conversation is the memory, so a turn prompt embeds only what is new since
  that participant last spoke, and a reference file is inlined once then
  referenced by name. Prompt size stops growing with the transcript (measured:
  1,490 → 35,243 chars became a flat 1.5k–7k). Per-phase override:
  `phases[].context` = `auto` | `none` | `delta` | `full`.
- **Over the inline limit** (usually only the final synthesis) — the delivery
  ladder, each rung losing more than the one above:
  1. attachment + stub, used **only when the upload chip verifies as new**;
  2. chunked inline — N composer messages, parts 1..N−1 marked "do not respond
     yet"; nothing lost, nothing to upload;
  3. truncated head+tail inline, with an explicit marker.
- **A refusal is not an answer:** a reply saying it cannot read the attached
  file is treated as a failed delivery and re-sent truncated once, never
  recorded as the turn.
- **"Message too long" rejection:** detected automatically; the transport
  re-delivers as an attachment, then (Claude) rolls over to a new chat.
- **Rollover:** opens a fresh conversation, re-uploads reference files, and
  gives that participant a **full re-brief** on its next turn (a fresh chat
  remembers nothing, so a delta would strand it).
- **Tuning:** override per mission via `metadata` keys such as
  `claude_chat_budget_chars: "250000"` — see `MISSION-CONTRACT.md`.
- **Manual mode:** prints an advisory when a prompt exceeds the engine's safe
  paste size; browsers may auto-convert big pastes to attachments themselves.

---

## Responses

- End each pasted response with a line containing only `END`
  (or press `Ctrl+Z` then `Enter` on Windows / `Ctrl+D` on macOS/Linux).
- Responses may span multiple lines.

---

## Generated Files (in the output directory)

| File                | Contents                                             |
|---------------------|------------------------------------------------------|
| `ledger.md`         | Full transcript: every prompt, response, checkpoint. |
| `checkpoints.md`    | Each checkpoint and your decision.                   |
| *(one per declared output)* | The mission's deliverables — **only on Converge or natural completion.** A single-output mission gets `recommendation.md` from one synthesis turn; a multi-output mission gets one file per `outputs[]` entry, each from **its own** synthesis turn. |
| `metadata.json`     | Run statistics (status, objective, phases, rounds, turns, checkpoints, timestamps). |
| `prompt_overflow_*.md` | Full prompts that exceeded a browser's composer limit and were delivered as attachments instead. Prune with `--prune-spills`. |
| `evidence.json`     | Per-run peer scores + objective metrics per participant (including per-turn latency). |
| `ledger.jsonl`      | Machine-readable turn-by-turn record, written **as each turn completes** — the source `--resume` reads. |

> `evidence-log.jsonl` (project root, append-only) accumulates one summary line
> per run — including the `run_dir` that produced it, so any accumulated score
> can be traced back to its transcript. Summarise it with `python main.py --report`.

> Runs no longer overwrite each other: each `python main.py` without `-o` gets
> its own `outputs/run-<timestamp>/`. An explicit `-o` **is** reused, so pass a
> new folder if you want to keep an earlier explicit run.
