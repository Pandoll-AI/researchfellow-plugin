"""Rehearsal mode — the synthetic path can never contaminate the real one.
  1. --mode real REFUSES input carrying the in-band is_synthetic watermark,
  2. --mode rehearsal runs WITHOUT gates and writes only under rehearsal/,
  3. watermark detection covers CSV headers and JSON shapes.
"""

from __future__ import annotations

import json

import analysis_runner as ar

from conftest import requires_stats_stack


def _synthetic_csv(tmp_path):
    p = tmp_path / "synthetic_cohort.csv"
    rows = ["exposed,event,time,is_synthetic"]
    rows += [f"{i % 2},{(i // 2) % 2},{30 + i},1" for i in range(40)]
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def test_real_mode_refuses_synthetic_input(tmp_path, run_script):
    """The guard fires BEFORE gate checks — no state.json/gates.json needed."""
    csv_path = _synthetic_csv(tmp_path)
    proc = run_script("analysis_runner.py", "--mode", "real",
                      "--project-dir", str(tmp_path / ".research"),
                      "--data-path", str(csv_path))
    assert proc.returncode == 1
    assert "is_synthetic" in proc.stderr and "rehearsal" in proc.stderr


def test_watermark_detection_shapes(tmp_path):
    csv_path = _synthetic_csv(tmp_path)
    assert ar._carries_synthetic_watermark(str(csv_path)) is True

    clean = tmp_path / "real.csv"
    clean.write_text("exposed,event\n1,0\n", encoding="utf-8")
    assert ar._carries_synthetic_watermark(str(clean)) is False

    j1 = tmp_path / "agg.json"
    j1.write_text(json.dumps({"is_synthetic": 1, "total": 10}), encoding="utf-8")
    assert ar._carries_synthetic_watermark(str(j1)) is True

    j2 = tmp_path / "records.json"
    j2.write_text(json.dumps({"records": [{"exposed": 1, "is_synthetic": 1}]}), encoding="utf-8")
    assert ar._carries_synthetic_watermark(str(j2)) is True

    j3 = tmp_path / "clean.json"
    j3.write_text(json.dumps({"records": [{"exposed": 1, "event": 0}]}), encoding="utf-8")
    assert ar._carries_synthetic_watermark(str(j3)) is False


@requires_stats_stack
def test_rehearsal_runs_without_gates_and_stays_in_rehearsal_tree(tmp_path, run_script):
    project_dir = tmp_path / ".research"
    project_dir.mkdir()  # deliberately NO state.json, NO gates.json
    csv_path = _synthetic_csv(tmp_path)

    proc = run_script("analysis_runner.py", "--mode", "rehearsal",
                      "--project-dir", str(project_dir),
                      "--data-path", str(csv_path))
    assert proc.returncode == 0, proc.stderr

    out = project_dir / "rehearsal" / "analysis" / "results.json"
    assert out.exists()
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["source"] == "rehearsal"
    assert result["watermark"] == "NOT REAL DATA — REHEARSAL ONLY"
    assert not (project_dir / "analysis" / "rehearsal").exists()
    assert not (project_dir / "analysis" / "real").exists()
