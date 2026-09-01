# Mission Contract — Canonical Specification

This document is the **authoritative specification** of the FRELAN Mission
Contract. The Python dataclasses in [`frelan/mission_contract.py`](frelan/mission_contract.py)
**implement** this specification; they do not define it. When this document and
the code disagree, this document is correct and the code is a bug.

---

## 1. What a Mission Contract is

A Mission Contract is an **immutable, declarative** description of *what* a
mission is — never *how* the engine executes it. It belongs to the **Mission
Layer**. It contains no execution mechanics: no loops, no state, no
model-specific or transport-specific behaviour.

All runtime state (current phase, round, status, ledger, checkpoints) lives in
the separate **Mission Instance** and is the single authoritative record of a
run. The contract itself never changes once loaded.

### Where contracts live

A contract file **is** a meeting type. `missions/` (and one level of subfolders,
excluding `pre-planning/`, which holds the `--discover` stage) is scanned at
startup to build the meeting-type menu, so authoring a meeting type is adding a
file — there is no registry and no generator. Founder-authored contracts belong
in `missions/custom/`; they are listed as `[custom] <name>`, and the menu also
accepts a typed path for a contract kept elsewhere. A file that fails the
validation rules in §5 is omitted from the menu rather than breaking it.

---

## 2. Schema

A contract is authored as YAML or JSON. Field-by-field:

```yaml
id: string                      # REQUIRED, unique mission identifier
name: string                    # REQUIRED, human-readable mission name
objective: string               # REQUIRED, the mission's goal

metadata:                       # OPTIONAL, flat map<string,string>
  author: string
  version: string
  # What this meeting type is FOR, in a sentence or two — displayed under the
  # meeting-type menu next to `name` and `objective` (`objective` states the
  # goal; `summary` says when to reach for it). Required of every library
  # template by tests/test_mission_library.py; optional for a one-off contract.
  summary: string
  # Composer-limit overrides (all OPTIONAL, integer strings). The automated
  # transport types prompts up to <engine>_max_inline_chars into the browser
  # composer; anything larger travels as an attached .md file instead.
  # <engine>_chat_budget_chars caps a whole conversation before the transport
  # rolls over to a fresh chat (only meaningful for engines that resend their
  # history every turn, e.g. claude). Invalid values warn and fall back to the
  # adapter defaults (chatgpt 9000/-, gemini 18000/-, claude 12000/300000).
  # Peer scoring (OPTIONAL). "true" makes the renderer append a Contribution
  # Scoring rubric to final-phase prompts: each participant scores the others
  # (1-5 on evidence_quality / reasoning_depth / actionability / responsiveness,
  # never itself). Parsed scores + objective metrics are written to
  # outputs/evidence.json and appended to evidence-log.jsonl (project root).
  peer_scoring: string               # "true" to enable
  # Third-peer restriction (OPTIONAL). "unsupported" makes the runtime REFUSE
  # to inject Claude as an extra peer (--claude / --review / --high-complexity,
  # the CLI menu question, and the dashboard toggle). The injection appends a
  # peer to EVERY phase, so a contract whose phases seat single peers holding
  # opposed duties — Red/Blue — is broken by it. Declare it there; anything
  # else, and any contract that omits the key, permits the peer. The run is
  # reported and continues with the contract's own participants; it never
  # aborts, and the refusal is recorded in the run metadata as
  # claude_injected: false.
  claude_peer: string                # "unsupported" to refuse the injection
  # Workflow (OPTIONAL). Names the multi-stage composition this contract
  # expresses; pairs with phases[].stage. Free text — the runtime records and
  # displays it and never branches on it, so naming a workflow constrains
  # nothing. See CONCEPTUAL-MODEL.md §5.
  workflow: string                   # e.g. "research-architect-build"
  # Refresh policy (all OPTIONAL, integer strings; one schema for every
  # engine). Governs the transport's two reload behaviours: the F5-equivalent
  # tab refresh (no re-delivery — fires only when nothing is progressing) and
  # the lag reload (re-delivers — only when a message appears never sent).
  # Defaults: 30 / 2 / 35 / 2. See contracts/communication-contract.yaml.
  refresh_stalled_seconds: string    # e.g. "30"
  refresh_max_per_turn: string       # e.g. "2"
  refresh_lag_seconds: string        # e.g. "35"
  refresh_max_redeliveries: string   # e.g. "2"
  chatgpt_max_inline_chars: string   # e.g. "9000"
  gemini_max_inline_chars: string    # e.g. "18000"
  claude_max_inline_chars: string    # e.g. "12000"
  claude_chat_budget_chars: string   # e.g. "300000"

capabilities:                   # REQUIRED (may be empty): catalogue of abilities
  - id: string                  #   REQUIRED, unique capability id
    description: string         #   REQUIRED

participants:                   # REQUIRED, >= 1 participant
  - id: string                  #   REQUIRED, unique participant id
    display_name: string        #   REQUIRED, label used when addressing it
    type: string                #   OPTIONAL, "model" (default) | "agent"
    instructions: string        #   OPTIONAL, this participant's STANDING brief,
                                #   rendered into every one of its prompts
    assigned_engine:            #   REQUIRED, the participant's binding
      role: string              #     REQUIRED, its function in the discussion
      required_capabilities:    #     REQUIRED (may be empty), capability ids
        - string
      transport_provider: string  #   REQUIRED, Transport Layer selector (e.g. "browser")
      execution_engine: string    #   REQUIRED, Execution Layer selector (e.g. "chatgpt")

phases:                         # REQUIRED, >= 1 phase, executed in order
  - id: string                  #   REQUIRED, unique phase id
    name: string                #   REQUIRED
    objective: string           #   REQUIRED, this phase's goal
    participant_ids:            #   REQUIRED, >= 1, ordered turn order
      - string
    instructions: string        #   OPTIONAL, extra guidance rendered into prompts
    max_rounds: integer | null  #   OPTIONAL, cap on rounds within this phase
    context: string             #   OPTIONAL, discussion carried into prompts:
                                #   "auto" (default) | "none" | "delta" | "full"
    interaction: string         #   OPTIONAL, how the participants work together:
                                #   "sequential" (default) | "parallel"
    stage: string               #   OPTIONAL, workflow stage label; display and
                                #   provenance only, no execution meaning

governance:                     # REQUIRED
  checkpoint_interval: integer  #   REQUIRED, >= 1, rounds between checkpoints
  max_rounds: integer | null    #   OPTIONAL, global cap across the whole mission
  convergence_note: string      #   OPTIONAL, guidance shown to the Founder
  escalation_note: string       #   OPTIONAL
  synthesiser: string | null    #   OPTIONAL, participant id that writes the
                                #   final synthesis (default: first participant)

outputs:                        # REQUIRED (may be empty): declared deliverables
  - id: string                  #   REQUIRED, unique output id
    title: string               #   REQUIRED
    description: string         #   REQUIRED
    filename: string            #   REQUIRED, bare Markdown filename
                                #   (no path separators, drive letter, or '..')
```

