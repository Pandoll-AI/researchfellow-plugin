"""analysis_runner.py real-mode gates — the single most brand-critical behaviour.

An LLM must not be able to run real-data analysis without the three hard gates,
and a real analysis must never silently degrade to a partial result. These tests
drive the CLI end-to-end and assert on exit codes + stderr.
"""

from __future__ import annotations

import json
import shutil

import pytest

from conftest import requires_stats_stack

RUNNER = "analysis_runner.py"


def _project(tmp_path, fixtures_dir, fixture, *, with_data=True, with_qc=False, has_critical=False):
    src = fixtures_dir / "state" / fixture / "state.json"
    shutil.copy(src, tmp_path / "state.json")
    if with_data:
        (tmp_path / "data.csv").write_text("exposed,event\n1,1\n0,0\n1,0\n0,1\n1,1\n0,0\n")
    if with_qc:
        (tmp_path / "qc-report.json").write_text(json.dumps({"has_critical": has_critical}))
    return tmp_path


def _run_real(run_script, proj):
    return run_script(RUNNER, "--mode", "real", "--project-dir", str(proj), "--data-path", str(proj / "data.csv"))


def test_real_blocked_when_gate_unapproved(run_script, tmp_path, fixtures_dir):
    proj = _project(tmp_path, fixtures_dir, "v2_gates_unapproved")
    proc = _run_real(run_script, proj)
    assert proc.returncode == 1
    assert "gate" in proc.stderr.lower()
    assert "gate.qc" in proc.stderr


def test_real_no_gate_record_blocks(run_script, tmp_path):
    (tmp_path / "data.csv").write_text("exposed,event\n1,1\n0,0\n")
    proc = _run_real(run_script, tmp_path)  # no state.json, no gates.json
    assert proc.returncode == 1
    assert "no gate approval record" in proc.stderr.lower()


def test_real_corrupted_state_blocks(run_script, tmp_path, fixtures_dir):
    proj = _project(tmp_path, fixtures_dir, "corrupted")
    proc = _run_real(run_script, proj)
    assert proc.returncode == 1
    assert "unreadable" in proc.stderr.lower()


def test_real_approved_state_passes_the_gate(run_script, tmp_path, fixtures_dir):
    """Approved gates must clear the gate check. It may still stop later on a
    missing stats dependency, but NEVER with a gate-approval error."""
    proj = _project(tmp_path, fixtures_dir, "v2_gates_approved", with_qc=True, has_critical=False)
    proc = _run_real(run_script, proj)
    err = proc.stderr.lower()
    assert "missing required real-data gate" not in err
    assert "qc report not found" not in err
    assert "missing required has_critical" not in err
    assert "state integrity" not in err


def test_real_qc_critical_blocks(run_script, tmp_path, fixtures_dir):
    proj = _project(tmp_path, fixtures_dir, "v2_gates_approved", with_qc=True, has_critical=True)
    proc = _run_real(run_script, proj)
    assert proc.returncode == 1
    assert "qc has critical" in proc.stderr.lower()


@pytest.mark.parametrize(
    "payload,expect_msg",
    [
        ({"has_critical": None}, "invalid/ambiguous has_critical"),
        ({"has_critical": 0}, "invalid/ambiguous has_critical"),
        ({"has_critical": True}, "qc has critical"),
    ],
)
def test_real_qc_has_critical_non_false_blocks(run_script, tmp_path, fixtures_dir, payload, expect_msg):
    """F1: only explicit boolean false clears QC; null/0/true fail-closed."""
    proj = _project(tmp_path, fixtures_dir, "v2_gates_approved", with_qc=False)
    (proj / "qc-report.json").write_text(json.dumps(payload))
    proc = _run_real(run_script, proj)
    assert proc.returncode == 1
    assert expect_msg in proc.stderr.lower()


def test_real_qc_has_critical_false_still_passes_gate(run_script, tmp_path, fixtures_dir):
    """F1 happy-path invariant: explicit false must not be blocked by QC value check."""
    proj = _project(tmp_path, fixtures_dir, "v2_gates_approved", with_qc=True, has_critical=False)
    proc = _run_real(run_script, proj)
    err = proc.stderr.lower()
    assert "qc has critical" not in err
    assert "invalid/ambiguous has_critical" not in err
    assert "qc report not found" not in err


def test_real_qc_file_missing_blocks(run_script, tmp_path, fixtures_dir):
    """A-2: gates approved but no qc-report.json → runner fail-closed."""
    proj = _project(tmp_path, fixtures_dir, "v2_gates_approved", with_qc=False)
    proc = _run_real(run_script, proj)
    assert proc.returncode == 1
    assert "qc" in proc.stderr.lower()


def test_real_qc_report_missing_has_critical_key_blocks(run_script, tmp_path, fixtures_dir):
    """A-2: legacy QC report without has_critical key must not silently pass."""
    proj = _project(tmp_path, fixtures_dir, "v2_gates_approved", with_qc=False)
    (proj / "qc-report.json").write_text(json.dumps({"summary": "old report shape"}))
    proc = _run_real(run_script, proj)
    assert proc.returncode == 1
    err = proc.stderr.lower()
    assert "has_critical" in err or "qc" in err


def test_real_forged_soft_retroactive_gate_blocks(run_script, tmp_path, fixtures_dir):
    """A-3: validate violation on real-data path blocks the runner last line of defence."""
    proj = _project(tmp_path, fixtures_dir, "v2_gates_approved", with_qc=True, has_critical=False)
    state = json.loads((proj / "state.json").read_text())
    state["gates"]["gate.qc"] = {"status": "approved", "type": "soft", "retroactive": True}
    (proj / "state.json").write_text(json.dumps(state))
    proc = _run_real(run_script, proj)
    assert proc.returncode == 1
    err = proc.stderr.lower()
    assert "gate" in err or "integrity" in err or "violat" in err


def test_synthetic_is_aggregate_only_no_false_precision(run_script, tmp_path):
    """The false-precision regression: a 2x2/synthetic result must NOT carry a
    fabricated CI or p-value."""
    (tmp_path / "state.json").write_text(json.dumps({"project_name": "t"}))
    proc = run_script(RUNNER, "--mode", "synthetic", "--project-dir", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    result = json.loads((tmp_path / "analysis" / "synthetic" / "results.json").read_text())
    glm = result["model_fits"]["glm_binomial"]
    assert glm["status"] == "aggregate_only"
    assert glm.get("ci_p_available") is False
    assert "or_ci95" not in glm and "p_value" not in glm


@requires_stats_stack
def test_real_individual_fit_produces_real_ci(run_script, tmp_path, fixtures_dir):
    """With the stats stack present, individual-level data yields a genuine OR+CI."""
    proj = _project(tmp_path, fixtures_dir, "v2_gates_approved", with_data=False, with_qc=True, has_critical=False)
    # A dataset with signal and enough rows to converge.
    rows = ["exposed,event"] + ["1,1"] * 40 + ["1,0"] * 20 + ["0,1"] * 10 + ["0,0"] * 50
    (proj / "data.csv").write_text("\n".join(rows) + "\n")
    proc = _run_real(run_script, proj)
    assert proc.returncode == 0, proc.stderr
    result = json.loads((proj / "analysis" / "real" / "results.json").read_text())
    glm = result["model_fits"]["glm_binomial"]
    assert glm["status"] == "ok"
    assert "individual" in glm["method"].lower()
    assert len(glm["or_ci95"]) == 2 and "p_value" in glm
