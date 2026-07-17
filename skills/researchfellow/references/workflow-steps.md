# 13-Step Research Workflow

## Overview

ResearchFellow guides retrospective clinical research from initial idea to a
submission-ready manuscript, then through reviewer revision. Progress is judged by an
**artifact DAG**, not a linear cursor — a step is enterable when the artifacts and hard
gates it depends on are present and valid, regardless of how the project arrived there.

**Entry is DAG-decided, not "previous step done".** Before entering any step N, run:

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/state_tool.py can-enter --project-dir research --step N
```

On exit 2, explain the returned `missing_artifacts` / `draft_artifacts` /
`missing_hard_gates` and do not proceed. The full DAG (required `[req]` / recommended
`[rec]` artifacts, gate anchors, reverse-fill, cascade) lives in
`references/state-machine.md` — this file gives the per-step *procedure*.

**Modes:**
- **Planning Mode** (Steps 1–8): synthetic/mock data. All outputs carry a "NOT REAL DATA"
  watermark and can never enter the manuscript.
- **Real-Data Mode** (Steps 9–13): requires the three hard gates
  (`gate.feasibility`, `gate.protocol`, `gate.qc`). Results are publication-grade.

Gate ids are semantic (not ordinals). Types (hard/soft) and anchors are in
`references/guardrails.md` and `references/state-machine.md`.

---

## Step 1: Idea / PICO Structuring

**Purpose:** Transform a free-text research idea into a structured PICO/PECO framework.

**Entry:** none (`idea` is the first artifact).

**Process:**
1. Extract Population, Exposure, Comparator, Outcome, Time, Setting.
2. Mark uncertain fields `"confidence": "low"` and **tell the user** which parts are
   uncertain, asking if they can clarify.
3. Use `templates/pico-template.json` as schema; save to `research/01_pico/idea.json`.
4. Suggest 2–3 study-design candidates (cohort, case-control, cross-sectional) with brief
   pros/cons; identify potential biases and key covariates.

**Output:** `idea` → `research/01_pico/idea.json`

**Gate:** `gate.go-no-go` (soft) — clinically meaningful? retrospective time axis feasible?
not oversaturated? Evaluated on the idea before Literature Scoping.

---

## Step 2: Literature Scoping

**Purpose:** Systematic search of existing literature to map the evidence landscape.

**Entry:** `idea` [req]; `gate.go-no-go` (soft).

**Process:**
1. Generate PubMed queries from PICO — **show the queries to the user first** and let them
   adjust before running.
2. Run:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/pubmed_search.py \
       --query "<query>" --email "<email>" --retmax 20 --output research/02_literature/literature/
   ```
3. Show the top 5–10 titles and ask if the direction looks right; save queries to
   `research/02_literature/literature/queries.json`.

**Output:** `literature` → `research/02_literature/literature/`

**Gate:** none (query finalization recommended).

---

## Step 3: Evidence Table

**Purpose:** Structured extraction of key data from retrieved literature.

**Entry:** `idea` [req]; `literature` [rec].

**Process:**
1. For each paper extract: design, sample, exposure, outcome, effect size, covariates,
   limitations.
2. Compute effect-direction consistency; identify gaps; assess novelty.
3. Build the table with `templates/evidence-table-template.json`.
4. **Present a summary:** "N편 분석 결과, 효과 방향 일관성은 X, 발견된 gap은 Y."

**Output:** `evidence_table` → `research/03_evidence_table/evidence-table.json`

**Gate:** `gate.novelty` (soft) — identified gap supported by PMID evidence. *(Remote:
Step 3 can be deepened by `novelty_check` if the MCP server is configured — optional.)*

---

## Step 4: Variable Definition

**Purpose:** Define all variables needed for the study.

**Entry:** `idea` [req]; `evidence_table` [rec]; `gate.novelty` (soft).

**Process:**
1. List required variables: exposure, outcome, covariates, time variables, exclusions.
2. Specify definitions, coding, measurement windows; label required/recommended/optional.
3. If a dataset schema is provided, attempt auto-mapping and **flag unmapped variables**.
4. Present the list organized by category (exposure / outcome / covariates / time).

**Output:** `variables` → `research/04_variables/variables.json`

