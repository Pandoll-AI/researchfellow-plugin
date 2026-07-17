#!/usr/bin/env python3
"""Data quality checker for the Research Assistant skill.

Validates extracted research data for common issues:
- Temporal violations (outcome before index date)
- Missing data rates
- Distribution anomalies
- Duplicate records
- Coding consistency

Usage:
    python3 qc_checker.py --data-path data.csv --output .research/qc-report.json
    python3 qc_checker.py --data-path data.json --output .research/qc-report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple


# guardrails.md — Missing primary exposure/outcome > 50% is a QC critical flag.
MISSING_CRITICAL_RATE = 0.5

# Convention column names when SAP/variables required-field list is not supplied.
DEFAULT_EXPOSURE_COLUMNS = ("exposure", "exposed", "treatment", "tx")
DEFAULT_OUTCOME_COLUMNS = ("outcome", "event", "outcome_event")

# Date parse candidates, tried in order for unambiguous formats.
# ISO / non-padded YYYY-M-D first (Python %m/%d accept non-zero-padded values).
_ISO_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d")

# Ambiguity policy for slash-separated numeric dates (MM/DD/YYYY vs DD/MM/YYYY):
# When both formats parse to *different* calendar dates, treat the value as
# unparseable → critical finding (no silent preference). When both formats yield
# the same date (e.g. 05/05/2024), accept it. Prefer explicit ISO in source data.
DATE_AMBIGUITY_POLICY = (
    "ambiguous_slash_dates_are_critical: if MM/DD/YYYY and DD/MM/YYYY both parse "
    "to different dates, treat as parse failure (blocking); identical results accepted"
)


def _load_data(data_path: str) -> tuple[list[dict], str]:
    """Load data from CSV or JSON."""
    if data_path.endswith(".json"):
        with open(data_path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            records = data.get("records", data.get("data", []))
        else:
            records = data
        return records, "json"

    try:
        import pandas as pd
        df = pd.read_csv(data_path)
        return df.to_dict("records"), "csv"
    except ImportError:
        print("ERROR: pandas required for CSV files", file=sys.stderr)
        sys.exit(1)


def _parse_date(value: Any) -> Tuple[Optional[date], Optional[str]]:
    """Parse a date value. Returns (date, error) where error is None on success.

    error values:
      - "unparseable": no supported format matched
      - "ambiguous": both MDY and DMY parse to different dates (see DATE_AMBIGUITY_POLICY)
    """
    if value is None or value == "":
        return None, None
    if isinstance(value, datetime):
        return value.date(), None
    if isinstance(value, date):
        return value, None

    text = str(value).strip()
    if not text:
        return None, None

    for fmt in _ISO_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date(), None
        except ValueError:
            pass

    mdy: Optional[date] = None
    dmy: Optional[date] = None
    try:
        mdy = datetime.strptime(text, "%m/%d/%Y").date()
    except ValueError:
        pass
    try:
        dmy = datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError:
        pass

    if mdy is not None and dmy is not None:
        if mdy == dmy:
            return mdy, None
        # Deterministic policy: do not guess — critical parse failure.
        return None, "ambiguous"
    if mdy is not None:
        return mdy, None
    if dmy is not None:
        return dmy, None

    return None, "unparseable"


def check_temporal_order(records: list[dict]) -> dict:
    """Check that outcome dates are after index dates (parsed, not string-compared)."""
    violations = 0
    checked = 0
    parse_failures = 0

    for rec in records:
        index_raw = rec.get("index_date")
        outcome_raw = rec.get("outcome_date")
        if not index_raw or not outcome_raw:
            continue

        index_date, index_err = _parse_date(index_raw)
        outcome_date, outcome_err = _parse_date(outcome_raw)

        if index_err or outcome_err or index_date is None or outcome_date is None:
            # Parse failure is blocking (critical) — never a quiet pass.
            parse_failures += 1
            continue

        checked += 1
        if outcome_date < index_date:
            violations += 1

    is_critical = violations > 0 or parse_failures > 0
    return {
        "check": "temporal_order",
        "description": "Outcome date must be after index date",
        "checked": checked,
        "violations": violations,
        "parse_failures": parse_failures,
        "date_ambiguity_policy": DATE_AMBIGUITY_POLICY,
        "critical": is_critical,
        "severity": "critical" if is_critical else "pass",
    }


def _resolve_required_fields(
    records: list[dict],
    required_fields: Optional[Sequence[str]] = None,
) -> List[str]:
    """Return the primary exposure/outcome columns to enforce missing-data criticals.

    Prefer an explicit SAP/variables list. Otherwise fall back to the first
    convention column name present in the data (or the canonical name if absent,
    so an all-missing renamed column can still surface when named conventionally).
    """
    if required_fields:
        return list(required_fields)

    present: set = set()
    for rec in records:
        present.update(rec.keys())

    resolved: List[str] = []
    for group in (DEFAULT_EXPOSURE_COLUMNS, DEFAULT_OUTCOME_COLUMNS):
        found = next((c for c in group if c in present), None)
        if found is not None:
            resolved.append(found)
        else:
            # No convention column present — skip group (cannot rate a missing col).
            pass
    return resolved


def check_missing_data(
    records: list[dict],
    required_fields: Optional[Sequence[str]] = None,
) -> dict:
    """Check missing data rates per column.

    Required primary fields (exposure/outcome) with missing rate
    strictly greater than MISSING_CRITICAL_RATE (50%, guardrails.md) are critical.
    Other columns above 30% remain warnings only.
    """
    if not records:
        return {
            "check": "missing_data",
            "columns": {},
            "severity": "pass",
            "critical": False,
            "critical_missing_columns": [],
            "required_fields": [],
            "missing_critical_rate": MISSING_CRITICAL_RATE,
        }

    columns: set = set()
    for rec in records:
        columns.update(rec.keys())

    missing_rates: Dict[str, dict] = {}
    n = len(records)
    high_missing: List[str] = []
    critical_missing: List[str] = []
    required = _resolve_required_fields(records, required_fields)
    required_set = set(required)

    for col in sorted(columns):
        missing = sum(1 for rec in records if rec.get(col) is None or rec.get(col) == "")
        rate = missing / n if n > 0 else 0
        missing_rates[col] = {"missing": missing, "total": n, "rate": round(rate, 4)}
        if rate > 0.3:
            high_missing.append(col)
        if col in required_set and rate > MISSING_CRITICAL_RATE:
            critical_missing.append(col)

    # Required fields listed but absent from every row still count as 100% missing.
    for col in required:
        if col not in missing_rates:
            missing_rates[col] = {"missing": n, "total": n, "rate": 1.0}
            critical_missing.append(col)
            high_missing.append(col)

    is_critical = len(critical_missing) > 0
    if is_critical:
        severity = "critical"
    elif high_missing:
        severity = "warning"
    else:
        severity = "pass"

    return {
        "check": "missing_data",
        "columns": missing_rates,
        "high_missing_columns": high_missing,
        "critical_missing_columns": critical_missing,
        "required_fields": required,
        "missing_critical_rate": MISSING_CRITICAL_RATE,
        "severity": severity,
        "critical": is_critical,
    }


def _find_id_field(records: list[dict]) -> Optional[str]:
    for field in ("patient_id", "id", "subject_id", "record_id"):
        if records[0].get(field) is not None:
            return field
    return None


def _find_outcome_field(records: list[dict]) -> Optional[str]:
    for field in DEFAULT_OUTCOME_COLUMNS:
        if field in records[0]:
            return field
    return None


def check_duplicates(records: list[dict]) -> dict:
    """Check for duplicate records and same-ID conflicting outcomes.

    Plain ID duplicates remain a warning. Identical id_field values with
    disagreeing outcome values are critical (guardrails.md).
    """
    if not records:
        return {
            "check": "duplicates",
            "total": 0,
            "duplicates": 0,
            "conflicting_outcomes": 0,
            "severity": "pass",
            "critical": False,
        }

    id_field = _find_id_field(records)
    outcome_field = _find_outcome_field(records)
    conflicting = 0

    if id_field is None:
        seen: set = set()
        dups = 0
        for rec in records:
            key = json.dumps(rec, sort_keys=True, default=str)
            if key in seen:
                dups += 1
            seen.add(key)
    else:
        ids = [rec.get(id_field) for rec in records]
        counts = Counter(ids)
        dups = sum(1 for c in counts.values() if c > 1)

        if outcome_field is not None:
            by_id: Dict[Any, set] = defaultdict(set)
            for rec in records:
                rid = rec.get(id_field)
                if rid is None:
                    continue
                by_id[rid].add(json.dumps(rec.get(outcome_field), default=str))
            conflicting = sum(1 for outcomes in by_id.values() if len(outcomes) > 1)

    is_critical = conflicting > 0
    if is_critical:
        severity = "critical"
    elif dups > 0:
        severity = "warning"
    else:
        severity = "pass"

    return {
        "check": "duplicates",
        "id_field": id_field,
        "outcome_field": outcome_field,
        "total": len(records),
        "duplicates": dups,
        "conflicting_outcomes": conflicting,
        "severity": severity,
        "critical": is_critical,
    }


def check_distributions(records: list[dict]) -> dict:
    """Basic distribution checks for numeric columns."""
    if not records:
        return {"check": "distributions", "columns": {}, "severity": "pass", "critical": False}

    numeric_stats = {}
    anomalies = []

    for col in records[0].keys():
        values = []
        for rec in records:
            v = rec.get(col)
            if v is not None and v != "":
                try:
                    values.append(float(v))
                except (ValueError, TypeError):
                    continue

        if len(values) < 10:
            continue

        values.sort()
        n = len(values)
        mean = sum(values) / n
        variance = sum((x - mean) ** 2 for x in values) / n
        std = variance ** 0.5
        min_val = values[0]
        max_val = values[-1]
        median = values[n // 2]

        numeric_stats[col] = {
            "n": n,
            "mean": round(mean, 4),
            "std": round(std, 4),
            "min": min_val,
            "median": median,
            "max": max_val,
        }

        # Flag extreme ranges
        if std > 0 and (max_val - min_val) > 10 * std:
            anomalies.append(f"{col}: extreme range ({min_val} to {max_val}, std={std:.2f})")

    return {
        "check": "distributions",
        "columns": numeric_stats,
        "anomalies": anomalies,
        "severity": "warning" if anomalies else "pass",
        "critical": False,
    }


def check_event_counts(records: list[dict]) -> dict:
    """Check if event counts are sufficient for analysis."""
    if not records:
        return {"check": "event_counts", "severity": "pass", "critical": False}

    event_field = None
    for field in ["event", "outcome", "outcome_event"]:
        if records[0].get(field) is not None:
            event_field = field
            break

    if event_field is None:
        return {"check": "event_counts", "severity": "info", "message": "No event column found", "critical": False}

    events = sum(1 for rec in records if rec.get(event_field) in (1, True, "1", "yes", "Yes"))
    total = len(records)

    warnings = []
    is_critical = events < 5
    if events < 10:
        warnings.append(f"Very low event count ({events}). Consider simpler models.")
    if total > 0 and events / total < 0.01:
        warnings.append(f"Event rate very low ({events}/{total} = {events/total:.4f})")

    if is_critical:
        severity = "critical"
    elif warnings:
        severity = "warning"
    else:
        severity = "pass"

    return {
        "check": "event_counts",
        "event_field": event_field,
        "total": total,
        "events": events,
        "event_rate": round(events / total, 4) if total > 0 else 0,
        "warnings": warnings,
        "severity": severity,
        "critical": is_critical,
    }


def run_qc(data_path: str, required_fields: Optional[Sequence[str]] = None) -> dict:
    """Run all QC checks and return report."""
    records, fmt = _load_data(data_path)

    if not records:
        return {
            "data_path": data_path,
            "format": fmt,
            "record_count": 0,
            "checks": [],
            "has_critical": True,
            "summary": "No records found",
            "generated_at": datetime.now().isoformat(),
        }

    checks = [
        check_temporal_order(records),
        check_missing_data(records, required_fields=required_fields),
        check_duplicates(records),
        check_distributions(records),
        check_event_counts(records),
    ]

    has_critical = any(c.get("critical") for c in checks)
    severity_counts = {}
    for c in checks:
        sev = c.get("severity", "pass")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    return {
        "data_path": data_path,
        "format": fmt,
        "record_count": len(records),
        "checks": checks,
        "has_critical": has_critical,
        "severity_summary": severity_counts,
        "generated_at": datetime.now().isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description="Run data quality checks")
    parser.add_argument("--data-path", required=True, help="Path to data file (CSV or JSON)")
    parser.add_argument("--output", required=True, help="Output path for QC report JSON")
    args = parser.parse_args()

    if not os.path.exists(args.data_path):
        print(f"ERROR: Data file not found: {args.data_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Running QC checks on {args.data_path}...")
    report = run_qc(args.data_path)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nQC Report: {args.output}")
    print(f"  Records: {report['record_count']}")
    print(f"  Checks: {len(report['checks'])}")
    for check in report["checks"]:
        icon = "CRITICAL" if check.get("critical") else check.get("severity", "pass").upper()
        print(f"  [{icon}] {check['check']}")

    if report["has_critical"]:
        print("\n  *** CRITICAL issues found. Analysis blocked until resolved. ***")
        sys.exit(1)
    else:
        print("\n  All checks passed. Ready for analysis.")


if __name__ == "__main__":
    main()
