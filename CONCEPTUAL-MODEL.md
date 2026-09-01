# Conceptual Model — Participants, Interaction, Workflow

> **Status: Canonical (prescriptive) for vocabulary; descriptive for status.**
> **Dated:** 2026-08-28. Supersedes nothing; it names distinctions that were
> previously implicit.
>
> This document defines the words the runtime, the contracts, and the dashboard
> all use, and states — for every pattern it names — whether that pattern is
> **implemented**, **experimental**, **specified**, **conceptual**, or
> **deferred**. Nothing here claims functionality the runtime does not have.
> Where a claim is unproven, it says so and says what would prove it.

---

## 1. The eight words

Eight distinct questions. They are kept separate because collapsing them into
one generic "agent" abstraction is how a system loses the ability to say which
of them changed.

| Word | Question it answers | Where it lives today |
|---|---|---|
| **Model** | *What intelligence runs?* | `participants[].assigned_engine.execution_engine` |
| **Agent** | *What configured worker wraps a model?* | `participants[].type: agent` + `participants[].instructions` |
| **Participant** | *Who takes part?* | `participants[]` |
| **Role** | *What responsibility does it hold?* | `participants[].assigned_engine.role` |
| **Capability** | *What can it do?* | `capabilities[]` + `assigned_engine.required_capabilities` |
| **Interaction** | *How do they work together?* | `phases[].interaction` |
| **Workflow** | *How are activities composed?* | `metadata.workflow` + `phases[].stage` |
| **Governance** | *How is the mission controlled?* | `governance` + checkpoints |

### Model

The intelligence engine: ChatGPT, Gemini, Claude, a local model later. Selected
by `execution_engine`, which the interpreter passes through and never branches
on. **A model does not own a role.** The same model may hold different roles in
different missions, and the same role may be held by different models.

### Agent

A configured worker built around a model — same engine, its own working
identity. Declared as `type: agent` plus an `instructions` **standing brief**
that is rendered into every one of that participant's prompts.

The distinction from a phase's `instructions` is the whole point:

- a **phase's** instructions apply to everyone in that phase;
- a **participant's** instructions apply to that participant in every phase.

An agent is not required. `type` defaults to `model`, and most contracts are
better off with plain model participants.

**Not implemented, deliberately:** tools, persistent memory across runs,
autonomous spawning, agent hierarchies. None has a concrete requirement here.

### Participant

An entity taking part in a mission. Today it is either a **Model** seated as
itself or an **Agent** backed by a model. Human participation is not modelled:
the Founder participates through **governance** (checkpoints, topic injection,
reference files), which is a different relationship and is already implemented.
Adding a human *participant* would need a turn the runtime waits on
indefinitely, and nothing needs it. **Conceptual.**

### Role

The responsibility a participant holds — `peer_analyst`, `proposer`, `critic`,
`builder`, `validator`. A free string, rendered into prompts, never branched on.
It is independent of model identity by construction, and
`tests/test_interaction.py` pins that independence.

### Capability

What a participant *can* do: `research.web`, `reasoning.strategic`,
`architecture`, `implementation.code`, `validation.independent`, `critique`,
`synthesis`. Declared and referenced; unchanged by this work.

Capability is **not** interaction. `validation.independent` is a capability —
something a participant can do. A *validation gate* is an execution pattern —
something a phase does. They are separate axes and must not be merged.

### Interaction

How a phase's participants work together. See §5.

### Workflow

How activities compose into stages. See §4.

### Governance

How the mission is controlled: checkpoint cadence, round caps, the Founder's
Continue / Converged / Escalate / Terminate decision, the declared synthesiser.
Unchanged by this work, and deliberately kept separate from interaction —
changing a phase's interaction changes neither the checkpoint cadence nor any
decision available to the Founder.

---

## 2. The target relationship

```
MISSION  (one Mission Contract)
│
├── WORKFLOW            optional — metadata.workflow + phases[].stage
│
├── PHASE / ACTIVITY    phases[]
│    ├── PARTICIPANTS   phases[].participant_ids → participants[]
│    │    ├── MODEL     assigned_engine.execution_engine
│    │    └── AGENT     type: agent + instructions (still backed by a model)
│    ├── ROLE           assigned_engine.role
│    ├── CAPABILITIES   assigned_engine.required_capabilities
│    ├── INTERACTION    phases[].interaction
│    └── CONTEXT        phases[].context   (how much discussion is carried)
│
├── GOVERNANCE          governance{} + checkpoints
└── OUTPUTS             outputs[]
```

Capability = WHAT · Role = RESPONSIBILITY · Participant = WHO ·
Model = INTELLIGENCE · Agent = CONFIGURED WORKER · Interaction = HOW ·
Workflow = COMPOSITION · Governance = CONTROL

