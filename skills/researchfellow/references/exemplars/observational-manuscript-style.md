# Exemplar style — high-impact observational manuscripts

> Distilled **conventions** from top general-medical journals (NEJM, JAMA, The
> Lancet, Annals of Internal Medicine) for retrospective/observational studies.
> This is a style guide, **not** copyrighted text — no article content is
> reproduced. Use it to shape voice, structure, and ordering toward a target
> venue. Generic exemplar style is free; venue-specific optimization and reviewer
> anticipation are the paid tier (journal_fit / reviewer_playbook).

## How to use
Pick the closest venue's conventions, then write the manuscript template
(`templates/manuscript-template.md`) in that voice. Keep every numeric claim
traceable to an analysis output table (never write a number the emitted script
did not produce).

---

## Structured abstract (most venues cap ~250–350 words)

Order and label sections explicitly. Common patterns:

- **NEJM-like**: Background · Methods · Results · Conclusions. Terse. Results lead
  with the primary effect estimate + 95% CI + absolute numbers. Funding/registration
  noted at end.
- **JAMA-like**: Importance · Objective · Design/Setting/Participants · Exposures ·
  Main Outcomes and Measures · Results · Conclusions and Relevance. The most
  granular structured abstract — each label is mandatory. Results must give the
  primary outcome with absolute event rates in both groups AND the relative effect
  with CI. Include the number analyzed and key demographics.
- **Lancet-like**: Background · Methods · Findings · Interpretation · Funding.
  "Findings" (not "Results") and "Interpretation" (not "Conclusions"). Findings
  open with the cohort size and dates, then the primary estimate.
- **Annals-like**: Background · Objective · Design · Setting · Patients ·
  Measurements · Results · Limitations · Conclusion. Uniquely puts **Limitations
  in the abstract** — surface the single most important one.

## First Methods sentence
State the design in the first sentence ("We conducted a retrospective cohort
study of …"). For routinely-collected data, name the source and coverage
immediately (satisfies RECORD R1/R4). Give time-zero explicitly.

## Results narration order
1. Cohort assembly: numbers eligible → included → analyzed (flow); dates.
2. Baseline: reference Table 1; note balance (or, after PS weighting, SMDs).
3. Primary outcome: **absolute** event counts/rates in each group first, then the
   **relative** effect (HR/RR/OR) with 95% CI. Report both — reviewers ask for it.
4. Secondary/subgroup, then sensitivity analyses (E-value, alternative definitions).
5. Do not introduce methods or interpretation in Results.

## Discussion shape (first paragraph = the answer)
Open with a one–two sentence plain statement of the main finding tied to the
objective. Then: comparison with prior literature → mechanism/plausibility →
strengths → **limitations** (selection/confounding by indication, information
bias/misclassification, unmeasured confounding, generalizability) → clinical
implications → a measured conclusion. Avoid causal language for observational
associations unless a formal causal design (e.g. target-trial emulation) justifies it.

## Table 1 conventions
Rows = baseline covariates; columns = exposure groups (+ overall). Report n (%) for
categorical, mean (SD) or median (IQR) for continuous. After PS matching/weighting,
add a standardized-mean-difference column instead of p-values (comparing balance,
not testing it). Do not p-value-test Table 1 in a weighted/matched design.

## Sentence-level register
- Past tense for what was done and found; present tense for what is known.
- Precise, unhedged reporting of estimates; hedged causal interpretation.
- Define each abbreviation once; keep the primary estimate un-abbreviated in the abstract.
- Absolute + relative together ("… 12.1% vs 8.4%; adjusted HR 1.42, 95% CI 1.10–1.83").
