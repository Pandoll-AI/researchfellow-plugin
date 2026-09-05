#!/usr/bin/env python3
"""Data-access contract for the ResearchFellow skill.

LLM hosts must not Read raw extracts. This script is the only allowed view:
schema labels, dtypes (full-table scan), and aggregate tables. Individual
records never leave this process.

Usage:
    python3 query_guard.py --data <path> --op {schema|freq|agg} [--by COL ...]

stdout JSON keys (fixed): columns, dtypes, result, suppressed,
suppression_note, warnings.

freq/agg: any cell with n=1 is dropped on its own (the group key and any
min/max/mean of that singleton never leave). Numeric min/max/mean are
emitted only when ≥2 finite measurements support them. Nonfinite tokens
are omitted with a warning and never echoed. PII groups are pooled at the
masked key before aggregation. suppression_note records how many cells
were withheld — never the original keys. If every cell is dropped,
`result` is omitted and suppressed=true.

schema emits column labels and dtypes only, never cell values, so it is
not a suppression target — including 1-row files. The note then reads
"schema는 값 미포함이라 억제 대상 아님" and n_rows is omitted.

ABSOLUTE RULE — a matched PII value or any fragment of it is NEVER written to
stdout, stderr, or an exception message (same principle as phi_screener.py).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import statistics
import sys
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phi_detect  # noqa: E402

CONTRACT_KEYS = ("columns", "dtypes", "result", "suppressed", "suppression_note", "warnings")
SUPPORTED_OPS = ("schema", "freq", "agg")
SUPPORTED_EXTS = {".csv", ".tsv"}
REJECTED_EXTS = {
    ".xlsx", ".xlsm", ".xls", ".sas7bdat", ".parquet", ".dta", ".sav", ".json",
}
SUPPRESSION_NOTE = "1건 결과는 공개하지 않음"
SCHEMA_NOT_SUPPRESSED_NOTE = "schema는 값 미포함이라 억제 대상 아님"
SMALL_N_THRESHOLD = 30
SMALL_N_WARNING = "셀 도수 n≤30 — 통계적 유효성 주의 (소표본)"
NONFINITE_WARNING = "비유한·오버플로 수치는 집계에서 제외함 (원값 미표시)"
MASK_FMT = "***MASKED({kind})***"

DTYPE_INTEGER = "integer"
DTYPE_FLOAT = "float"
DTYPE_STRING = "string"
DTYPE_CATEGORICAL = "categorical"
DTYPE_DATE = "date"
DTYPE_BINARY = "binary"

BINARY_SETS = (
    frozenset({"0", "1"}),
    frozenset({"true", "false"}),
    frozenset({"yes", "no"}),
    frozenset({"y", "n"}),
    frozenset({"t", "f"}),
)

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d")

# Column-name heuristics (Korean + English). Matched against a compacted form
# so PT_NM / patient_name / 환자명 all land on the same kind.
_PII_NAME_COMPACT = frozenset({
    "name", "patientname", "ptname", "ptnm", "patnm", "patname",
    "fullname", "firstname", "lastname", "surname", "givenname",
    "이름", "성명", "성함", "환자명",
})
_PII_NAME_KR = ("이름", "성명", "성함", "환자명")
_PII_PHONE_COMPACT = frozenset({
    "phone", "tel", "mobile", "contact", "hp", "cellphone",
    "연락처", "전화", "휴대폰", "핸드폰",
})
_PII_PHONE_KR = ("연락처", "전화", "휴대폰", "핸드폰")
_PII_RRN_COMPACT = frozenset({
    "rrn", "ssn", "nationalid", "residentid",
    "주민번호", "주민등록번호",
})
_PII_RRN_KR = ("주민번호", "주민등록번호")
_PII_EMAIL_COMPACT = frozenset({"email", "e-mail", "mail", "이메일"})
_PII_EMAIL_KR = ("이메일",)
_PII_MRN_COMPACT = frozenset({
    "mrn", "patientid", "subjectid", "patid", "ptid", "chartno", "chartnumber",
    "등록번호", "병록번호", "환자번호", "차트번호",
})
_PII_MRN_KR = ("등록번호", "병록번호", "환자번호", "차트번호")
_PII_ADDR_COMPACT = frozenset({"address", "addr", "주소", "거주지"})
_PII_ADDR_KR = ("주소", "거주지")
_PII_DOB_COMPACT = frozenset({
    "dob", "birth", "birthdate", "birthday", "birthdt",
    "생년월일", "생일",
})
_PII_DOB_KR = ("생년월일", "생일")

_RULE_TO_KIND = {
    "krn_rrn": "rrn",
    "phone_kr": "phone",
    "email": "email",
    "person_name": "name",
    "exact_birthdate": "dob",
}
_KIND_PRIORITY = {
    "rrn": 0, "phone": 1, "email": 2, "name": 3, "dob": 4, "mrn": 5, "address": 6, "pii": 9,
}


class GuardError(Exception):
    """Fail-closed error. `message` must never contain cell values."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------------
