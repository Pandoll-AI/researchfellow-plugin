"""synth_builder.py — watermarked fake-cohort generator. Contracts under test:
  1. deterministic — same spec, same bytes,
  2. triple watermark: in-band is_synthetic=1 on every row + meta sidecar,
  3. consent is part of the CLI contract (--consented-at required),
  4. samplers respect their parameters and outcome models see covariates.
"""

from __future__ import annotations

import csv
import json
import random

import synth_builder as sb


SPEC = {
    "n": 200,
    "variables": {
        "age": {"dist": "normal", "params": {"mu": 65, "sigma": 12, "min": 18, "max": 95}},
        "exposed": {"dist": "bernoulli", "params": {"p": 0.4}},
        "event": {"dist": "outcome_model",
                  "params": {"baseline_logit": -2.0, "coefficients": {"exposed": 1.5}}},
        "time": {"dist": "outcome_model_survival",
                 "params": {"baseline_hazard": 0.002, "coefficients": {"exposed": 0.4},
                            "follow_up_days": 730, "admin_censor": True,
                            "event_column": "event_obs"}},
    },
}


def test_deterministic_same_spec_same_rows():
    seed = sb.derive_seed(SPEC, 200)
    assert sb.build_cohort(SPEC, 200, seed) == sb.build_cohort(SPEC, 200, seed)
    assert seed == sb.derive_seed(json.loads(json.dumps(SPEC)), 200)  # canonical


def test_every_row_carries_the_inband_watermark(tmp_path):
    rows = sb.build_cohort(SPEC, 50, 7)
    assert all(r["is_synthetic"] == 1 for r in rows)
    out = tmp_path / "synthetic_cohort.csv"
    header = sb.write_csv(rows, str(out))
    assert header[-1] == "is_synthetic"
    with open(out, newline="", encoding="utf-8") as f:
        assert all(rec["is_synthetic"] == "1" for rec in csv.DictReader(f))


def test_meta_sidecar_declares_fake(tmp_path):
    meta_path = tmp_path / "synthetic_cohort.meta.json"
    sb.write_meta(str(meta_path), spec=SPEC, n=50, seed=7,
                  consented_at="2026-07-16T12:00:00Z", variables_hash=None,
                  columns=["age", "is_synthetic"])
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["watermark"] == "NOT REAL DATA"
    assert meta["consented_at"] == "2026-07-16T12:00:00Z"
    assert "가짜" in meta["consent_text"]


def test_samplers_respect_bounds_and_direction():
    rows = sb.build_cohort(SPEC, 2000, 42)
    ages = [r["age"] for r in rows]
    assert min(ages) >= 18 and max(ages) <= 95
    times = [r["time"] for r in rows]
    assert max(times) <= 730
    censored = [r for r in rows if r["event_obs"] == 0]
    assert censored and all(r["time"] == 730 for r in censored)
    # strong positive exposure coefficient -> higher event rate in the exposed
    exposed = [r for r in rows if r["exposed"] == 1]
    unexposed = [r for r in rows if r["exposed"] == 0]
    rate = lambda g: sum(r["event"] for r in g) / len(g)  # noqa: E731
    assert rate(exposed) > rate(unexposed)


def test_unknown_dist_is_an_error():
    bad = {"variables": {"x": {"dist": "zipf", "params": {}}}}
    try:
        sb.build_cohort(bad, 5, 1)
    except ValueError as exc:
        assert "zipf" in str(exc)
    else:
        raise AssertionError("unknown dist must raise")


def test_cli_requires_consent_and_writes_both_outputs(tmp_path, run_script):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(SPEC), encoding="utf-8")
    csv_path = tmp_path / "rehearsal" / "synthetic_cohort.csv"
    meta_path = tmp_path / "rehearsal" / "synthetic_cohort.meta.json"

    missing_consent = run_script("synth_builder.py",
                                 "--spec-path", str(spec_path),
                                 "--output-csv", str(csv_path),
                                 "--output-meta", str(meta_path))
    assert missing_consent.returncode == 2  # argparse error: --consented-at required

    ok = run_script("synth_builder.py",
                    "--spec-path", str(spec_path), "--n", "30",
                    "--output-csv", str(csv_path), "--output-meta", str(meta_path),
                    "--consented-at", "2026-07-16T12:00:00Z")
    assert ok.returncode == 0, ok.stderr
    assert csv_path.exists() and meta_path.exists()
    assert "가짜" in ok.stdout
