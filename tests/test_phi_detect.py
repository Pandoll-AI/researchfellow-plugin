"""phi_detect.py — the detection engine extracted from phi_screener.py.

Contracts under test:
  1. backend selection is explicit — unknown RF_PHI_BACKEND raises, never a
     silent fallback,
  2. the finding schema is byte-compatible with the legacy phi_screener one,
  3. RuleBackend's free-text path only ever emits TEXT_ELIGIBLE_RULES,
  4. redact_text never leaks an original value — including matches that
     straddle the excerpt cutoff — and never returns a half-masked string.
"""

from __future__ import annotations

from typing import List

import phi_detect

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


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------
def test_default_backend_is_rules():
    assert phi_detect.get_backend().name == "rules"


def test_env_backend_selection(monkeypatch):
    monkeypatch.setenv("RF_PHI_BACKEND", "rules")
    assert phi_detect.get_backend().name == "rules"


def test_unsupported_backend_raises_explicit_error(monkeypatch):
    monkeypatch.setenv("RF_PHI_BACKEND", "presidio")
    try:
        phi_detect.get_backend()
    except phi_detect.UnsupportedPHIBackendError as exc:
        msg = str(exc)
        assert "presidio" in msg and "rules" in msg
    else:
        raise AssertionError("unknown backend must raise, not fall back silently")


# ---------------------------------------------------------------------------
# Tabular detection — legacy schema compatibility
# ---------------------------------------------------------------------------
def test_detect_tabular_schema_matches_legacy():
    header = ["주민번호", "연락처", "이메일", "환자명"]
    names = ["김철수", "이영희", "박민준", "최수빈", "정하늘"]
    body = [[RRN_DISPLAY, PHONE, EMAIL, names[i]] for i in range(5)]

    findings = phi_detect.detect_tabular(header, body)
    fired = {f["rule_id"] for f in findings}
    assert {"krn_rrn", "phone_kr", "email", "person_name"}.issubset(fired), fired
    for f in findings:
        assert set(f) == {"column", "rule_id", "severity", "match_count",
                          "match_rate", "example_rows", "downgraded_from_critical"}
        assert all(isinstance(r, int) for r in f["example_rows"])
    assert phi_detect.max_severity(findings) in ("warning", "critical")


def test_low_match_rate_downgrades_critical_to_warning():
    header = ["memo"]
    body = [["clean row"] for _ in range(99)] + [[f"연락처 {PHONE}"]]
    findings = phi_detect.detect_tabular(header, body)
    (finding,) = [f for f in findings if f["rule_id"] == "phone_kr"]
    assert finding["severity"] == "warning"
    assert finding["downgraded_from_critical"] is True


# ---------------------------------------------------------------------------
# Free-text detection — rule subset contract
# ---------------------------------------------------------------------------
def test_detect_text_never_emits_column_gated_rules():
    text = "\n".join([
        f"환자 김철수 ({RRN_DISPLAY})",
        "김철수",                      # bare Korean name line — must NOT fire
        f"보호자 연락처: {PHONE}",
        f"문의: {EMAIL}",
        "생년월일: 1990-01-01",        # full date — column-gated, must NOT fire
    ])
    findings = phi_detect.detect_text(text)
    fired = {f["rule_id"] for f in findings}
    assert fired
    assert fired.issubset(set(phi_detect.TEXT_ELIGIBLE_RULES)), fired
    assert not fired & set(phi_detect.COLUMN_GATED_RULES)


# ---------------------------------------------------------------------------
# redact_text — no-leak, merging, cutoff boundary, self-check
# ---------------------------------------------------------------------------
def test_redact_text_no_leak_adversarial():
    text = f"주민번호 {RRN_DISPLAY} / 전화 {PHONE} / 메일 {EMAIL} 끝"
    masked, findings = phi_detect.redact_text(text)
    for secret in SECRETS:
        assert secret not in masked, f"LEAK: {secret!r} survived masking"
    assert "[MASKED:" in masked
    assert masked.startswith("주민번호 ") and masked.endswith(" 끝")
    assert findings  # computed on the original buffer


def test_redact_text_clean_input_is_untouched():
    text = "age,sex,los_days — 관찰 코호트 요약."
    masked, findings = phi_detect.redact_text(text)
    assert masked == text
    assert findings == []


def test_merge_spans_collapses_overlaps():
    spans = [
        phi_detect.Span(0, 5, "email", "warning"),
        phi_detect.Span(3, 8, "phone_kr", "critical"),
        phi_detect.Span(20, 25, "email", "warning"),
    ]
    merged = phi_detect._merge_spans(spans)
    assert [(s.start, s.end) for s in merged] == [(0, 8), (20, 25)]


def test_redact_text_boundary_at_excerpt_cutoff():
    """An identifier straddling the 3000-char excerpt cutoff must not leave a
    fragment behind: masking happens BEFORE slicing, so the slice can only cut
    a placeholder token, never original identifier characters."""
    prefix = "x" * 2995
    text = prefix + EMAIL + " tail"          # EMAIL occupies chars 2995..3013
    masked, _ = phi_detect.redact_text(text)
    excerpt = masked[:3000]
    assert EMAIL not in excerpt
    assert "kim.cs" not in excerpt and "hospital.kr" not in excerpt


def test_redact_text_self_check_blanks_on_leftover():
    """A backend that under-reports spans must yield '' (fail-closed), never a
    partially masked string."""
    class BuggyBackend:
        name = "buggy"
        _real = phi_detect.RuleBackend()

        def detect_text(self, text: str):
            return self._real.detect_text(text)

        def detect_tabular(self, header, body):
            return self._real.detect_tabular(header, body)

        def find_spans(self, text: str) -> List[phi_detect.Span]:
            return self._real.find_spans(text)[:1]  # drops every match but the first

        def __init__(self):
            pass

    text = f"first {EMAIL} second lee@clinic.org"
    masked, _ = phi_detect.redact_text(text, backend=BuggyBackend())
    assert masked == ""