# Cell typing (no value is ever returned to the caller as an error payload)
# ---------------------------------------------------------------------------
def _is_int_token(value: str) -> bool:
    s = value.strip()
    if not re.fullmatch(r"[+-]?\d+", s):
        return False
    if re.fullmatch(r"[+-]?0\d+", s):
        return False  # leading-zero codes are not integers
    return True


def _is_float_token(value: str) -> bool:
    s = value.strip()
    if not s or _is_int_token(s):
        return False
    try:
        float(s)
    except ValueError:
        return False
    return True


def _is_date_token(value: str) -> bool:
    s = value.strip()
    if not s:
        return False
    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(s, fmt)
        except ValueError:
            continue
        if fmt == "%Y%m%d" and len(s) != 8:
            continue
        return True
    return False


def _looks_like_typed_data(value: str) -> bool:
    """True when a cell looks like a data value, not a column label."""
    v = value.strip()
    if not v:
        return False
    if _is_int_token(v) or _is_float_token(v) or _is_date_token(v):
        return True
    if phi_detect.PHONE_RE.search(v) or phi_detect.EMAIL_RE.search(v):
        return True
    if phi_detect.RRN_RE.search(v):
        return True
    if v.lower() in {"true", "false", "yes", "no"}:
        return True
    return False


def _value_fits_dtype(value: str, dtype: str) -> bool:
    v = value.strip()
    if dtype == DTYPE_INTEGER:
        return _is_int_token(v)
    if dtype == DTYPE_FLOAT:
        return _is_int_token(v) or _is_float_token(v)
    if dtype == DTYPE_DATE:
        return _is_date_token(v)
    if dtype == DTYPE_BINARY:
        return v.lower() in {x for pair in BINARY_SETS for x in pair}
    # string / categorical: any token "fits"
    return True


def infer_dtype(values: Sequence[str]) -> str:
    """Estimate a column dtype from every non-empty cell (full scan)."""
    nonempty = [str(v).strip() for v in values if str(v).strip() != ""]
    if not nonempty:
        return DTYPE_STRING

    unique_low = {v.lower() for v in nonempty}
    if unique_low in BINARY_SETS:
        return DTYPE_BINARY

    if all(_is_date_token(v) for v in nonempty):
        return DTYPE_DATE

    all_numeric = True
    has_float = False
    for v in nonempty:
        if _is_int_token(v):
            continue
        if _is_float_token(v):
            has_float = True
            continue
        all_numeric = False
        break
    if all_numeric:
        return DTYPE_FLOAT if has_float else DTYPE_INTEGER

    n_unique = len(set(nonempty))
    n = len(nonempty)
    if n_unique <= 20 or (n >= 10 and n_unique / n <= 0.10):
        return DTYPE_CATEGORICAL
    return DTYPE_STRING


# ---------------------------------------------------------------------------
# PII column classification — names first, then phi_detect value scan
# ---------------------------------------------------------------------------
def _compact_col(name: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]", "", name.strip().lower())


