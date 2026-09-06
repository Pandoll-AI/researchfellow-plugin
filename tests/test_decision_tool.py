"""decision_tool.py — Level B/C decision log and post-hoc check."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

DECISION = "decision_tool.py"
PHONE = "010-1234-5678"
CHOSEN = "30-day all-cause mortality UNIQUE_TOKEN"


def _init_project(tmp_path, run_script):
    project = tmp_path / "research"
    proc = run_script("project_layout.py", "init", "--project-dir", str(project))
    assert proc.returncode == 0, proc.stderr
    return project


def _read_jsonl(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _patch_state(project, **artifact_fields):
    state_path = project / ".system" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    artifacts = state.setdefault("artifacts", {})
    artifacts["real_results"] = {
        "path": "10_analysis/real_results/results.json",
        "validity": "valid",
        **artifact_fields,
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")


def test_record_writes_jsonl_and_audit_without_content_text(tmp_path, run_script):
    project = _init_project(tmp_path, run_script)
    proc = run_script(
        DECISION, "record",
        "--project-dir", str(project),
        "--step", "5",
        "--level", "C",
        "--field", "primary_outcome",
        "--chosen", CHOSEN,
        "--alternatives", "in-hospital death|ICU death",
        "--rationale", "clinical importance",
        "--impact", "changes the endpoint",
        "--source", "user",
        "--artifact-ref", "protocol",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["id"] == "d-0001"
    assert payload["field"] == "primary_outcome"
    assert payload["kind"] == "decision"
    assert payload["chosen"] == CHOSEN

    decisions = _read_jsonl(project / ".system" / "decisions.jsonl")
    assert len(decisions) == 1
    assert decisions[0]["id"] == "d-0001"
    assert decisions[0]["alternatives"] == ["in-hospital death", "ICU death"]

    audit = _read_jsonl(project / ".system" / "audit.jsonl")
    recorded = [event for event in audit if event.get("event") == "DECISION_RECORDED"]
    assert len(recorded) == 1
    details = recorded[0]["details"]
    assert set(details) == {"decision_id", "step", "level", "field", "kind"}
    assert details["decision_id"] == "d-0001"
    audit_text = (project / ".system" / "audit.jsonl").read_text(encoding="utf-8")
    assert CHOSEN not in audit_text
    assert "UNIQUE_TOKEN" not in audit_text
    assert "clinical importance" not in audit_text
    assert "changes the endpoint" not in audit_text


def test_record_masks_phone_in_rationale(tmp_path, run_script):
    project = _init_project(tmp_path, run_script)
    proc = run_script(
        DECISION, "record",
        "--project-dir", str(project),
        "--step", "5",
        "--level", "B",
        "--field", "comparator",
        "--chosen", "usual care",
        "--rationale", f"call PI at {PHONE} before locking",
        "--source", "recommended_accepted",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert PHONE not in payload["rationale"]
    assert payload["_phi"]["masked"] is True
    assert "phone_kr" in payload["_phi"]["rules"]
    disk = (project / ".system" / "decisions.jsonl").read_text(encoding="utf-8")
    assert PHONE not in disk
    assert "[MASKED:phone_kr]" in disk


def test_list_filters_by_field_and_step(tmp_path, run_script):
    project = _init_project(tmp_path, run_script)
    common = [
        DECISION, "record",
        "--project-dir", str(project),
        "--source", "user",
    ]
    first = run_script(*common, "--step", "5", "--level", "C", "--field", "primary_outcome", "--chosen", "A")
    second = run_script(*common, "--step", "5", "--level", "B", "--field", "comparator", "--chosen", "B")
    third = run_script(*common, "--step", "6", "--level", "C", "--field", "primary_outcome", "--chosen", "C")
    assert first.returncode == second.returncode == third.returncode == 0

    by_field = run_script(DECISION, "list", "--project-dir", str(project), "--field", "primary_outcome")
    assert by_field.returncode == 0, by_field.stderr
    field_rows = json.loads(by_field.stdout)
    assert [row["id"] for row in field_rows] == ["d-0001", "d-0003"]

    by_step = run_script(DECISION, "list", "--project-dir", str(project), "--step", "5")
    assert {row["id"] for row in json.loads(by_step.stdout)} == {"d-0001", "d-0002"}

    both = run_script(
        DECISION, "list",
        "--project-dir", str(project),
        "--field", "primary_outcome",
        "--step", "5",
    )
    assert [row["id"] for row in json.loads(both.stdout)] == ["d-0001"]


def test_check_ok_without_real_results(tmp_path, run_script):
    project = _init_project(tmp_path, run_script)
    recorded = run_script(
        DECISION, "record",
        "--project-dir", str(project),
        "--step", "5",
        "--level", "C",
        "--field", "primary_outcome",
        "--chosen", CHOSEN,
        "--source", "user",
    )
    assert recorded.returncode == 0, recorded.stderr
    proc = run_script(DECISION, "check", "--project-dir", str(project))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"ok": True}


def test_check_blocks_post_hoc_primary_change(tmp_path, run_script):
    project = _init_project(tmp_path, run_script)
    _patch_state(project, verified_at="2020-01-01T00:00:00Z")
    recorded = run_script(
        DECISION, "record",
        "--project-dir", str(project),
        "--step", "11",
        "--level", "C",
        "--field", "primary_outcome",
        "--chosen", CHOSEN,
        "--rationale", "pre-specified in the protocol",
        "--source", "user",
    )
    assert recorded.returncode == 0, recorded.stderr
    proc = run_script(DECISION, "check", "--project-dir", str(project))
    assert proc.returncode == 2, proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert set(payload["blocker"]) == {"what", "why", "unlock", "meanwhile"}
    assert all(isinstance(payload["blocker"][key], str) and payload["blocker"][key] for key in payload["blocker"])
    assert payload["decisions"] == ["d-0001"]


def test_check_ok_when_decision_is_before_t0(tmp_path, run_script):
    project = _init_project(tmp_path, run_script)
    _patch_state(project, verified_at="2026-01-01T00:00:00Z")
    (project / ".system" / "decisions.jsonl").write_text(
        json.dumps({
            "id": "d-0001",
            "at": "2025-12-01T00:00:00Z",
            "step": 5,
            "level": "C",
            "field": "primary_outcome",
            "chosen": CHOSEN,
            "alternatives": [],
            "rationale": "pre-specified",
            "impact": "",
            "source": "user",
            "artifact_ref": None,
            "kind": "decision",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    proc = run_script(DECISION, "check", "--project-dir", str(project))
    assert proc.returncode == 0, proc.stdout
    assert json.loads(proc.stdout) == {"ok": True}


def test_check_uses_path_mtime_as_t0(tmp_path, run_script):
    project = _init_project(tmp_path, run_script)
    results = project / "10_analysis" / "real_results" / "results.json"
    results.parent.mkdir(parents=True, exist_ok=True)
    results.write_text("{}", encoding="utf-8")
    past = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(results, (past, past))
    _patch_state(project)
    recorded = run_script(
        DECISION, "record",
        "--project-dir", str(project),
        "--step", "11",
        "--level", "C",
        "--field", "primary_outcome",
        "--chosen", CHOSEN,
        "--source", "user",
    )
    assert recorded.returncode == 0, recorded.stderr
    proc = run_script(DECISION, "check", "--project-dir", str(project))
    assert proc.returncode == 2, proc.stdout
    assert json.loads(proc.stdout)["ok"] is False


def test_check_t0_unknown_when_no_timestamp_source(tmp_path, run_script):
    project = _init_project(tmp_path, run_script)
    _patch_state(project)
    proc = run_script(DECISION, "check", "--project-dir", str(project))
    assert proc.returncode == 0, proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["reason"] == "t0_unknown"
    assert isinstance(payload.get("note"), str) and payload["note"]


def test_check_ok_on_reversal_to_pre_t0_chosen(tmp_path, run_script):
    project = _init_project(tmp_path, run_script)
    _patch_state(project, verified_at="2020-01-01T00:00:00Z")
    (project / ".system" / "decisions.jsonl").write_text(
        json.dumps({
            "id": "d-0001",
            "at": "2019-12-01T00:00:00Z",
            "step": 5,
            "level": "C",
            "field": "primary_outcome",
            "chosen": CHOSEN,
            "alternatives": [],
            "rationale": "",
            "impact": "",
            "source": "user",
            "artifact_ref": None,
            "kind": "decision",
        }, ensure_ascii=False)
        + "\n"
        + json.dumps({
            "id": "d-0002",
            "at": "2021-01-01T00:00:00Z",
            "step": 11,
            "level": "C",
            "field": "primary_outcome",
            "chosen": CHOSEN,
            "alternatives": [],
            "rationale": "",
            "impact": "",
            "source": "user",
            "artifact_ref": None,
            "kind": "decision",
        }, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    proc = run_script(DECISION, "check", "--project-dir", str(project))
    assert proc.returncode == 0, proc.stdout
    assert json.loads(proc.stdout) == {"ok": True}


def test_check_ok_for_exploratory_kind(tmp_path, run_script):
    project = _init_project(tmp_path, run_script)
    _patch_state(project, verified_at="2020-01-01T00:00:00Z")
    recorded = run_script(
        DECISION, "record",
        "--project-dir", str(project),
        "--step", "11",
        "--level", "C",
        "--field", "primary_outcome",
        "--chosen", "in-hospital death",
        "--source", "user",
        "--kind", "exploratory",
    )
    assert recorded.returncode == 0, recorded.stderr
    payload = json.loads(recorded.stdout)
    assert payload["kind"] == "exploratory"
    proc = run_script(DECISION, "check", "--project-dir", str(project))
    assert proc.returncode == 0, proc.stdout
    assert json.loads(proc.stdout) == {"ok": True}


def test_record_rejects_non_slug_field(tmp_path, run_script):
    project = _init_project(tmp_path, run_script)
    proc = run_script(
        DECISION, "record",
        "--project-dir", str(project),
        "--step", "5",
        "--level", "C",
        "--field", "a b",
        "--chosen", CHOSEN,
        "--source", "user",
    )
    assert proc.returncode == 1, proc.stdout
    assert json.loads(proc.stdout) == {"error": "field must be a slug"}
    decisions = project / ".system" / "decisions.jsonl"
    assert not decisions.exists() or decisions.read_text(encoding="utf-8") == ""


def test_record_appends_after_partial_line(tmp_path, run_script):
    project = _init_project(tmp_path, run_script)
    path = project / ".system" / "decisions.jsonl"
    path.write_text(
        json.dumps({
            "id": "d-0001",
            "at": "2025-01-01T00:00:00Z",
            "step": 5,
            "level": "C",
            "field": "comparator",
            "chosen": "usual care",
            "alternatives": [],
            "rationale": "",
            "impact": "",
            "source": "user",
            "artifact_ref": None,
            "kind": "decision",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    assert not path.read_bytes().endswith(b"\n")
    proc = run_script(
        DECISION, "record",
        "--project-dir", str(project),
        "--step", "5",
        "--level", "B",
        "--field", "missing_data",
        "--chosen", "complete case",
        "--source", "recommended_accepted",
    )
    assert proc.returncode == 0, proc.stderr
    listed = run_script(DECISION, "list", "--project-dir", str(project))
    assert listed.returncode == 0, listed.stderr
    rows = json.loads(listed.stdout)
    assert [row["id"] for row in rows] == ["d-0001", "d-0002"]
    assert rows[1]["field"] == "missing_data"
