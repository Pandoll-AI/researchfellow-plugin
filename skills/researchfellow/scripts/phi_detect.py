#!/usr/bin/env python3
"""PHI detection engine for the ResearchFellow skill (FR-T6, FR-M10).

Library module, NO CLI — see phi_screener.py for the CLI wrapper. This file is
the single source of truth for personal-identifier detection: phi_screener.py
(tabular/text file screening) and material_scanner.py (intake excerpt masking)
both delegate here. Standard library only: re, os, dataclasses, typing. File
I/O and format parsing (csv/xlsx loaders) stay in the callers.

ABSOLUTE RULE — a matched value or any fragment of it is NEVER present in a
finding, a return value, a log line, or an exception message. Findings carry
only {column, rule_id, severity, match_count, match_rate, example_rows (row
numbers only), downgraded_from_critical}. redact_text() replaces matched spans
with placeholders and never returns a partially masked string (see below).

Backend contract (for future adapters — e.g. Presidio, local NER):
  * implement PHIBackend (detect_tabular / detect_text / find_spans),
  * run fully offline — a backend must never send scanned content anywhere,
  * uphold the no-leak rule above in every return value,
  * register in BACKENDS. Selection: get_backend(name) -> env RF_PHI_BACKEND
    -> "rules". Unknown names raise UnsupportedPHIBackendError — never a
    silent fallback.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

# ---------------------------------------------------------------------------
# Patterns (compiled once). None of these values are ever emitted.
# ---------------------------------------------------------------------------
RRN_RE = re.compile(r"(?<!\d)(\d{6})[- ]?([1-4]\d{6})(?!\d)")
PHONE_RE = re.compile(r"(?<!\d)01[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
DATE_RE = re.compile(r"(?<!\d)(?:\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{8})(?!\d)")
KOR_SYLLABLE_RE = re.compile(r"^[가-힣]{2,4}$")

NAME_COL_HINTS = ("이름", "성명", "성함", "환자명", "name", "patient_name", "pt_name")
BIRTH_COL_HINTS = ("생년월일", "생일", "birth", "dob", "birthdate", "birth_date")

# Rule -> base severity
BASE_SEVERITY = {
    "krn_rrn": "critical",
    "phone_kr": "critical",
    "person_name": "critical",
    "email": "warning",
    "exact_birthdate": "warning",
}

MATCH_RATE_DOWNGRADE_THRESHOLD = 0.05  # critical -> warning below this rate
MAX_EXAMPLE_ROWS = 5

# Rules a backend can apply to free text (value patterns, no column context)
# vs rules that only make sense with a column name to gate on. This split is a
# limitation of the CURRENT RuleBackend, not of the PHIBackend protocol — a
# future NER backend may legitimately emit person_name from free text.
TEXT_ELIGIBLE_RULES: Tuple[str, ...] = ("krn_rrn", "phone_kr", "email")
COLUMN_GATED_RULES: Tuple[str, ...] = ("person_name", "exact_birthdate")


# ---------------------------------------------------------------------------
# Checksum for Korean Resident Registration Number (오탐 감축)
# ---------------------------------------------------------------------------
def _rrn_checksum_valid(digits: str) -> bool:
    """Validate the 13-digit RRN check digit. `digits` must be 13 chars, no sep."""
    if len(digits) != 13 or not digits.isdigit():
        return False
    weights = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
    total = sum(int(digits[i]) * weights[i] for i in range(12))
    check = (11 - (total % 11)) % 10
    return check == int(digits[12])


# ---------------------------------------------------------------------------
# Per-value rule checkers. Return True on a (validated) match. No value leaks.
# ---------------------------------------------------------------------------
def _hit_rrn(value: str) -> bool:
    for m in RRN_RE.finditer(value):
        if _rrn_checksum_valid(m.group(1) + m.group(2)):
            return True
    return False


def _hit_phone(value: str) -> bool:
    return PHONE_RE.search(value) is not None


def _hit_email(value: str) -> bool:
    return EMAIL_RE.search(value) is not None


def _hit_full_date(value: str) -> bool:
    return DATE_RE.search(value) is not None


def _is_korean_name(value: str) -> bool:
    return KOR_SYLLABLE_RE.match(value.strip()) is not None


def _column_name_matches(col: str, hints: Tuple[str, ...]) -> bool:
    low = col.lower()
    return any(h.lower() in low for h in hints)


# ---------------------------------------------------------------------------
# Finding schema (identical to the legacy phi_screener report entries)
# ---------------------------------------------------------------------------
def build_finding(column: Optional[str], rule_id: str, match_rows: List[int], n_items: int) -> Dict[str, Any]:
    match_count = len(match_rows)
    match_rate = round(match_count / n_items, 4) if n_items else 0.0
    severity = BASE_SEVERITY[rule_id]
    downgraded = False
    if severity == "critical" and match_rate < MATCH_RATE_DOWNGRADE_THRESHOLD:
        severity = "warning"
        downgraded = True
    return {
        "column": column,
        "rule_id": rule_id,
        "severity": severity,
        "match_count": match_count,
        "match_rate": match_rate,
        "example_rows": match_rows[:MAX_EXAMPLE_ROWS],  # ROW NUMBERS ONLY
        "downgraded_from_critical": downgraded,
    }


def max_severity(findings: List[Dict[str, Any]]) -> str:
    """Aggregate finding severities to 'critical' | 'warning' | 'clean'."""
    if any(f.get("severity") == "critical" for f in findings):
        return "critical"
    if any(f.get("severity") == "warning" for f in findings):
        return "warning"
    return "clean"


# ---------------------------------------------------------------------------
# Backend interface
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Span:
    """A character-offset match inside a text buffer. Carries NO matched value."""
    start: int
    end: int
    rule_id: str
    severity: str


class PHIBackend(Protocol):
    name: str

    def detect_tabular(self, header: Sequence[str], body: Sequence[Sequence[str]]) -> List[Dict[str, Any]]: ...

    def detect_text(self, text: str) -> List[Dict[str, Any]]: ...

    def find_spans(self, text: str) -> List[Span]: ...


class RuleBackend:
    """Regex + RRN-checksum + column-name-gating backend (the P0/P1 rule set).

    Known limitation: detect_text/find_spans only apply TEXT_ELIGIBLE_RULES —
    person_name and exact_birthdate need a column name to gate on, so free-text
    name detection is out of reach for this backend (a future NER/Presidio
    backend is the intended fix; see the roadmap).
    """

    name = "rules"

    def detect_tabular(self, header: Sequence[str], body: Sequence[Sequence[str]]) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []
        n_rows = len(body)
        if n_rows == 0:
            return findings

        for col_idx, col in enumerate(header):
            values = [(row[col_idx] if col_idx < len(row) else "") for row in body]

            # ---- value-scan rules (apply to every column) ----
            for rule_id, checker in (
                ("krn_rrn", _hit_rrn),
                ("phone_kr", _hit_phone),
                ("email", _hit_email),
            ):
                match_rows = [i + 1 for i, v in enumerate(values) if v and checker(str(v))]
                if match_rows:
                    findings.append(build_finding(col, rule_id, match_rows, n_rows))

            # ---- person_name: column-name gated + value heuristic ----
            if _column_name_matches(col, NAME_COL_HINTS):
                nonempty = [(i, v) for i, v in enumerate(values) if str(v).strip()]
                name_rows = [i for i, v in nonempty if _is_korean_name(str(v))]
                if nonempty:
                    kor_frac = len(name_rows) / len(nonempty)
                    unique_ratio = len({str(v).strip() for _, v in nonempty}) / len(nonempty)
                    if kor_frac >= 0.70 and unique_ratio >= 0.5:
                        match_rows = [i + 1 for i in name_rows]
                        findings.append(build_finding(col, "person_name", match_rows, n_rows))

            # ---- exact_birthdate: column-name gated + full-date value ----
            if _column_name_matches(col, BIRTH_COL_HINTS):
                match_rows = [i + 1 for i, v in enumerate(values) if v and _hit_full_date(str(v))]
                if match_rows:
                    findings.append(build_finding(col, "exact_birthdate", match_rows, n_rows))

        return findings

    def detect_text(self, text: str) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []
        lines = text.splitlines() or [text]
        n_lines = len(lines)

        for rule_id, checker in (
            ("krn_rrn", _hit_rrn),
            ("phone_kr", _hit_phone),
            ("email", _hit_email),
        ):
            rows = [i + 1 for i, ln in enumerate(lines) if checker(ln)]
            if rows:
                findings.append(build_finding(None, rule_id, rows, n_lines))
        return findings

    def find_spans(self, text: str) -> List[Span]:
        spans: List[Span] = []
        for m in RRN_RE.finditer(text):
            if _rrn_checksum_valid(m.group(1) + m.group(2)):
                spans.append(Span(m.start(), m.end(), "krn_rrn", BASE_SEVERITY["krn_rrn"]))
        for m in PHONE_RE.finditer(text):
            spans.append(Span(m.start(), m.end(), "phone_kr", BASE_SEVERITY["phone_kr"]))
        for m in EMAIL_RE.finditer(text):
            spans.append(Span(m.start(), m.end(), "email", BASE_SEVERITY["email"]))
        return spans


# ---------------------------------------------------------------------------
# Backend registry + selection
# ---------------------------------------------------------------------------
BACKENDS: Dict[str, type] = {"rules": RuleBackend}


class UnsupportedPHIBackendError(ValueError):
    """Requested PHI backend is not available — no silent fallback allowed."""


def get_backend(name: Optional[str] = None) -> PHIBackend:
    """Resolve name -> env RF_PHI_BACKEND -> 'rules'. Unknown values raise."""
    key = name or os.environ.get("RF_PHI_BACKEND", "").strip() or "rules"
    factory = BACKENDS.get(key)
    if factory is None:
        raise UnsupportedPHIBackendError(
            f"Unknown PHI backend '{key}' (available: {', '.join(sorted(BACKENDS))})"
        )
    return factory()


# ---------------------------------------------------------------------------
# Module-level conveniences (default backend unless one is passed)
# ---------------------------------------------------------------------------
def detect_tabular(header: Sequence[str], body: Sequence[Sequence[str]],
                   *, backend: Optional[PHIBackend] = None) -> List[Dict[str, Any]]:
    return (backend or get_backend()).detect_tabular(header, body)


def detect_text(text: str, *, backend: Optional[PHIBackend] = None) -> List[Dict[str, Any]]:
    return (backend or get_backend()).detect_text(text)


def _merge_spans(spans: List[Span]) -> List[Span]:
    """Sort by start and collapse overlapping/adjacent-overlap spans into union
    ranges so no original character between two matches can survive masking."""
    merged: List[Span] = []
    for span in sorted(spans, key=lambda s: (s.start, -s.end)):
        if merged and span.start < merged[-1].end:
            prev = merged[-1]
            merged[-1] = Span(prev.start, max(prev.end, span.end), prev.rule_id, prev.severity)
        else:
            merged.append(span)
    return merged


def redact_text(text: str, *, backend: Optional[PHIBackend] = None,
                placeholder_fmt: str = "[MASKED:{rule_id}]") -> Tuple[str, List[Dict[str, Any]]]:
    """Mask every detected span in `text`; return (masked_text, findings).

    Findings are computed on the ORIGINAL buffer (severity/counts describe the
    file, not the excerpt) and are metadata-only as always. Masking is a merged
    single forward pass, then a defensive re-scan of the output: if anything
    still matches (a span-computation bug), the whole text is blanked to "" —
    a partially masked string is never returned (fail-closed).
    """
    be = backend or get_backend()
    findings = be.detect_text(text)
    spans = be.find_spans(text)
    if not spans:
        return text, findings

    parts: List[str] = []
    cursor = 0
    for span in _merge_spans(spans):
        parts.append(text[cursor:span.start])
        parts.append(placeholder_fmt.format(rule_id=span.rule_id))
        cursor = span.end
    parts.append(text[cursor:])
    masked = "".join(parts)

    if be.find_spans(masked):  # defensive self-check — never return a half-mask
        return "", findings
    return masked, findings
