"""Schema-v3 visible project layout and lazy migration contracts."""

from __future__ import annotations

import json
import os
import shutil

import pytest


LAYOUT = "project_layout.py"


def test_init_creates_visible_scaffold_and_valid_v3_state(tmp_path, run_script):
    project = tmp_path / "research"
    proc = run_script(LAYOUT, "init", "--project-dir", str(project))
    assert proc.returncode == 0, proc.stderr
    for number, slug in ((1, "pico"), (10, "analysis"), (13, "revision")):
        readme = project / f"{number:02d}_{slug}" / "README.md"
        assert readme.exists()
        assert readme.read_text(encoding="utf-8").splitlines()[0].startswith("# ")
    assert "NOT REAL DATA" in (project / "rehearsal" / "README.md").read_text(encoding="utf-8")
    assert (project / "00_materials").is_dir()
    assert (project / ".system" / "desk").is_dir()
    assert (project / ".system" / "audit.jsonl").is_file()
    assert (project / ".system" / "materials.json").is_file()
    assert (project / ".gitignore").read_text(encoding="utf-8") == "00_materials/\n.system/desk/\n"
    verdict = run_script("state_tool.py", "validate", "--project-dir", str(project))
    assert verdict.returncode == 0, verdict.stderr
    assert json.loads(verdict.stdout)["schema"] == "v3"


