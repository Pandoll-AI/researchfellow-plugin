# 12-Step Research Workflow

## Overview

The RRA workflow guides retrospective medical research from initial idea to submission-ready manuscript. Each step builds on the previous, with HITL (Human-In-The-Loop) gates at critical decision points.

**Modes:**
- **Planning Mode** (Steps 1-8): Uses synthetic/mock data. Results carry "NOT REAL DATA" watermark.
- **Real-Data Mode** (Steps 9-12): Requires gate approvals. Results are publication-grade.

---

## Step 1: Idea / PICO Structuring

**Purpose:** Transform a free-text research idea into a structured PICO/PECO framework.

**Inputs:** Free-text idea (disease, exposure, outcome, data source)

**Process:**
1. Extract Population, Exposure, Comparator, Outcome, Time, Setting
2. Mark uncertain fields with low confidence
3. Suggest 2-3 study design candidates (cohort, case-control, cross-sectional)
4. Identify potential biases
5. Generate key covariate suggestions

**Output:** `.research/idea.json`

**Gate:** Gate#1 (Go/No-Go) — Is this clinically meaningful? Is a retrospective time axis feasible?

---

## Step 2: Literature Scoping

**Purpose:** Systematic search of existing literature to understand the evidence landscape.

**Inputs:** PICO structure, keywords, MeSH terms

**Process:**
1. Generate PubMed search queries (version-controlled)
2. Execute searches via `scripts/pubmed_search.py`
3. Retrieve titles, abstracts, metadata
4. Iteratively refine queries with user input

**Output:** `.research/literature/queries.json`, `.research/literature/items/`

**Gate:** None (but query finalization recommended)

---

## Step 3: Evidence Table

**Purpose:** Structured extraction of key data from retrieved literature.

**Inputs:** Literature items (abstracts/full text if available)

**Process:**
1. For each paper, extract: design, sample, exposure, outcome, effect size, covariates, limitations
2. Compute effect direction consistency
3. Identify gaps in the literature
4. Assess novelty of proposed research

**Output:** `.research/evidence-table.json`

**Gate:** Gate#2 (Novelty) — Confirmed gap/novelty with PMID evidence

---

## Step 4: Variable Definition

**Purpose:** Define all variables needed for the study.

**Inputs:** PICO, evidence table, dataset schema (if available)

**Process:**
1. List required variables: exposure, outcome, covariates, time variables, exclusion criteria
2. Specify definitions, coding, measurement windows
3. Label each as required/recommended/optional
4. If dataset schema provided: attempt auto-mapping, flag unmapped variables

**Output:** `.research/variables.json`

**Gates:**
- Gate#3 (Endpoint) — Primary endpoint confirmed
- Gate#4 (Feasibility) — Required variables mappable, time axis available

---

## Step 5: Protocol

**Purpose:** Generate a formal study protocol document.

**Inputs:** PICO, evidence, variables, study design decisions

**Process:**
1. Fill protocol template with accumulated data
2. Include: background, objectives, methods, design, cohort definition, variables, analysis outline, ethics, limitations
3. Version the document

**Output:** `.research/protocol.md`

**Gate:** Gate#5 (Protocol Approval) — Required before real-data execution

---

## Step 6: SAP (Statistical Analysis Plan)

**Purpose:** Pre-specify all analyses before seeing real data.

**Inputs:** Protocol, variable spec, outcome type, design

**Process:**
1. Select primary analysis model based on outcome type and design
2. Define sensitivity analyses with rationale
3. Define pre-specified subgroup analyses
4. Specify missing data handling strategy
5. Any analysis added after SAP approval → automatically labeled "exploratory"

**Output:** `.research/sap.md`

**Gate:** None (locked at Gate#5)

---

## Step 7: Table/Figure Shells

**Purpose:** Create empty structures for all planned tables and figures.

**Inputs:** SAP, journal limits (if known)

**Process:**
1. Generate Table 1 shell (baseline characteristics)
2. Generate primary analysis results table
3. Generate subgroup/sensitivity tables
4. Generate cohort flow diagram structure
5. Generate figure shells (forest plot, survival curve, etc.)

**Output:** `.research/shells/`

**Gate:** None

---

## Step 8: Synthetic Dry-Run

**Purpose:** Verify the entire analysis pipeline using synthetic data.

**Inputs:** SAP, variable definitions, analysis code

**Process:**
1. Generate synthetic data based on variable specs
2. Run end-to-end analysis pipeline
3. Verify output format and completeness
4. Mark all outputs with "NOT REAL DATA" watermark

**Output:** `.research/analysis/synthetic/`

**Critical Rule:** Synthetic results MUST NOT be used in manuscript Results, Conclusions, or Abstract.

**Gate:** None

---

## Step 9: Data Preparation & QC

**Purpose:** Extract real data and verify quality.

**Inputs:** Cohort DSL definition, dataset access

**Process:**
1. Define cohort using Cohort DSL (see `cohort-dsl.md`)
2. Compile DSL to SQL via `scripts/dsl_compiler.py`
3. User executes extraction query
4. Run QC checks via `scripts/qc_checker.py`:
   - Outcome date after index date
   - Missing data rates
   - Distribution anomalies
   - Coding consistency
   - Duplicate detection

**Output:** QC report, extraction plan

**Gate:** Gate#9 (Data QC) — Critical flags must be resolved before analysis

**Blockers:** Analysis blocked if QC has critical flags (e.g., outcome before index date)

---

## Step 10: Real Analysis

**Purpose:** Execute pre-specified analyses on real data.

**Prerequisites:** Gate#4, Gate#5, Gate#9 must be approved.

**Inputs:** Real data, approved SAP

**Process:**
1. Verify all required gates are approved
2. Run analysis via `scripts/analysis_runner.py` (real mode)
3. Generate tables and figures
4. Label all outputs as pre-specified or exploratory
5. Run model diagnostics

**Output:** `.research/analysis/real/`

**Gate:** Gate#10 (Results Interpretation)

---

## Step 11: Manuscript

**Purpose:** Generate IMRD manuscript draft.

**Inputs:** Protocol, SAP, real analysis results, evidence table

**Process:**
1. Generate manuscript using IMRD template
2. Methods section auto-matched against protocol
3. Results reference only real analysis outputs
4. Discussion includes required bias/limitation paragraphs
5. Run STROBE/RECORD checklist mapping

**Output:** `.research/manuscript.md`, `.research/checklist.json`

**Gate:** Gate#11 (Manuscript Approval)

---

## Step 12: Submission Package

**Purpose:** Compile all artifacts for journal submission.

**Inputs:** All approved artifacts

**Process:**
1. Compile: manuscript, tables, figures, supplementary materials
2. Generate checklist report
3. Generate audit trail summary (provenance)
4. Verify all gates approved
5. Format per target journal guidelines (if specified)

**Output:** Final submission package

**Gate:** None (final)
