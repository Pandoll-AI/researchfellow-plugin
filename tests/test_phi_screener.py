"""phi_screener.py — the strongest existing asset. Two things must hold:
  1. it detects Korean identifiers (recall), and
  2. it NEVER writes a matched value (or fragment) into its report (no-leak).
The no-leak property is the load-bearing guarantee behind "PHI never leaves the
machine", so it gets an explicit adversarial check.
"""

from __future__ import annotations

import json

import phi_screener as phi

_RRN_WEIGHTS = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]


def _valid_rrn(first12: str) -> str:
    s = sum(int(first12[i]) * _RRN_WEIGHTS[i] for i in range(12))
    check = (11 - (s % 11)) % 10
    return first12 + str(check)


def _write_phi_csv(tmp_path):
    rrn = _valid_rrn("900101123456")            # 13-digit, checksum-valid
    rrn_display = f"{rrn[:6]}-{rrn[6:]}"
    secrets = {
        "rrn": rrn,
        "rrn_display": rrn_display,
        "phone": "010-1234-5678",
        "email": "kim.cs@hospital.kr",
        "name0": "김철수",
    }
    names = ["김철수", "이영희", "박민준", "최수빈", "정하늘"]
    phones = ["010-1234-5678", "010-2345-6789", "010-3456-7890", "010-4567-8901", "010-5678-9012"]
    emails = ["kim.cs@hospital.kr", "lee@clinic.org", "park@med.kr", "choi@hosp.com", "jung@lab.net"]
    lines = ["주민번호,연락처,이메일,환자명"]
    for i in range(5):
        lines.append(f"{rrn_display},{phones[i]},{emails[i]},{names[i]}")
    path = tmp_path / "phi.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, secrets, names, phones, emails


def test_detects_korean_identifiers(tmp_path):
    path, *_ = _write_phi_csv(tmp_path)
    report = phi.run_screen(str(path))
    fired = {f["rule_id"] for f in report["findings"]}
    assert {"krn_rrn", "phone_kr", "email", "person_name"}.issubset(fired), fired
    assert report["max_severity"] in ("warning", "critical")


def test_report_never_leaks_a_matched_value(tmp_path):
    """The adversarial no-leak check: no injected identifier — raw or formatted,
    nor any name/phone/email — may appear anywhere in the serialized report."""
    path, secrets, names, phones, emails = _write_phi_csv(tmp_path)
    report = phi.run_screen(str(path))
    blob = json.dumps(report, ensure_ascii=False)

    for value in secrets.values():
        assert value not in blob, f"LEAK: secret value surfaced in report: {value!r}"
    for value in [*names, *phones, *emails]:
        assert value not in blob, f"LEAK: identifier surfaced in report: {value!r}"

    # Findings must carry only structural metadata (row numbers, not values).
    for finding in report["findings"]:
        assert set(finding).issuperset({"column", "rule_id", "match_count", "example_rows"})
        assert all(isinstance(r, int) for r in finding["example_rows"])


def test_clean_input_yields_no_findings(tmp_path):
    path = tmp_path / "clean.csv"
    path.write_text("age,sex,los_days\n64,M,7\n71,F,3\n", encoding="utf-8")
    report = phi.run_screen(str(path))
    assert report["findings"] == []
    assert report["max_severity"] == "clean"
