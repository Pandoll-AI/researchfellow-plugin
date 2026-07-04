#!/usr/bin/env python3
"""PHI / personal-identifier screener for the ResearchFellow skill (FR-T6, FR-M10).

Rule-based screening of tabular (CSV / XLSX) or plain-text material for Korean
personal identifiers. Standard library only, fully offline.

ABSOLUTE RULE — a matched value or any fragment of it is NEVER written to
stdout, stderr, the output JSON, or any exception message. Findings report only
{column, rule_id, match_count, match_rate, example_rows (row numbers only)}.

Usage:
    python3 phi_screener.py --data-path data.csv  --output .research/phi-report_m-001.json
    python3 phi_screener.py --data-path data.xlsx --output .research/phi-report_m-002.json

Exit codes:
    0  clean    (no findings)
    1  warning  (only warning-level findings)
    2  critical (at least one critical finding)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Patterns (compiled once). None of these values are ever emitted.
# ---------------------------------------------------------------------------
RRN_RE = re.compile(r"(?<!\d)(\d{6})[- ]?([1-4]\d{6})(?!\d)")
PHONE_RE = re.compile(r"(?<!\d)01[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
DATE_RE = re.compile(r"(?<!\d)(?:\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{8})(?!\d)")
KOR_SYLLABLE_RE = re.compile(r"^[가-힣]{2,4}$")

NAME_COL_HINTS = ("이름", "성명", "성함", "환자명", "name", "patient_name", "pt_name")
BIRTH_COL_HINTS = ("생년월일", "생일", "birth", "dob", "birthdate", "birth_date")

# Rule -> base severity
BASE_SEVERITY = {
    "krn_rrn": "critical",
    "phone_kr": "critical",
    "person_name": "critical",
    "email": "warning",
    "exact_birthdate": "warning",
}

MATCH_RATE_DOWNGRADE_THRESHOLD = 0.05  # critical -> warning below this rate
MAX_EXAMPLE_ROWS = 5

RECOMMENDATION = (
    "식별자로 의심되는 컬럼/패턴이 감지되었습니다. 원본을 가명화(비식별화)한 뒤 "
    "다시 반입하는 것을 권장합니다. 스크리닝은 보조적 검사이며 최종 확인 책임은 "
    "사용자에게 있습니다."
)
RECOMMENDATION_CLEAN = (
    "규칙 스캔에서 식별자 패턴이 감지되지 않았습니다. 스크리닝은 보조적 검사이며 "
    "최종 확인 책임은 사용자에게 있습니다."
)


# ---------------------------------------------------------------------------
# Checksum for Korean Resident Registration Number (오탐 감축)
# ---------------------------------------------------------------------------
def _rrn_checksum_valid(digits: str) -> bool:
    """Validate the 13-digit RRN check digit. `digits` must be 13 chars, no sep."""
    if len(digits) != 13 or not digits.isdigit():
        return False
    weights = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
    total = sum(int(digits[i]) * weights[i] for i in range(12))
    check = (11 - (total % 11)) % 10
    return check == int(digits[12])


# ---------------------------------------------------------------------------
# Per-value rule checkers. Return True on a (validated) match. No value leaks.
# ---------------------------------------------------------------------------
def _hit_rrn(value: str) -> bool:
    for m in RRN_RE.finditer(value):
        if _rrn_checksum_valid(m.group(1) + m.group(2)):
            return True
    return False


def _hit_phone(value: str) -> bool:
    return PHONE_RE.search(value) is not None


def _hit_email(value: str) -> bool:
    return EMAIL_RE.search(value) is not None


def _hit_full_date(value: str) -> bool:
    return DATE_RE.search(value) is not None


def _is_korean_name(value: str) -> bool:
    return KOR_SYLLABLE_RE.match(value.strip()) is not None


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def _load_csv(path: str) -> Tuple[List[str], List[List[str]]]:
    import csv

    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return [], []
    header = [str(c) for c in rows[0]]
    body = [[str(c) for c in r] for r in rows[1:]]
    return header, body


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> List[str]:
    strings: List[str] = []
    try:
        data = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return strings
    root = ET.fromstring(data)
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    for si in root.findall(f"{ns}si"):
        # concatenate all <t> descendants (handles rich text runs)
        parts = [t.text or "" for t in si.iter(f"{ns}t")]
        strings.append("".join(parts))
    return strings


def _xlsx_first_sheet_path(zf: zipfile.ZipFile) -> str:
    names = zf.namelist()
    candidates = sorted(n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml"))
    return candidates[0] if candidates else "xl/worksheets/sheet1.xml"


def _load_xlsx(path: str, max_rows: int = 100000) -> Tuple[List[str], List[List[str]]]:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as zf:
        shared = _xlsx_shared_strings(zf)
        sheet_path = _xlsx_first_sheet_path(zf)
        root = ET.fromstring(zf.read(sheet_path))

    def col_index(ref: str) -> int:
        letters = re.match(r"[A-Z]+", ref or "")
        if not letters:
            return 0
        idx = 0
        for ch in letters.group(0):
            idx = idx * 26 + (ord(ch) - ord("A") + 1)
        return idx - 1

    rows_out: List[List[str]] = []
    sheet_data = root.find(f"{ns}sheetData")
    if sheet_data is None:
        return [], []
    for row in sheet_data.findall(f"{ns}row"):
        cells: Dict[int, str] = {}
        max_c = -1
        for c in row.findall(f"{ns}c"):
            ci = col_index(c.get("r", ""))
            ctype = c.get("t")
            v_el = c.find(f"{ns}v")
            text = ""
            if ctype == "s":  # shared string index
                if v_el is not None and v_el.text is not None:
                    si = int(v_el.text)
                    if 0 <= si < len(shared):
                        text = shared[si]
            elif ctype == "inlineStr":
                is_el = c.find(f"{ns}is")
                if is_el is not None:
                    text = "".join(t.text or "" for t in is_el.iter(f"{ns}t"))
            else:
                if v_el is not None and v_el.text is not None:
                    text = v_el.text
            cells[ci] = text
            if ci > max_c:
                max_c = ci
        row_list = [cells.get(i, "") for i in range(max_c + 1)]
        rows_out.append(row_list)
        if len(rows_out) >= max_rows:
            break

    if not rows_out:
        return [], []
    header = [str(c) for c in rows_out[0]]
    width = len(header)
    body = []
    for r in rows_out[1:]:
        r = list(r) + [""] * (width - len(r))
        body.append(r[:width])
    return header, body


# ---------------------------------------------------------------------------
# Tabular screening
# ---------------------------------------------------------------------------
def _column_name_matches(col: str, hints: Tuple[str, ...]) -> bool:
    low = col.lower()
    return any(h.lower() in low for h in hints)


def _build_finding(column: Optional[str], rule_id: str, match_rows: List[int], n_rows: int) -> Dict[str, Any]:
    match_count = len(match_rows)
    match_rate = round(match_count / n_rows, 4) if n_rows else 0.0
    severity = BASE_SEVERITY[rule_id]
    downgraded = False
    if severity == "critical" and match_rate < MATCH_RATE_DOWNGRADE_THRESHOLD:
        severity = "warning"
        downgraded = True
    return {
        "column": column,
        "rule_id": rule_id,
        "severity": severity,
        "match_count": match_count,
        "match_rate": match_rate,
        "example_rows": match_rows[:MAX_EXAMPLE_ROWS],  # ROW NUMBERS ONLY
        "downgraded_from_critical": downgraded,
    }


def screen_tabular(header: List[str], body: List[List[str]]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    n_rows = len(body)
    if n_rows == 0:
        return findings

    for col_idx, col in enumerate(header):
        values = [(row[col_idx] if col_idx < len(row) else "") for row in body]

        # ---- value-scan rules (apply to every column) ----
        for rule_id, checker in (
            ("krn_rrn", _hit_rrn),
            ("phone_kr", _hit_phone),
            ("email", _hit_email),
        ):
            match_rows = [i + 1 for i, v in enumerate(values) if v and checker(str(v))]
            if match_rows:
                findings.append(_build_finding(col, rule_id, match_rows, n_rows))

        # ---- person_name: column-name gated + value heuristic ----
        if _column_name_matches(col, NAME_COL_HINTS):
            nonempty = [(i, v) for i, v in enumerate(values) if str(v).strip()]
            name_rows = [i for i, v in nonempty if _is_korean_name(str(v))]
            if nonempty:
                kor_frac = len(name_rows) / len(nonempty)
                unique_ratio = len({str(v).strip() for _, v in nonempty}) / len(nonempty)
                if kor_frac >= 0.70 and unique_ratio >= 0.5:
                    match_rows = [i + 1 for i in name_rows]
                    findings.append(_build_finding(col, "person_name", match_rows, n_rows))

        # ---- exact_birthdate: column-name gated + full-date value ----
        if _column_name_matches(col, BIRTH_COL_HINTS):
            match_rows = [i + 1 for i, v in enumerate(values) if v and _hit_full_date(str(v))]
            if match_rows:
                findings.append(_build_finding(col, "exact_birthdate", match_rows, n_rows))

    return findings


# ---------------------------------------------------------------------------
# Free-text screening (non-tabular input)
# ---------------------------------------------------------------------------
def screen_text(text: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    lines = text.splitlines() or [text]
    n_lines = len(lines)

    def line_hits(checker) -> List[int]:
        return [i + 1 for i, ln in enumerate(lines) if checker(ln)]

    for rule_id, checker in (
        ("krn_rrn", _hit_rrn),
        ("phone_kr", _hit_phone),
        ("email", _hit_email),
    ):
        rows = line_hits(checker)
        if rows:
            findings.append(_build_finding(None, rule_id, rows, n_lines))
    return findings


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def _detect_kind(path: str) -> str:
    low = path.lower()
    if low.endswith((".csv", ".tsv")):
        return "csv"
    if low.endswith((".xlsx", ".xlsm")):
        return "xlsx"
    return "text"


def run_screen(data_path: str) -> Dict[str, Any]:
    kind = _detect_kind(data_path)
    fmt = "tabular"
    screened_columns: List[str] = []

    if kind == "csv":
        header, body = _load_csv(data_path)
        screened_columns = header
        findings = screen_tabular(header, body)
        n_rows = len(body)
    elif kind == "xlsx":
        header, body = _load_xlsx(data_path)
        screened_columns = header
        findings = screen_tabular(header, body)
        n_rows = len(body)
    else:
        fmt = "text"
        with open(data_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        findings = screen_text(text)
        n_rows = len(text.splitlines())

    has_critical = any(f["severity"] == "critical" for f in findings)
    has_warning = any(f["severity"] == "warning" for f in findings)
    max_severity = "critical" if has_critical else ("warning" if has_warning else "clean")

    return {
        "data_path": data_path,
        "format": fmt,
        "n_rows": n_rows,
        "screened_columns": screened_columns,
        "findings": findings,
        "finding_count": len(findings),
        "max_severity": max_severity,
        "recommendation": RECOMMENDATION if findings else RECOMMENDATION_CLEAN,
        "note": "매치된 값은 어디에도 기록되지 않습니다. 행 번호만 표기합니다.",
        "generated_at": datetime.now().isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Screen material for PHI / personal identifiers (offline)")
    parser.add_argument("--data-path", required=True, help="Path to CSV / XLSX / text file")
    parser.add_argument("--output", required=True, help="Output path for PHI report JSON")
    args = parser.parse_args()

    if not os.path.exists(args.data_path):
        print(f"ERROR: Data file not found: {args.data_path}", file=sys.stderr)
        sys.exit(1)

    try:
        report = run_screen(args.data_path)
    except Exception as exc:  # never let a value leak via a traceback of file contents
        print(f"ERROR: screening failed ({type(exc).__name__})", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"PHI screen: {args.data_path}")
    print(f"  Report: {args.output}")
    print(f"  Findings: {report['finding_count']}  Max severity: {report['max_severity']}")
    for fnd in report["findings"]:
        col = fnd["column"] if fnd["column"] is not None else "(text)"
        print(
            f"  [{fnd['severity'].upper()}] {fnd['rule_id']} in column '{col}': "
            f"{fnd['match_count']} match(es), rate={fnd['match_rate']}"
        )

    if report["max_severity"] == "critical":
        sys.exit(2)
    elif report["max_severity"] == "warning":
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
