# Statistical Analysis Plan v{{version}}

**Project:** {{project_name}}
**Protocol version:** {{protocol_version}}
**Date:** {{date}}
**Status:** Draft

---

## 1. Study Design Summary
{{design_summary}}

## 2. Analysis Populations
{{analysis_populations}}

## 3. Primary Analysis

### 3.1 Primary Outcome
{{primary_outcome}}

### 3.2 Statistical Model
{{primary_model}}

### 3.3 Covariates for Adjustment
{{covariates}}

### 3.4 Effect Measure
{{effect_measure}}

### 3.5 Handling of Missing Data
{{missing_data}}

## 4. Secondary Analyses

### 4.1 Secondary Outcomes
{{secondary_analyses}}

## 5. Sensitivity Analyses (Pre-specified)

| # | Description | Rationale | Label |
|---|------------|-----------|-------|
{{sensitivity_table}}

## 6. Subgroup Analyses (Pre-specified)

| # | Subgroup | Variable | Rationale | Label |
|---|----------|----------|-----------|-------|
{{subgroup_table}}

## 7. Exploratory Analyses

*Any analysis not listed above will be labeled as exploratory in the manuscript.*

{{exploratory_analyses}}

## 8. Multiple Comparisons
{{multiple_comparisons}}

## 9. Model Diagnostics

- Proportional hazards assumption (if Cox): {{ph_check}}
- Multicollinearity (VIF): {{vif_check}}
- Goodness of fit: {{gof_check}}
- Influential observations: {{influential_obs}}

## 10. Sample Size and Precision

효과크기는 문헌 근거로만 적는다. 발명하지 않는다.

### 10.1 Effect size source (literature only)
{{effect_size_source}}

### 10.2 A priori calculation (when still possible)

Approach: closed-form / simulation

{{sample_size_approach}}

### 10.3 Already-collected retrospective data

post-hoc power 대신 precision과 confidence interval을 우선한다.

{{precision_plan}}

## 11. Tables and Figures Plan

| # | Type | Content | Pre-spec? |
|---|------|---------|-----------|
{{tables_figures}}

## 12. Software and Packages
{{software}}

---

*SAP는 결과를 보기 전에 추정 목표와 방법을 기록한다. 결과를 본 뒤의 변경을 막지는 않는다 — 사전 지정과 사후 변경을 구분해 기록하며, 추가분은 exploratory로 표시한다.*