**Gate:** `gate.endpoint` (soft) — primary endpoint confirmed (measurement + time window).
Variable feasibility feeds the **hard** `gate.feasibility`, which is enforced later at
Step 9 entry.

---

## Step 5: Protocol

**Purpose:** Generate a formal study protocol document.

**Entry:** `idea` [req], `variables` [req]; `evidence_table` [rec]; `gate.endpoint` (soft).

**Process:**
1. Fill `templates/protocol-template.md` from accumulated project data.
2. Include background, objectives, methods, design, cohort definition, variables, analysis
   outline, ethics, limitations; version the document.
3. **Show a summary** of key decisions (design, cohort, endpoints) before generating.

**Output:** `protocol` → `research/05_protocol/protocol.md`

**Gate:** `gate.protocol` (**hard**) — protocol reviewed and approved. Required (with
`gate.feasibility`) before any real-data step; enforced deterministically at Step 9 entry.

---

## Step 6: SAP (Statistical Analysis Plan)

**Purpose:** Pre-specify all analyses before seeing real data.

**Entry:** `protocol` [req], `variables` [req].

**Process:**
1. Select the primary analysis model from outcome type and design.
2. Pre-specify sensitivity and subgroup analyses; specify missing-data handling.
3. Tell the user: "SAP 승인 후 추가되는 분석은 자동으로 'exploratory'로 표시됩니다."

**Output:** `sap` → `research/06_sap/sap.md`

**Gate:** none. *(Remote: Step 6 can be deepened by `methodology_advisor` if configured.)*

---

## Step 7: Table/Figure Shells

**Purpose:** Create empty structures for all planned tables and figures.

**Entry:** `sap` [req].

**Process:** generate Table 1 shell, primary results table, subgroup/sensitivity tables,
cohort flow diagram, and figure shells (forest plot, survival curve).

**Output:** `shells` → `research/07_shells/shells/`

**Gate:** none.

---

## Step 8: Synthetic Dry-Run

**Purpose:** Verify the entire analysis pipeline using synthetic data.

**Entry:** `sap` [req], `variables` [req]; `shells` [rec].

**Process:**
1. Explain: "이 단계는 가짜 데이터로 분석 파이프라인이 제대로 작동하는지 확인하는 것입니다.
   실제 결과가 아닙니다."
2. Run:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/analysis_runner.py \
       --mode synthetic --project-dir research --sap-version v0.1
   ```
3. Show results with a clear "NOT REAL DATA" label.

**Output:** `synthetic_results` → `research/08_dry_run/synthetic_results/`

**Critical rule:** synthetic results MUST NOT enter manuscript Results/Conclusions/Abstract.

**Gate:** none.

---

## Step 9: Data Preparation & QC

**Purpose:** Extract real data and verify quality. **This is the Real-Data Mode boundary.**

**Entry:** `protocol` [req], `variables` [req]; **hard gates `gate.feasibility` +
`gate.protocol` must be approved** (deterministically checked by `can-enter --step 9`).

**Process:**
0. **No data yet? Offer the rehearsal path** (before any gate talk): "아직 실제
   데이터가 없으시면, 가짜 데이터로 나머지 전 과정을 미리 체험해볼 수 있어요."
   On interest, follow `references/synthetic-data.md` — forced consent, synth_builder,
   `--mode rehearsal`, everything under `research/rehearsal/` only. Rehearsal never
   touches steps/gates/execution_mode and emits no telemetry step events. Do NOT
   interrogate why the user has no data (IRB/data reality stay a self-checklist — D7).
1. Explain the transition: "여기서부터 실제 데이터를 다룹니다. feasibility·protocol 게이트
   승인이 필요합니다."
2. Define the cohort with the Cohort DSL (see `references/cohort-dsl.md`) and compile:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/dsl_compiler.py \
       --dsl research/09_data_qc/extraction-plan.dsl --output research/09_data_qc/extraction-plan.sql
   ```
