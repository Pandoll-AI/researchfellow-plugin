# State Machine v2 — Artifact DAG

> Read this before entering any step, reversing any gate, or importing material.
> The deterministic judge is `scripts/state_tool.py`; every table below is mirrored
> as a module constant there and **must stay identical** (review checkpoint).

## v2 Overview — the `current_step` cursor is gone

v1 advanced a single `current_step` cursor: step N could start only when the cursor
sat on N. ResearchFellow enters at *any* stage (a half-written draft, a bare dataset,
reviewer comments), so a cursor is the wrong model.

**v2 judges by an artifact DAG.** A step is enterable iff the artifacts and hard gates
it depends on are present and valid — regardless of how the project got there.
The 13 steps are just the *default happy path*; `focus_step` and `next_action` are
advisory labels (FR-E6), not a lock.

- Writing state is the **host LLM's job**. `state_tool.py` only *judges* (read-only,
  exit codes + JSON). There is no write engine in P0.
- `steps.*.status == "imported"` counts as done for downstream entry, but flags a
  provenance limitation (FR-G5).
- `artifacts.*.validity == "draft"` (imported, unverified) does **not** satisfy a
  `[req]` edge until the Intake Gate promotes it to `valid`.

## state.json v2 schema

Single file (`.research/state.json`, NFR-4). The v1 `gates.json` split is folded back
in. Gate keys are semantic ids; the v1 ordinal is preserved as `legacy_id`.

Top-level fields: `schema_version` (=2), `project_id` (uuid4, telemetry attribution
only), `project_name`, `research_card` (nullable; PICO 확정·변경 시 갱신, FR-I2),
`created_at`, `entry_point`, `execution_mode`
(`planning` | `real_data`), `focus_step`, `next_action`, `steps`, `artifacts`, `gates`,
`rehearsal`, `blockers`. See `templates/project-init.json` for the empty initializer.

**Outside the DAG (never registered as artifacts, never a `[req]`/gate input):**
`.research/compliance-checklist.json` (self-attested only — no deterministic
consequence), `.research/desk/` (Desk sessions/answers), and the entire
`.research/rehearsal/` tree (synthetic practice outputs — physically separated from
real artifacts; rehearsal activity never flips `execution_mode` to `real_data` and
never touches `steps.*.status`).

### Field conventions

| Field | Rule |
|---|---|
| `steps.*.status` | one of `pending / in_progress / completed / skipped / blocked / imported`. `imported` = satisfied by an imported asset, ranks with `completed` for entry but is the FR-G5 limitation discriminator. |
| `artifacts.*.origin` | one of `generated / imported / reverse_filled`. |
| `artifacts.*.validity` | one of `draft / valid / invalidated`. `draft` = imported/reverse-filled and **not yet confirmed** → does not satisfy `[req]` in DAG judging. `invalidated` = a cascade knocked it out. |
| `artifacts.*.source` | material id (`m-003`) or `null`. Bidirectional with `materials.json` `promoted_to`. |
| `artifacts.*` other | `path`, `produced_by_step`, `version` (int, cascade trigger), `verified_at`. |
| `gates.*.type` | `hard` or `soft`. **hard** = deterministically enforced by `state_tool` / `analysis_runner`. **soft** = LLM-conversational. |
| `gates.*.retroactive` | `true` only for a soft gate approved retroactively at Intake. **A hard gate may never be `retroactive:true`** — a validate invariant. |
| `gates.*.status` | `pending / approved / rejected / changes_requested`. Approval is immutable — re-review is a *new* entry, never an in-place edit. |
| `steps.13.rounds` | `[{round, comments_material, response_letter, diff, closed_at}]`. Step 13 is the only re-enterable step. |

Artifact entry shape:

```json
"protocol": { "path": "protocol.md", "origin": "generated|imported|reverse_filled",
              "validity": "draft|valid|invalidated", "source": "m-003 or null",
              "produced_by_step": 5, "version": 1, "verified_at": null }
```

## Artifact DAG

`[req]` = required (absent/draft/invalidated ⇒ entry blocked, deterministic).
`[rec]` = recommended (warn, LLM may proceed after user confirmation).

| Step | Required artifacts | Required gate | Produces |
|---|---|---|---|
| 1 | — | — | idea |
| 2 | idea[req] | go-no-go(soft) | literature |
| 3 | idea[req], literature[rec] | — | evidence_table |
| 4 | idea[req], evidence_table[rec] | novelty(soft) | variables |
| 5 | idea[req], variables[req], evidence_table[rec] | endpoint(soft) | protocol |
| 6 | protocol[req], variables[req] | — | sap |
| 7 | sap[req] | — | shells |
| 8 | sap[req], variables[req], shells[rec] | — | synthetic_results |
| 9 | protocol[req], variables[req] | **feasibility(hard), protocol(hard)** | extraction_plan, qc_report |
| 10 | sap[req], qc_report[req] | **qc(hard)** (+ step-9 hard 2 stay approved) | real_results |
| 11 | real_results[req], protocol[req], sap[req], evidence_table[rec] | results(soft) | manuscript, checklist |
| 12 | manuscript[req], checklist[req] | manuscript(soft) | submission_package |
| 13 | manuscript[req] + reviewer_comments material | — | revision/round-N/ (loops) |

