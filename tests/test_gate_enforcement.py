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


# ---------------------------------------------------------------------------
# Authoritative state vs legacy gates.json (fail-closed, no silent fallback)
# ---------------------------------------------------------------------------

_APPROVED_LEGACY_GATES = {
    "4": {"status": "approved"},
    "5": {"status": "approved"},
    "9": {"status": "approved"},
}

_SAMPLE_CSV = "exposed,event\n1,1\n0,0\n1,0\n0,1\n1,1\n0,0\n"


def _write_approved_legacy_bait(proj):
    """Approved gates.json + clean QC — would pass if existing state were ignored."""
    (proj / "gates.json").write_text(json.dumps(_APPROVED_LEGACY_GATES))
    (proj / "qc-report.json").write_text(json.dumps({"has_critical": False}))


def _assert_blocked_without_echo_or_traceback(proc, *needles):
    assert proc.returncode == 1
    err = proc.stderr
    err_l = err.lower()
    assert "traceback" not in err_l
    for needle in needles:
        assert needle.lower() in err_l, err


def test_real_v1_pending_does_not_fall_through_to_approved_legacy_gates(run_script, tmp_path, fixtures_dir):
    """v1 state with pending gate 9 must not be rescued by approved gates.json."""
    proj = _project(tmp_path, fixtures_dir, "v1_legacy")
    _write_approved_legacy_bait(proj)
    proc = _run_real(run_script, proj)
    _assert_blocked_without_echo_or_traceback(proc, "missing required real-data gate", "gate.qc")


def test_real_hybrid_state_does_not_fall_through_to_approved_legacy_gates(run_script, tmp_path, fixtures_dir):
    proj = _project(tmp_path, fixtures_dir, "v2_hybrid_violation")
    _write_approved_legacy_bait(proj)
    proc = _run_real(run_script, proj)
    _assert_blocked_without_echo_or_traceback(proc, "state integrity")


def test_real_unsupported_state_does_not_fall_through_to_approved_legacy_gates(run_script, tmp_path):
    (tmp_path / "data.csv").write_text(_SAMPLE_CSV)
    (tmp_path / "state.json").write_text(json.dumps({
        "schema_version": 99,
        "gates": {
            "gate.feasibility": {"status": "approved", "type": "hard", "retroactive": False},
            "gate.protocol": {"status": "approved", "type": "hard", "retroactive": False},
            "gate.qc": {"status": "approved", "type": "hard", "retroactive": False},
        },
    }))
    _write_approved_legacy_bait(tmp_path)
    proc = _run_real(run_script, tmp_path)
    _assert_blocked_without_echo_or_traceback(proc, "state integrity")


@pytest.mark.parametrize("payload", ["null", "[]"])
def test_real_non_object_state_blocks_even_with_legacy_gates(run_script, tmp_path, payload):
    (tmp_path / "data.csv").write_text(_SAMPLE_CSV)
    (tmp_path / "state.json").write_text(payload)
    _write_approved_legacy_bait(tmp_path)
    proc = _run_real(run_script, tmp_path)
    _assert_blocked_without_echo_or_traceback(proc, "state.json must be a json object")
    assert "None" not in proc.stderr
    assert "null" not in proc.stderr.lower()


def test_real_existing_state_blocks_when_shared_validation_unavailable(tmp_path, fixtures_dir, monkeypatch, capsys):
    """Missing check_real_data_gates/detect_schema must fail closed if state exists."""
    import analysis_runner as ar

    proj = _project(tmp_path, fixtures_dir, "v2_gates_approved", with_qc=True, has_critical=False)
    _write_approved_legacy_bait(proj)
    monkeypatch.setattr(ar, "check_real_data_gates", None)
    monkeypatch.setattr(ar, "detect_schema", None)
    with pytest.raises(SystemExit) as exc:
        ar.run_real(str(proj), str(proj / "data.csv"), "v0.1")
    assert exc.value.code == 1
    err = capsys.readouterr().err.lower()
    assert "shared real-data gate validation is unavailable" in err
    assert "traceback" not in err


def test_real_no_state_legacy_gates_json_still_accepted(run_script, tmp_path):
    """Genuinely absent state still accepts well-formed approved gates.json."""
    (tmp_path / "data.csv").write_text(_SAMPLE_CSV)
    (tmp_path / "gates.json").write_text(json.dumps(_APPROVED_LEGACY_GATES))
    (tmp_path / "qc-report.json").write_text(json.dumps({"has_critical": False}))
    proc = _run_real(run_script, tmp_path)
    err = proc.stderr.lower()
    assert "no gate approval record" not in err
    assert "missing required gate" not in err
    assert "state integrity" not in err
    assert "must be a json object of gate records" not in err