3. After the user extracts the data, run QC:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/qc_checker.py \
       --data-path <path> --output research/09_data_qc/qc-report.json
   ```
4. Show the QC summary clearly — critical issues in **bold**.

**Output:** `extraction_plan`, `qc_report` → `research/09_data_qc/`

**Gate:** `gate.qc` (**hard**) — no critical QC flags (or explained/excluded). Blocks Step 10.

---

## Step 10: Real Analysis

**Purpose:** Execute pre-specified analyses on real data.

**Entry:** `sap` [req], `qc_report` [req]; **hard `gate.qc` approved, and the Step-9 hard
gates (`gate.feasibility`, `gate.protocol`) still approved.** Verify with:

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/state_tool.py gate-check --project-dir research --for real-analysis
```

**Process:**
1. Run (the runner imports the *same* gate-check function — an unapproved gate physically
   blocks the run, FR-G4):
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/analysis_runner.py \
       --mode real --project-dir research --data-path <path>
   ```
2. Present effect sizes, confidence intervals, p-values; label each analysis pre-specified
   or exploratory; run model diagnostics.

**Output:** `real_results` → `research/10_analysis/real_results/`

**Gate:** `gate.results` (soft) — pre-specified vs exploratory labeled, estimates plausible.

---

## Step 11: Manuscript

**Purpose:** Generate an IMRD manuscript draft.

**Entry:** `real_results` [req], `protocol` [req], `sap` [req]; `evidence_table` [rec];
`gate.results` (soft).

**Process:**
1. Generate from `templates/manuscript-template.md`; Methods auto-matched to the protocol.
   **Author natively in English** — do not draft in another language and translate (calqued
   syntax reads awkwardly). Working notes may stay in the team's language; the manuscript is English.
2. **Results reference only real analysis outputs.** Discussion includes the required
   bias/limitations paragraphs. If any artifact is `imported`/`draft`, surface its
   provenance in **Limitations** (FR-G5).
3. Run the STROBE/RECORD checklist mapping (see `references/checklist-templates.md`).
4. **Show coverage:** "22개 항목 중 N개 충족, 누락 항목: ...".

**Output:** `manuscript`, `checklist` → `research/11_manuscript/manuscript.md`, `research/11_manuscript/checklist.json`

**Gate:** `gate.manuscript` (soft) — Methods match protocol, every numeric claim references
a table/figure, checklist coverage adequate. *(Remote: `checklist_map` can deepen Step 11.)*

---

## Step 12: Submission Package

**Purpose:** Compile all artifacts for journal submission.

**Entry:** `manuscript` [req], `checklist` [req]; `gate.manuscript` (soft).

**Process:** compile manuscript, tables, figures, supplements; generate the checklist
report and an audit-trail (provenance) summary; verify all gates approved; format per the
target journal's guidelines if specified.

**Compliance advice (FYI only — never a gate, never a blocker):** read
`research/.system/compliance-checklist.json` and, if any item is unchecked, add ONE advisory
line to the package summary — e.g. "제출 전 확인: 자기 점검 항목 중 N개가 미확인
상태입니다 (IRB 승인, …). 대부분의 저널이 Methods에 IRB 승인 정보를 요구합니다."
This is the ONLY place the checklist is voiced; the flow never interrogates the user
about IRB or data reality (2026-07-16 D7).

**Output:** `submission_package` → `research/12_submission/submission_package/`

**Gate:** none (final for the initial submission). *(Remote: `integrity_report` optional.)*

---

## Step 13: Revision Loop

**Purpose:** Respond to reviewer comments after submission. **The only re-enterable step.**

**Entry:** `manuscript` [req] valid **+ a `reviewer_comments` material** registered in
`materials.json`.

**Rounds convention:** each reviewer round appends an entry to `steps.13.rounds`:

```json
{ "round": 1, "comments_material": "m-012",
  "response_letter": "13_revision/round-1/response.md",
  "diff": "13_revision/round-1/diff.md", "closed_at": null }
```

**Process:**
1. Parse the reviewer comments into a point-by-point issue list.
2. For each point, revise the manuscript and record the change in
   `13_revision/round-<N>/diff.md`; draft the reply in
   `13_revision/round-<N>/response.md`.
3. When the letter + diff are finalized, set that round's `closed_at`. **A new round opens
   a *new* entry — never mutate a closed one.**
4. If a revision bumps `manuscript` (version increase), run `cascade --changed manuscript`
   and apply the result before continuing.

**Output:** `13_revision/round-<N>/` (loops)

**Gate:** none. *(Remote: `reviewer_playbook` can deepen Step 13 if configured.)*