**Entry rule:** `enterable(N) ⇔ ∀a ∈ req(N): validity == "valid" ∧ ∀g ∈ hard_gates(N): approved`.
soft gates and `[rec]` are resolved by the LLM in conversation; `[req]` and hard gates
are decided deterministically by `state_tool can-enter`.

Convention: **before entering a step, run `state_tool can-enter --step N`. On exit 2,
explain the `missing_artifacts` / `draft_artifacts` / `missing_hard_gates` and do not
proceed. After re-running an upstream step, apply the `cascade` output, then re-run
`validate`.**

## Reverse-fill (FR-W4)

Principle: only reverse-extract the `[req]` *upstream* of the arrival step. Remaining
upstream steps are marked `skipped`.

| Imported role | Reverse-extract targets |
|---|---|
| protocol | idea (PICO), variables |
| sap | variables (top-up). If protocol absent, do **not** reverse-extract — register a blocker. |
| manuscript_draft | idea, protocol (Methods summary), sap (statistical-analysis section) — all `draft` |
| analysis_output (+code) | real_results (imported) — no upstream reverse-extraction; replace with provenance interview |
| background / competing_paper | evidence_table increment (`draft`) |

Procedure:
1. Write the file to the template schema, with a top `"_provenance": {origin, source_material, verified:false}`.
2. Register in `artifacts` as `origin: reverse_filled, validity: draft` + audit (`ARTIFACT_REVERSE_FILLED`).
3. **Never overwrite an existing `valid` artifact.** On conflict, write a separate path
   (e.g. `idea.imported.json`) and ask a merge question in the briefing.
4. Only after the Intake Gate confirms → `valid`.

## Invalidation cascade (FR-W8)

Static adjacency list — **identical here and in `state_tool.py` `INVALIDATION_ADJACENCY`**.
Only these seven artifacts are cascade sources; other artifacts are leaves or feed only
`[rec]` edges (handled by LLM conversation, not deterministic cascade).

```
idea         → literature, evidence_table, variables, protocol, sap, shells,
               synthetic_results, extraction_plan, qc_report, real_results,
               manuscript, checklist, submission_package, revision   (all downstream)
variables    → protocol, sap, shells, synthetic_results, extraction_plan, qc_report,
               real_results, manuscript, checklist, submission_package, revision
protocol     → sap, shells, synthetic_results, extraction_plan, qc_report,
               real_results, manuscript, checklist, submission_package, revision
sap          → shells, synthetic_results, real_results, manuscript, checklist,
               submission_package, revision
qc_report    → real_results, manuscript, checklist, submission_package, revision
real_results → manuscript, checklist, submission_package, revision
manuscript   → checklist, submission_package, revision
```

Triggers:
1. **artifact `version` bump** → descendants become `invalidated`, their producing steps
   revert to `pending`, and **soft** gates whose anchor artifact is in the invalidated
   set revert to `pending`. (Run `state_tool cascade --changed <artifact>`.)
2. **gate reversal** → cascade from the gate's *anchor* artifact (below), then apply
   trigger ①.
3. **`MATERIAL_RECLASSIFIED`** → invalidate artifacts whose `source == m-id`, then ①.

Gate → anchor artifact map (identical to `state_tool.py` `GATE_ANCHOR`):

| Gate | Anchor |
|---|---|
| gate.go-no-go | idea |
| gate.novelty | evidence_table |
| gate.endpoint | variables |
| gate.feasibility | variables |
| gate.protocol | protocol |
| gate.qc | qc_report |
| gate.results | real_results |
| gate.manuscript | manuscript |

## Intake Gate (FR-W5)

One body with the briefing's "confirm the starting point" agenda:

**Copy order only — the procedure, verdicts, order, and invariants below are unchanged.**
Before the Batch confirm request, open with the `research_card`, then narrate "자료에서
제가 읽어낸 그림". Apply the three provenance categories: 전달받음 / 추론 (each
reverse-filled item exposes its `_provenance` as material name + extraction location) /
불확실. Only after this understanding statement ask for the batch confirmation
("맞으면 한 번에 확정, 틀린 항목만 짚어주세요").

1. **Batch confirm** — summarize reverse-filled `draft` artifacts on one screen
   ("confirm all at once, or point out only the wrong items"). On approval:
   `draft → valid`, `step → imported`, `ARTIFACT_IMPORTED × N`.
2. **Retroactive soft gates, batched** — summarize each unresolved soft gate with a
   one-sentence rationale, approve in one pass: `approved` + `retroactive:true` +
   `GATE_RETROACTIVE × N`. Only contested gates stay `pending` + blocker.
