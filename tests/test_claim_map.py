"""claim_map.py — deterministic claim → evidence binding.

Pins the CLI JSON contract, fail-closed schema_version, unmapped heuristic,
and the duplicate-anchor rule (each occurrence is a distinct claim, document order).
"""

from __future__ import annotations

import json

SCRIPT = "claim_map.py"


def _run(run_script, manuscript, evidence):
    return run_script(
        SCRIPT,
        "--manuscript", str(manuscript),
        "--evidence", str(evidence),
    )


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_1_normal_mapping_matches_expected_json(run_script, fixtures_dir):
    """(1) 정상 매핑 fixture → 기대 JSON과 diff 0."""
    proc = _run(
        run_script,
        fixtures_dir / "claimmap_ok_manuscript.md",
        fixtures_dir / "claimmap_ok_evidence.json",
    )
    expected_path = fixtures_dir / "claimmap_ok_expected.json"
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == expected_path.read_text(encoding="utf-8")
    got = json.loads(proc.stdout)
    assert got == _load(expected_path)
    assert [c["status"] for c in got["claims"]] == ["verified", "verified", "verified"]
    assert got["unmapped"] == []


def test_2_unknown_pmid_anchor_status(run_script, fixtures_dir):
    """(2) 고의 오인용 — missing PMID → unverified; mixed id → mismatch."""
    proc = _run(
        run_script,
        fixtures_dir / "claimmap_badpmid_manuscript.md",
        fixtures_dir / "claimmap_badpmid_evidence.json",
    )
    expected_path = fixtures_dir / "claimmap_badpmid_expected.json"
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got == _load(expected_path)
    assert got["claims"][0]["anchor"] == {"kind": "text", "id": "99999999"}
    assert got["claims"][0]["status"] == "unverified"
    assert got["claims"][0]["sources"] == [
        {"type": "pmid", "id": "99999999", "verified": False},
    ]
    assert got["claims"][1]["status"] == "mismatch"
    assert got["claims"][1]["sources"][0]["verified"] is True
    assert got["claims"][1]["sources"][1]["verified"] is False


def test_3_unanchored_effect_estimate_is_unmapped(run_script, fixtures_dir):
    """(3) 앵커 없는 수치 주장 → unmapped에 잡힘."""
    proc = _run(
        run_script,
        fixtures_dir / "claimmap_unmapped_manuscript.md",
        fixtures_dir / "claimmap_unmapped_evidence.json",
    )
    expected_path = fixtures_dir / "claimmap_unmapped_expected.json"
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got == _load(expected_path)
    assert got["claims"] == []
    assert len(got["unmapped"]) == 1
    assert "OR" in got["unmapped"][0]["text"]
    assert "1.45" in got["unmapped"][0]["text"]


def test_4_duplicate_anchor_ids_are_deterministic(run_script, fixtures_dir):
    """(4) 같은 (kind, id) 중복 앵커 → 문서 순서대로 각각 별도 claim (명시 규칙)."""
    proc = _run(
        run_script,
        fixtures_dir / "claimmap_dup_manuscript.md",
        fixtures_dir / "claimmap_dup_evidence.json",
    )
    expected_path = fixtures_dir / "claimmap_dup_expected.json"
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got == _load(expected_path)
    anchors = [c["anchor"] for c in got["claims"]]
    assert anchors == [
        {"kind": "table", "id": "2"},
        {"kind": "table", "id": "2"},
    ]
    assert got["claims"][0]["text"] != got["claims"][1]["text"]
    assert got["unmapped"] == []

    malformed = _run(
        run_script,
        fixtures_dir / "claimmap_malformed_manuscript.md",
        fixtures_dir / "claimmap_unmapped_evidence.json",
    )
    assert malformed.returncode != 0
    assert "malformed claim anchor" in malformed.stderr
    assert "graph:1" in malformed.stderr


def test_5_schema_version_mismatch_is_fail_closed(run_script, fixtures_dir):
    """(5) schema_version 불일치 evidence → fail-closed 비-0 exit."""
    proc = _run(
        run_script,
        fixtures_dir / "claimmap_unmapped_manuscript.md",
        fixtures_dir / "claimmap_schema_evidence.json",
    )
    assert proc.returncode != 0
    assert "schema_version mismatch" in proc.stderr
    assert "99" in proc.stderr
