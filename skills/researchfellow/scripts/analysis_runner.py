#!/usr/bin/env python3
"""Statistical analysis runner for the Research Assistant skill.

Runs analysis in synthetic or real mode. Computes effect measures (RR, OR),
fits GLM binomial and Cox PH models when dependencies are available.

Usage:
    python3 analysis_runner.py --mode synthetic --project-dir research/ --sap-version v0.1
    python3 analysis_runner.py --mode real --project-dir research/ --data-path data.csv --sap-version v0.1
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import warnings
from datetime import datetime
from typing import Any, Dict, List, Optional

# Real-data gate check shares the SAME function state_tool.py uses, so an LLM
# cannot bypass the gate by editing prose (FR-G4 last line of defense).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from state_tool import check_real_data_gates, detect_schema
    from rf_paths import (
        resolve_analysis_output_dir,
        resolve_analysis_plan_report_path,
        resolve_analysis_scripts_dir,
        resolve_qc_report_path,
        resolve_rehearsal_analysis_dir,
        resolve_state_path,
    )
except ImportError:  # pragma: no cover - fallback to legacy gates.json path
    check_real_data_gates = None
    detect_schema = None
    resolve_analysis_output_dir = None
    resolve_analysis_plan_report_path = None
    resolve_analysis_scripts_dir = None
    resolve_qc_report_path = None
    resolve_rehearsal_analysis_dir = None
    resolve_state_path = None


class MissingDependency(Exception):
    """Raised when a REAL-mode model fit needs a stats package that is absent.

    In real mode this is fatal (the runner exits 1). We never downgrade a real
    analysis to a partial result — see requirements.txt policy note.
    """

    def __init__(self, dep: str) -> None:
        self.dep = dep
        super().__init__(dep)


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    if denominator == 0:
        return None
    return numerator / denominator


def compute_effects(row_counts: Dict[str, Any]) -> Dict[str, Optional[float]]:
    n_exposed = float(row_counts.get("exposed", 0) or 0)
    n_unexposed = float(row_counts.get("unexposed", 0) or 0)
    ev_exposed = float(row_counts.get("events_exposed", 0) or 0)
    ev_unexposed = float(row_counts.get("events_unexposed", 0) or 0)

    risk_exposed = _safe_div(ev_exposed, n_exposed)
    risk_unexposed = _safe_div(ev_unexposed, n_unexposed)

    rr = None
    if risk_exposed is not None and risk_unexposed not in (None, 0):
        rr = risk_exposed / risk_unexposed

    odds_exposed = _safe_div(ev_exposed, max(n_exposed - ev_exposed, 0))
    odds_unexposed = _safe_div(ev_unexposed, max(n_unexposed - ev_unexposed, 0))

    or_est = None
    if odds_exposed is not None and odds_unexposed not in (None, 0):
        or_est = odds_exposed / odds_unexposed

    return {
        "risk_exposed": risk_exposed,
        "risk_unexposed": risk_unexposed,
        "risk_ratio": rr,
        "odds_ratio": or_est,
    }


def effect_from_counts(row_counts: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate 2x2 effect estimate — the HONEST result for aggregate input.

    A 2x2 table gives a point OR/RR and NOTHING more. A logistic/GLM fit on two
    aggregated rows is a saturated model whose CI/p-value are artefacts, not
    inference. So we deliberately return `status: aggregate_only` and no CI/p:
    individual-level data (and the emitted analysis script) are required for a
    real confidence interval. This is the fix for the "false precision" finding.
    """
    n_exposed = int(row_counts.get("exposed", 0) or 0)
    n_unexposed = int(row_counts.get("unexposed", 0) or 0)
    if n_exposed <= 0 or n_unexposed <= 0:
        return {"status": "skipped", "reason": "insufficient_group_counts"}

    effects = compute_effects(row_counts)
    return {
        "status": "aggregate_only",
        "method": "2x2_effect_estimate",
        "odds_ratio": effects["odds_ratio"],
        "risk_ratio": effects["risk_ratio"],
        "ci_p_available": False,
        "note": (
            "OR/RR are point estimates from a 2x2 table. Confidence intervals and "
            "p-values require individual-level data — run the emitted analysis "
            "script on your records, do not report CI/p from aggregate counts."
        ),
    }


