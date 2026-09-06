"""Woolf (log-method) 95% CI for 2x2 aggregate effects."""

from __future__ import annotations

import pytest

import analysis_runner as ar


def test_woolf_ci_matches_statsmodels_table2x2():
    try:
        from statsmodels.stats.contingency_tables import Table2x2
    except ImportError:
        pytest.skip("statsmodels not installed")

    a, b, c, d = 20, 80, 10, 90
    result = ar.effect_from_counts({
        "exposed": a + b,
        "unexposed": c + d,
        "events_exposed": a,
        "events_unexposed": c,
    })
    table = Table2x2([[a, b], [c, d]])
    sm_or = table.oddsratio_confint()
    sm_rr = table.riskratio_confint()

    assert result["status"] == "aggregate_only"
    assert result["ci_available"] is True
    assert result["ci_method"] == "woolf_log"
    assert result["p_value_available"] is False
    assert "ci_p_available" not in result
    assert result["or_ci95"][0] == pytest.approx(float(sm_or[0]), abs=1e-3)
    assert result["or_ci95"][1] == pytest.approx(float(sm_or[1]), abs=1e-3)
    assert result["rr_ci95"][0] == pytest.approx(float(sm_rr[0]), abs=1e-3)
    assert result["rr_ci95"][1] == pytest.approx(float(sm_rr[1]), abs=1e-3)


@pytest.mark.parametrize(
    "counts",
    [
        {"exposed": 100, "unexposed": 100, "events_exposed": 0, "events_unexposed": 10},
        {"exposed": 20, "unexposed": 100, "events_exposed": 20, "events_unexposed": 10},
        {"exposed": 100, "unexposed": 100, "events_exposed": 20, "events_unexposed": 0},
        {"exposed": 100, "unexposed": 10, "events_exposed": 20, "events_unexposed": 10},
    ],
)
def test_zero_cell_ci_unavailable(counts):
    result = ar.effect_from_counts(counts)
    assert result["status"] == "aggregate_only"
    assert result["ci_available"] is False
    assert result["ci_reason"] == "zero_cell"
    assert result["or_ci95"] is None
    assert result["rr_ci95"] is None
    assert result["p_value_available"] is False
    assert "ci_p_available" not in result