def _kind_from_column_name(col: str) -> Optional[str]:
    raw = col.strip()
    compact = _compact_col(raw)
    if any(k in raw for k in _PII_NAME_KR) or compact in _PII_NAME_COMPACT:
        return "name"
    if compact.endswith("name") and compact not in {"filename", "varname", "colname", "treatmentname"}:
        return "name"
    if any(k in raw for k in _PII_PHONE_KR) or compact in _PII_PHONE_COMPACT:
        return "phone"
    if any(k in raw for k in _PII_RRN_KR) or compact in _PII_RRN_COMPACT:
        return "rrn"
    if any(k in raw for k in _PII_EMAIL_KR) or compact in _PII_EMAIL_COMPACT:
        return "email"
    if any(k in raw for k in _PII_MRN_KR) or compact in _PII_MRN_COMPACT:
        return "mrn"
    if any(k in raw for k in _PII_ADDR_KR) or compact in _PII_ADDR_COMPACT:
        return "address"
    if any(k in raw for k in _PII_DOB_KR) or compact in _PII_DOB_COMPACT:
        return "dob"
    return None


def _prefer_kind(current: Optional[str], incoming: str) -> str:
    if current is None:
        return incoming
    return incoming if _KIND_PRIORITY.get(incoming, 9) < _KIND_PRIORITY.get(current, 9) else current


def classify_pii_columns(header: Sequence[str], body: Sequence[Sequence[str]]) -> Dict[str, str]:
    """Map column name -> mask kind. Never stores a cell value."""
    kinds: Dict[str, str] = {}
    for col in header:
        kind = _kind_from_column_name(col)
        if kind:
            kinds[col] = kind

    findings = phi_detect.detect_tabular(header, body)
    for finding in findings:
        col = finding.get("column")
        rule_id = finding.get("rule_id")
        if not col or not rule_id:
            continue
        mapped = _RULE_TO_KIND.get(str(rule_id), "pii")
        kinds[str(col)] = _prefer_kind(kinds.get(str(col)), mapped)
    return kinds


def mask_label(kind: str) -> str:
    return MASK_FMT.format(kind=kind)


# ---------------------------------------------------------------------------
# Load + header validation
# ---------------------------------------------------------------------------
def _read_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise GuardError("io_error", "data file not readable") from exc


def _decode_text(data: bytes) -> str:
    if b"\x00" in data:
        raise GuardError("parse_failure", "binary or NUL content is not a valid table")
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise GuardError("parse_failure", "text decoding failed")