def test_real_no_state_legacy_gates_work_without_shared_validation(tmp_path, monkeypatch, capsys):
    import analysis_runner as ar

    (tmp_path / "data.csv").write_text(_SAMPLE_CSV)
    (tmp_path / "gates.json").write_text(json.dumps(_APPROVED_LEGACY_GATES))
    (tmp_path / "qc-report.json").write_text(json.dumps({"has_critical": False}))
    monkeypatch.setattr(ar, "check_real_data_gates", None)
    monkeypatch.setattr(ar, "detect_schema", None)
    # May still stop later on the stats stack, but must not die on gates.
    try:
        ar.run_real(str(tmp_path), str(tmp_path / "data.csv"), "v0.1")
    except SystemExit:
        err = capsys.readouterr().err.lower()
        assert "shared real-data gate validation is unavailable" not in err
        assert "no gate approval record" not in err
        assert "missing required" not in err
        assert "this analysis requires" in err


@pytest.mark.parametrize(
    "payload,needle",
    [
        ([1, 2, 3], "must be a json object of gate records"),
        ({"4": "approved", "5": {"status": "approved"}, "9": {"status": "approved"}},
         "gate records must be objects"),
    ],
)
def test_real_malformed_legacy_gates_json_blocks(run_script, tmp_path, payload, needle):
    (tmp_path / "data.csv").write_text(_SAMPLE_CSV)
    (tmp_path / "gates.json").write_text(json.dumps(payload))
    (tmp_path / "qc-report.json").write_text(json.dumps({"has_critical": False}))
    proc = _run_real(run_script, tmp_path)
    _assert_blocked_without_echo_or_traceback(proc, needle)
    assert "approved" not in proc.stderr or "must be objects" in proc.stderr.lower()


def test_real_malformed_legacy_gates_does_not_echo_payload(run_script, tmp_path):
    (tmp_path / "data.csv").write_text(_SAMPLE_CSV)
    (tmp_path / "gates.json").write_text(json.dumps(["SECRET_SHOULD_NOT_ECHO"]))
    (tmp_path / "qc-report.json").write_text(json.dumps({"has_critical": False}))
    proc = _run_real(run_script, tmp_path)
    _assert_blocked_without_echo_or_traceback(proc, "must be a json object of gate records")
    assert "SECRET_SHOULD_NOT_ECHO" not in proc.stderr


@pytest.mark.parametrize("payload", ["null", "[]", '"not-an-object"', "0"])
def test_real_malformed_qc_top_level_blocks_safely(run_script, tmp_path, fixtures_dir, payload):
    proj = _project(tmp_path, fixtures_dir, "v2_gates_approved", with_qc=False)
    (proj / "qc-report.json").write_text(payload)
    proc = _run_real(run_script, proj)
    _assert_blocked_without_echo_or_traceback(proc, "qc report must be a json object")
    assert "None" not in proc.stderr
    assert payload.strip('"') not in proc.stderr


def test_real_qc_invalid_has_critical_does_not_echo_value(run_script, tmp_path, fixtures_dir):
    proj = _project(tmp_path, fixtures_dir, "v2_gates_approved", with_qc=False)
    (proj / "qc-report.json").write_text(json.dumps({"has_critical": "SECRET_SHOULD_NOT_ECHO"}))
    proc = _run_real(run_script, proj)
    _assert_blocked_without_echo_or_traceback(proc, "invalid/ambiguous has_critical")
    assert "SECRET_SHOULD_NOT_ECHO" not in proc.stderr


def test_real_valid_v1_approved_state_passes_the_gate(run_script, tmp_path, fixtures_dir):
    src = json.loads((fixtures_dir / "state" / "v1_legacy" / "state.json").read_text())
    src["gates"]["9"]["status"] = "approved"
    (tmp_path / "state.json").write_text(json.dumps(src))
    (tmp_path / "data.csv").write_text(_SAMPLE_CSV)
    (tmp_path / "qc-report.json").write_text(json.dumps({"has_critical": False}))
    proc = _run_real(run_script, tmp_path)
    err = proc.stderr.lower()
    assert "missing required real-data gate" not in err
    assert "state integrity" not in err
    assert "qc report not found" not in err
    assert "must be a json object" not in err