---

## 3. Semantics

### 3.1 Participants and `assigned_engine`

A participant is an interchangeable execution engine taking part in the mission.
The `assigned_engine` structure deliberately separates four concerns so the
interpreter never has to special-case any model:

| Field                   | Layer            | Meaning                                            |
|-------------------------|------------------|----------------------------------------------------|
| `role`                  | Mission          | The participant's function (e.g. proposer, critic) |
| `required_capabilities` | Mission          | Capability ids this engine must satisfy            |
| `transport_provider`    | Transport Layer  | How the engine is reached (`browser` for the MVP)  |
| `execution_engine`      | Execution Layer  | Which model runs (`chatgpt`, `gemini`, …)          |

The interpreter reads these values only to **render** and **route** — it never
branches on them. Moving a participant from `browser` to a future `openai_api`
transport is a one-field contract edit with no interpreter change.

A participant is not necessarily a bare model. Two optional fields say what it
is:

| Field          | Default   | Meaning                                                      |
|----------------|-----------|--------------------------------------------------------------|
| `type`         | `"model"` | `"model"` — the engine seated as itself; `"agent"` — a configured worker built around an engine |
| `instructions` | `""`      | that participant's **standing brief**, rendered into every one of its prompts |

The distinction from a phase's `instructions` is load-bearing: a phase's
instructions apply to everyone in the phase, a participant's apply to that
participant in every phase. The standing brief is what lets two participants
share one engine and still hold different working identities.

