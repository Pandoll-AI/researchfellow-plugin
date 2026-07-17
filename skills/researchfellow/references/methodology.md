# Methodology — retrospective observational analysis selection

> Read this before proposing any Step-5/6 (protocol/SAP) analysis or any Step-10
> real analysis. This is the **method-selection knowledge layer**: you (the host
> LLM) reason with it to pick an appropriate method, then record the choice as an
> `analysis_plan` artifact. You do **not** invent numbers — `analysis_runner.py`
> emits a reproducible script and (when individual data + deps are present) fits
> real models. Aggregate 2×2 input yields a point estimate only, never CI/p.

Scope: retrospective observational designs (cohort, case-control, cross-sectional)
on routinely-collected data (EMR/claims/registry). Prediction models → TRIPOD
(see `checklist-templates.md`). RCT/PRISMA are out of current scope.

---

## 0. Estimand first (do this before choosing a model)

Pin the estimand before the method — the method serves the estimand, not the
reverse. Frame it as a **target trial** you are emulating:

- **Population** — eligibility, and the *time zero* at which follow-up starts.
- **Exposure/Treatment strategies** being contrasted, and how exposure is ascertained.
- **Comparator** (active comparator preferred over "non-user" to reduce confounding by indication).
- **Outcome** and its ascertainment window.
- **Contrast/measure** — risk difference, risk ratio, odds ratio, hazard ratio.
- **Time zero alignment** — eligibility, exposure assignment, and follow-up start
  must coincide. Misalignment is the usual source of **immortal time bias** (§4).

Record the estimand in the protocol; every method choice below references it.

---

## 1. Design → base analysis

| Design | Primary contrast | Base analysis |
|---|---|---|
| Cohort, binary outcome | RR / RD (report both relative and absolute) | log-binomial or Poisson-with-robust-SE for RR; logistic for OR (rare-outcome only) |
| Cohort, time-to-event | HR | Cox PH; competing risks if applicable (§4) |
| Case-control | OR | conditional logistic (matched) / logistic (unmatched) |
| Cross-sectional | prevalence ratio | log-binomial / Poisson-robust |

> **OR ≠ RR** unless the outcome is rare. For common outcomes report RR/RD, not OR —
> logistic OR overstates the effect. This is a frequent reviewer objection.

---

## 2. Confounding control (the core of retrospective work)

Unlike an RCT, confounding is not randomized away. Choose deliberately:

- **Multivariable regression** — adjust for measured confounders. Simple, but
  requires the outcome model to be correct and enough events (see EPV, §5).
- **Propensity score (PS)** — model probability of exposure given covariates, then:
  - *Matching* — intuitive, drops unmatched subjects (changes the estimand/population).
  - *Stratification* — deciles of PS.
  - *IPTW (inverse-probability-of-treatment weighting)* — keeps everyone, estimates
    ATE/ATT; **check for extreme weights** and consider stabilized/trimmed weights.
- **Standardization / g-computation** — model the outcome, predict under each
  exposure, average. Naturally gives marginal RR/RD.
- **Doubly-robust (AIPW / TMLE)** — combines PS + outcome model; consistent if
  *either* is correct. Preferred when feasible.
- **Unmeasured confounding is unavoidable** in observational data — quantify residual
  bias with an **E-value** (§6) rather than only stating it as a limitation.

Confounding by indication (sicker patients get treated) is the dominant threat;
an **active-comparator, new-user** design mitigates it more than any adjustment.

---

## 3. Choosing PS vs regression

- Few events but many confounders → **PS** (models exposure, not the rare outcome;
  sidesteps the EPV limit on the outcome model).
- Rich outcome model, adequate events → **regression** or **g-computation**.
- Want a marginal effect (RD/RR over the population) → **IPTW or standardization**.
- Want robustness to one model being wrong → **doubly-robust**.
- Always report **covariate balance** (standardized mean differences, target < 0.1)
  after PS matching/weighting — this is the PS analogue of "Table 1 balance".

---

## 4. Time-to-event specifics

- **Cox PH** — check the proportional-hazards assumption (Schoenfeld residuals). If
  violated: stratify, add time-interaction, or report time-specific effects.
