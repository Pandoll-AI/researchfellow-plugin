"""QC critical rules — temporal parse, missing-data threshold, conflicting outcomes.

Regression fixtures for integrity hardening A-1 (#7). Tests import the module
directly (pure functions); CLI exit codes are not the contract here.
"""

from __future__ import annotations

import json

import pytest

from qc_checker import (
    MISSING_CRITICAL_RATE,
    check_duplicates,
    check_event_counts,
    check_missing_data,
    check_temporal_order,
    run_qc,
)


# ---------------------------------------------------------------------------
# A-1.1 temporal_order — real date parse, not string compare
# ---------------------------------------------------------------------------

def test_temporal_non_padded_reversal_is_critical():
    """Non-padded YYYY-M-D must not silently pass via lexicographic str compare.

    String compare: '2024-10-1' < '2024-2-1' is True (wrong direction), but
    '2024-2-1' < '2024-10-1' is also True — so a real reversal with non-padded
    months can be missed. Date parse must catch outcome-before-index.
    """
    records = [
        {"index_date": "2024-10-1", "outcome_date": "2024-2-1"},  # outcome before index
    ]
    result = check_temporal_order(records)
    assert result["critical"] is True
    assert result["severity"] == "critical"
    assert result["violations"] >= 1


def test_temporal_unparseable_date_is_critical_not_silent_pass():
    records = [
        {"index_date": "not-a-date", "outcome_date": "also-bad"},
    ]
    result = check_temporal_order(records)
    assert result["critical"] is True
    assert result["severity"] == "critical"
    assert result.get("parse_failures", 0) >= 1


def test_temporal_iso_order_ok_is_not_critical():
    records = [
        {"index_date": "2024-01-15", "outcome_date": "2024-06-01"},
    ]
    result = check_temporal_order(records)
    assert result["critical"] is False
    assert result["severity"] == "pass"
    assert result["violations"] == 0


def test_temporal_ambiguity_policy_is_documented():
    """Ambiguous MM/DD vs DD/MM must follow the declared deterministic policy."""
    # 01/02/2024 is Jan 2 (MDY) or Feb 1 (DMY). Policy is recorded on the check.
    records = [
        {"index_date": "01/02/2023", "outcome_date": "03/04/2024"},
    ]
    result = check_temporal_order(records)
    assert "date_ambiguity_policy" in result
    assert result["date_ambiguity_policy"]  # non-empty documented policy string


# ---------------------------------------------------------------------------
# A-1.2 missing_data — required fields >50% = critical
# ---------------------------------------------------------------------------

def test_missing_primary_field_over_50_percent_is_critical():
    assert MISSING_CRITICAL_RATE == 0.5  # guardrails.md
    # 6/10 missing exposure (60%) — convention column "exposed"
    records = [{"exposed": None if i < 6 else 1, "event": 1} for i in range(10)]
    result = check_missing_data(records)
    assert result["critical"] is True
    assert result["severity"] == "critical"
    assert any(c in result.get("critical_missing_columns", []) for c in ("exposed", "exposure", "event", "outcome"))


def test_missing_required_fields_arg_overrides_convention():
    records = [{"my_exp": None if i < 6 else 1, "my_out": 1} for i in range(10)]
    result = check_missing_data(records, required_fields=["my_exp", "my_out"])
    assert result["critical"] is True
    assert "my_exp" in result["critical_missing_columns"]


def test_missing_below_threshold_is_not_critical():
    # 4/10 missing = 40% — warning territory, not critical
    records = [{"exposed": None if i < 4 else 1, "event": 1} for i in range(10)]
    result = check_missing_data(records)
    assert result["critical"] is False


# ---------------------------------------------------------------------------
# A-1.3 duplicates — same id + conflicting outcome = critical
# ---------------------------------------------------------------------------

def test_duplicate_id_conflicting_outcome_is_critical():
    records = [
        {"patient_id": "P1", "event": 1},
        {"patient_id": "P1", "event": 0},  # conflict
        {"patient_id": "P2", "event": 1},
    ]
    result = check_duplicates(records)
    assert result["critical"] is True
    assert result["severity"] == "critical"
    assert result.get("conflicting_outcomes", 0) >= 1


def test_duplicate_id_same_outcome_is_not_critical():
    records = [
        {"patient_id": "P1", "event": 1},
        {"patient_id": "P1", "event": 1},  # dup but consistent
    ]
    result = check_duplicates(records)
    assert result["critical"] is False
    assert result.get("conflicting_outcomes", 0) == 0


# ---------------------------------------------------------------------------
# A-1.5 event_counts severity aligns with critical flag
# ---------------------------------------------------------------------------

def test_event_counts_critical_severity_matches_flag():
    records = [{"event": 1}] + [{"event": 0}] * 20  # 1 event < 5
    result = check_event_counts(records)
    assert result["critical"] is True
    assert result["severity"] == "critical"


# ---------------------------------------------------------------------------
# run_qc integration — has_critical aggregates check flags
# ---------------------------------------------------------------------------

def test_run_qc_has_critical_from_conflicting_outcomes(tmp_path):
    data = [
        {"patient_id": "P1", "event": 1, "index_date": "2024-01-01", "outcome_date": "2024-06-01"},
        {"patient_id": "P1", "event": 0, "index_date": "2024-01-01", "outcome_date": "2024-06-01"},
    ]
    path = tmp_path / "data.json"
    path.write_text(json.dumps(data))
    report = run_qc(str(path))
    assert report["has_critical"] is True