`type` is declarative. The interpreter never branches on it; it exists so the
run record and the dashboard can state what a participant is rather than infer
it. Both fields default to what a contract written before they existed meant.

**Transport limitation.** Two participants declaring the same
`execution_engine` are valid at the contract level and load without complaint,
but the browser transport locates a tab by engine domain, so they would share
one conversation. See CONCEPTUAL-MODEL.md §11.

### 3.2 Capabilities

`capabilities` is the declared catalogue. Every id listed in a participant's
`required_capabilities` MUST exist in this catalogue (enforced by the loader).

### 3.3 Phases — the discussion strategy, as data

`phases` run in declaration order. Within a phase, `participant_ids` gives the
**ordered turn order**. One **round** = one full pass in which every listed
participant takes exactly one turn. This ordered structure *is* the discussion
strategy; there is no Strategy-pattern code — the interpreter simply executes
the declared order.

`max_rounds` (per phase) optionally bounds the phase. When omitted, the phase is
bounded only by governance and by the Founder's checkpoint decisions.

#### `context` — how much discussion a prompt carries

Each participant speaks in its own persistent conversation, which already holds
its previous prompts and answers. `context` declares how much of the discussion
the runtime re-sends into that conversation each turn:

| Value | Prompt carries | Use for |
|---|---|---|
| `auto` (default) | Full bounded window on a participant's first turn; only what is new since its last turn thereafter | Almost every phase |
| `none` | No discussion window at all | Genuinely independent phases — those whose `instructions` forbid referencing the other peers |
| `delta` | Only what is new, including on a first turn | A phase joining an already-long discussion |
| `full` | The whole bounded window, every turn | A phase that must re-read everything, e.g. a consolidation phase after a long gap |

The field is **declarative context policy, not execution logic**. The
interpreter never reads it; the Prompt Renderer does, while building the
prompt. Nothing about *how* the mission runs changes — only what each prompt
restates.

Omitting the field reproduces the behaviour of contracts written before it
existed, so every pre-existing contract remains valid and unchanged in meaning.

> **Why it exists.** Under `auto`, prompt size stops scaling with transcript
> length. Before this, every turn re-sent the whole discussion: on the
> 2026-08-19 `general_inquiry` run prompts grew 1,490 → 35,243 chars and 8 of
> 10 turns exceeded the composer and had to be delivered as file attachments.
> `none` fixes a second problem — an "independent" phase that forbids
> referencing the peers was still being handed their answers.

### 3.3b Phases — interaction and stage

`interaction` declares **how** a phase's participants work together. It is a
different question from `context` (how much of the discussion a prompt carries)
and from governance (how the mission is controlled).

| Value                     | Behaviour                                                                                          | Status       |
|---------------------------|----------------------------------------------------------------------------------------------------|--------------|
| `sequential` *(default)*  | one participant at a time; each turn sees the turns before it in this round                          | implemented  |
| `parallel`                | every prompt is rendered from the same round-start state and delivered before any reply is collected | experimental |

A `parallel` round therefore has two properties by construction: the engines
generate at the same time, and none of them can see another's answer from that
round. Replies are recorded in declaration order whatever order they arrive in,
and one participant's failure is recorded without ending the round.

**`context: none` is not parallelism.** It withholds the other participants'
answers while still running one turn after another. The two fields are
orthogonal and a contract sets each on its own — the R-A-B template's research
stage deliberately sets both.

An interaction the runtime cannot execute is rejected **at load time**, with the
supported set named in the error. It is never silently degraded to sequential.

`stage` is an optional workflow-stage label with **no execution meaning at
all** — it is recorded in `metadata.json` and displayed, nothing more. No
vocabulary is enforced; hard-coding a stage list would hard-code one workflow
into the runtime. See CONCEPTUAL-MODEL.md §5.

### 3.4 Governance and checkpoints

`checkpoint_interval` is measured in **completed rounds**: after every *N*
rounds the interpreter presents the checkpoint menu to the Founder:

```
[C] Continue   [V] Converged   [E] Escalate   [T] Terminate
```

The Founder decides. The interpreter never infers consensus on its own.

`max_rounds` (governance) is an optional **global** cap; reaching it completes
the mission. Regardless of caps, the Founder can always terminate at a
checkpoint, so a mission is never truly unbounded.

### 3.5 Outputs