- **Competing risks** — when a competing event precludes the outcome (e.g. death vs
  the event of interest), a standard Kaplan-Meier/Cox on cause-specific hazard
  **overestimates** cumulative incidence. Use **cause-specific Cox** for etiology or
  **Fine-Gray subdistribution** for prediction/absolute risk. State which and why.
- **Time-varying exposure** — if exposure status changes over follow-up, use a
  **time-dependent Cox** (start/stop records). Do not classify a whole subject by
  post-baseline exposure.
- **Immortal time bias** — the interval where the outcome *cannot* occur by design
  (e.g. time until a prescription defines "exposed"). Avoid by: time-zero alignment
  (§0), **landmark analysis**, or **time-dependent exposure**. Never allocate
  immortal time to the exposed group.

---

## 5. Preconditions & diagnostics (validate before trusting a fit)

`analysis_runner.py` surfaces these as warnings; you must address them in the SAP:

- **EPV (events per variable)** — aim ≥ 10 events per covariate for a stable
  regression; below that, prefer PS on exposure, penalization, or fewer covariates.
- **Positivity / overlap** — every covariate stratum must have both exposed and
  unexposed. Non-overlap → trim, restrict, or redefine the population; report it.
- **Separation** — perfect prediction gives infinite/unstable estimates; the runner
  flags `perfect_separation` rather than reporting a bogus CI.
- **Calibration/discrimination** — for prediction models (TRIPOD), report both;
  a single AUC is insufficient.

---

## 6. Sensitivity analysis menu (pick what fits the threats)

- **E-value** — how strong an unmeasured confounder would need to be to explain away
  the effect. Report for the point estimate and the CI bound nearest the null.
- **Quantitative bias analysis** — for misclassification / selection bias.
- **Alternative definitions** — vary exposure/outcome code definitions; RECORD R8.
- **Negative controls** — an outcome/exposure with no plausible causal link; a signal
  there reveals residual bias.
- **Missing data** — complete-case is a sensitivity *comparator*, not the primary
  approach if data are not MCAR. Prefer **multiple imputation (MICE)**; report the
  imputation model and number of imputations.

---

## 7. Method → reporting-item crosswalk

When you pick a method, you also incur reporting obligations. Map them so
`checklist_map.py` can verify coverage (STROBE/RECORD ids from `checklist-templates.md`):

| Method choice | Must report (item) |
|---|---|
| Any adjustment | STROBE 12a (all statistical methods, confounders) |
| Subgroup/interaction | STROBE 12b, 17 |
| Missing data / MICE | STROBE 12c, 14b |
| Loss to follow-up | STROBE 12d |
| Sensitivity (E-value, alt-def, neg-control) | STROBE 12e, 17 |
| PS matching/weighting | STROBE 12a + balance (SMD) table |
| Relative + absolute effect | STROBE 16a, 16c |
| Code definitions (EMR/claims) | RECORD R5; validation R6; sensitivity R8 |
| Study period / linkage | RECORD R4 / R3 |

---

## 8. `analysis_plan` artifact (what to record into state)

After selecting, record a machine-usable spec so the choice is auditable and the
runner/checklist can consume it:

```json
{
  "estimand": {"population": "...", "exposure": "...", "comparator": "...",
               "outcome": "...", "measure": "HR", "time_zero": "..."},
  "design": "cohort",
  "primary_method": "cox_ph",
  "confounding_strategy": "iptw",
  "covariates": ["age", "sex", "comorbidity_score"],
  "competing_risks": {"present": true, "approach": "fine_gray"},
  "missing_data": {"approach": "mice", "n_imputations": 20},
  "sensitivity": ["e_value", "alt_exposure_definition", "negative_control_outcome"],
  "reporting_items": ["STROBE-12a", "STROBE-12e", "STROBE-16a", "RECORD-R5", "RECORD-R8"],
  "preconditions_checked": ["epv", "positivity", "ph_assumption"]
}
```

Store as `research/10_analysis/analysis-plan.json`, register it as the `analysis_plan` artifact,
and confirm with the user before Step 9/10. The emitted R script (§8 analysis_plan artifact) is the
authoritative, reproducible analysis; the local Python fit is a preview.
