#!/usr/bin/env python3
"""Statistical analysis runner for the Research Assistant skill.

Runs analysis in synthetic or real mode. Computes effect measures (RR, OR),
fits GLM binomial and Cox PH models when dependencies are available.

Usage:
    python3 analysis_runner.py --mode synthetic --project-dir .research/ --sap-version v0.1
    python3 analysis_runner.py --mode real --project-dir .research/ --data-path data.csv --sap-version v0.1
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
except ImportError:  # pragma: no cover - fallback to legacy gates.json path
    check_real_data_gates = None
    detect_schema = None


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


def fit_glm_binomial(row_counts: Dict[str, Any]) -> Dict[str, Any]:
    try:
        import pandas as pd
        import statsmodels.api as sm
    except ImportError:
        return {"status": "skipped", "reason": "statsmodels_or_pandas_not_installed"}

    n_exposed = int(row_counts.get("exposed", 0) or 0)
    n_unexposed = int(row_counts.get("unexposed", 0) or 0)
    ev_exposed = int(row_counts.get("events_exposed", 0) or 0)
    ev_unexposed = int(row_counts.get("events_unexposed", 0) or 0)

    if n_exposed <= 0 or n_unexposed <= 0:
        return {"status": "skipped", "reason": "insufficient_group_counts"}

    data = pd.DataFrame({
        "event_rate": [ev_exposed / n_exposed, ev_unexposed / n_unexposed],
        "exposed": [1, 0],
        "n": [n_exposed, n_unexposed],
    })

    x = sm.add_constant(data[["exposed"]], has_constant="add")
    model = sm.GLM(data["event_rate"], x, family=sm.families.Binomial(), freq_weights=data["n"])

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = model.fit()
    except Exception as exc:
        return {"status": "failed", "reason": str(exc)}

    beta = float(fit.params.get("exposed", 0.0))
    conf_int = fit.conf_int().loc["exposed"].tolist()
    if not (math.isfinite(float(conf_int[0])) and math.isfinite(float(conf_int[1]))):
        return {"status": "failed", "reason": "unstable_estimate_perfect_separation"}

    return {
        "status": "ok",
        "method": "GLM-Binomial",
        "coef": beta,
        "odds_ratio": math.exp(beta),
        "or_ci95": [math.exp(float(conf_int[0])), math.exp(float(conf_int[1]))],
        "p_value": float(fit.pvalues.get("exposed", 1.0)),
    }


def fit_cox(survival_records: List[Dict], time_varying_records: Optional[List[Dict]] = None) -> Dict[str, Any]:
    if time_varying_records:
        return _fit_cox_time_varying(time_varying_records)

    if not survival_records:
        return {"status": "skipped", "reason": "no_survival_records"}

    try:
        import pandas as pd
        from lifelines import CoxPHFitter
    except ImportError:
        return {"status": "skipped", "reason": "lifelines_or_pandas_not_installed"}

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
    except ImportError:
        return {"status": "skipped", "reason": "lifelines_or_pandas_not_installed"}

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
    state_path = os.path.join(project_dir, "state.json")
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
            "glm_binomial": fit_glm_binomial(counts),
            "cox": {"status": "skipped", "reason": "planning_mode_no_real_survival_data"},
        },
    }


def run_real(project_dir: str, data_path: str, sap_version: str) -> dict:
    # Check real-data gates. Prefer state.json v2 via state_tool's shared
    # check_real_data_gates (gate.feasibility/protocol/qc). Fall back to the
    # legacy gates.json {"4","5","9"} logic when state is v1 or absent.
    state_path = os.path.join(project_dir, "state.json")
    gates_path = os.path.join(project_dir, "gates.json")

    state = None
    if os.path.exists(state_path):
        try:
            with open(state_path) as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            state = None

    if (state is not None and check_real_data_gates is not None
            and detect_schema is not None and detect_schema(state)[0] == "v2"):
        ok, missing = check_real_data_gates(state)
        if not ok:
            print(f"ERROR: Missing required real-data gate approvals: {missing}", file=sys.stderr)
            sys.exit(1)
    elif os.path.exists(gates_path):
        with open(gates_path) as f:
            gates = json.load(f)
        required = {"4", "5", "9"}
        approved = {g for g, info in gates.items() if info.get("status") == "approved"}
        missing = required - approved
        if missing:
            print(f"ERROR: Missing required gate approvals: {sorted(missing)}", file=sys.stderr)
            sys.exit(1)

    # Check QC
    qc_path = os.path.join(project_dir, "qc-report.json")
    if os.path.exists(qc_path):
        with open(qc_path) as f:
            qc = json.load(f)
        if qc.get("has_critical"):
            print("ERROR: QC has critical flags. Resolve before running real analysis.", file=sys.stderr)
            sys.exit(1)

    # Load data counts (expects JSON with row_counts)
    if data_path.endswith(".json"):
        with open(data_path) as f:
            data = json.load(f)
        counts = data.get("row_counts", data)
        survival_records = data.get("survival_records", [])
        time_varying_records = data.get("time_varying_records", [])
    else:
        # CSV support: compute counts from data
        try:
            import pandas as pd
            df = pd.read_csv(data_path)
            counts = {
                "total": len(df),
                "exposed": int(df["exposed"].sum()) if "exposed" in df.columns else 0,
                "unexposed": int((~df["exposed"].astype(bool)).sum()) if "exposed" in df.columns else 0,
                "events_exposed": int(df[df.get("exposed", False) == 1]["event"].sum()) if {"exposed", "event"}.issubset(df.columns) else 0,
                "events_unexposed": int(df[df.get("exposed", False) == 0]["event"].sum()) if {"exposed", "event"}.issubset(df.columns) else 0,
            }
            survival_records = df.to_dict("records") if {"time", "event", "exposed"}.issubset(df.columns) else []
            time_varying_records = []
        except ImportError:
            print("ERROR: pandas required for CSV data", file=sys.stderr)
            sys.exit(1)

    return {
        "source": "real",
        "sap_version": sap_version,
        "analyzed_at": datetime.now().isoformat(),
        "table1": counts,
        "model_summary": compute_effects(counts),
        "model_fits": {
            "glm_binomial": fit_glm_binomial(counts),
            "cox": fit_cox(survival_records, time_varying_records),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Run statistical analysis")
    parser.add_argument("--mode", required=True, choices=["synthetic", "real"])
    parser.add_argument("--project-dir", required=True, help="Path to .research/ directory")
    parser.add_argument("--sap-version", default="v0.1")
    parser.add_argument("--data-path", help="Path to real data (required for real mode)")
    args = parser.parse_args()

    if args.mode == "real" and not args.data_path:
        print("ERROR: --data-path required for real mode", file=sys.stderr)
        sys.exit(1)

    output_dir = os.path.join(args.project_dir, "analysis", args.mode)
    os.makedirs(output_dir, exist_ok=True)

    print(f"Running {args.mode} analysis...")

    if args.mode == "synthetic":
        result = run_synthetic(args.project_dir, args.sap_version)
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