`outputs` declares the deliverables the mission is expected to produce. The
runtime always also writes the canonical execution artifacts (ledger,
checkpoint summaries, runtime metadata). On a successful terminal state the
interpreter runs a dedicated **synthesis turn** to produce them.

**Who synthesises.** `governance.synthesiser` names the participant that writes
the synthesis. When it is omitted the first declared participant is used, which
is the historical behaviour — but declaring it is preferred: in a discussion of
equal peers, list position is not a reason for one engine to always hold the
final word.

**One output.** One synthesis turn. The response becomes that deliverable,
written to its `filename`.

**Several outputs — one synthesis turn per output.** The interpreter issues one
prompt per declared output, in declaration order, all to the same synthesiser.
Each turn asks for exactly that one document, wrapped in plain-text sentinels:

```
BEGIN-OUTPUT: <output id>
...the complete deliverable...
END-OUTPUT: <output id>
```

The replies are joined and parsed as one, so each recognised section is written
to its own `filename`. Sentinels are plain text rather than fenced blocks on
purpose — responses are harvested from the rendered DOM via `innerText`, which
strips markdown fences entirely (the same lesson `evidence.py` and
`discovery.py` encode).

Turn-per-output exists because a browser reply is the hard ceiling on a
deliverable: asking one reply for four documents produces four summaries, not
four documents. Only the **first** synthesis turn carries the discussion
transcript; the rest are short, because that engine's own conversation already
holds it. A four-document mission therefore sends one oversized prompt and three
small ones rather than one enormous one.

**Where the document skeleton belongs.** A synthesis prompt carries the output's
`title`, `description`, and `filename` — it does **not** carry phase
`instructions`. A multi-output mission that dictates its document structure must
put that structure in `description` (a literal `|` YAML scalar; a folded `>`
collapses the newlines and flattens the skeleton onto one line).

A declared output the synthesis omitted is reported as a warning, not silently
skipped. A reply that answers a per-output request but omits the sentinel lines
is wrapped in them and recorded as a `[DELIVERABLE]` system entry — a formatting
slip must not cost the whole document. If a multi-output synthesis contains no
sections at all, the whole response is written to the first declared output so
the run still produces a deliverable.

---

## 4. Immutability guarantee

The contract is loaded once and never mutated. The implementation enforces this
structurally: every type is a `frozen` dataclass and every collection field is a
`tuple` (never a `list`), so neither attribute reassignment nor in-place
collection mutation is possible.

---

## 5. Validation rules (enforced by the loader)

*(Added 2026-08-28)* `participants[].type` must be `"model"` or `"agent"`;
`participants[].instructions` must be a string; `phases[].interaction` must be
`"sequential"` or `"parallel"`; `phases[].stage` must be a string. Each is
optional, and an unrecognised value is reported with the allowed set named.

A contract is **invalid** (the loader raises `MissionValidationError`) if any of:

1. A required top-level key is missing (`id`, `name`, `objective`,
   `capabilities`, `participants`, `phases`, `governance`, `outputs`).
2. `participants` is empty, or any participant is missing `id`,
   `display_name`, or a well-formed `assigned_engine`.
3. Any `assigned_engine.required_capabilities` id is not in the declared
   `capabilities` catalogue.
4. `phases` is empty, or any phase has an empty `participant_ids`, or any
   `participant_ids` entry does not reference a declared participant.
5. `governance.checkpoint_interval` is missing or `< 1`.
6. Any id collection (participants, phases, capabilities, outputs) contains
   duplicates.
7. `governance.max_rounds` or any `phases[].max_rounds` is present but is not a
   positive integer (`null` is allowed). Caught here rather than mid-mission,
   where it would fail only after the run's browser work had been spent.
8. `governance.synthesiser` is present but does not reference a declared
   participant.
9. Any `outputs[].filename` is not a bare filename — path separators, a drive
   letter, or `..` are rejected, because the name is joined onto the run
   directory.
10. Any `phases[].context` is present but is not one of `auto`, `none`,
    `delta`, `full`. Rejected at load rather than silently falling back to the
    default, which would change what every prompt in that phase carries.

The loader reports **all** discovered problems together, not just the first.

---

## 6. Example

See [`missions/frelan_debate.yaml`](missions/frelan_debate.yaml) for a complete,
runnable contract: a moderated ChatGPT-vs-Gemini debate reached through the
browser transport.