def test_real_valid_v3_state_passes_the_gate(run_script, tmp_path, fixtures_dir):
    src = json.loads((fixtures_dir / "state" / "v2_gates_approved" / "state.json").read_text())
    src["schema_version"] = 3
    system = tmp_path / ".system"
    system.mkdir()
    (system / "state.json").write_text(json.dumps(src))
    qc_dir = tmp_path / "09_data_qc"
    qc_dir.mkdir()
    (qc_dir / "qc-report.json").write_text(json.dumps({"has_critical": False}))
    (tmp_path / "data.csv").write_text(_SAMPLE_CSV)
    proc = _run_real(run_script, tmp_path)
    err = proc.stderr.lower()
    assert "missing required real-data gate" not in err
    assert "state integrity" not in err
    assert "qc report not found" not in err
    assert "no gate approval record" not in err


_MALICIOUS_MARKER = "SYNTH_MARKER_SHOULD_NOT_ECHO"


def _assert_no_fit(proj):
    assert not (proj / "analysis" / "real" / "results.json").exists()
    assert not (proj / "10_analysis" / "real_results" / "results.json").exists()


@pytest.mark.parametrize(
    "state",
    [
        {
            "schema_version": 2,
            "gates": {
                "gate.feasibility": _MALICIOUS_MARKER,
                "gate.protocol": {},
                "gate.qc": {},
            },
        },
        {
            "schema_version": 2,
            "project_name": _MALICIOUS_MARKER,
            "gates": {
                "gate.feasibility": None,
                "gate.protocol": {},
                "gate.qc": {},
            },
        },
        {
            "schema_version": 2,
            "gates": [_MALICIOUS_MARKER],
        },
        {
            "schema_version": 2,
            "gates": {
                "gate.feasibility": {"status": "approved", "type": "hard", "retroactive": False},
                "gate.protocol": {"status": "approved", "type": "hard", "retroactive": False},
                "gate.qc": {"status": "approved", "type": "hard", "retroactive": False},
            },
            "steps": [_MALICIOUS_MARKER],
        },
        {
            "schema_version": 2,
            "gates": {
                "gate.feasibility": {"status": "approved", "type": "hard", "retroactive": False},
                "gate.protocol": {"status": "approved", "type": "hard", "retroactive": False},
                "gate.qc": {"status": "approved", "type": "hard", "retroactive": False},
            },
            "steps": {"1": _MALICIOUS_MARKER},
        },
    ],
)
def test_real_malformed_nested_state_blocks_without_traceback_or_fit(run_script, tmp_path, state):
    """Nested non-dict gates/steps must not traceback or leak payload into stderr."""
    (tmp_path / "data.csv").write_text(_SAMPLE_CSV)
    (tmp_path / "state.json").write_text(json.dumps(state))
    _write_approved_legacy_bait(tmp_path)
    proc = _run_real(run_script, tmp_path)
    _assert_blocked_without_echo_or_traceback(proc, "invalid shape")
    assert _MALICIOUS_MARKER not in proc.stderr
    assert _MALICIOUS_MARKER not in proc.stdout
    _assert_no_fit(tmp_path)


@pytest.mark.parametrize("which", ["state", "gates", "qc"])
def test_real_invalid_utf8_json_is_unreadable(run_script, tmp_path, fixtures_dir, which):
    garbage = b"\xff\xfe" + _MALICIOUS_MARKER.encode("ascii")
    (tmp_path / "data.csv").write_text(_SAMPLE_CSV)
    if which == "state":
        (tmp_path / "state.json").write_bytes(garbage)
        _write_approved_legacy_bait(tmp_path)
        needle = "state.json exists but is unreadable"
    elif which == "gates":
        (tmp_path / "gates.json").write_bytes(garbage)
        (tmp_path / "qc-report.json").write_text(json.dumps({"has_critical": False}))
        needle = "gates.json exists but is unreadable"
    else:
        src = (fixtures_dir / "state" / "v2_gates_approved" / "state.json").read_text()
        (tmp_path / "state.json").write_text(src)
        (tmp_path / "qc-report.json").write_bytes(garbage)
        needle = "qc-report.json exists but is unreadable"
    proc = _run_real(run_script, tmp_path)
    _assert_blocked_without_echo_or_traceback(proc, "unreadable", needle)
    assert _MALICIOUS_MARKER not in proc.stderr
    assert _MALICIOUS_MARKER not in proc.stdout
    _assert_no_fit(tmp_path)
