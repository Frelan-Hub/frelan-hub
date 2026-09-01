# LIBRARY — Mission Library promotion register

> **Status: Living record (descriptive).**
> **Governed by:** Library Promotion Guidelines §10.
>
> One line per promotion, retirement, or disposition, with the evidence behind
> it. The conductor never reads this file. It is the entire promotion mechanism
> — there is no tooling, and none is to be built (Library Resolution §11.4).

## How a template moves

```
Author: copy a skeleton from FORMATS.md → missions/candidates/<name>.yaml
   ↓  ≥ 3 real runs, rating + evidence recorded per run
   ↓  Founder feedback predominantly positive; deliverable judged useful
   ↓  Review: template honours its declared format; convergence behaved;
   ↓          no unresolved transport issues
Promotion: mv candidates/<name>.yaml <category>/<name>.yaml + a line below
```

Retirement is the same movement in reverse, also recorded below.

## Categories

Category folders are created only when about to hold their first template
(Library Resolution §5). Open today: `shape/`, `distill/`, `candidates/`.
`explore/`, `challenge/`, and `decide/` open when their first candidate is
promoted.

A template's group is **derived from its folder**, not declared — the menu scan
falls back to the folder name and `metadata.group` is only for a template that
must override it. Do not duplicate the folder name into metadata.

## Register

