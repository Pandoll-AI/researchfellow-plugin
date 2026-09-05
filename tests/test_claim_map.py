"""claim_map.py — deterministic claim → evidence binding.

Pins the CLI JSON contract, fail-closed schema_version, unmapped heuristic,
and the duplicate-anchor rule (each occurrence is a distinct claim, document order).
"""

from __future__ import annotations

import json

import pytest

import claim_map as cm

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


def test_unmapped_or_was_without_ci(run_script, fixtures_dir):
    """The crude OR was 1.45. (no CI) → unmapped."""
    proc = _run(
        run_script,
        fixtures_dir / "claimmap_or_was_manuscript.md",
        fixtures_dir / "claimmap_unmapped_evidence.json",
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got["claims"] == []
    assert any("OR was 1.45" in item["text"] for item in got["unmapped"])


def test_unmapped_ahr(run_script, fixtures_dir):
    """aHR=0.72 → unmapped."""
    proc = _run(
        run_script,
        fixtures_dir / "claimmap_ahr_manuscript.md",
        fixtures_dir / "claimmap_unmapped_evidence.json",
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got["claims"] == []
    assert any("aHR=0.72" in item["text"] for item in got["unmapped"])


def test_fake_table_anchor_is_unverified(run_script, fixtures_dir):
    """<!-- claim: table:999 --> with no Table 999 in the manuscript → unverified."""
    proc = _run(
        run_script,
        fixtures_dir / "claimmap_fake_table_manuscript.md",
        fixtures_dir / "claimmap_unmapped_evidence.json",
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert len(got["claims"]) == 1
    assert got["claims"][0]["anchor"] == {"kind": "table", "id": "999"}
    assert got["claims"][0]["status"] == "unverified"


def test_schema_version_integer_is_fail_closed(run_script, fixtures_dir):
    """evidence schema_version: 1 (JSON integer) → fail-closed, including map_claims."""
    proc = _run(
        run_script,
        fixtures_dir / "claimmap_unmapped_manuscript.md",
        fixtures_dir / "claimmap_schema_int_evidence.json",
    )
    assert proc.returncode != 0
    assert "schema_version mismatch" in proc.stderr
    with pytest.raises(cm.ClaimMapError, match="schema_version mismatch"):
        cm.map_claims("# x\n", {"schema_version": 1, "papers": []})


def test_text_anchor_non_identifier_is_unverified(run_script, fixtures_dir):
    """text 앵커 비식별자 → unverified (not mismatch)."""
    proc = _run(
        run_script,
        fixtures_dir / "claimmap_text_nonid_manuscript.md",
        fixtures_dir / "claimmap_unmapped_evidence.json",
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert len(got["claims"]) == 1
    assert got["claims"][0]["anchor"] == {"kind": "text", "id": "not-an-id"}
    assert got["claims"][0]["status"] == "unverified"


_EMPTY_EVIDENCE = {"schema_version": "1", "papers": []}


def test_malformed_ids_cannot_verify_even_when_in_evidence():
    """Syntactic junk in evidence.json must not become verified=true."""
    for bad in ("10.fake", "10.123", "10.1234", "10.123/foo", "10.1234567890/x"):
        report = cm.map_claims(
            f"Result <!-- claim: text:{bad} -->",
            {"schema_version": "1", "papers": [{"doi": bad, "pmid": bad}]},
        )
        assert report["claims"], bad
        claim = report["claims"][0]
        assert claim["status"] == "unverified", bad
        assert all(not src["verified"] for src in claim["sources"]), (bad, claim["sources"])
        assert cm.parse_identifier(bad) is None, bad


def test_valid_doi_forms_preserve_identity():
    ident = "10.1001/JAMA.2024.1234"
    assert cm.parse_identifier(ident) == ("doi", ident)
    assert cm.parse_identifier("https://doi.org/" + ident) == ("doi", ident)
    assert cm.parse_identifier("https://dx.doi.org/" + ident) == ("doi", ident)
    assert cm.parse_identifier("http://doi.org/" + ident) == ("doi", ident)
    assert cm.parse_identifier("doi:" + ident) == ("doi", ident)
    assert cm.parse_identifier("DOI: " + ident) == ("doi", ident)

    paren = "10.1234/foo(bar)"
    assert cm.parse_identifier(paren) == ("doi", paren)
    assert cm.parse_identifier(paren + ".") == ("doi", paren)
    assert cm.parse_identifier(paren + ").") == ("doi", paren)
    assert cm._normalize_doi(paren) == paren

    evidence = {
        "schema_version": "1",
        "papers": [{"doi": "https://doi.org/10.1001/jama.2024.1234"}],
    }
    report = cm.map_claims(
        f"Cite ({ident}). <!-- claim: text:{ident} -->",
        evidence,
    )
    claim = report["claims"][0]
    assert claim["status"] == "verified"
    doi_src = [s for s in claim["sources"] if s["type"] == "doi"]
    assert doi_src[0]["id"] == ident
    assert doi_src[0]["verified"] is True


def test_pmid_numeric_behavior_preserved():
    assert cm.parse_identifier("123") == ("pmid", "123")
    assert cm.parse_identifier("38812345") == ("pmid", "38812345")
    assert cm.parse_identifier("PMID:38812345") == ("pmid", "38812345")
    report = cm.map_claims(
        "n=1 <!-- claim: text:123 -->",
        {"schema_version": "1", "papers": [{"pmid": "123"}]},
    )
    assert report["claims"][0]["status"] == "verified"
    assert report["claims"][0]["sources"] == [
        {"type": "pmid", "id": "123", "verified": True},
    ]


def test_signed_estimates_unmapped_without_anchor():
    cases = [
        "SMD = -0.45.",
        "RD = \u22120.2.",
        "SMD = .5.",
        "HR = +1.2e-3.",
        "OR = 1.2e-3",
        "95% CI -0.2 to 0.3",
        "SMD = -.5",
    ]
    for text in cases:
        assert cm.EFFECT_RE.search(text), text
        report = cm.map_claims(text, _EMPTY_EVIDENCE)
        assert report["claims"] == [], text
        assert report["unmapped"], text
        assert report["unmapped"][0]["text"] == text


def test_anchored_estimates_remain_mapped():
    manuscript = (
        "The SMD = -0.45.\n"
        "<!-- claim: table:2 -->\n"
        "\n"
        "RD = \u22120.2.\n"
        "<!-- claim: table:2 -->\n"
        "\n"
        "**Table 2.** Results.\n"
    )
    report = cm.map_claims(manuscript, _EMPTY_EVIDENCE)
    assert report["unmapped"] == []
    assert [c["status"] for c in report["claims"]] == ["verified", "verified"]
    assert [c["text"] for c in report["claims"]] == [
        "The SMD = -0.45.",
        "RD = \u22120.2.",
    ]


def test_duplicate_anchor_output_preserves_manuscript_order():
    manuscript = (
        "First SMD = -0.45.\n"
        "<!-- claim: table:2 -->\n"
        "\n"
        "Second SMD = .5.\n"
        "<!-- claim: table:2 -->\n"
        "\n"
        "Table 2. Results.\n"
    )
    report = cm.map_claims(manuscript, _EMPTY_EVIDENCE)
    assert [c["anchor"] for c in report["claims"]] == [
        {"kind": "table", "id": "2"},
        {"kind": "table", "id": "2"},
    ]
    assert [c["text"] for c in report["claims"]] == [
        "First SMD = -0.45.",
        "Second SMD = .5.",
    ]
    assert report["unmapped"] == []


def test_effect_word_boundaries_skip_unrelated_prose():
    for text in (
        "STANDARD = 1.0",
        "The ASMD value is unrelated",
        "ORDER 5 was submitted.",
        "HR department 12",
    ):
        assert cm.EFFECT_RE.search(text) is None, text
        report = cm.map_claims(text, _EMPTY_EVIDENCE)
        assert report["unmapped"] == [], text
