"""env_check.py — preflight JSON for `/rf doctor`."""

from __future__ import annotations

import json
import time

import env_check

SCRIPT = "env_check.py"
EXPECTED_KEYS = {
    "python",
    "packages",
    "plugin_version",
    "mcp",
    "modes",
    "next_action",
    "project",
}
BAD_MCP = "http://127.0.0.1:9/"


def test_stdout_keys_match_contract(run_script):
    proc = run_script(SCRIPT, "--mcp-url", BAD_MCP)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert set(payload) == EXPECTED_KEYS


def test_missing_packages_disable_real_mode(monkeypatch):
    monkeypatch.setattr(env_check, "_package_version", lambda name: None)
    report = env_check.build_report(mcp_url=BAD_MCP)
    assert report["modes"]["real"] is False
    assert report["packages"] == {
        "pandas": None,
        "statsmodels": None,
        "lifelines": None,
    }


def test_bad_mcp_url_unreachable_within_timeout(run_script):
    started = time.monotonic()
    proc = run_script(SCRIPT, "--mcp-url", BAD_MCP)
    elapsed = time.monotonic() - started
    assert elapsed < 3.5, elapsed
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["mcp"]["reachable"] is False
    assert payload["mcp"]["url"] == BAD_MCP


def test_missing_project_dir_still_exits_zero(tmp_path, run_script):
    proc = run_script(
        SCRIPT,
        "--project-dir",
        str(tmp_path / "no-such-project"),
        "--mcp-url",
        BAD_MCP,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert set(payload) == EXPECTED_KEYS


def test_project_dir_with_state_json_reports_schema_version(tmp_path, run_script):
    project = tmp_path / "research"
    (project / ".system").mkdir(parents=True)
    (project / ".system" / "state.json").write_text(
        json.dumps({"schema_version": 3}),
        encoding="utf-8",
    )
    proc = run_script(
        SCRIPT,
        "--project-dir",
        str(project),
        "--mcp-url",
        BAD_MCP,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["project"]["exists"] is True
    assert payload["project"]["schema_version"] == 3
    assert payload["project"]["dir"] == str(project.resolve())
    if payload["modes"]["synthetic"]:
        assert payload["next_action"] == env_check.NEXT_ACTION_PROJECT


def test_absent_project_dir_reports_exists_false(tmp_path, run_script):
    missing = tmp_path / "no-such-project"
    proc = run_script(
        SCRIPT,
        "--project-dir",
        str(missing),
        "--mcp-url",
        BAD_MCP,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["project"]["exists"] is False
    assert payload["project"]["schema_version"] is None
    assert payload["project"]["dir"] == str(missing.resolve())
