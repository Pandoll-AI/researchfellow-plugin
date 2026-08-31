"""query_guard.py — data-access contract.

The host LLM never Reads raw extracts; this script is the only allowed view.
Tests lock the eight verification items from the data-access contract:
  (1) 1-row freq/agg is suppressed
  (2) PII column-name variants are masked
  (3) value-based PII (harmless colnames) is masked or suppressed
  (4) n≤30 cells emit a statistical-validity warning
  (5) first-row-is-data fails closed
  (6) unsupported format / parse failure fails closed without leaking rows
  (7) schema/freq/agg happy-path JSON keys match the contract exactly
  (8) dtypes come from a full-table scan, not a first-row peek
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SCRIPT = "query_guard.py"
CONTRACT_KEYS = {"columns", "dtypes", "result", "suppressed", "suppression_note", "warnings"}
SUPPRESSION_NOTE = "1건 결과는 공개하지 않음"

# Values planted in fixtures — none of these may appear in any process output.
PII_NAMES = ("김철수", "이영희", "박민준", "최수빈", "정하늘")
PII_LATIN = ("Kim Cheolsu", "Lee Younghee", "Park Minjun", "Choi Subin", "Jung Haneul")
PII_INITIALS = ("KCS", "LYH", "PMJ", "CSB", "JHN")
PHONES = (
    "010-1234-5678", "010-2345-6789", "010-3456-7890",
    "010-4567-8901", "010-5678-9012",
)
RRNS = (
    "900101-1234568", "850505-2345674", "750101-1234568",
    "990101-1234563", "800101-1234560",
)
SINGLE_SECRET = "SECRET_SINGLE_ROW"
XLSX_LEAK = "LEAK_XLSX_ROW"
PARSE_LEAK = "LEAK_PARSE_ROW"


def _fx(fixtures_dir: Path, name: str) -> Path:
    return fixtures_dir / name


def _run(run_script, fixtures_dir: Path, filename: str, op: str, *by: str):
    args = ["--data", str(_fx(fixtures_dir, filename)), "--op", op]
    if by:
        args.extend(["--by", *by])
    return run_script(SCRIPT, *args)


def _blob(proc) -> str:
    return (proc.stdout or "") + (proc.stderr or "")


def _payload(proc) -> dict:
    return json.loads(proc.stdout)


def _assert_no_leak(blob: str, *secrets: str) -> None:
    for secret in secrets:
        assert secret not in blob, f"LEAK: {secret!r} surfaced in process output"


# ---------------------------------------------------------------------------
# (1) 1-row freq/agg → suppressed, no result, no original values
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("op,by", [("freq", ("sex",)), ("agg", ("sex",))])
def test_1_single_row_freq_agg_suppressed(run_script, fixtures_dir, op, by):
    proc = _run(run_script, fixtures_dir, "queryguard_single.csv", op, *by)
    assert proc.returncode == 0, proc.stderr
    payload = _payload(proc)
    assert payload["suppressed"] is True
    assert "result" not in payload
    assert payload["suppression_note"] == SUPPRESSION_NOTE
    _assert_no_leak(_blob(proc), SINGLE_SECRET, "64")


# ---------------------------------------------------------------------------
# (2) PII column-name variants → mask label, no original values
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("col", ["환자명", "patient_name", "PT_NM"])
def test_2_pii_column_name_variants_masked(run_script, fixtures_dir, col):
    proc = _run(run_script, fixtures_dir, "queryguard_pii_colnames.csv", "freq", col)
    assert proc.returncode == 0, proc.stderr
    payload = _payload(proc)
    blob = _blob(proc)
    _assert_no_leak(blob, *PII_NAMES, *PII_LATIN, *PII_INITIALS)
    if payload.get("suppressed"):
        return
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "***MASKED(" in dumped
    assert col in payload["columns"]


# ---------------------------------------------------------------------------
# (3) value-based PII (harmless colnames) → mask or suppress, no original values
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("col,secrets", [("note", PHONES), ("code", RRNS)])
def test_3_value_based_pii_masked_or_suppressed(run_script, fixtures_dir, col, secrets):
    proc = _run(run_script, fixtures_dir, "queryguard_pii_values.csv", "freq", col)
    assert proc.returncode == 0, proc.stderr
    blob = _blob(proc)
    _assert_no_leak(blob, *secrets)
    payload = _payload(proc)
    if payload.get("suppressed"):
        assert "result" not in payload
        return
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "***MASKED(" in dumped


# ---------------------------------------------------------------------------
# (4) n≤30 cells → warnings carry a statistical-validity warning
# ---------------------------------------------------------------------------
def test_4_small_n_emits_validity_warning(run_script, fixtures_dir):
    proc = _run(run_script, fixtures_dir, "queryguard_small_n.csv", "freq", "group")
    assert proc.returncode == 0, proc.stderr
    payload = _payload(proc)
    assert payload["warnings"], payload
    joined = " ".join(str(w) for w in payload["warnings"])
    assert "n≤30" in joined or "n<=30" in joined
    assert "유효" in joined


# ---------------------------------------------------------------------------
# (5) first row is data → fail-closed, non-zero exit
# ---------------------------------------------------------------------------
def test_5_header_mismatch_fails_closed(run_script, fixtures_dir):
    proc = _run(run_script, fixtures_dir, "queryguard_no_header.csv", "schema")
    assert proc.returncode != 0
    blob = _blob(proc)
    _assert_no_leak(blob, "7.25", "2020-01-15", "3.50")
    payload = _payload(proc)
    assert payload.get("error")


# ---------------------------------------------------------------------------
# (6) unsupported format / parse failure → fail-closed, no original rows
# ---------------------------------------------------------------------------
def test_6_unsupported_format_fails_closed(run_script, fixtures_dir):
    proc = _run(run_script, fixtures_dir, "queryguard_unsupported.xlsx", "schema")
    assert proc.returncode != 0
    blob = _blob(proc)
    _assert_no_leak(blob, XLSX_LEAK, "김원본", "010-1111-2222")
    payload = _payload(proc)
    assert payload.get("error")


def test_6_parse_failure_fails_closed(run_script, fixtures_dir, tmp_path):
    empty = _run(run_script, fixtures_dir, "queryguard_parse_fail.csv", "schema")
    assert empty.returncode != 0
    assert json.loads(empty.stdout).get("error")

    leaky = tmp_path / "queryguard_nul.csv"
    leaky.write_bytes(f"{PARSE_LEAK},김원본,010-0000-0000\x00garbage\n".encode("utf-8"))
    proc = run_script(SCRIPT, "--data", str(leaky), "--op", "schema")
    assert proc.returncode != 0
    blob = _blob(proc)
    _assert_no_leak(blob, PARSE_LEAK, "김원본", "010-0000-0000")
    assert json.loads(proc.stdout).get("error")


# ---------------------------------------------------------------------------
# (7) schema / freq / agg happy path — JSON keys match the contract exactly
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "op,by",
    [
        ("schema", ()),
        ("freq", ("sex",)),
        ("agg", ("sex",)),
    ],
)
def test_7_happy_path_keys_match_contract(run_script, fixtures_dir, op, by):
    proc = _run(run_script, fixtures_dir, "queryguard_ok.csv", op, *by)
    assert proc.returncode == 0, proc.stderr
    payload = _payload(proc)
    assert set(payload.keys()) == CONTRACT_KEYS
    assert payload["suppressed"] is False
    assert isinstance(payload["columns"], list) and payload["columns"]
    assert isinstance(payload["dtypes"], dict) and payload["dtypes"]
    assert isinstance(payload["result"], dict)
    assert isinstance(payload["warnings"], list)
    assert "age" in payload["columns"]
    assert payload["dtypes"]["age"] == "integer"
    assert payload["dtypes"]["sex"] in {"categorical", "string"}
    assert payload["dtypes"]["index_date"] == "date"
    assert payload["dtypes"]["outcome"] == "binary"


# ---------------------------------------------------------------------------
# (8) dtypes from a full-table scan (type shifts in later rows)
# ---------------------------------------------------------------------------
def test_8_dtypes_use_full_table_scan(run_script, fixtures_dir):
    proc = _run(run_script, fixtures_dir, "queryguard_dtype_shift.csv", "schema")
    assert proc.returncode == 0, proc.stderr
    payload = _payload(proc)
    # First 10 data rows of `score` are integers; row 11 is 20.5.
    # A first-row (or first-N) peek would call it integer; full scan → float.
    assert payload["dtypes"]["score"] == "float"
    assert payload["dtypes"]["id"] == "integer"
    assert payload["dtypes"]["flag"] == "binary"
    assert "20.5" not in _blob(proc)