def fit_glm_individual(
    df: Any,
    outcome_col: str = "event",
    exposure_col: str = "exposed",
    covariates: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Real per-record logistic regression (REAL mode). Raises MissingDependency
    when statsmodels/pandas are absent — real analysis must not silently skip."""
    try:
        import numpy as np  # noqa: F401  (statsmodels needs it)
        import statsmodels.api as sm
    except ImportError as exc:
        raise MissingDependency("statsmodels") from exc

    cols = [exposure_col] + list(covariates or [])
    missing_cols = [c for c in cols + [outcome_col] if c not in df.columns]
    if missing_cols:
        return {"status": "failed", "reason": f"missing_columns:{missing_cols}"}

    # Explicit design matrix (no formula strings — avoids column-name injection).
    x = sm.add_constant(df[cols].astype(float), has_constant="add")
    y = df[outcome_col].astype(float)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = sm.Logit(y, x).fit(disp=0)
    except Exception as exc:  # PerfectSeparation, singular matrix, non-convergence
        return {"status": "failed", "reason": type(exc).__name__}

    beta = float(fit.params.get(exposure_col, float("nan")))
    conf_int = fit.conf_int().loc[exposure_col].tolist()
    lo, hi = float(conf_int[0]), float(conf_int[1])
    if not (math.isfinite(beta) and math.isfinite(lo) and math.isfinite(hi)):
        return {"status": "failed", "reason": "unstable_estimate_perfect_separation"}

    return {
        "status": "ok",
        "method": "Logistic (individual-level)",
        "n": int(df.shape[0]),
        "adjusted_for": list(covariates or []),
        "coef": beta,
        "odds_ratio": math.exp(beta),
        "or_ci95": [math.exp(lo), math.exp(hi)],
        "p_value": float(fit.pvalues.get(exposure_col, 1.0)),
    }


def fit_cox(survival_records: List[Dict], time_varying_records: Optional[List[Dict]] = None) -> Dict[str, Any]:
    if time_varying_records:
        return _fit_cox_time_varying(time_varying_records)

    if not survival_records:
        return {"status": "skipped", "reason": "no_survival_records"}

    try:
        import pandas as pd
        from lifelines import CoxPHFitter
    except ImportError as exc:  # real-mode caller: fatal, never a silent skip
        raise MissingDependency("lifelines") from exc

    df = pd.DataFrame(survival_records)
    required = {"time", "event", "exposed"}
    if not required.issubset(set(df.columns)):
        return {"status": "failed", "reason": "survival_records_missing_columns"}

    cph = CoxPHFitter()
    try:
        cph.fit(df, duration_col="time", event_col="event")
    except Exception as exc:
        return {"status": "failed", "reason": str(exc)}

    summary = cph.summary
    if "exposed" not in summary.index:
        return {"status": "failed", "reason": "exposed_covariate_not_in_model"}

    row = summary.loc["exposed"]
    return {
        "status": "ok",
        "method": "CoxPH",
        "hazard_ratio": float(row["exp(coef)"]),
        "hr_ci95": [float(row["exp(coef) lower 95%"]), float(row["exp(coef) upper 95%"])],
        "p_value": float(row["p"]),
    }


def _fit_cox_time_varying(records: List[Dict]) -> Dict[str, Any]:
    try:
        import pandas as pd
        from lifelines import CoxTimeVaryingFitter
    except ImportError as exc:  # real-mode caller: fatal, never a silent skip
        raise MissingDependency("lifelines") from exc

    df = pd.DataFrame(records)
    required = {"id", "start", "stop", "event", "exposed"}
    if not required.issubset(set(df.columns)):
        return {"status": "failed", "reason": "time_varying_records_missing_columns"}

    ctv = CoxTimeVaryingFitter()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ctv.fit(df, id_col="id", start_col="start", stop_col="stop", event_col="event")
    except Exception as exc:
        return {"status": "failed", "reason": str(exc)}

    summary = ctv.summary
    if "exposed" not in summary.index:
        return {"status": "failed", "reason": "exposed_covariate_not_in_model"}

    row = summary.loc["exposed"]
    return {
        "status": "ok",
        "method": "CoxTimeVarying",
        "hazard_ratio": float(row["exp(coef)"]),
        "hr_ci95": [float(row["exp(coef) lower 95%"]), float(row["exp(coef) upper 95%"])],
        "p_value": float(row["p"]),
    }


def run_synthetic(project_dir: str, sap_version: str) -> dict:
    state_path = resolve_state_path(project_dir) if resolve_state_path else os.path.join(project_dir, "state.json")
    project_id = "unknown"
    if os.path.exists(state_path):
        with open(state_path) as f:
            project_id = json.load(f).get("project_name", "unknown")

    seed = abs(hash(f"{project_id}:{sap_version}")) % 10_000
    rng = random.Random(seed)

    total = 800 + rng.randint(0, 400)
    exposed = int(total * (0.3 + rng.random() * 0.3))
    unexposed = total - exposed
    events_exposed = int(exposed * (0.05 + rng.random() * 0.08))
    events_unexposed = int(unexposed * (0.03 + rng.random() * 0.05))

    counts = {
        "total": total,
        "exposed": exposed,
        "unexposed": unexposed,
        "events_exposed": events_exposed,
        "events_unexposed": events_unexposed,
    }

    return {
        "source": "synthetic",
        "watermark": "NOT REAL DATA",
        "sap_version": sap_version,
        "generated_at": datetime.now().isoformat(),
        "table1": counts,
        "model_summary": compute_effects(counts),
        "model_fits": {
            # Synthetic data is aggregate mock — never dress it as inference.
            "glm_binomial": effect_from_counts(counts),
            "cox": {"status": "skipped", "reason": "planning_mode_no_real_survival_data"},
        },
    }


# ---------------------------------------------------------------------------
# Reproducible-script emission (`plan` mode). The tool does NOT compute numbers
# here — it emits an auditable R script the user runs in their own stats env, and
# checks preconditions. This is how methodology breadth (IPTW, Fine-Gray, MICE,
# ...) is covered without re-implementing every method in Python.
# ---------------------------------------------------------------------------

# method key -> (R packages, model snippet builder). Snippets are templates keyed
# on the analysis_plan artifact (see references/methodology.md §8).
_R_PACKAGES = {
    "logistic": ["sandwich", "lmtest"],
    "log_binomial": ["sandwich", "lmtest"],
    "poisson_robust": ["sandwich", "lmtest"],
    "cox_ph": ["survival"],
    "fine_gray": ["cmprsk", "survival"],
    "conditional_logistic": ["survival"],
}

_CONFOUNDING_PACKAGES = {
    "iptw": ["WeightIt", "cobalt", "survey"],
    "ps_match": ["MatchIt", "cobalt"],
    "gcomp": ["marginaleffects"],
    "doubly_robust": ["WeightIt", "marginaleffects"],
}


def _r_covariate_formula(covariates: List[str]) -> str:
    return " + ".join(covariates) if covariates else "1"


def emit_analysis_script(method_spec: Dict[str, Any]) -> str:
    """Build a reproducible R analysis script from an analysis_plan artifact.

    The script is the authoritative analysis (medical-stats convention = R). It is
    emitted, never executed by this tool. Unknown method keys degrade to a clearly
    flagged TODO block rather than a silent omission.
    """
    est = method_spec.get("estimand", {}) or {}
    method = method_spec.get("primary_method", "logistic")
    strategy = method_spec.get("confounding_strategy", "multivariable")
    covariates = list(method_spec.get("covariates", []) or [])
    exposure = est.get("exposure_var", "exposed")
    outcome = est.get("outcome_var", "event")
    measure = est.get("measure", "OR")

    pkgs = sorted(set(_R_PACKAGES.get(method, []) + _CONFOUNDING_PACKAGES.get(strategy, [])))
    cov_formula = _r_covariate_formula(covariates)

    lines: List[str] = []
    lines.append("# ============================================================")
    lines.append("# ResearchFellow — reproducible analysis script (AUTHORITATIVE).")
    lines.append("# Generated from analysis-plan.json. Run in your own R env on")
    lines.append("# individual-level data. This tool does NOT fabricate results.")
    lines.append("# ============================================================")
    lines.append(f"# Estimand : {est.get('population','?')} | exposure={exposure} "
                 f"vs {est.get('comparator','?')} | outcome={outcome} | measure={measure}")
    lines.append(f"# Method   : primary={method}, confounding={strategy}")
    lines.append(f"# Adjusted : {', '.join(covariates) if covariates else '(none)'}")
    lines.append("")
    if pkgs:
        lines.append(f"# install.packages(c({', '.join(repr(p) for p in pkgs)}))")
        for p in pkgs:
            lines.append(f"library({p})")
    lines.append("")
    lines.append('df <- read.csv("data.csv")  # <- your individual-level extract')
    lines.append("")

    # --- confounding-control setup ---
    if strategy == "ps_match":
        lines.append("# Propensity-score matching (report SMD balance, cobalt::love.plot)")
        lines.append(f"m <- matchit({exposure} ~ {cov_formula}, data = df, method = \"nearest\")")
        lines.append("summary(m); love.plot(m)")
        lines.append("adf <- match.data(m)")
        data_obj, weights = "adf", None
    elif strategy in ("iptw", "doubly_robust"):
        lines.append("# IPTW: stabilized weights; inspect extremes before fitting")
        lines.append(f"w <- weightit({exposure} ~ {cov_formula}, data = df, "
                     "method = \"ps\", estimand = \"ATE\", stabilize = TRUE)")
        lines.append("bal.tab(w, un = TRUE)  # standardized mean differences (< 0.1)")
        data_obj, weights = "df", "w$weights"
    else:
        data_obj, weights = "df", None

    lines.append("")
    lines.append("# --- primary model ---")
    rhs = f"{exposure} + {cov_formula}" if strategy in ("multivariable", "none") and covariates else exposure
    warg = f", weights = {weights}" if weights else ""
    if method == "cox_ph":
        time = est.get("time_var", "time")
        lines.append(f"fit <- coxph(Surv({time}, {outcome}) ~ {rhs}, data = {data_obj}{warg})")
        lines.append("cox.zph(fit)  # proportional-hazards assumption check")
        lines.append("summary(fit)  # HR + 95% CI")
    elif method == "fine_gray":
        time = est.get("time_var", "time")
        lines.append("# Competing risks: subdistribution hazard (event coded 1=event, 2=competing)")
        lines.append(f"fg <- crr({data_obj}${time}, {data_obj}${outcome}, "
                     f"cov1 = model.matrix(~ {rhs}, {data_obj})[,-1])")
        lines.append("summary(fg)")
    elif method == "log_binomial":
        lines.append(f"fit <- glm({outcome} ~ {rhs}, data = {data_obj}, "
                     f"family = binomial(link = \"log\"){warg})")
        lines.append("coeftest(fit, vcov = sandwich)  # robust SE -> risk ratio")
    elif method == "poisson_robust":
        lines.append(f"fit <- glm({outcome} ~ {rhs}, data = {data_obj}, "
                     f"family = poisson(){warg})")
        lines.append("coeftest(fit, vcov = sandwich)  # robust SE -> rate/risk ratio")
    elif method == "conditional_logistic":
        strata = est.get("strata_var", "match_id")
        lines.append(f"fit <- clogit({outcome} ~ {exposure} + {cov_formula} + strata({strata}), data = {data_obj})")
        lines.append("summary(fit)")
    elif method == "logistic":
        lines.append(f"fit <- glm({outcome} ~ {rhs}, data = {data_obj}, "
                     f"family = binomial(){warg})")
        lines.append("if (exists(\"sandwich\")) coeftest(fit, vcov = sandwich) else summary(fit)")
        lines.append("# NOTE: OR approximates RR only for a RARE outcome (methodology.md §1)")
    else:
        lines.append(f"# TODO(unknown primary_method={method!r}): no template — specify the model.")

    lines.append("")
    lines.append("# --- sensitivity analyses ---")
    for s in method_spec.get("sensitivity", []) or []:
        if s == "e_value":
            lines.append("# E-value: install.packages('EValue'); EValue::evalues.OR(<est>, <lo>, <hi>)")
        else:
            lines.append(f"# sensitivity: {s} (see methodology.md §6)")
    lines.append("")
    lines.append("# --- report both relative and absolute effects (STROBE 16a/16c) ---")
    return "\n".join(lines) + "\n"


def check_preconditions(counts: Dict[str, Any], method_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Thin, deterministic precondition warnings (methodology.md §5). Warns only;
    never fits a model."""
    warnings_out: List[Dict[str, Any]] = []
    n_cov = len(method_spec.get("covariates", []) or [])
    events = int(counts.get("events_exposed", 0) or 0) + int(counts.get("events_unexposed", 0) or 0)
    n_exposed = int(counts.get("exposed", 0) or 0)
    n_unexposed = int(counts.get("unexposed", 0) or 0)

    if n_cov and events / max(n_cov, 1) < 10:
        warnings_out.append({
            "check": "epv",
            "severity": "warning",
            "detail": f"~{events} events for {n_cov} covariates (EPV {events / n_cov:.1f} < 10). "
                      "Prefer PS-on-exposure, penalization, or fewer covariates.",
        })
    if n_exposed == 0 or n_unexposed == 0:
        warnings_out.append({
            "check": "positivity",
            "severity": "critical",
            "detail": "one exposure group is empty — no overlap; effect not estimable.",
        })
    if method_spec.get("primary_method") == "cox_ph":
        warnings_out.append({
            "check": "ph_assumption",
            "severity": "info",
            "detail": "Cox chosen — verify proportional hazards (cox.zph) in the emitted script.",
        })
    if method_spec.get("competing_risks", {}).get("present") and \
            method_spec.get("competing_risks", {}).get("approach") not in ("fine_gray", "cause_specific"):
        warnings_out.append({
            "check": "competing_risks",
            "severity": "warning",
            "detail": "competing risks present but approach is not fine_gray/cause_specific (methodology.md §4).",
        })
    return warnings_out


def run_plan(project_dir: str, plan_path: str, data_path: Optional[str] = None) -> dict:
    """Emit the reproducible R script + precondition report from an analysis plan.
    Planning activity — no gates, stdlib-only (data preconditions optional)."""
    with open(plan_path) as f:
        method_spec = json.load(f)

    script = emit_analysis_script(method_spec)
    scripts_dir = (resolve_analysis_scripts_dir(project_dir) if resolve_analysis_scripts_dir
                   else os.path.join(project_dir, "analysis", "scripts"))
    os.makedirs(scripts_dir, exist_ok=True)
    script_path = os.path.join(scripts_dir, "analysis.R")
    with open(script_path, "w") as f:
        f.write(script)

    preconditions: List[Dict[str, Any]] = []
    counts: Optional[Dict[str, Any]] = None
    if data_path and os.path.exists(data_path):
        try:
            if data_path.endswith(".json"):
                with open(data_path) as f:
                    data = json.load(f)
                counts = data.get("row_counts", data)
            else:
                counts = _counts_from_df(_read_csv_df(data_path))
        except MissingDependency:
            counts = None  # preconditions are best-effort in planning
        if counts:
            preconditions = check_preconditions(counts, method_spec)

    return {
        "mode": "plan",
        "script_path": script_path,
        "authoritative": "R script (run in your own environment)",
        "method": {
            "primary_method": method_spec.get("primary_method"),
            "confounding_strategy": method_spec.get("confounding_strategy"),
            "covariates": method_spec.get("covariates", []),
        },
        "preconditions": preconditions,
        "generated_at": datetime.now().isoformat(),
    }


def _require_pandas():
    try:
        import pandas as pd
    except ImportError as exc:
        raise MissingDependency("pandas") from exc
    return pd


def _read_csv_df(path: str):
    return _require_pandas().read_csv(path)


def _load_records_df(records: List[Dict]):
    return _require_pandas().DataFrame(records)


def _counts_from_df(df) -> Dict[str, int]:
    """Aggregate a 2x2 count table from an individual-level frame.

    Replaces the original fragile `df[df.get("exposed", False) == 1]` indexing
    with explicit boolean masks that don't misbehave on missing columns.
    """
    if "exposed" not in df.columns:
        return {"total": int(len(df)), "exposed": 0, "unexposed": 0,
                "events_exposed": 0, "events_unexposed": 0}
    exposed = df["exposed"].astype(bool)
    events_exposed = events_unexposed = 0
    if "event" in df.columns:
        ev = df["event"].astype(bool)
        events_exposed = int((exposed & ev).sum())
        events_unexposed = int((~exposed & ev).sum())
    return {
        "total": int(len(df)),
        "exposed": int(exposed.sum()),
        "unexposed": int((~exposed).sum()),
        "events_exposed": events_exposed,
        "events_unexposed": events_unexposed,
    }


def _load_and_fit(data_path: str):
    """Load data + fit models — shared by real and rehearsal modes.

    individual_df is set only when we have individual-level records (CSV, or
    JSON with a `records` list); otherwise the input is aggregate.
    MissingDependency anywhere below is FATAL — an analysis must never be
    reported as a partial/skipped result (same rule in both modes)."""
    individual_df = None
    survival_records: List[Dict] = []
    time_varying_records: List[Dict] = []

    try:
        if data_path.endswith(".json"):
            with open(data_path) as f:
                data = json.load(f)
            survival_records = data.get("survival_records", [])
            time_varying_records = data.get("time_varying_records", [])
            records = data.get("records")
            if records:
                individual_df = _load_records_df(records)
                counts = _counts_from_df(individual_df)
            else:
                counts = data.get("row_counts", data)
        else:
            # CSV is individual-level. pandas is a hard requirement here.
            individual_df = _read_csv_df(data_path)
            counts = _counts_from_df(individual_df)
            if {"time", "event", "exposed"}.issubset(individual_df.columns):
                survival_records = individual_df.to_dict("records")

        if individual_df is not None and {"event", "exposed"}.issubset(individual_df.columns):
            glm_fit = fit_glm_individual(individual_df)
        else:
            glm_fit = effect_from_counts(counts)
        cox_fit = fit_cox(survival_records, time_varying_records)
    except MissingDependency as exc:
        print(
            f"ERROR: this analysis requires '{exc.dep}' but it is not installed. "
            "Install runtime deps (pip install -r requirements.txt) and re-run — "
            "an analysis must not be reported as a partial/skipped result.",
            file=sys.stderr,
        )
        sys.exit(1)

    return counts, glm_fit, cox_fit


def _carries_synthetic_watermark(data_path: str) -> bool:
    """True when the input carries synth_builder's in-band watermark. Real-data
    analysis refuses such input outright (guardrail #1, enforced in code)."""
    try:
        if data_path.endswith(".json"):
            with open(data_path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                if data.get("is_synthetic"):
                    return True
                records = data.get("records") or data.get("survival_records") or []
                return bool(records and isinstance(records[0], dict) and records[0].get("is_synthetic"))
            return False
        with open(data_path, encoding="utf-8", errors="replace") as f:
            header = f.readline()
        return "is_synthetic" in [c.strip().strip('"') for c in header.split(",")]
    except OSError:
        return False


def run_rehearsal(project_dir: str, data_path: str, sap_version: str) -> dict:
    """Rehearsal analysis: the same fitting pipeline as real mode, NO gate
    checks (rehearsal is practice, not evidence). Output is watermarked and the
    caller stores it under research/rehearsal/ — physically separated from
    real artifacts; state.json steps/artifacts/execution_mode are untouched."""
    counts, glm_fit, cox_fit = _load_and_fit(data_path)
    return {
        "source": "rehearsal",
        "watermark": "NOT REAL DATA — REHEARSAL ONLY",
        "sap_version": sap_version,
        "analyzed_at": datetime.now().isoformat(),
        "table1": counts,
        "model_summary": compute_effects(counts),
        "model_fits": {
            "glm_binomial": glm_fit,
            "cox": cox_fit,
        },
    }


def run_real(project_dir: str, data_path: str, sap_version: str) -> dict:
    # Refuse synthetic input before anything else — a rehearsal file must never
    # masquerade as real evidence, however the gates stand.
    if _carries_synthetic_watermark(data_path):
        print("ERROR: input data carries an is_synthetic watermark — real-data "
              "analysis refuses synthetic input. Use --mode rehearsal instead.",
              file=sys.stderr)
        sys.exit(1)

    # Check real-data gates. Prefer state.json v2 via state_tool's shared
    # check_real_data_gates (gate.feasibility/protocol/qc). Fall back to the
    # legacy gates.json {"4","5","9"} logic when state is v1 or absent.
    state_path = resolve_state_path(project_dir) if resolve_state_path else os.path.join(project_dir, "state.json")
    gates_path = os.path.join(project_dir, "gates.json")

    state = None
    if os.path.exists(state_path):
        try:
            with open(state_path) as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            # Corrupted state must block, never silently fall through (FR-G4).
            print("ERROR: state.json exists but is unreadable. Fix it before real analysis.", file=sys.stderr)
            sys.exit(1)

    if (state is not None and check_real_data_gates is not None
            and detect_schema is not None and detect_schema(state)[0] in ("v2", "v3")):
        ok, missing = check_real_data_gates(state)
        if not ok:
            print(f"ERROR: Missing required real-data gate approvals: {missing}", file=sys.stderr)
            sys.exit(1)
    elif os.path.exists(gates_path):
        try:
            with open(gates_path) as f:
                gates = json.load(f)
        except (json.JSONDecodeError, OSError):
            print("ERROR: gates.json exists but is unreadable. Fix it before real analysis.", file=sys.stderr)
            sys.exit(1)
        required = {"4", "5", "9"}
        approved = {g for g, info in gates.items() if info.get("status") == "approved"}
        missing = required - approved
        if missing:
            print(f"ERROR: Missing required gate approvals: {sorted(missing)}", file=sys.stderr)
            sys.exit(1)
    else:
        # No approval evidence at all (no v2 state, no legacy gates.json):
        # real-data analysis is gated, so absence of gates means blocked.
        print("ERROR: No gate approval record found (state.json v2 or gates.json). "
              "Real analysis requires gate.feasibility, gate.protocol and gate.qc approvals.", file=sys.stderr)
        sys.exit(1)

    # Check QC
    qc_path = (resolve_qc_report_path(project_dir) if resolve_qc_report_path
               else os.path.join(project_dir, "qc-report.json"))
    if os.path.exists(qc_path):
        try:
            with open(qc_path) as f:
                qc = json.load(f)
        except (json.JSONDecodeError, OSError):
            print("ERROR: qc-report.json exists but is unreadable. Resolve before real analysis.", file=sys.stderr)
            sys.exit(1)
        if qc.get("has_critical"):
            print("ERROR: QC has critical flags. Resolve before running real analysis.", file=sys.stderr)
            sys.exit(1)

    counts, glm_fit, cox_fit = _load_and_fit(data_path)

    return {
        "source": "real",
        "sap_version": sap_version,
        "analyzed_at": datetime.now().isoformat(),
        "table1": counts,
        "model_summary": compute_effects(counts),
        "model_fits": {
            "glm_binomial": glm_fit,
            "cox": cox_fit,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Run statistical analysis")
    parser.add_argument("--mode", required=True, choices=["synthetic", "real", "plan", "rehearsal"])
    parser.add_argument("--project-dir", required=True, help="Path to the project directory")
    parser.add_argument("--sap-version", default="v0.1")
    parser.add_argument("--data-path", help="Path to real data (required for real mode)")
    parser.add_argument("--plan-path", help="Path to analysis-plan.json (required for plan mode)")
    args = parser.parse_args()

    if args.mode in ("real", "rehearsal") and not args.data_path:
        print(f"ERROR: --data-path required for {args.mode} mode", file=sys.stderr)
        sys.exit(1)

    # plan mode: emit the reproducible R script + precondition report. No gates
    # (planning), stdlib-only. Data is optional (used only for preconditions).
    if args.mode == "plan":
        if not args.plan_path:
            print("ERROR: --plan-path required for plan mode", file=sys.stderr)
            sys.exit(1)
        report = run_plan(args.project_dir, args.plan_path, args.data_path)
        report_path = (resolve_analysis_plan_report_path(args.project_dir) if resolve_analysis_plan_report_path
                       else os.path.join(args.project_dir, "analysis", "plan-report.json"))
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"Analysis plan emitted.\n  Script (authoritative, run in R): {report['script_path']}")
        print(f"  Plan report: {report_path}")
        for w in report["preconditions"]:
            print(f"  [{w['severity'].upper()}] {w['check']}: {w['detail']}")
        sys.exit(0)

    if args.mode == "rehearsal":
        # Physically separated tree — rehearsal outputs can never collide with
        # real artifacts (state-machine.md "Outside the DAG").
        output_dir = (resolve_rehearsal_analysis_dir(args.project_dir) if resolve_rehearsal_analysis_dir
                      else os.path.join(args.project_dir, "rehearsal", "analysis"))
    elif resolve_analysis_output_dir is not None:
        output_dir = resolve_analysis_output_dir(args.project_dir, args.mode)
    else:
        output_dir = os.path.join(args.project_dir, "analysis", args.mode)
    os.makedirs(output_dir, exist_ok=True)

    print(f"Running {args.mode} analysis...")

    if args.mode == "synthetic":
        result = run_synthetic(args.project_dir, args.sap_version)
    elif args.mode == "rehearsal":
        result = run_rehearsal(args.project_dir, args.data_path, args.sap_version)
    else:
        result = run_real(args.project_dir, args.data_path, args.sap_version)

    output_path = os.path.join(output_dir, "results.json")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Results saved to {output_path}")

    # Print summary
    effects = result.get("model_summary", {})
    print(f"\n--- Summary ---")
    print(f"  Risk Ratio: {effects.get('risk_ratio')}")
    print(f"  Odds Ratio: {effects.get('odds_ratio')}")
    for name, fit in result.get("model_fits", {}).items():
        if fit.get("status") == "ok":
            print(f"  {name}: {fit}")
    if result.get("watermark"):
        print(f"\n  *** {result['watermark']} ***")


if __name__ == "__main__":
    main()