def _assert_supported_format(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in SUPPORTED_EXTS:
        return ext
    if ext in REJECTED_EXTS or ext:
        shown = ext or "unknown"
        raise GuardError(
            "unsupported_format",
            f"unsupported format: {shown} (csv/tsv only)",
        )
    raise GuardError("unsupported_format", "unsupported format: unknown (csv/tsv only)")


def load_rows(path: str) -> List[List[str]]:
    ext = _assert_supported_format(path)
    text = _decode_text(_read_bytes(path))
    delimiter = "\t" if ext == ".tsv" else ","
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
        rows = [[str(cell) for cell in row] for row in reader]
    except csv.Error as exc:
        raise GuardError("parse_failure", "csv parse failed") from exc
    if not rows:
        raise GuardError("parse_failure", "empty table")
    return rows


def assert_header_row(header: Sequence[str], body: Sequence[Sequence[str]]) -> None:
    """Fail-closed when the first row looks like data rather than labels.

    Checks (1) that sampled header cells are not typed data values, and
    (2) that header cells do not all fit the dtypes inferred from the body
    unless those dtypes are all string-like.
    """
    if not header or all(not str(c).strip() for c in header):
        raise GuardError("header_mismatch", "first row looks like data, not a header")

    typed = sum(1 for cell in header if _looks_like_typed_data(str(cell)))
    n = len(header)
    if typed >= 1 and typed * 2 >= n:
        raise GuardError("header_mismatch", "first row looks like data, not a header")

    if not body:
        return

    width = len(header)
    body_types = [
        infer_dtype([(row[i] if i < len(row) else "") for row in body])
        for i in range(width)
    ]
    compatible = 0
    for i, cell in enumerate(header):
        if _value_fits_dtype(str(cell), body_types[i]):
            compatible += 1
    non_string = any(t not in {DTYPE_STRING, DTYPE_CATEGORICAL} for t in body_types)
    if compatible == width and non_string:
        raise GuardError("header_mismatch", "first row looks like data, not a header")


def split_header_body(rows: Sequence[Sequence[str]]) -> Tuple[List[str], List[List[str]]]:
    header = [str(c) for c in rows[0]]
    width = len(header)
    if width == 0:
        raise GuardError("parse_failure", "empty header")
    if len(set(header)) != width:
        raise GuardError("parse_failure", "duplicate column labels")

    body: List[List[str]] = []
    for row in rows[1:]:
        if len(row) > width:
            raise GuardError("parse_failure", "column count mismatch")
        padded = [str(c) for c in row] + [""] * (width - len(row))
        body.append(padded[:width])
    assert_header_row(header, body)
    return header, body


def records_from(header: Sequence[str], body: Sequence[Sequence[str]]) -> List[Dict[str, str]]:
    return [{header[i]: row[i] for i in range(len(header))} for row in body]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _group_key(record: Dict[str, str], by_cols: Sequence[str]) -> Tuple[str, ...]:
    return tuple(record.get(col, "") for col in by_cols)


def _masked_by_map(
    key: Tuple[str, ...],
    by_cols: Sequence[str],
    pii_kinds: Dict[str, str],
) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for col, raw in zip(by_cols, key):
        if col in pii_kinds:
            out[col] = mask_label(pii_kinds[col])
        else:
            out[col] = raw
    return out


def _group_records_masked(
    records: Sequence[Dict[str, str]],
    by_cols: Sequence[str],
    pii_kinds: Dict[str, str],
) -> Tuple[List[Tuple[str, ...]], Dict[Tuple[str, ...], List[Dict[str, str]]], Dict[Tuple[str, ...], Dict[str, str]]]:
    """Group rows by the already-masked key, preserving first-seen order."""
    groups: Dict[Tuple[str, ...], List[Dict[str, str]]] = {}
    order: List[Tuple[str, ...]] = []
    by_maps: Dict[Tuple[str, ...], Dict[str, str]] = {}
    for rec in records:
        by_map = _masked_by_map(_group_key(rec, by_cols), by_cols, pii_kinds)
        ident = tuple(by_map.get(col, "") for col in by_cols)
        if ident not in groups:
            groups[ident] = []
            order.append(ident)
            by_maps[ident] = by_map
        groups[ident].append(rec)
    return order, groups, by_maps


def _stat_sample_n(stats: Dict[str, Any]) -> Optional[int]:
    raw = stats.get("_n")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _collapse_cells(cells: List[Dict[str, Any]], by_cols: Sequence[str]) -> List[Dict[str, Any]]:
    """Merge cells whose (already masked) by-maps are identical.

    Means are weighted by each column's finite sample count (`_n`), never by
    group size. Production agg groups at the masked key first; this path is
    a defensive merge and for direct callers.
    """
    merged: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    order: List[Tuple[str, ...]] = []
    for cell in cells:
        by_map = cell["by"]
        ident = tuple(by_map.get(col, "") for col in by_cols)
        if ident not in merged:
            merged[ident] = {
                "by": dict(by_map),
                "n": 0,
            }
            if "values" in cell:
                merged[ident]["values"] = {}
            order.append(ident)
        bucket = merged[ident]
        n_add = int(cell["n"])
        prev_n = int(bucket["n"])
        if "values" in cell:
            bucket.setdefault("values", {})
            for col, stats in cell["values"].items():
                existing = bucket["values"].get(col)
                if existing is None:
                    bucket["values"][col] = dict(stats)
                else:
                    left_n = _stat_sample_n(existing)
                    right_n = _stat_sample_n(stats)
                    if (
                        left_n is not None
                        and right_n is not None
                        and "mean" in existing
                        and "mean" in stats
                    ):
                        total = left_n + right_n
                        existing["mean"] = round(
                            (existing["mean"] * left_n + stats["mean"] * right_n) / total, 6
                        )
                        existing["_n"] = total
                    else:
                        # Group size is not a sample count. Do not emit a lie.
                        existing.pop("mean", None)
                        existing.pop("_n", None)
                    if "min" in stats and "min" in existing:
                        existing["min"] = min(existing["min"], stats["min"])
                    elif "min" in stats:
                        existing["min"] = stats["min"]
                    if "max" in stats and "max" in existing:
                        existing["max"] = max(existing["max"], stats["max"])
                    elif "max" in stats:
                        existing["max"] = stats["max"]
        bucket["n"] = prev_n + n_add
    return [merged[k] for k in order]


def _parse_finite_numbers(values: Iterable[str]) -> Tuple[Optional[List[float]], int]:
    """Parse numeric tokens into finite floats.

    Empty → missing (skip). Nonfinite / overflow → skip and count.
    Any other non-numeric token fails the column (None).
    """
    finite: List[float] = []
    n_nonfinite = 0
    for raw in values:
        s = str(raw).strip()
        if not s:
            continue
        try:
            num = float(s)
        except ValueError:
            return None, n_nonfinite
        except OverflowError:
            n_nonfinite += 1
            continue
        if not math.isfinite(num):
            n_nonfinite += 1
            continue
        finite.append(num)
    return finite, n_nonfinite


def _stats_from_nums(nums: Sequence[float], dtype: str) -> Dict[str, Any]:
    return {
        "mean": round(statistics.mean(nums), 6),
        "min": min(nums) if dtype == DTYPE_FLOAT else int(min(nums)),
        "max": max(nums) if dtype == DTYPE_FLOAT else int(max(nums)),
        "_n": len(nums),
    }


def _strip_private_stat_keys(cells: Sequence[Dict[str, Any]]) -> None:
    for cell in cells:
        values = cell.get("values")
        if not isinstance(values, dict):
            continue
        for stats in values.values():
            if isinstance(stats, dict):
                stats.pop("_n", None)


def _note_nonfinite(warnings: Optional[List[str]], n_nonfinite: int) -> None:
    if n_nonfinite and warnings is not None and NONFINITE_WARNING not in warnings:
        warnings.append(NONFINITE_WARNING)


def _numeric_stats(values: Iterable[str], dtype: str) -> Optional[Dict[str, Any]]:
    if dtype not in {DTYPE_INTEGER, DTYPE_FLOAT}:
        return None
    nums, _n_nonfinite = _parse_finite_numbers(values)
    if nums is None or len(nums) < 2:
        return None
    stats = _stats_from_nums(nums, dtype)
    stats.pop("_n", None)
    return stats


def _collect_small_n(cells: Sequence[Dict[str, Any]]) -> bool:
    return any(int(cell.get("n", 0)) <= SMALL_N_THRESHOLD for cell in cells)


def build_schema_result(n_rows: int, n_columns: int) -> Dict[str, Any]:
    # Labels/dtypes live on the payload; n_rows on a 1-row file would
    # advertise a singleton extract, so it is withheld.
    result: Dict[str, Any] = {"n_columns": n_columns}
    if n_rows != 1:
        result["n_rows"] = n_rows
    return result


def build_freq_result(
    records: Sequence[Dict[str, str]],
    by_cols: Sequence[str],
    pii_kinds: Dict[str, str],
) -> Dict[str, Any]:
    order, groups, by_maps = _group_records_masked(records, by_cols, pii_kinds)
    cells = [
        {"by": by_maps[key], "n": len(groups[key])}
        for key in order
    ]
    return {"by": list(by_cols), "n_rows": len(records), "cells": cells}


def build_agg_result(
    records: Sequence[Dict[str, str]],
    by_cols: Sequence[str],
    dtypes: Dict[str, str],
    pii_kinds: Dict[str, str],
    warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    order, groups, by_maps = _group_records_masked(records, by_cols, pii_kinds)

    numeric_cols = [
        col for col, dt in dtypes.items()
        if dt in {DTYPE_INTEGER, DTYPE_FLOAT} and col not in pii_kinds and col not in by_cols
    ]
    cells: List[Dict[str, Any]] = []
    for key in order:
        rows = groups[key]
        n = len(rows)
        cell: Dict[str, Any] = {
            "by": by_maps[key],
            "n": n,
        }
        # n=1 cells are dropped later. Per-column min/max/mean need ≥2 finite
        # measurements even when the group itself is larger.
        if n > 1:
            values: Dict[str, Any] = {}
            for col in numeric_cols:
                nums, n_nonfinite = _parse_finite_numbers(r.get(col, "") for r in rows)
                _note_nonfinite(warnings, n_nonfinite)
                if nums is None or len(nums) < 2:
                    continue
                values[col] = _stats_from_nums(nums, dtypes[col])
            if values:
                cell["values"] = values
        cells.append(cell)
    cells = _collapse_cells(cells, by_cols)
    _strip_private_stat_keys(cells)
    return {"by": list(by_cols), "n_rows": len(records), "cells": cells}


def _drop_singleton_cells(
    cells: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    """Remove n=1 cells. Returns (kept, dropped_count) — no group keys."""
    kept: List[Dict[str, Any]] = []
    dropped = 0
    for cell in cells:
        if int(cell.get("n", 0)) > 1:
            kept.append(cell)
        else:
            dropped += 1
    return kept, dropped


def _cell_suppression_note(dropped: int) -> str:
    return f"{SUPPRESSION_NOTE} (셀 {dropped}개 억제)"


def _resolve_by(header: Sequence[str], by_cols: Optional[Sequence[str]], op: str) -> List[str]:
    requested = list(by_cols or [])
    if op == "schema":
        return []
    if not requested:
        if op == "freq":
            raise GuardError("invalid_argument", "--by is required for freq")
        return []  # overall agg
    unknown = [c for c in requested if c not in header]
    if unknown:
        raise GuardError("invalid_argument", "unknown grouping column")
    return requested


# ---------------------------------------------------------------------------
# Public driver
# ---------------------------------------------------------------------------
def run_query(path: str, op: str, by_cols: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    if op not in SUPPORTED_OPS:
        raise GuardError("invalid_argument", "unsupported op")
    header, body = split_header_body(load_rows(path))
    dtypes = {
        header[i]: infer_dtype([row[i] for row in body])
        for i in range(len(header))
    }
    pii_kinds = classify_pii_columns(header, body)
    records = records_from(header, body)
    resolved_by = _resolve_by(header, by_cols, op)

    warnings: List[str] = []
    if op == "schema":
        result: Dict[str, Any] = build_schema_result(len(records), len(header))
    elif op == "freq":
        result = build_freq_result(records, resolved_by, pii_kinds)
    else:
        result = build_agg_result(records, resolved_by, dtypes, pii_kinds, warnings)

    dropped = 0
    if op in ("freq", "agg") and isinstance(result.get("cells"), list):
        kept, dropped = _drop_singleton_cells(result["cells"])
        result["cells"] = kept
        if _collect_small_n(kept) or (dropped and not kept):
            warnings.append(SMALL_N_WARNING)

    payload: Dict[str, Any] = {
        "columns": list(header),
        "dtypes": dtypes,
        "result": result,
        "suppressed": False,
        "suppression_note": "",
        "warnings": warnings,
    }
    if op == "schema":
        # schema는 값 미포함이라 억제 대상 아님
        if len(records) == 1:
            payload["suppression_note"] = SCHEMA_NOT_SUPPRESSED_NOTE
        return payload

    if dropped:
        payload["suppression_note"] = _cell_suppression_note(dropped)
    if op in ("freq", "agg") and not result.get("cells"):
        payload.pop("result")
        payload["suppressed"] = True
        if not payload["suppression_note"]:
            payload["suppression_note"] = SUPPRESSION_NOTE
    return payload


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))


def _emit_error(code: str, message: str) -> None:
    _emit({
        "error": code,
        "message": message,
        "columns": [],
        "dtypes": {},
        "suppressed": True,
        "suppression_note": "",
        "warnings": [],
    })


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Schema / frequency / aggregate view of a tabular extract (no raw rows)"
    )
    parser.add_argument("--data", required=True, help="Path to csv/tsv extract")
    parser.add_argument("--op", required=True, choices=SUPPORTED_OPS, help="schema | freq | agg")
    parser.add_argument(
        "--by", nargs="+", default=[], metavar="COL",
        help="Grouping columns for freq/agg",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not os.path.isfile(args.data):
            raise GuardError("io_error", "data file not found")
        payload = run_query(args.data, args.op, args.by)
        _emit(payload)
        return 0
    except GuardError as exc:
        _emit_error(exc.code, exc.message)
        return 1
    except Exception:
        # Never stringify the exception — it might contain a cell value.
        _emit_error("internal_error", "query failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
