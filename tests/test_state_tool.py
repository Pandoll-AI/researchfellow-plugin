"""state_tool.py — the read-only judge that IS the audit brand promise.

These lock the deterministic invariants and the v1/v2 schema handling that were
previously guarded only by a "cross-checked in review" comment.
"""

from __future__ import annotations

import json

import pytest

STATE = "state_tool.py"


@pytest.mark.parametrize(
    "fixture,exit_code,schema",
    [
        ("v2_clean", 0, "v2"),
        ("v2_gates_approved", 0, "v2"),
        ("v2_gates_unapproved", 0, "v2"),
        ("v2_hybrid_violation", 1, "hybrid"),
        ("v2_draft_downstream", 1, "v2"),
        ("v1_legacy", 0, "v1"),
    ],
)
def test_validate_exit_and_schema(run_script, fixtures_dir, fixture, exit_code, schema):
    proc = run_script(STATE, "validate", "--project-dir", str(fixtures_dir / "state" / fixture))
    assert proc.returncode == exit_code, proc.stderr
    report = json.loads(proc.stdout)
    assert report["schema"] == schema


def test_corrupted_state_fails_closed(run_script, fixtures_dir):
    proc = run_script(STATE, "validate", "--project-dir", str(fixtures_dir / "state" / "corrupted"))
    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert report["schema"] is None


def test_hybrid_reports_schema_consistency(run_script, fixtures_dir):
    proc = run_script(STATE, "validate", "--project-dir", str(fixtures_dir / "state" / "v2_hybrid_violation"))
    report = json.loads(proc.stdout)
    invariants = {v["invariant"] for v in report["violations"]}
    assert "schema_consistency" in invariants


def test_draft_downstream_invariant(run_script, fixtures_dir):
    proc = run_script(STATE, "validate", "--project-dir", str(fixtures_dir / "state" / "v2_draft_downstream"))
    report = json.loads(proc.stdout)
    hits = [v for v in report["violations"] if v["invariant"] == "draft_has_no_valid_downstream"]
    assert hits, report["violations"]
    assert hits[0]["artifact"] == "protocol" and hits[0]["downstream"] == "sap"


def test_v3_draft_downstream_invariant(run_script, tmp_path, fixtures_dir):
    state = json.loads((fixtures_dir / "state" / "v2_draft_downstream" / "state.json").read_text())
    state["schema_version"] = 3
    system = tmp_path / ".system"
    system.mkdir()
    (system / "state.json").write_text(json.dumps(state), encoding="utf-8")
    proc = run_script(STATE, "validate", "--project-dir", str(tmp_path))
    assert proc.returncode == 1
    assert any(v["invariant"] == "draft_has_no_valid_downstream" for v in json.loads(proc.stdout)["violations"])


def test_unsupported_schema_fails_validate_and_can_enter_closed(run_script, tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({
        "schema_version": 99,
        "gates": {"gate.qc": {"status": "approved"}},
    }), encoding="utf-8")
    validate = run_script(STATE, "validate", "--project-dir", str(tmp_path))
    assert validate.returncode == 1
    assert any(v["invariant"] == "supported_schema_version" for v in json.loads(validate.stdout)["violations"])
    can_enter = run_script(STATE, "can-enter", "--project-dir", str(tmp_path), "--step", "10")
    assert can_enter.returncode == 2
    assert json.loads(can_enter.stdout)["allowed"] is False
    gate_check = run_script(STATE, "gate-check", "--project-dir", str(tmp_path), "--for", "real-analysis")
    assert gate_check.returncode == 2


@pytest.mark.parametrize(
    "fixture,ok,missing",
    [
        ("v2_gates_approved", True, []),
        ("v2_gates_unapproved", False, ["gate.qc"]),
        ("v2_clean", False, ["gate.feasibility", "gate.protocol", "gate.qc"]),
        ("v1_legacy", False, ["gate.qc"]),  # numeric key 9 == pending
    ],
)
def test_gate_check_real_analysis(run_script, fixtures_dir, fixture, ok, missing):
    proc = run_script(STATE, "gate-check", "--project-dir", str(fixtures_dir / "state" / fixture), "--for", "real-analysis")
    report = json.loads(proc.stdout)
    assert report["ok"] is ok
    assert report["missing"] == missing
    assert proc.returncode == (0 if ok else 2)


def test_v1_numeric_gate_mapping_is_semantic(run_script, fixtures_dir):
    """A v1 file with numeric keys must be judged via the semantic mapping."""
    proc = run_script(STATE, "gate-check", "--project-dir", str(fixtures_dir / "state" / "v1_legacy"), "--for", "real-analysis")
    report = json.loads(proc.stdout)
    assert report["schema"] == "v1"
    assert report["missing"] == ["gate.qc"]  # key "9" pending -> gate.qc


def test_can_enter_blocks_on_missing_hard_gate(run_script, fixtures_dir):
    # Step 10 needs gate.qc (hard); v2_gates_unapproved has qc pending.
    proc = run_script(STATE, "can-enter", "--project-dir", str(fixtures_dir / "state" / "v2_gates_unapproved"), "--step", "10")
    report = json.loads(proc.stdout)
    assert report["allowed"] is False
    assert "gate.qc" in report["missing_hard_gates"]
    assert proc.returncode == 2
