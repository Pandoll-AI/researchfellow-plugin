#!/usr/bin/env python3
"""Synthetic cohort builder — rehearsal mode's data source (D8).

Generates a row-level FAKE dataset from a distribution spec the host LLM
authors (templates/synth-spec-template.json) so a user without data can walk
steps 9-13 end to end. Standard library only, fully offline, deterministic.

CONSENT IS PART OF THE CONTRACT: `--consented-at` is required — the LLM passes
the timestamp of the user's explicit acknowledgement ("이 데이터는 완전한
가짜입니다. 파이프라인 연습·검증용이며 논문 결과로 사용할 수 없습니다").
Generation without that acknowledgement must not happen.

Triple watermark (a copied or renamed file stays recognizably fake):
  1. in-band column `is_synthetic=1` on every row  — travels with the data;
     analysis_runner --mode real refuses any input carrying it,
  2. meta sidecar (<output>.meta.json) with "NOT REAL DATA", seed, spec, consent,
  3. path convention: outputs live under research/rehearsal/ (or a filename
     containing 'synthetic').

Determinism: seed = sha256(canonical-spec | n) unless --seed is given — same
spec, same rows, byte for byte (hashlib, NOT hash(): PYTHONHASHSEED randomizes
the latter across processes).

Usage:
    python3 synth_builder.py --spec-path research/rehearsal/synth-spec.json --n 500 \
        --output-csv research/rehearsal/synthetic_cohort.csv \
        --output-meta research/rehearsal/synthetic_cohort.meta.json \
        --consented-at 2026-07-16T12:00:00Z [--variables-path research/04_variables/variables.json] [--seed N]

Exit codes: 0 ok / 1 input error.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

CONSENT_TEXT = (
    "이 데이터는 완전한 가짜입니다. 파이프라인 연습·검증용이며 "
    "논문 결과로 사용할 수 없습니다."
)
WATERMARK = "NOT REAL DATA"
MODEL_DISTS = ("outcome_model", "outcome_model_survival")


# ---------------------------------------------------------------------------
# Samplers (stdlib random only)
# ---------------------------------------------------------------------------
def sample_bernoulli(rng: random.Random, p: float) -> int:
    return 1 if rng.random() < p else 0


def sample_normal(rng: random.Random, mu: float, sigma: float,
                  lo: float | None = None, hi: float | None = None) -> float:
    for _ in range(100):  # truncate by redraw
        v = rng.gauss(mu, sigma)
        if (lo is None or v >= lo) and (hi is None or v <= hi):
            return round(v, 2)
    return round(min(max(mu, lo if lo is not None else mu), hi if hi is not None else mu), 2)


def sample_categorical(rng: random.Random, categories: List[Any], weights: List[float]) -> Any:
    return rng.choices(categories, weights=weights, k=1)[0]


def sample_uniform(rng: random.Random, lo: float, hi: float) -> float:
    return round(rng.uniform(lo, hi), 2)


def sample_exponential(rng: random.Random, mean: float) -> float:
    return round(rng.expovariate(1.0 / mean), 2)


def _linear(coefficients: Dict[str, float], row: Dict[str, Any], intercept: float) -> float:
    total = intercept
    for var, coef in coefficients.items():
        try:
            total += coef * float(row.get(var, 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def sample_outcome_logit(rng: random.Random, row: Dict[str, Any],
                         baseline_logit: float, coefficients: Dict[str, float]) -> int:
    p = 1.0 / (1.0 + math.exp(-_linear(coefficients, row, baseline_logit)))
    return 1 if rng.random() < p else 0


def sample_survival(rng: random.Random, row: Dict[str, Any], *, baseline_hazard: float,
                    coefficients: Dict[str, float], follow_up_days: float,
                    admin_censor: bool) -> Dict[str, Any]:
    """Proportional-hazards exponential time + administrative censoring.
    Returns {time, observed} — the caller writes `observed` into the spec's
    event_column (default "event")."""
    rate = baseline_hazard * math.exp(_linear(coefficients, row, 0.0))
    t = rng.expovariate(max(rate, 1e-9))
    if admin_censor and t > follow_up_days:
        return {"time": round(follow_up_days, 1), "observed": 0}
    return {"time": round(t, 1), "observed": 1}


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
def derive_seed(spec: Dict[str, Any], n: int) -> int:
    canonical = json.dumps(spec, sort_keys=True, ensure_ascii=False)
    return int(hashlib.sha256(f"{canonical}|{n}".encode("utf-8")).hexdigest(), 16) % (2 ** 32)


def build_cohort(spec: Dict[str, Any], n: int, seed: int) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    variables: Dict[str, Dict[str, Any]] = spec.get("variables", {})
    plain = [(k, v) for k, v in variables.items() if v.get("dist") not in MODEL_DISTS]
    models = [(k, v) for k, v in variables.items() if v.get("dist") in MODEL_DISTS]

    rows: List[Dict[str, Any]] = []
    for i in range(n):
        row: Dict[str, Any] = {"row_id": i + 1}
        for name, cfg in plain:  # covariates first
            p = cfg.get("params", {})
            dist = cfg.get("dist")
            if dist == "bernoulli":
                row[name] = sample_bernoulli(rng, p["p"])
            elif dist == "normal":
                row[name] = sample_normal(rng, p["mu"], p["sigma"], p.get("min"), p.get("max"))
            elif dist == "categorical":
                row[name] = sample_categorical(rng, p["categories"], p.get("weights") or [1] * len(p["categories"]))
            elif dist == "uniform":
                row[name] = sample_uniform(rng, p["lo"], p["hi"])
            elif dist == "exponential":
                row[name] = sample_exponential(rng, p["mean"])
            else:
                raise ValueError(f"unknown dist '{dist}' for variable '{name}'")
        for name, cfg in models:  # outcome models see the covariates
            p = cfg.get("params", {})
            if cfg["dist"] == "outcome_model":
                row[name] = sample_outcome_logit(rng, row, p.get("baseline_logit", -2.0),
                                                 p.get("coefficients", {}))
            else:  # outcome_model_survival
                res = sample_survival(rng, row,
                                      baseline_hazard=p.get("baseline_hazard", 0.01),
                                      coefficients=p.get("coefficients", {}),
                                      follow_up_days=p.get("follow_up_days", 365),
                                      admin_censor=p.get("admin_censor", True))
                row[name] = res["time"]
                row[p.get("event_column", "event")] = res["observed"]
        row["is_synthetic"] = 1  # in-band watermark, always last write
        rows.append(row)
    return rows


def write_csv(rows: List[Dict[str, Any]], path: str) -> List[str]:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    header: List[str] = []
    for row in rows:
        for k in row:
            if k not in header:
                header.append(k)
    if "is_synthetic" in header:  # keep the watermark visibly last
        header.remove("is_synthetic")
        header.append("is_synthetic")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return header


def write_meta(path: str, *, spec: Dict[str, Any], n: int, seed: int,
               consented_at: str, variables_hash: str | None, columns: List[str]) -> None:
    meta = {
        "watermark": WATERMARK,
        "consent_text": CONSENT_TEXT,
        "consented_at": consented_at,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "n": n,
        "seed": seed,
        "columns": columns,
        "spec": spec,
        "variables_json_sha256": variables_hash,
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a watermarked synthetic cohort (rehearsal only)")
    parser.add_argument("--spec-path", required=True, help="Distribution spec JSON (see synth-spec-template.json)")
    parser.add_argument("--n", type=int, default=None, help="Rows (default: spec.n or 500)")
    parser.add_argument("--seed", type=int, default=None, help="Override the derived deterministic seed")
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-meta", required=True)
    parser.add_argument("--consented-at", required=True,
                        help="ISO timestamp of the user's explicit fake-data acknowledgement")
    parser.add_argument("--variables-path", help="variables.json — recorded (hashed) in the meta for provenance")
    args = parser.parse_args()

    try:
        with open(args.spec_path, encoding="utf-8") as f:
            spec = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: spec unreadable ({type(exc).__name__})", file=sys.stderr)
        sys.exit(1)

    variables_hash = None
    if args.variables_path:
        try:
            with open(args.variables_path, "rb") as f:
                variables_hash = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            pass  # provenance nicety only

    n = args.n or int(spec.get("n", 500))
    seed = args.seed if args.seed is not None else (spec.get("seed") or derive_seed(spec, n))

    try:
        rows = build_cohort(spec, n, seed)
    except (KeyError, ValueError, TypeError) as exc:
        print(f"ERROR: invalid spec — {exc}", file=sys.stderr)
        sys.exit(1)

    columns = write_csv(rows, args.output_csv)
    write_meta(args.output_meta, spec=spec, n=n, seed=seed,
               consented_at=args.consented_at, variables_hash=variables_hash,
               columns=columns)

    print(f"Synthetic cohort: {args.output_csv}")
    print(f"  Rows: {n}  Seed: {seed}  Watermark: {WATERMARK}")
    print(f"  Meta: {args.output_meta}")
    print("  주의: 이 데이터는 완전한 가짜입니다 — 논문 결과로 사용할 수 없습니다.")
    sys.exit(0)


if __name__ == "__main__":
    main()