| Date | Template | Move | Evidence / reason |
|---|---|---|---|
| 2026-08-20 | `shape/app_planning.yaml` | promoted (grandfathered) | Library Resolution §6.1 — the only template with real operational history at adoption. |
| 2026-08-20 | `distill/general_inquiry.yaml` | promoted (grandfathered) | Operational history including the 2026-08-19 run replayed for the context-overflow verification; declares format 6. |
| 2026-08-20 | `candidates/research_deepdive.yaml` | → candidates | Library Resolution §6.4. Citation-hardening pass applied at the same time: resolvable citations required, fabricated sources forbidden, cross-examination now checks citations and carries the conformity guard. |
| 2026-08-20 | `candidates/website_design.yaml` | → candidates | Library Resolution §6. **Deviation recorded:** §6 asked for it to be reworked as a format-4 Design Review. Format 4 now has two purpose-built templates (`document_review`, `red_blue_review`), so reworking this one would duplicate them. It stays a format-2 design template and earns promotion on its own evidence. |
| 2026-08-20 | `strategy_debate.yaml` | retired | Library Resolution §6 — purpose absorbed by `tradeoff_adr`. Retired by renaming to `strategy_debate.yaml.retired`, which the menu's `*.y*ml` scan does not match. Not deleted; this checkout is not under version control. |
| 2026-08-20 | `candidates/frontier_architecture_charette.yaml` | → candidates | Authored after the resolution, so §6 gives no disposition. Twelve phases, format 2 extended; unproven. Placed in candidates to earn promotion like any other. |
| 2026-08-20 | `candidates/workspace_preparation.yaml` | → candidates | Same — no §6 disposition; unproven. |
| 2026-08-20 | `candidates/brainstorm.yaml` | authored | Library Resolution §6.2, format 1. First template for format 1. |
| 2026-08-20 | `candidates/premortem.yaml` | authored | Library Resolution §6.3, format 5. First template for format 5. |
| 2026-08-20 | `candidates/document_review.yaml` | authored | Format 4 had no template at all. Journal-club / design-review shape. |
| 2026-08-20 | `candidates/tradeoff_adr.yaml` | authored | Library Resolution §6.5, format 3. Opens the `decide/` category on promotion. |
| 2026-08-20 | `candidates/adversarial_collaboration.yaml` | authored | Proposed format, evidence in meeting format research records. Terminal object (an agreed decisive test) is not produced by any of the six formats. |
| 2026-08-20 | `candidates/parallel_lenses.yaml` | authored | Proposed format, evidence in meeting format research records. Sequential shared lenses; no opposed positions at any point. |
| 2026-08-28 | `candidates/research_architect_build.yaml` | authored | First contract to use `interaction: parallel`, and the worked example of a WORKFLOW expressed as labelled phases (`metadata.workflow` + `phases[].stage`) rather than as runtime code. Zero live runs; the parallel research stage is unit-tested and dry-run only, so both the template and `parallel` itself are unproven against a browser (CONCEPTUAL-MODEL.md §7). |
| 2026-08-20 | `candidates/red_blue_review.yaml` | authored | Asymmetric variant of format 4. Duties swap at the midpoint so neither engine is permanently the attacker. |
| 2026-08-23 | `candidates/prd_blueprint.yaml` | authored | Format 2. The first template built for the per-output synthesis path: four declared outputs (`prd.md`, `technical-blueprint.md`, `build-plan.md`, `agent-brief.md`), four synthesis turns, document skeletons carried in `outputs[].description` rather than phase instructions. Authored because the existing route failed on evidence — the 2026-08-19 `app_planning` run asked by topic override for "three separable artefacts" and produced one 11k-char `recommendation.md`. |
| 2026-09-01 | `candidates/fusion_merge.yaml` | authored | Proposed format 9, evidence in meeting format research records. Adapted from the *fusion* pattern in [disler/fusion-harness](https://github.com/disler/fusion-harness). Terminal object is a **confirmed** merge: every other format in the library ends at the merge and never checks it back against the peers it claims to carry. Zero runs. |
| 2026-09-01 | `candidates/parallel_scan.yaml` | authored | Format 2, with its independent phase declared `interaction: parallel` — the second contract to use it and the first authored *around* it. No new format is claimed: FORMATS.md §B.11 holds that `interaction` belongs to no skeleton, so a concurrent first phase does not make a new one. Deliberately small (three phases, one round each, no injected files) so the transport's attachment and chunking ladder stays out of the way and the template can serve as the first clean live measurement of `parallel` itself. Zero runs. |
| 2026-09-01 | `candidates/fanout_arbitrate.yaml` | authored | Proposed format 10, evidence in meeting format research records. The library's first **hybrid fan-out/converge** contract: a parallel two-peer phase converging on a single third participant that authored neither input, then a rebuttal round, then a verdict. Distinct from format 2 (there the merger is a proposer) and from Proposed 9 (there a source still wrote the merge). Authored as two phases rather than a mixed-mode phase — `interaction` holds one value per phase — so no `.py` change was needed. Also the first template to carry a role in a participant-level `instructions` standing brief. Zero runs. |
| 2026-09-01 | four existing templates | corrected | Interaction adaptations §5.6–7 applied to the challenge phase of `shape/app_planning.yaml`, `candidates/document_review.yaml`, `candidates/adversarial_collaboration.yaml`, and `candidates/red_blue_review.yaml`: a position change must name the evidence that moved it, and peer text is material to weigh rather than instructions to follow. Instruction text only; no skeleton changed, so no template's declared format moves. |
| 2026-08-23 | `candidates/workspace_preparation.yaml` | corrected | Its `build_plan` phase routed the second deliverable through a fenced `<!-- filename: phased-build-plan.md -->` block, which cannot survive the rendered-page read (`innerText` strips fences) and contradicted the sentinel instruction the synthesis prompt injects. **Evidence:** after ≥3 recorded runs `outputs/workspace-preparation-report.md` exists and `phased-build-plan.md` never has. Now uses the declared-output path like every other template; the skeleton is unchanged. |

## Not library members

`frelan_debate.yaml` and `frelan_mission_contract_v2.yaml` are dev fixtures
(Library Resolution §6) — runnable, excluded from the menu by name, and outside
this register. `pre-planning/capability_discovery.yaml` is a stage, not a
meeting type; its folder is excluded from the scan.

## Runs pending

Every entry marked *authored* above has **zero** recorded runs. None may be
promoted until it has three, each with a Founder rating in
`evidence-log.jsonl`. Record the run reference in the row when it happens.