---

## 3. Execution model

The four layers and the inward dependency rule are unchanged. What this work
added is one dispatch point in the run loop:

```
for each round:
    read phases[current].interaction
      ├── "sequential" → run each participant's turn in order   (unchanged)
      └── "parallel"   → render all → deliver all → collect all
    then: round accounting, checkpoint cadence, phase advance   (identical)
```

Round accounting is identical for both. `tests/test_interaction.py` asserts that
a parallel round and a sequential round of the same contract produce the same
`rounds_completed`, checkpoint count, and turn count.

### Why parallel needs no threads

The `Transport` protocol already separates the two halves of a turn:
`deliver_prompt` submits and returns; `collect_response` is what waits. So
delivering every prompt before collecting any reply makes the engines generate
**at the same time**, using the protocol exactly as it was designed, with no
threading, no async, and no transport change.

This matters practically: Playwright's sync API is not thread-safe, so a
thread-based implementation would have meant rewriting the Transport Layer to
buy the same overlap.

### What parallel guarantees

| Property | How it is guaranteed |
|---|---|
| Concurrent generation | every `deliver` precedes the first `collect` |
| No cross-talk in a round | every prompt is rendered from the same round-start ledger |
| Participant identity | each turn is delivered to and recorded against its own participant |
| Deterministic ordering | replies are recorded in declaration order, not arrival order |
| Independent failure | one participant's delivery or collection failure is recorded and the round continues |
| Timeout handling | owned by the transport, per collection, exactly as in a sequential turn |
| Join / synthesis | the round boundary is the join; synthesis is unchanged and still mission-level |

### What parallel does not do

- It does not overlap the *reading* of replies. Collection still polls one tab
  at a time, so a later participant's recorded `duration_seconds` can include
  time it spent finished but not yet read. The ledger note on every parallel
  round says so.
- It does not make a `context: none` phase parallel, and parallel does not
  imply `context: none`. **Context isolation is not parallelism.** They are
  orthogonal fields and a contract sets each on its own.

---

## 4. Meeting model

A meeting type is a Mission Contract in `missions/`. It declares its
participants, their roles and capabilities, its phases, the interaction each
phase uses, its governance, and its outputs. Adding a meeting type is dropping a
`.yaml` in `missions/candidates/` — never editing a `.py` file. That is unchanged.

What is new is that a phase may now say **how** its participants work, not only
who they are and what they are told.

---

## 5. Workflow model

**Implemented as data. There is no workflow engine, and none is to be built.**

A workflow is composed *inside one contract*, as labelled phases:

- `metadata.workflow` — the workflow's name (free text; uses the existing
  metadata map, so this needed no schema change).
- `phases[].stage` — which stage a phase belongs to (free text).

Neither carries execution meaning. The runtime reads them only to record them in
`metadata.json` and to display them. No stage vocabulary is enforced: hard-coding
a stage list would hard-code one workflow into a runtime that is meant to hold
any of them.

**Research → Architect → Build is therefore one possible workflow, not the
architecture.** It ships as `missions/candidates/research_architect_build.yaml`
and there is no R-A-B branch anywhere in the interpreter.

**Cross-mission chaining is deferred.** Composing several *runs* into a pipeline
(one mission's deliverable becoming the next mission's reference file) would
need run chaining, artifact hand-off, and a scheduler. Today the same result is
reached by hand: run a mission, then inject its output as a reference file for
the next. No requirement yet justifies the machinery.

---

## 6. Interaction model

### Status of every pattern named in this project

| Pattern | Status | Where it lives |
|---|---|---|
| **sequential** | **Implemented** | `phases[].interaction: sequential` (the default). The historical behaviour, unchanged. |
| **parallel** | **Experimental** | `phases[].interaction: parallel`. Implemented and unit-tested (`tests/test_interaction.py`) and exercised end to end against a scripted transport. **Not yet evidenced by a live browser run** — see §7. |
| **synthesis** | **Implemented, at mission level** | `governance.synthesiser` + the final synthesis turn(s). It is the mission's terminal step, not a phase interaction, and was not duplicated as one. |
| **debate** | **Specified by contract data** | Expressed today by ordered `participant_ids` + phase `instructions`. `adversarial_collaboration`, `red_blue_review`, `tradeoff_adr` already are debates. No runtime primitive is needed and none was added. |
| **critique** | **Specified by contract data** | As debate. `document_review`, `premortem`, the R-A-B validation stage. |
| **relay** | **Specified by contract data** | A sequential phase with one round and a turn order *is* a relay. Naming it as a separate execution mode would add a label without adding behaviour. |
| **validation_gate** | **Deferred** | Specified in §8 below; deliberately not built. |
| **delegation / handoff** | **Deferred** | Analysed in §9 below. |
| **pipeline** | **Deferred** | Cross-mission composition; see §5. |

