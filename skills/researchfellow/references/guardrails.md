# Guardrails & Gate Rules

## Execution Modes

### Planning Mode (Steps 1-8)
- Synthetic/mock data only
- All outputs carry "NOT REAL DATA" watermark
- Results cannot be inserted into manuscript Results, Conclusions, or Abstract

### Real-Data Mode (Steps 9-13)
- Requires all three real-data gates: gate.feasibility, gate.protocol, gate.qc
- Results are publication-grade
- Full audit trail required

## Gate Rules

### Required Real-Data Gates
These gates MUST be approved before entering Real-Data Mode:

| Gate | Purpose | Blocking Rule |
|------|---------|--------------|
| gate.feasibility | Feasibility | Required variable coverage met, time axis available |
| gate.protocol | Protocol Approval | Protocol reviewed and approved by PI |
| gate.qc | Data QC | No critical QC flags, data quality acceptable |

### Gate Approval Criteria

**gate.go-no-go (Go/No-Go)**
- Is the research question clinically meaningful?
- Is a retrospective time axis feasible?
- Is this not an oversaturated topic (novelty check)?

**gate.novelty (Novelty)**
- Identified gap supported by PMID evidence
- No novelty claims without supporting references

**gate.endpoint (Endpoint)**
- Primary endpoint clearly defined
- Measurement method specified
- Time window for outcome assessment defined

**gate.feasibility (Feasibility)**
- Required variable coverage >= threshold
- Index date and outcome timestamp available
- Cohort definition implementable in data

**gate.protocol (Protocol)**
- All methods sections complete
- Bias mitigation strategies defined
- Statistical analysis approach pre-specified

**gate.qc (Data QC)**
- Zero temporal violations (outcome before index) OR explained/excluded
- No critical coding errors
- Event count meets minimum for planned analysis

**gate.results (Results)**
- Pre-specified vs exploratory analyses correctly labeled
- No multiple testing issues unexplained
- Effect estimates clinically plausible

**gate.manuscript (Manuscript)**
- Methods match protocol
- All numeric claims reference analysis output tables
- STROBE/RECORD checklist coverage adequate
- Limitations section includes required bias categories

## Safety Rules

### Never Do:
1. Insert synthetic/planning-mode results into manuscript Results, Conclusions, or Abstract
2. Generate numeric claims without a source table/figure reference
3. Make novelty claims without supporting PMID
4. Allow real-data analysis without required gate approvals
5. Allow analysis when QC has critical flags
6. Generate causal language for observational studies (use "association" framing)

### Always Do:
1. Label pre-specified vs exploratory analyses
2. Include bias/limitation discussion (selection bias, information bias, unmeasured confounding, generalizability)
3. Watermark synthetic data outputs
4. Version all documents (protocol, SAP, manuscript)
5. Append to audit log on every state change
6. Hash data snapshots for reproducibility

## QC Critical Flags (Analysis Blockers)

These findings in QC block real-data analysis:
- Outcome date before index date (temporal violation)
- Event count < 5 (model instability)
- Missing primary outcome > 50%
- Missing primary exposure > 50%
- Duplicate patient IDs with conflicting outcomes

## Retrospective Study Bias Checklist

The following biases must be addressed in protocol and discussed in manuscript:
1. **Immortal time bias** — detected by DSL validator
2. **Confounding by indication** — require adjustment strategy in SAP
3. **Time-varying confounding** — consider time-varying Cox model
4. **Misclassification** — document coding definitions, sensitivity analysis
5. **Competing risks** — consider Fine-Gray model if applicable
6. **Informative censoring** — document censoring assumptions
7. **Multiple testing** — pre-specify primary analysis, label exploratory

## FR-G5 — Imported Artifact Provenance

Claims based on imported artifacts must carry their provenance status; unverified provenance is automatically reflected in the manuscript's Limitations section.
