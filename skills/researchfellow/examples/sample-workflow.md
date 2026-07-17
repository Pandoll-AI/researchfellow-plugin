# Sample Workflow: Metformin and Cardiovascular Outcomes in T2DM

This example demonstrates the 13-step ResearchFellow workflow (12+1: Step 13 is cyclic and optional).

---

## Receipt mode — collection loop

**User:** "당뇨 환자 청구자료와 연구 메모를 올렸습니다."

**ResearchFellow:** "제2형 당뇨병 환자에서 metformin과 sulfonylurea의 심혈관 사건을
비교하려는 자료로 이해했습니다. 더 주실 것이 있으신가요?"

**User:** "이것으로 끝입니다."

한 배치로 스캔한 뒤 브리핑을 시작합니다. research card: "제2형 당뇨병 성인에서
metformin 단독요법이 sulfonylurea 단독요법보다 MACE 위험과 관련되는지 평가하는 후향
코호트 연구입니다."

---

## Step 1: PICO Structuring

**User input:** "Does metformin reduce cardiovascular events in type 2 diabetes patients compared to sulfonylurea?"

**Output (`idea.json`):**
```json
{
  "research_question": "Does metformin reduce cardiovascular events in type 2 diabetes patients compared to sulfonylurea?",
  "structured": {
    "population": { "description": "Adults with type 2 diabetes mellitus", "age_range": ">=18", "setting": "Outpatient", "confidence": "high" },
    "exposure": { "description": "Metformin monotherapy", "definition": "First prescription of metformin", "index_date": "First metformin prescription date", "confidence": "high" },
    "comparator": { "description": "Sulfonylurea monotherapy", "definition": "First prescription of sulfonylurea", "confidence": "high" },
    "outcome": {
      "primary": { "description": "Major adverse cardiovascular event (MACE)", "type": "time-to-event", "measurement": "ICD codes for MI, stroke, CV death", "time_window": "Up to 5 years", "confidence": "high" }
    },
    "time": { "follow_up_period": "Up to 5 years", "index_date_definition": "First prescription date", "study_period": "2010-2023" }
  },
  "study_design_candidates": ["Retrospective cohort", "Active-comparator new-user design"],
  "potential_biases": ["Confounding by indication", "Immortal time bias", "Time-varying confounding"],
  "key_covariates": ["age", "sex", "BMI", "HbA1c", "eGFR", "smoking", "prior CVD history", "concomitant medications"]
}
```

**Gate#1:** Approved — clinically meaningful, feasible with claims data.

**Step completion report:** PICO를 구조화했습니다. 산출물은 `research/01_pico/`에 있고,
같은 요약을 `research/01_pico/SUMMARY.md`에 전체 재작성해 저장했습니다. 다음으로 문헌을
살피면 근거 공백을 확인할 수 있습니다. `research/PROGRESS.md`도 갱신되었습니다.

---

## Step 2: Literature Search

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/pubmed_search.py \
  --query "(metformin OR biguanide) AND (sulfonylurea) AND (cardiovascular OR MACE) AND (type 2 diabetes) AND (cohort OR retrospective)" \
  --email researcher@hospital.org \
  --retmax 30 \
  --mindate 2018/01/01 \
  --output research/02_literature/literature/
```

Found 847 results, retrieved top 30.

---

## Step 3: Evidence Table

Extracted data from 30 papers. Key findings:
- 15 cohort studies, 3 meta-analyses, 12 other designs
- Consistent direction: metformin associated with lower CV risk (HR 0.70-0.90)
- Gap: Few studies in elderly (>75), limited data on eGFR <30

**Gate#2:** Approved — confirmed gap in elderly population subgroup.

---

## Step 4: Variable Definition

Defined 23 variables: 1 exposure, 1 primary outcome, 3 secondary outcomes, 15 covariates, 3 time variables.

**Gate#3:** Primary endpoint = first MACE (composite: MI, stroke, CV death).
**Gate#4:** Feasibility confirmed — all required variables available in claims database.

---

## Step 5: Protocol

Generated protocol v0.1 with active-comparator new-user design.

**Gate#5:** Approved after minor revision (added washout period specification).

---

## Step 6: SAP

- Primary: Cox proportional hazards, adjusted for 12 covariates
- Sensitivity: ITT, per-protocol, competing risk (Fine-Gray)
- Subgroups: age (<65, 65-75, >75), eGFR (<60, >=60), prior CVD

---

## Step 7: Table/Figure Shells

Generated: Table 1 (baseline), Table 2 (primary results), Table 3 (subgroups), Figure 1 (flow diagram), Figure 2 (Kaplan-Meier), Figure 3 (forest plot).

---

## Step 8: Synthetic Dry-Run

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/analysis_runner.py --mode synthetic --project-dir research/ --sap-version v0.1
```

Output: Pipeline verified. Tables populated with synthetic data. **NOT REAL DATA** watermark applied.

---

## Step 9: Data Prep & QC

Cohort DSL:
```
INCLUDE: patients.age >= 18
INCLUDE: patients.diabetes_type = 'T2DM'
INCLUDE: prescriptions.drug_class IN ('metformin', 'sulfonylurea')
EXCLUDE: patients.prior_insulin = 1
EXCLUDE: patients.age < 18
INDEX: prescriptions.first_prescription_date
FOLLOWUP: outcomes.mace_date OR patients.death_date OR patients.last_visit_date
```

QC: 0 temporal violations, 3.2% missing BMI, 12,450 patients, 892 events.

**Gate#9:** Approved.

---

## Step 10: Real Analysis

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/analysis_runner.py --mode real --project-dir research/ --data-path extracted_cohort.csv --sap-version v0.1
```

Primary result: HR 0.78 (95% CI 0.68-0.89, p<0.001).

**Gate#10:** Approved — results clinically plausible, consistent with literature.

---

## Step 11: Manuscript

Generated IMRD manuscript. STROBE checklist: 21/22 items covered (item 22 pending funder info).

**Gate#11:** Approved after revision of Discussion limitations.

---

## Step 12: Submission Package

Final package:
- manuscript.md (4,200 words)
- 3 tables, 3 figures
- STROBE checklist (complete)
- Protocol and SAP (supplementary)
- Audit trail summary
