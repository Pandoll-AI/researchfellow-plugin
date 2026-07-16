#!/usr/bin/env python3
"""PHI / personal-identifier screener CLI for the ResearchFellow skill (FR-T6, FR-M10).

Thin CLI wrapper: file loading (CSV / XLSX / plain text) and the exit-code
contract live here; ALL detection logic lives in phi_detect.py (the engine,
backend-swappable via RF_PHI_BACKEND). Standard library only, fully offline.

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

# Hard import, deliberately no try/except (contrast: material_scanner's soft
# import). Detection is this script's sole purpose — if the engine is missing,
# crashing immediately is the correct fail-closed behavior.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phi_detect  # noqa: E402

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
# Loaders
#
# XML parsing uses stdlib ElementTree by design: FR-T7 pins this script to the
# standard library (no defusedxml), inputs are the user's own local files (no
# untrusted remote XML), ET does not resolve external entities, and modern
# expat (>=2.4) caps entity amplification. Worst case is a local parse failure,
# which the callers already treat as a screening error.
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
# Driver
# ---------------------------------------------------------------------------
def _detect_kind(path: str) -> str:
    low = path.lower()
    if low.endswith((".csv", ".tsv")):
        return "csv"
    if low.endswith((".xlsx", ".xlsm")):
        return "xlsx"
    return "text"


def run_screen(data_path: str, *, backend: Optional["phi_detect.PHIBackend"] = None) -> Dict[str, Any]:
    kind = _detect_kind(data_path)
    fmt = "tabular"
    screened_columns: List[str] = []

    if kind == "csv":
        header, body = _load_csv(data_path)
        screened_columns = header
        findings = phi_detect.detect_tabular(header, body, backend=backend)
        n_rows = len(body)
    elif kind == "xlsx":
        header, body = _load_xlsx(data_path)
        screened_columns = header
        findings = phi_detect.detect_tabular(header, body, backend=backend)
        n_rows = len(body)
    else:
        fmt = "text"
        with open(data_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        findings = phi_detect.detect_text(text, backend=backend)
        n_rows = len(text.splitlines())

    return {
        "data_path": data_path,
        "format": fmt,
        "n_rows": n_rows,
        "screened_columns": screened_columns,
        "findings": findings,
        "finding_count": len(findings),
        "max_severity": phi_detect.max_severity(findings),
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
