"""material_scanner.py — first coverage, focused on the intake PHI boundary.

Scanner-produced excerpts (docx / md / txt / code, headings included) feed the
host-LLM Stage 2 classification, so they must be masked BEFORE they enter the
scan report — always, independent of --phi-screen — and the fallback when the
engine is unavailable must be withholding, never raw emission (fail-closed).
"""

from __future__ import annotations

import json
import zipfile

import material_scanner as ms

_RRN_WEIGHTS = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]


def _valid_rrn(first12: str) -> str:
    s = sum(int(first12[i]) * _RRN_WEIGHTS[i] for i in range(12))
    check = (11 - (s % 11)) % 10
    return first12 + str(check)


RRN = _valid_rrn("900101123456")
RRN_DISPLAY = f"{RRN[:6]}-{RRN[6:]}"
PHONE = "010-1234-5678"
EMAIL = "kim.cs@hospital.kr"
SECRETS = (RRN, RRN_DISPLAY, PHONE, EMAIL)

_DOCX_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _make_minimal_docx(path, paragraphs, headings=()):
    """A one-part docx: scan_docx only reads word/document.xml, so the other
    OPC parts ([Content_Types].xml, rels) are unnecessary for the fixture."""
    body = []
    for h in headings:
        body.append(
            f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
            f"<w:r><w:t>{h}</w:t></w:r></w:p>"
        )
    for p in paragraphs:
        body.append(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>")
    doc = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{_DOCX_NS}"><w:body>{"".join(body)}</w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", doc)
    return path


# ---------------------------------------------------------------------------
# docx: body + headings both masked, nothing leaks from the whole result
# ---------------------------------------------------------------------------
def test_scan_docx_masks_body_and_headings(tmp_path):
    path = _make_minimal_docx(
        tmp_path / "case-notes.docx",
        paragraphs=[f"보호자 연락처 {PHONE}", f"문의 {EMAIL}"],
        headings=[f"증례 요약 ({RRN_DISPLAY})"],
    )
    result = ms.scan_docx(str(path))

    excerpt = result["structure"]["excerpt"]
    assert "[MASKED:" in excerpt
    for secret in SECRETS:
        assert secret not in excerpt
    for heading in result["structure"]["headings"]:
        for secret in SECRETS:
            assert secret not in heading
    assert result["phi"]["screened"] is True
    assert result["phi"]["severity"] in ("warning", "critical")
    assert result["excerpt_source"] == "scanner"


def test_scan_docx_full_result_no_leak(tmp_path):
    path = _make_minimal_docx(
        tmp_path / "notes.docx",
        paragraphs=[f"주민번호 {RRN_DISPLAY} 전화 {PHONE} 메일 {EMAIL}"],
    )
    blob = json.dumps(ms.scan_docx(str(path)), ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in blob, f"LEAK: {secret!r} surfaced in scan result"


# ---------------------------------------------------------------------------
# md / txt documents
# ---------------------------------------------------------------------------
def test_scan_text_document_masks_md_heading_and_body(tmp_path):
    path = tmp_path / "draft.md"
    path.write_text(
        f"# 문의처 {EMAIL}\n\n대상자 주민등록번호 {RRN_DISPLAY} 를 포함한 기록.\n",
        encoding="utf-8",
    )
    result = ms.scan_text_document(str(path), "md")

    excerpt = result["structure"]["excerpt"]
    for secret in SECRETS:
        assert secret not in excerpt
    assert "[MASKED:" in excerpt
    for heading in result["structure"]["headings"]:
        assert EMAIL not in heading
    assert result["phi"]["screened"] is True


def test_scan_text_document_clean_input_untouched(tmp_path):
    body = "# 방법\n\n후향 코호트, 성향점수 매칭. 결측은 MICE.\n"
    path = tmp_path / "clean.md"
    path.write_text(body, encoding="utf-8")
    result = ms.scan_text_document(str(path), "md")

    assert result["structure"]["excerpt"] == body[: ms.TEXT_EXCERPT_LEN]
    assert result["phi"] == {
        "screened": True, "backend": "rules", "target": "excerpt",
        "severity": "clean", "finding_count": 0,
    }


# ---------------------------------------------------------------------------
# code: role signature judged on the ORIGINAL text, excerpt masked
# ---------------------------------------------------------------------------
def test_scan_code_masks_excerpt_preserves_role_signature(tmp_path):
    path = tmp_path / "model.R"
    path.write_text(
        f"# 대상자 {RRN_DISPLAY}\nfit <- coxph(Surv(time, event) ~ exposed, data = df)\n",
        encoding="utf-8",
    )
    result = ms.scan_code(str(path), "r")

    assert result["rule_role_hint"]["role"] == "analysis_code"
    assert result["rule_role_hint"]["certainty"] == "strong"
    excerpt = result["structure"]["excerpt"]
    assert RRN_DISPLAY not in excerpt and RRN not in excerpt
    assert "coxph" in excerpt  # masking must not disturb non-PHI code text


# ---------------------------------------------------------------------------
# fail-closed paths
# ---------------------------------------------------------------------------
def test_fail_closed_when_engine_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "phi_detect", None)
    path = tmp_path / "notes.txt"
    path.write_text(f"전화 {PHONE}\n", encoding="utf-8")
    result = ms.scan_text_document(str(path), "txt")

    assert result["structure"]["excerpt"] == ""
    assert result["excerpt_source"] == "unscreened"
    assert result["phi"]["screened"] is False
    assert result["phi"]["reason"] == "phi_engine_unavailable"


def test_fail_closed_on_unsupported_backend_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RF_PHI_BACKEND", "nonexistent")
    path = tmp_path / "notes.txt"
    path.write_text(f"메일 {EMAIL}\n", encoding="utf-8")
    result = ms.scan_text_document(str(path), "txt")

    assert result["structure"]["excerpt"] == ""
    assert result["phi"]["screened"] is False
    assert result["phi"]["reason"] == "UnsupportedPHIBackendError"


# ---------------------------------------------------------------------------
# CLI end-to-end: masking is on WITHOUT --phi-screen, flags reach the report
# ---------------------------------------------------------------------------
def test_cli_end_to_end_sets_phi_suspect_flag(tmp_path, run_script):
    src = tmp_path / "memo.txt"
    src.write_text(f"보호자 {PHONE} / 문의 {EMAIL}\n", encoding="utf-8")
    out = tmp_path / "scan-report.json"

    proc = run_script(
        "material_scanner.py",
        "--input", str(src),
        "--project-dir", str(tmp_path / ".research"),
        "--output", str(out),
    )
    assert proc.returncode == 0, proc.stderr

    report = json.loads(out.read_text(encoding="utf-8"))
    (entry,) = report["entries"]
    assert "phi_suspect" in entry.get("flags", [])
    assert entry["phi"]["screened"] is True
    blob = json.dumps(report, ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in blob, f"LEAK: {secret!r} surfaced in scan report"