### Reading the statuses

- **Implemented** — built, tested, and observed working.
- **Experimental** — built and unit-tested; not yet observed against a live
  browser. The dashboard labels it as such wherever it appears.
- **Specified by contract data** — achievable today by authoring a contract; no
  runtime feature required or wanted.
- **Deferred** — analysed, with a written record of what it would take and why
  it was not built yet.

---

## 7. What is still unproven about `parallel`

Stated plainly, because "experimental" is otherwise a word that means nothing:

1. **Background-tab behaviour is unmeasured.** Delivering to tab B brings B to
   the front while A is still generating. Browser chat UIs are expected to keep
   streaming in a background tab, but this has not been measured here for
   ChatGPT, Gemini, or Claude. If one of them throttles or pauses when hidden,
   parallel would be slower than sequential rather than faster, and would still
   be *correct* — only the benefit would be absent.
2. **The wall-clock saving is unquantified.** No live A/B has been run.
3. **Recorded durations are conservative.** A later participant's
   `duration_seconds` includes polling latency, so parallel turn timings are not
   directly comparable with sequential ones.

**What would settle it:** run
`missions/candidates/research_architect_build.yaml` live against Chrome, and
compare the research stage's wall-clock time and per-turn durations with the
same phase forced to `sequential`. Record it in `evidence-log.jsonl` and cite it
in `missions/LIBRARY.md`. Until then the dashboard says "experimental" and so
does this document.

---

## 8. Validation gate — deferred, with the specification

**Decision: not implemented.** It is written down so the decision is inspectable
rather than lost.

What it would be:

```
builder turn(s) → validator turn → verdict PASS | FAIL
                                     ├── PASS  → phase completes early
                                     └── FAIL  → retry (bounded), then escalate
```

What it would require:

1. A **verdict wire format** parsed out of a model's free text — a third wire
   contract alongside `BEGIN-OUTPUT:` and `frelan-scores`.
2. **Early phase completion**, which the instance and interpreter do not have:
   a phase ends on `max_rounds` today, never on a decision.
3. **Retry accounting** interacting with the round cap and checkpoint cadence.
4. **A governance consequence** — a FAIL that escalates is a governance
   decision, which is currently the Founder's alone.

Why it was not built:

- **No mission needs it.** Every template in the library expresses review as an
  ordinary phase, and the R-A-B contract's validation stage is deliberately a
  review, not a gate. Building the mechanism first and looking for the need
  afterwards adds unnecessary speculative machinery.
- **It cannot be evidenced.** Its whole value is what it does to a real
  discussion, and there is no live run to point at.
- **It is the largest of the candidates.** Four new mechanisms, one of them a
  frozen-contract-class wire format, against `parallel`'s one dispatch point.

It remains a good idea. It should be built when a mission actually wants to stop
on a FAIL — and the first such contract should be authored before the mechanism
is.

---

## 9. Delegation / handoff — deferred, with the finding

**Finding: the existing envelope already carries most of what a delegation
needs, and no message bus is required.**

A delegation from A to B needs the receiver to understand the task, the relevant
context, the expected output, the constraints, who originated it, and where in
the mission it sits. Of those:

| Needed | Carried today by |
|---|---|
| task | the phase objective + instructions |
| relevant context | the discussion window, budgeted by `phases[].context` |
| originating participant | the ledger — every turn is attributed |
| phase / workflow stage | `phases[].id`, `phases[].stage` |
| expected output | **partially** — `outputs[].description`, but only at synthesis |
| constraints | **not explicitly** — carried as prose in instructions |

So a handoff *is* expressible today: an ordered phase whose instructions say
"take X from the previous stage and produce Y". The R-A-B contract does exactly
that between stages.

What is genuinely missing is a **structured** expected-output-and-constraints
envelope for a mid-mission turn. That is a real gap, and it is small. It is
deferred because nothing has yet failed for want of it — prose instructions have
been sufficient in every template — and because adding a second, structured way
to say what a phase wants would compete with the instruction text rather than
replace it.

**A message bus is not required and should not be added.** The ledger is already
the append-only record every participant's prompt is derived from.

---

## 10. Automatic model selection (the "Layer 2.5 Conductor") — deferred

Automatic capability-to-model routing (specifying a Conductor that resolves
role/capability requirements to a model at runtime) was evaluated:

- The contract **already** binds explicitly, and explicit selection must remain
  available in any design.
