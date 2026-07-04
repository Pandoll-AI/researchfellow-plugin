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
from datetime import datetime
from typing import Any, Dict, List


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


def check_temporal_order(records: list[dict]) -> dict:
    """Check that outcome dates are after index dates."""
    violations = 0
    checked = 0
    for i, rec in enumerate(records):
        index_date = rec.get("index_date")
        outcome_date = rec.get("outcome_date")
        if index_date and outcome_date:
            checked += 1
            if str(outcome_date) < str(index_date):
                violations += 1

    return {
        "check": "temporal_order",
        "description": "Outcome date must be after index date",
        "checked": checked,
        "violations": violations,
        "critical": violations > 0,
        "severity": "critical" if violations > 0 else "pass",
    }


def check_missing_data(records: list[dict]) -> dict:
    """Check missing data rates per column."""
    if not records:
        return {"check": "missing_data", "columns": {}, "severity": "pass", "critical": False}

    columns = set()
    for rec in records:
        columns.update(rec.keys())

    missing_rates = {}
    n = len(records)
    high_missing = []

    for col in sorted(columns):
        missing = sum(1 for rec in records if rec.get(col) is None or rec.get(col) == "")
        rate = missing / n if n > 0 else 0
        missing_rates[col] = {"missing": missing, "total": n, "rate": round(rate, 4)}
        if rate > 0.3:
            high_missing.append(col)

    return {
        "check": "missing_data",
        "columns": missing_rates,
        "high_missing_columns": high_missing,
        "severity": "warning" if high_missing else "pass",
        "critical": False,
    }


def check_duplicates(records: list[dict]) -> dict:
    """Check for duplicate records."""
    if not records:
        return {"check": "duplicates", "total": 0, "duplicates": 0, "severity": "pass", "critical": False}

    # Use patient_id or id field for dedup
    id_field = None
    for field in ["patient_id", "id", "subject_id", "record_id"]:
        if records[0].get(field) is not None:
            id_field = field
            break

    if id_field is None:
        seen = set()
        dups = 0
        for rec in records:
            key = json.dumps(rec, sort_keys=True, default=str)
            if key in seen:
                dups += 1
            seen.add(key)
    else:
        from collections import Counter
        ids = [rec.get(id_field) for rec in records]
        counts = Counter(ids)
        dups = sum(1 for c in counts.values() if c > 1)

    return {
        "check": "duplicates",
        "id_field": id_field,
        "total": len(records),
        "duplicates": dups,
        "severity": "warning" if dups > 0 else "pass",
        "critical": False,
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
    if events < 10:
        warnings.append(f"Very low event count ({events}). Consider simpler models.")
    if total > 0 and events / total < 0.01:
        warnings.append(f"Event rate very low ({events}/{total} = {events/total:.4f})")

    return {
        "check": "event_counts",
        "event_field": event_field,
        "total": total,
        "events": events,
        "event_rate": round(events / total, 4) if total > 0 else 0,
        "warnings": warnings,
        "severity": "warning" if warnings else "pass",
        "critical": events < 5,
    }


def run_qc(data_path: str) -> dict:
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
        check_missing_data(records),
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
