"""Snapshot and non-blocking contracts for the progress renderer."""

from __future__ import annotations

import json
import shutil

import pytest


RENDERER = "progress_renderer.py"


@pytest.mark.parametrize("layout", ["v3", "legacy"])
def test_renderer_matches_snapshots_and_is_byte_stable(tmp_path, fixtures_dir, run_script, layout):
    source = fixtures_dir / "progress" / layout
    project = tmp_path / layout
    shutil.copytree(source, project)
    state_path = project / ".system" / "state.json" if layout == "v3" else project / "state.json"
    audit_path = project / ".system" / "audit.jsonl" if layout == "v3" else project / "audit.jsonl"
    before_state, before_audit = state_path.read_bytes(), audit_path.read_bytes()

    first = run_script(RENDERER, "render", "--project-dir", str(project))
    assert first.returncode == 0
    assert first.stderr == ""
    first_progress = (project / "PROGRESS.md").read_bytes()
    first_log = (project / "RESEARCH_LOG.md").read_bytes()
    assert first_progress == (source / "expected_PROGRESS.md").read_bytes()
    assert first_log == (source / "expected_RESEARCH_LOG.md").read_bytes()
    assert state_path.read_bytes() == before_state
    assert audit_path.read_bytes() == before_audit

    second = run_script(RENDERER, "render", "--project-dir", str(project))
    assert second.returncode == 0
    assert (project / "PROGRESS.md").read_bytes() == first_progress
    assert (project / "RESEARCH_LOG.md").read_bytes() == first_log


def test_renderer_skips_unknown_events_and_never_quotes_unsafe_details(tmp_path, run_script):
    project = tmp_path / "legacy"
    project.mkdir()
    (project / "state.json").write_text(
        '{"project_name":"safe", "steps": {}, "gates": {}, '
        '"artifacts":{"protocol":{"path":"05_protocol/protocol.md"}}}',
        encoding="utf-8",
    )
    (project / "audit.jsonl").write_text(
        '{"timestamp":"2026-07-17T01:00:00Z","event":"FUTURE_EVENT","details":{"note":"Patient 123"}}\n'
        '{"timestamp":"2026-07-17T02:00:00Z","event":"ARTIFACT_CREATED","details":{"artifact":"protocol","path":"05_protocol/protocol.md","version":1,"note":"Patient 123"}}\n',
        encoding="utf-8",
    )
    proc = run_script(RENDERER, "render", "--project-dir", str(project))
    assert proc.returncode == 0
    log = (project / "RESEARCH_LOG.md").read_text(encoding="utf-8")
    assert "FUTURE_EVENT" not in log
    assert "Patient 123" not in log
    assert "05_protocol/protocol.md v1" in log


def test_renderer_omits_audit_paths_not_declared_by_state(tmp_path, run_script):
    project = tmp_path / "legacy"
    project.mkdir()
    (project / "state.json").write_text(
        '{"steps":{"13":{"rounds":[{"round":14,'
        '"response_letter":"13_revision/round-14/response.md",'
        '"diff":"13_revision/round-14/diff.md","closed_at":null}]}},'
        '"gates":{},"artifacts":{"protocol":{"path":"05_protocol/protocol.md"}}}',
        encoding="utf-8",
    )
    (project / "audit.jsonl").write_text(
        '{"timestamp":"2026-07-17T01:00:00Z","event":"ARTIFACT_CREATED",'
        '"details":{"artifact":"protocol","path":"PHI-Jane-Doe-123.txt","version":1}}\n'
        '{"timestamp":"2026-07-17T01:01:00Z","event":"ARTIFACT_CREATED",'
        '"details":{"artifact":"protocol","path":"05_protocol","version":2}}\n'
        '{"timestamp":"2026-07-17T01:02:00Z","event":"ARTIFACT_UPDATED",'
        '"details":{"artifact":"protocol","path":"13_revision/round-14","version":3}}\n',
        encoding="utf-8",
    )
    proc = run_script(RENDERER, "render", "--project-dir", str(project))
    assert proc.returncode == 0
    log = (project / "RESEARCH_LOG.md").read_text(encoding="utf-8")
    progress = (project / "PROGRESS.md").read_text(encoding="utf-8")
    assert "PHI-Jane-Doe-123.txt" not in log
    assert "( v1)" not in log
    assert "(05_protocol v2)" in log
    assert "(13_revision/round-14 v3)" in log
    assert "Revision round 14 진행 중" in progress


def test_renderer_keeps_exit_zero_for_broken_state(tmp_path, run_script):
    project = tmp_path / "broken"
    project.mkdir()
    (project / "state.json").write_text('{ not json', encoding="utf-8")
    (project / "audit.jsonl").write_text('', encoding="utf-8")
    proc = run_script(RENDERER, "render", "--project-dir", str(project))
    assert proc.returncode == 0
    assert len(proc.stderr.splitlines()) == 1


def test_renderer_appends_decisions_and_knowledge_check(tmp_path, run_script):
    project = tmp_path / "legacy"
    project.mkdir()
    (project / "state.json").write_text(
        '{"project_name":"decisions", "steps": {}, "gates": {}, "artifacts":{}}',
        encoding="utf-8",
    )
    (project / "audit.jsonl").write_text(
        '{"timestamp":"2026-07-17T01:00:00Z","event":"DECISION_RECORDED",'
        '"details":{"decision_id":"d-0001","step":5,"level":"C","field":"primary_outcome","kind":"decision"}}\n',
        encoding="utf-8",
    )
    long_chosen = "30-day mortality " + ("x" * 100)
    (project / "decisions.jsonl").write_text(
        json.dumps({
            "id": "d-0001",
            "at": "2026-07-17T01:00:00Z",
            "step": 5,
            "level": "C",
            "field": "primary_outcome",
            "chosen": long_chosen,
            "alternatives": ["in-hospital death"],
            "rationale": "clinical importance",
            "impact": "changes endpoint",
            "source": "user",
            "artifact_ref": "protocol",
            "kind": "decision",
            "_phi": {"masked": True, "rules": ["phone_kr"]},
        }, ensure_ascii=False)
        + "\n"
        + json.dumps({
            "id": "d-0002",
            "at": "2026-07-17T01:05:00Z",
            "step": 5,
            "level": "A",
            "field": "knowledge_check",
            "chosen": "Time zero는 노출 시작 시점이다",
            "alternatives": [],
            "rationale": "",
            "impact": "",
            "source": "autonomous",
            "artifact_ref": None,
            "kind": "knowledge_check",
        }, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    proc = run_script(RENDERER, "render", "--project-dir", str(project))
    assert proc.returncode == 0, proc.stderr
    log = (project / "RESEARCH_LOG.md").read_text(encoding="utf-8")
    assert "연구 결정이 기록되었습니다 (primary_outcome)" in log
    assert "## Decisions" in log
    assert "| At | Step | Level | Field | Chosen | Source |" in log
    assert "primary_outcome" in log
    assert long_chosen[:80] in log
    assert long_chosen not in log
    assert "## Knowledge Check" in log
    assert "Step 5: Time zero는 노출 시작 시점이다" in log
    assert "clinical importance" not in log
    assert "changes endpoint" not in log
    assert "in-hospital death" not in log

