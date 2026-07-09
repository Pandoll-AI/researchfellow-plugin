"""analysis_runner.py `plan` mode — emits a reproducible R script from an
analysis_plan artifact and reports preconditions. The tool must NEVER fabricate
numbers here; it only writes an auditable script + warnings.
"""

from __future__ import annotations

import json

RUNNER = "analysis_runner.py"


def _plan(tmp_path, spec):
    p = tmp_path / "analysis-plan.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    return p


def test_plan_emits_cox_r_script(run_script, tmp_path):
    spec = {
        "estimand": {"population": "adults", "exposure_var": "drug", "comparator": "other",
                     "outcome_var": "died", "time_var": "fu_days", "measure": "HR"},
        "design": "cohort",
        "primary_method": "cox_ph",
        "confounding_strategy": "iptw",
        "covariates": ["age", "sex", "cci"],
        "competing_risks": {"present": False},
        "sensitivity": ["e_value"],
    }
    plan = _plan(tmp_path, spec)
    proc = run_script(RUNNER, "--mode", "plan", "--project-dir", str(tmp_path), "--plan-path", str(plan))
    assert proc.returncode == 0, proc.stderr

    script = (tmp_path / "analysis" / "scripts" / "analysis.R").read_text()
    assert "coxph(Surv(fu_days, died)" in script
    assert "cox.zph(fit)" in script            # PH assumption check emitted
    assert "weightit(drug ~ age + sex + cci" in script  # IPTW setup
    assert "EValue" in script                  # sensitivity stub
    # The tool emits code, never results.
    assert "risk_ratio" not in script.lower() or "report both" in script.lower()


def test_plan_reports_epv_precondition(run_script, tmp_path):
    spec = {
        "estimand": {"exposure_var": "exposed", "outcome_var": "event"},
        "primary_method": "logistic",
        "confounding_strategy": "multivariable",
        "covariates": ["a", "b", "c", "d", "e"],  # 5 covariates
    }
    plan = _plan(tmp_path, spec)
    # few events -> EPV < 10
    data = tmp_path / "data.csv"
    rows = ["exposed,event"] + ["1,1", "0,1"] + ["1,0"] * 30 + ["0,0"] * 30
    data.write_text("\n".join(rows) + "\n")
    proc = run_script(RUNNER, "--mode", "plan", "--project-dir", str(tmp_path),
                      "--plan-path", str(plan), "--data-path", str(data))
    assert proc.returncode == 0, proc.stderr
    report = json.loads((tmp_path / "analysis" / "plan-report.json").read_text())
    checks = {w["check"] for w in report["preconditions"]}
    assert "epv" in checks


def test_plan_requires_plan_path(run_script, tmp_path):
    proc = run_script(RUNNER, "--mode", "plan", "--project-dir", str(tmp_path))
    assert proc.returncode == 1
    assert "plan-path" in proc.stderr.lower()