3. **Real-data 3 gates, individually** (when arrival step ≥ 9 or analysis_output is
   confirmed) — `feasibility` and `protocol` approved individually; `qc` cannot be
   approved without passing the 3-question provenance interview (data really exists /
   QC done / code exists → `PROVENANCE_ATTESTED`). **Retroactive is forbidden here.**
   If not approved: `execution_mode` stays `planning`, Step 10 is deterministically blocked.
4. **Finalize** — record `arrival_step`, `focus_step`, `next_action`.

Provenance interview verdicts: all yes → `gate.qc` may be formally approved +
`real_results` imported / data-exists = no → results are reference-only (no manuscript
Results use + FR-G5 limitation reserved) / QC = no → `gate.qc` stays `pending`, Step 9
recommended, numeric claims blocked.

## v1 recognition & lazy upgrade (NFR-5)

- **Detect v1:** no `schema_version` **and** gate keys are numeric strings.
- On open, interpret via the mapping below and keep working. **At the next state
  change**, atomically rewrite the whole file to v2 (`SCHEMA_UPGRADED` event). The
  `artifacts` registry is reconstructed from filesystem existence + step status.
- The root `audit.jsonl` is left in place; new events append to `.research/audit.jsonl`.
- Because v1 has no registry, `can-enter` reconstructs validity with:
  **producing step `completed` ∧ that step's artifact file exists in the project dir
  ⇒ `valid`** (v1 has no `draft`).

### v1 ordinal → v2 semantic gate id

Identical to `state_tool.py` `V1_GATE_MAP`.

| v1 key | v2 id | type |
|---|---|---|
| 1 | gate.go-no-go | soft |
| 2 | gate.novelty | soft |
| 3 | gate.endpoint | soft |
| 4 | gate.feasibility | hard |
| 5 | gate.protocol | hard |
| 9 | gate.qc | hard |
| 10 | gate.results | soft |
| 11 | gate.manuscript | soft |

Real-data hard gates in v1 are keys `4`, `5`, `9`.

## Invariants

`state_tool validate` enforces the deterministic ones (marked ✓):

1. ✓ A **hard gate** is never `retroactive:true`.
2. ✓ At most **one** step `in_progress`.
3. ✓ A `draft` artifact never has a `valid` structural downstream (v2 registry).
4. ✓ No v1/v2 **hybrid** state (numeric keys with `schema_version`, or vice-versa).
5. Gate approval is **immutable** — re-review creates a new entry, never edits in place.
6. `audit.jsonl` is **append-only**, never modified.
7. Synthetic / imported-unverified results **never** enter manuscript Results/Conclusions.

## Audit events (FR-W9)

Location: `.research/audit.jsonl` (moved from the project root in v2; a v1 root
`audit.jsonl` is left untouched and appended to under `.research/`).

Existing: `PROJECT_INIT`, `STEP_STARTED`, `STEP_COMPLETED`, `GATE_APPROVED`,
`GATE_REJECTED`, `GATE_CHANGES_REQUESTED`, `ARTIFACT_CREATED`, `ARTIFACT_UPDATED`.

New (v2): `ENTRY_POINT`, `ARTIFACT_IMPORTED`, `ARTIFACT_REVERSE_FILLED`,
`GATE_RETROACTIVE`, `MATERIAL_RECLASSIFIED`, `PROVENANCE_ATTESTED`,
`ARTIFACT_INVALIDATED`, `SCHEMA_UPGRADED`, `PHI_DETECTED`.

## state_tool.py usage contract

Read-only judge, stdlib only, never writes.

```
state_tool.py validate   --project-dir .research/            # exit 0 / 1 ; {schema, violations[]}
state_tool.py can-enter  --project-dir .research/ --step N   # exit 0 / 2 ; {allowed, missing_artifacts, draft_artifacts, missing_hard_gates, warnings}
state_tool.py gate-check --project-dir .research/ --for real-analysis  # exit 0 / 2 ; {ok, missing}
state_tool.py cascade    --project-dir .research/ --changed <artifact>  # exit 0   ; {invalidate_artifacts, reset_steps, reset_gates}
```

- Works on both v2 and v1 state.json (v1 interpreted through the mapping tables above).
- `analysis_runner.py --mode real` imports the **same** `check_real_data_gates(state)`
  function, so an LLM that ignores instructions is still physically blocked (FR-G4).
- Workflow: **`can-enter` before entering a step; after any upstream re-run apply
  `cascade`, then `validate`.**

## Step 13 rounds

Step 13 (Revision Loop) is the only re-enterable step. Each reviewer round appends an
entry to `steps.13.rounds`:

```json
{ "round": 1, "comments_material": "m-012", "response_letter": "revision/round-1/response.md",
  "diff": "revision/round-1/diff.md", "closed_at": null }
```

Entering round N requires `manuscript[req]` valid plus a `reviewer_comments` material.
`closed_at` is set when the round's response letter + diff are finalized; a new round
opens a new entry rather than mutating a closed one.
