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

## Step Transition

단계를 완료하면 세 무브로 보고합니다: 만든 내용의 요약 → 다음 단계가 왜 필요한지
한 문장 안내 → 진행 여부 확인. 첫 무브의 요약은 채팅에 쓴 것과 같은 내용을 해당 단계
폴더의 `SUMMARY.md`에도 저장합니다. `SUMMARY.md`는 매번 한국어 산문으로 전체를 새로
작성해 이전 요약을 덮어쓰며, 보고에는 산출물 폴더 경로를 반드시 포함합니다. 단, rehearsal
활동은 real 단계 폴더에 어떤 파일도 쓰지 않고 요약을 `rehearsal/` 아래에만 저장하거나
생략합니다. `imported` 상태로 반입된 단계는 완료 무브가 없으므로 `SUMMARY.md`를 만들지 않습니다.

Steps 1 · 4 · 5 · 6 · 9 · 10 · 11의 P5 보고에는 §Knowledge Check 한 문장을 붙입니다.
게이트가 아닙니다.

---

## Step 1: Idea / PICO Structuring

**Purpose:** Transform a free-text research idea into a structured PICO/PECO framework.

**Entry:** none (`idea` is the first artifact).

**Process:**
1. Extract Population, Exposure, Comparator, Outcome, Time, Setting.
2. Mark uncertain fields `"confidence": "low"` and **tell the user** which parts are
   uncertain, asking if they can clarify.
3. Use `templates/pico-template.json` as schema; save to `research/01_pico/idea.json`.
   On PICO confirm, fill `rival_hypotheses` with two entries, each
   `{hypothesis, refutation_condition}`.
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
   limitations. Set `direction` to `supporting` | `contradictory` | `null`.
2. Compute effect-direction consistency; identify gaps; assess novelty.
   Run an explicit pass for dissenting and contradictory literature — supporting
   papers alone are not the table.
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
   Do this from `query_guard` output, never by Reading the data file.
4. Present the list organized by category (exposure / outcome / covariates / time).

**Data-access contract:** LLM은 데이터 파일(csv/xlsx 등 원자료)을 직접 읽지 않는다.
원자료는 ignore 목록에 등재한다 (`references/material-intake.md`). 스키마·도수·집계는
`query_guard` 출력만 읽는다.

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/query_guard.py \
    --data <path> --op {schema|freq|agg} [--by COL ...]
```

stdout JSON (필드명 고정): `{columns, dtypes, result, suppressed, suppression_note, warnings}`

- 단일 레코드로 좁혀지면 `result` 생략, `suppressed=true`. 억제 사실은 알린다.
- PII를 시사하는 컬럼(이름·연락처·주민번호 등)은 마스킹 라벨만.
- 셀 n≤30이면 `warnings`에 통계 유효성 경고.

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

**Purpose:** 이 단계의 목적은 결과를 보기 전에 추정 목표와 방법을 기록해 두는 것이다. 결과를 본 뒤의 변경을 막지는 않는다 — 다만 무엇이 사전 지정이었고 무엇이 사후 변경인지 구분되어 기록된다.

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

**Design-judgment checkpoint:** 표 shell이 실제 설계와 맞는지 심사숙고하는 자리이다.
eligibility · time zero · exposure · comparator · primary outcome · estimand가
shell에 그대로 보이는지 대조한다. 같은 껍질을 표·코호트 흐름·forest/survival
레이아웃 등 한눈에 들어오는 시각으로 여러 장 보여, 논문화할지 / 분석을 더할지 /
다른 자료가 필요한지 판단하게 한다.

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
5. Map numeric claims to evidence (anchor grammar in `templates/manuscript-template.md`):
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/claim_map.py \
       --manuscript <md> --evidence <evidence.json>
   ```
   stdout JSON (필드명 고정): `{claims:[{text, anchor:{kind:"table|figure|text", id}, sources:[{type:"pmid|doi", id, verified}], status:"verified|unverified|mismatch"}], unmapped:[]}`.

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

---

## Knowledge Check

시험을 치르지 않는다. 단계 종료 P5 보고에 한 문장을 넣어, 연구자가 이 단계의
분석을 이해했다는 흔적을 남긴다. 게이트가 아니다 — 이 문장을 확인하지 않았다고
진행을 막지 않는다. 아래 단계만 해당한다.

| Step | 한 문장 |
|---|---|
| 1 | 누구에서 무엇과 무엇을 비교해 어떤 outcome을 언제 측정하는 연구인지 설명할 수 있습니다. |
| 4 | 각 주요 변수가 exposure/outcome/confounder 중 어떤 역할인지 확인했습니다. |
| 5 | 연구대상, time zero, comparator, outcome과 follow-up을 확인했습니다. |
| 6 | 무슨 effect를 추정하며 왜 이 분석방법을 사용하는지 확인했습니다. |
| 9 | 실제 분석대상이 어떻게 만들어졌고 주요 데이터 문제를 어떻게 처리했는지 확인했습니다. |
| 10 | Primary estimate와 불확실성, 주요 assumption 및 sensitivity result를 확인했습니다. |
| 11 | 논문의 주장이 실제 설계와 분석이 허용하는 범위를 넘지 않는지 확인했습니다. |
