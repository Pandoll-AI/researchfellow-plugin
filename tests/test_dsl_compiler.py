"""dsl_compiler.py — cohort DSL parsing + the (honestly heuristic) bias guards.

Note: the immortal-time / temporal checks are keyword HEURISTICS, not real bias
detection. These tests pin the parser contract and confirm the heuristics fire on
their trigger phrases — they do not claim the heuristics catch real bias.
"""

from __future__ import annotations

import pytest

import dsl_compiler as dsl


def test_parse_requires_index_and_followup():
    with pytest.raises(dsl.DSLValidationError):
        dsl.parse_cohort_dsl("INCLUDE: age >= 18")


def test_invalid_clause_format_raises():
    with pytest.raises(dsl.DSLValidationError):
        dsl.parse_cohort_dsl("age >= 18\nINDEX: t0\nFOLLOWUP: t1")


def test_duplicate_index_raises():
    with pytest.raises(dsl.DSLValidationError):
        dsl.parse_cohort_dsl("INDEX: a\nINDEX: b\nFOLLOWUP: c")


def test_immortal_time_heuristic_warns_not_fatal():
    # Downgraded from fatal validation to an honest, non-blocking heuristic.
    dsl_text = "INCLUDE: survival >= 90 days\nINDEX: first prescription\nFOLLOWUP: outcome_or_censor"
    warnings = dsl.validate_spec(dsl.parse_cohort_dsl(dsl_text))
    assert any("immortal time" in w.lower() for w in warnings)
    assert any("heuristic" in w.lower() for w in warnings)


def test_temporal_violation_heuristic_warns_not_fatal():
    dsl_text = "INCLUDE: outcome before index\nINDEX: t0\nFOLLOWUP: t1"
    warnings = dsl.validate_spec(dsl.parse_cohort_dsl(dsl_text))
    assert any("heuristic" in w.lower() for w in warnings)


def test_valid_dsl_compiles_to_sql():
    sql, digest, warnings = dsl.compile_dsl("INDEX: cohort_start\nFOLLOWUP: outcome_or_censor")
    assert isinstance(sql, str) and sql.strip()
    assert digest  # provenance hash present