def _legacy_project(tmp_path, fixtures_dir):
    legacy = tmp_path / ".research"
    shutil.copytree(fixtures_dir / "state" / "v2_gates_approved", legacy)
    (legacy / "protocol.md").write_text("# protocol\n", encoding="utf-8")
    (legacy / "materials").mkdir()
    (legacy / "materials" / "abc_source.txt").write_text("source", encoding="utf-8")
    state = json.loads((legacy / "state.json").read_text(encoding="utf-8"))
    state["project_id"] = "legacy-project-123"
    state["artifacts"]["protocol"] = {
        "path": "protocol.md", "origin": "generated", "validity": "valid",
        "source": None, "produced_by_step": 5, "version": 1, "verified_at": None,
    }
    (legacy / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (legacy / "audit.jsonl").write_text('{"event":"PROJECT_INIT"}\n', encoding="utf-8")
    return legacy


def test_migrate_rewrites_paths_appends_audit_and_is_idempotent(tmp_path, fixtures_dir, run_script):
    legacy = _legacy_project(tmp_path, fixtures_dir)
    project = tmp_path / "research"
    proc = run_script(LAYOUT, "migrate", "--from", str(legacy), "--to", str(project))
    assert proc.returncode == 0, proc.stderr
    assert not legacy.exists()
    state = json.loads((project / ".system" / "state.json").read_text(encoding="utf-8"))
    assert state["schema_version"] == 3
    assert state["artifacts"]["protocol"]["path"] == "05_protocol/protocol.md"
    assert (project / "05_protocol" / "protocol.md").exists()
    assert (project / "00_materials" / "abc_source.txt").exists()
    assert '"event": "SCHEMA_UPGRADED"' in (project / ".system" / "audit.jsonl").read_text(encoding="utf-8")
    assert run_script(LAYOUT, "migrate", "--from", str(legacy), "--to", str(project)).returncode == 0
    verdict = run_script("state_tool.py", "validate", "--project-dir", str(project))
    assert verdict.returncode == 0, verdict.stdout
    can_enter = run_script("state_tool.py", "can-enter", "--project-dir", str(project), "--step", "10")
    assert can_enter.returncode == 0, can_enter.stdout


def test_migrate_dry_run_and_target_conflict_leave_legacy_unchanged(tmp_path, fixtures_dir, run_script):
    legacy = _legacy_project(tmp_path, fixtures_dir)
    target = tmp_path / "research"
    dry = run_script(LAYOUT, "migrate", "--from", str(legacy), "--to", str(target), "--dry-run")
    assert dry.returncode == 0, dry.stderr
    assert json.loads(dry.stdout)["moves"]
    assert (legacy / "state.json").exists() and not target.exists()
    target.mkdir()
    failed = run_script(LAYOUT, "migrate", "--from", str(legacy), "--to", str(target))
    assert failed.returncode == 1
    assert (legacy / "state.json").exists()


def test_migrate_refuses_v1_project_and_leaves_source_intact(tmp_path, run_script):
    legacy = tmp_path / ".research"
    legacy.mkdir()
    (legacy / "state.json").write_text(json.dumps({
        "project_name": "v1-legacy",
        "current_step": 5,
        "steps": {"1": {"status": "completed"}},
        "gates": {"1": {"status": "approved"}, "4": {"status": "pending"}},
    }), encoding="utf-8")
    failed = run_script(LAYOUT, "migrate", "--from", str(legacy), "--to", str(tmp_path / "research"))
    assert failed.returncode == 1
    assert "v1" in failed.stderr
    assert (legacy / "state.json").exists()
    assert not (tmp_path / "research").exists()


def test_schema_upgraded_event_follows_audit_convention(tmp_path, fixtures_dir, run_script):
    legacy = _legacy_project(tmp_path, fixtures_dir)
    project = tmp_path / "research"
    assert run_script(LAYOUT, "migrate", "--from", str(legacy), "--to", str(project)).returncode == 0
    lines = (project / ".system" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    event = next(json.loads(line) for line in lines
                 if line and json.loads(line).get("event") == "SCHEMA_UPGRADED")
    assert event["timestamp"].endswith("Z")
    assert event["details"]["from_schema"] == 2
    assert event["details"]["to_schema"] == 3
    assert event["details"]["migration_receipt"] == {
        "project_id": "legacy-project-123", "source_path": str(legacy.resolve()),
    }


def test_scanner_uses_v3_materials_store(tmp_path, run_script):
    project = tmp_path / "research"
    assert run_script(LAYOUT, "init", "--project-dir", str(project)).returncode == 0
    source = tmp_path / "note.txt"
    source.write_text("cohort notes", encoding="utf-8")
    output = project / ".system" / "scan-report.json"
    proc = run_script("material_scanner.py", "--input", str(source), "--project-dir", str(project), "--output", str(output))
    assert proc.returncode == 0, proc.stderr
    entry = json.loads(output.read_text(encoding="utf-8"))["entries"][0]
    assert entry["stored_as"].startswith("00_materials/")
    assert (project / entry["stored_as"]).exists()


@pytest.mark.parametrize("unsafe_path", ["../outside.txt", "/tmp/researchfellow-outside.txt"])
def test_migrate_rejects_unsafe_registry_paths_without_touching_external_files(
        tmp_path, fixtures_dir, run_script, unsafe_path):
    legacy = _legacy_project(tmp_path, fixtures_dir)
    outside = tmp_path / "outside.txt"
    outside.write_text("must stay", encoding="utf-8")
    state_path = legacy / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["artifacts"]["protocol"]["path"] = str(outside) if unsafe_path.startswith("/") else unsafe_path
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = state_path.read_bytes()

    for dry_run in (True, False):
        args = [LAYOUT, "migrate", "--from", str(legacy), "--to", str(tmp_path / "research")]
        if dry_run:
            args.append("--dry-run")
        proc = run_script(*args)
        assert proc.returncode == 1
        assert "artifacts.protocol.path" in proc.stderr
        assert outside.read_text(encoding="utf-8") == "must stay"
        assert state_path.read_bytes() == before
        assert not (tmp_path / "research").exists()


def test_migrate_dry_run_has_no_source_or_external_side_effects(tmp_path, fixtures_dir, run_script):
    legacy = _legacy_project(tmp_path, fixtures_dir)
    external = tmp_path / "external.txt"
    external.write_text("unchanged", encoding="utf-8")
    before = {path.relative_to(legacy): path.read_bytes() for path in legacy.rglob("*") if path.is_file()}
    proc = run_script(LAYOUT, "migrate", "--from", str(legacy), "--to", str(tmp_path / "research"), "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["moves"]
    assert {path.relative_to(legacy): path.read_bytes() for path in legacy.rglob("*") if path.is_file()} == before
    assert external.read_text(encoding="utf-8") == "unchanged"
    assert not (tmp_path / "research").exists()


def test_migrate_preserves_residual_source_when_v3_receipt_is_unrelated(tmp_path, fixtures_dir, run_script):
    legacy = _legacy_project(tmp_path, fixtures_dir)
    target = tmp_path / "research"
    assert run_script(LAYOUT, "init", "--project-dir", str(target)).returncode == 0
    audit = target / ".system" / "audit.jsonl"
    audit.write_text(json.dumps({"event": "SCHEMA_UPGRADED", "details": {
        "from_schema": 2, "to_schema": 3,
        "migration_receipt": {"project_id": "other-project", "source_path": str(tmp_path / "other")},
    }}) + "\n", encoding="utf-8")
    proc = run_script(LAYOUT, "migrate", "--from", str(legacy), "--to", str(target))
    assert proc.returncode == 1
    assert "receipt does not match" in proc.stderr
    assert legacy.exists()


@pytest.mark.parametrize("mutate", [
    lambda state: state.update({"gates": {"1": {"status": "approved"}}}),
    lambda state: state.pop("schema_version"),
])
def test_migrate_requires_exact_v2_schema(tmp_path, fixtures_dir, run_script, mutate):
    legacy = _legacy_project(tmp_path, fixtures_dir)
    state_path = legacy / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    mutate(state)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    proc = run_script(LAYOUT, "migrate", "--from", str(legacy), "--to", str(tmp_path / "research"))
    assert proc.returncode == 1
    assert legacy.exists() and not (tmp_path / "research").exists()


def test_migrate_rewrites_material_and_round_paths(tmp_path, fixtures_dir, run_script):
    legacy = _legacy_project(tmp_path, fixtures_dir)
    (legacy / "revision").mkdir()
    (legacy / "revision" / "round-1").mkdir()
    (legacy / "revision" / "round-1" / "response.md").write_text("reply", encoding="utf-8")
    (legacy / "revision" / "round-1" / "diff.md").write_text("diff", encoding="utf-8")
    (legacy / "materials.json").write_text(json.dumps({"materials": [{
        "id": "m-1", "stored_as": "materials/abc_source.txt",
    }]}), encoding="utf-8")
    (legacy / "scan-report.json").write_text(json.dumps({"entries": [{
        "stored_as": "materials/abc_source.txt",
    }]}), encoding="utf-8")
    state_path = legacy / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["steps"]["13"] = {"rounds": [{
        "round": 1, "response_letter": "revision/round-1/response.md", "diff": "revision/round-1/diff.md",
    }]}
    state_path.write_text(json.dumps(state), encoding="utf-8")

    target = tmp_path / "research"
    proc = run_script(LAYOUT, "migrate", "--from", str(legacy), "--to", str(target))
    assert proc.returncode == 0, proc.stderr
    state = json.loads((target / ".system" / "state.json").read_text(encoding="utf-8"))
    round_one = state["steps"]["13"]["rounds"][0]
    assert round_one["response_letter"] == "13_revision/round-1/response.md"
    assert round_one["diff"] == "13_revision/round-1/diff.md"
    for name in ("materials.json", "scan-report.json"):
        payload = json.loads((target / ".system" / name).read_text(encoding="utf-8"))
        stored = payload["materials"][0]["stored_as"] if name == "materials.json" else payload["entries"][0]["stored_as"]
        assert stored == "00_materials/abc_source.txt"
        assert (target / stored).exists()


def test_migrate_reports_every_target_collision_before_execution(tmp_path, fixtures_dir, run_script):
    legacy = _legacy_project(tmp_path, fixtures_dir)
    (legacy / "submission").mkdir()
    (legacy / "submission-package").mkdir()
    proc = run_script(LAYOUT, "migrate", "--from", str(legacy), "--to", str(tmp_path / "research"))
    assert proc.returncode == 1
    assert "migration target collisions" in proc.stderr
    assert "submission" in proc.stderr and "submission-package" in proc.stderr
    assert legacy.exists() and not (tmp_path / "research").exists()


def test_migrate_rejects_external_symlink_without_copying_it(tmp_path, fixtures_dir, run_script):
    legacy = _legacy_project(tmp_path, fixtures_dir)
    external = tmp_path / "outside.txt"
    external.write_text("not project data", encoding="utf-8")
    os.symlink(external, legacy / "linked-outside.txt")
    proc = run_script(LAYOUT, "migrate", "--from", str(legacy), "--to", str(tmp_path / "research"))
    assert proc.returncode == 1
    assert "external symlink" in proc.stderr and "linked-outside.txt" in proc.stderr
    assert external.read_text(encoding="utf-8") == "not project data"
    assert legacy.exists() and not (tmp_path / "research").exists()