- Automatic selection needs a registry of models with capability claims.
  Treating model mapping as data rather than a rigid routing gate avoids
  deadlocks and incumbency lock-in.
- There are **three** browser engines and no evidence base for choosing between
  them. Selecting among three known engines is a decision the Founder can make
  in a line of YAML, and does today.

**Decision: not implemented.** The previous Layer 2.5 design is not to be built
as specified. If automatic selection is wanted later, the smallest sufficient
version is a resolver that maps `required_capabilities` to an engine using a
plain override table with a default fallback — data, not architecture, and
behind the existing contract boundary so an explicit `execution_engine` always
wins.

---

## 11. Known limitation — one conversation per engine

**The browser transport finds a tab by engine domain, not by participant**
(`playwright_auto._get_page_for_participant`). Two participants backed by the
same `execution_engine` therefore share one browser conversation and would
interleave in it.

This constrains the hybrid coordination the conceptual model otherwise permits:

```
Research Agent A → Gemini     ✅ distinct engines, works today
Research Agent B → ChatGPT    ✅
Architect Agent  → Gemini     ❌ shares Gemini's conversation with Agent A
```

The **contract layer** places no such restriction, and it is right not to: two
participants on one engine is a valid thing to declare, and the loader accepts
it. The limitation is the transport's, and it is recorded here and shown on the
dashboard's Agents view rather than hidden.

Lifting it would mean addressing a conversation per participant (a tab or chat
URL per participant id) instead of per engine. That is a transport change, not
an architecture change, and it is not needed until a contract wants it.

---

## 12. Sweet Spot review

Every abstraction added by this work, against the same six questions.

### `phases[].interaction`

- **Why does it exist?** Nothing could say *how* a phase's participants work
  together; the runtime had exactly one execution pattern and no way to name it.
- **What capability does it enable?** Concurrent generation across engines.
- **Could the existing system have done this?** No. `context: none` isolates
  context but still runs one turn after another.
- **Agnostic?** Yes — a free string validated against a runtime vocabulary; no
  provider, model, or transport is named.
- **Smallest sufficient?** One optional field, one dispatch point in the run
  loop, no transport change.
- **New coupling?** The interpreter reads one contract field. It already reads
  several.

### `phases[].stage` + `metadata.workflow`

- **Why?** A multi-stage mission could not say which stage a phase belonged to,
  so neither the dashboard nor the run record could report it, and runs could
  not be compared by stage.
- **Capability?** Workflows expressible and comparable as data.
- **Could the existing system?** `phase.name` communicates a stage to a human
  but is not machine-readable or comparable across contracts.
- **Agnostic?** Yes — free text, no enforced vocabulary, no engine.
- **Smallest sufficient?** `workflow` needed *no* schema change (existing
  metadata map). `stage` is one optional string with zero execution semantics.
- **New coupling?** None. Nothing in the runtime branches on either.

### `participants[].type`

- **Why?** The dashboard is required to distinguish a model seated as itself
  from an agent built around one, and inventing that distinction by inference
  (guessing from display names) would be fragile and implicit.
- **Capability?** Honest reporting of what a participant is.
- **Could the existing system?** Only by inference. An explicit optional field
  is cheaper and truthful.
- **Agnostic?** Yes.
- **Smallest sufficient?** One optional string with a default that preserves
  every existing contract's meaning.
- **New coupling?** None — declarative, never branched on.
- **Honest note:** this is the weakest of the four. It buys clarity, not
  behaviour. It earns its place only because the alternative was inference.

### `participants[].instructions`

- **Why?** Per-participant standing guidance had nowhere to live. Phase
  instructions apply to everyone in the phase.
- **Capability?** Two participants on the same engine with different working
  identities — which is what makes an agent an agent rather than a renamed
  model.
- **Could the existing system?** No.
- **Agnostic?** Yes.
- **Smallest sufficient?** One optional string, budgeted like every other
  repeating prompt section.
- **New coupling?** The renderer reads one more contract field.

### Deliberately not added

`Agent` as a class; a tool registry; agent memory; a workflow engine; a
validation-gate mechanism; a delegation envelope; a capability-to-model
resolver; a message bus; new dashboard pages. Each was considered, and each
failed at least one of the six questions — most often the first.

---

## 13. Where to look

| Question | File |
|---|---|
| Contract schema and field semantics | [MISSION-CONTRACT.md](MISSION-CONTRACT.md) |
| The dashboard's structure | [DASHBOARD.md](DASHBOARD.md) |
| Interaction behaviour, as tests | `tests/test_interaction.py` |
| A workflow, as a contract | `missions/candidates/research_architect_build.yaml` |
