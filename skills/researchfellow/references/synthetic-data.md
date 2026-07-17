# Synthetic Data & Rehearsal Mode

Lets a user WITHOUT data experience steps 9–13 end to end ("모의 완주") on a
deterministic, watermarked fake cohort. Rehearsal is practice, never evidence:
its outputs live in a physically separate tree and can never become real
artifacts.

## Consent — forced, verbatim (D8)

Before generating anything, ask with AskUserQuestion and record the answer:

```
Question: "이 데이터는 완전한 가짜입니다. 파이프라인 연습·검증용이며 논문 결과로
           사용할 수 없습니다. 생성할까요?"
Options:
  - "동의하고 생성" — 가짜 코호트를 만들어 전 과정을 연습합니다
  - "아니요" — 실제 데이터가 준비되면 다시 진행합니다
```

On consent: append audit `SYNTHETIC_DATA_GENERATED {n, seed, consented_at}` and set
`state.json.rehearsal = {active: true, generated_at, seed, consented_at}` — this is
the ONLY state.json change rehearsal ever makes (steps/artifacts/gates/execution_mode
stay untouched).

## Pipeline

1. Author `research/rehearsal/synth-spec.json` from `variables.json` + the protocol —
   clinically plausible parameters, schema in `templates/synth-spec-template.json`
   (dists: bernoulli, truncated normal, categorical, uniform, exponential,
   outcome_model(logit), outcome_model_survival(PH exponential + admin censoring)).
2. Generate (consented-at = the timestamp of the consent above):
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/synth_builder.py \
       --spec-path research/rehearsal/synth-spec.json \
       --variables-path research/04_variables/variables.json \
       --output-csv research/rehearsal/synthetic_cohort.csv \
       --output-meta research/rehearsal/synthetic_cohort.meta.json \
       --consented-at <ISO>
   ```
3. Walk the steps against the fake cohort, all outputs under `research/rehearsal/`:
   - extraction plan: dsl_compiler → `research/rehearsal/extraction-plan.sql`
   - QC: qc_checker → `research/rehearsal/qc-report.json`
   - analysis: `analysis_runner.py --mode rehearsal --data-path
     research/rehearsal/synthetic_cohort.csv` → `research/rehearsal/analysis/results.json`
     (no gate checks — rehearsal is practice; output carries
     `"NOT REAL DATA — REHEARSAL ONLY"`)
   - manuscript practice: `research/rehearsal/manuscript-draft.md` only — **never**
     `research/11_manuscript/manuscript.md`, and synthetic numbers never enter a real manuscript's
     Results/Conclusions/Abstract (계명 1 unchanged).
4. Every rehearsal output message carries the fake-data label; the dashboard shows a
   separate "연습 모드" badge that never mixes with real progress counts.

## Hard rules

- **Triple watermark**: `is_synthetic=1` in-band column + `.meta.json` sidecar +
  rehearsal path. `analysis_runner --mode real` REFUSES any input carrying the
  in-band watermark (exit 1) — a rehearsal file cannot masquerade as real data
  even if copied or renamed.
- Rehearsal emits **no telemetry step events** (the funnel tracks real progress).
- Ending rehearsal: when real data arrives, run the REAL Step 9 flow from scratch
  (gates and all). `research/rehearsal/` may be kept (practice reference) or
  deleted — either way it never feeds the real pipeline.
