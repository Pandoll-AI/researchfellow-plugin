#!/usr/bin/env python3
"""Claim → evidence binder (FREE integrity guardrail).

Deterministically maps manuscript numeric claims and citations onto
`evidence.json` (literature table). No network lookups.

Anchor grammar (from templates/manuscript-template.md):
    <!-- claim: <kind>:<id> -->   kind ∈ {table, figure, text}

Usage:
    python3 claim_map.py --manuscript manuscript.md --evidence evidence.json

Stdout JSON (field names fixed):
    {claims:[{text, anchor:{kind, id}, sources:[{type, id, verified}],
              status}], unmapped:[]}

Exit codes:
    0  report emitted
    1  input error / schema_version mismatch / malformed claim grammar
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# evidence-table-template.json declares schema_version as the string "1".
EXPECTED_SCHEMA_VERSION = "1"

ANCHOR_KINDS = ("table", "figure", "text")

# Valid anchors. Kind is case-insensitive; id is the rest of the token.
CLAIM_RE = re.compile(
    r"<!--\s*claim:\s*(table|figure|text)\s*:\s*(\S+?)\s*-->",
    re.IGNORECASE,
)
# Any claim-shaped comment, used to fail-closed on unknown kinds / missing ids.
CLAIM_COMMENT_RE = re.compile(r"<!--\s*claim:\s*([^>]*?)\s*-->", re.IGNORECASE)

PMID_IN_TEXT_RE = re.compile(r"PMID[:\s]*?(\d{6,9})", re.IGNORECASE)
DOI_IN_TEXT_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")

# Unmapped heuristic: effect-estimate abbreviations / full names with a number,
# or a CI span. Connectors: = | : | was | of. CI is not required.
_EFFECT_ABBREV = r"aOR|aHR|aRR|IRR|SMD|RD|RR|OR|HR"
_EFFECT_FULL = (
    r"(?:adjusted\s+)?(?:odds|hazard|risk)\s+ratios?"
    r"|incidence\s+rate\s+ratios?"
    r"|standardized\s+mean\s+differences?"
    r"|risk\s+differences?"
)
_EFFECT_CONN = r"(?:=|:|\bwas\b|\bof\b)?"
_EFFECT_NUM = r"\d+(?:\.\d+)?"
EFFECT_RE = re.compile(
    r"(?:"
    r"\b(?:%s)\b\s*%s\s*%s"
    r"|\b(?:%s)\s*%s\s*%s"
    r"|(?:95\s*%%\s*CI|\bCI)\s*%s\s*[\(\[]?\s*%s\s*[-–—,to]+\s*%s"
    r")"
    % (_EFFECT_ABBREV, _EFFECT_CONN, _EFFECT_NUM,
       _EFFECT_FULL, _EFFECT_CONN, _EFFECT_NUM,
       _EFFECT_CONN, _EFFECT_NUM, _EFFECT_NUM),
    re.IGNORECASE,
)

HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


class ClaimMapError(Exception):
    """Fail-closed input / grammar error (CLI exit 1)."""


def _schema_version_ok(raw: Any) -> bool:
    """Template declares schema_version as the JSON string "1", not the integer 1."""
    return raw == EXPECTED_SCHEMA_VERSION


def require_schema_version(evidence: Dict[str, Any]) -> None:
    if not isinstance(evidence, dict):
        raise ClaimMapError("evidence must be a JSON object")
    if "schema_version" not in evidence or not _schema_version_ok(evidence.get("schema_version")):
        raise ClaimMapError(
            "schema_version mismatch: expected "
            f"{EXPECTED_SCHEMA_VERSION!r}, got {evidence.get('schema_version')!r}"
        )


def _normalize_doi(value: str) -> str:
    s = value.strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^doi:\s*", "", s, flags=re.IGNORECASE)
    return s.rstrip(").,;]")


def parse_identifier(raw: str) -> Optional[Tuple[str, str]]:
    """Return (type, id) for a PMID or DOI token, else None."""
    s = raw.strip()
    s = re.sub(r"^(PMID|doi)\s*:\s*", "", s, flags=re.IGNORECASE)
    s = _normalize_doi(s) if not re.fullmatch(r"\d+", s) else s
    if re.fullmatch(r"\d+", s):
        return ("pmid", s)
    if s.lower().startswith("10."):
        doi = s.rstrip(").,;]")
        if doi.lower().startswith("10."):
            return ("doi", doi)
    return None


def _identifier_format_ok(kind: str, ident: str) -> bool:
    if kind == "pmid":
        return bool(re.fullmatch(r"\d+", ident))
    if kind == "doi":
        return ident.startswith("10.")
    return False


def load_evidence(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ClaimMapError(f"evidence is not valid JSON: {exc}") from exc
    require_schema_version(data)
    papers = data.get("papers", [])
    if papers is None:
        papers = []
    if not isinstance(papers, list):
        raise ClaimMapError("evidence.papers must be a list")
    data["papers"] = papers
    return data


def index_evidence(evidence: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """PMID/DOI → paper. Duplicate ids are last-wins; existence is boolean."""
    pmids: Dict[str, Any] = {}
    dois: Dict[str, Any] = {}
    for paper in evidence.get("papers") or []:
        if not isinstance(paper, dict):
            continue
        pmid = str(paper.get("pmid") or "").strip()
        if pmid and re.fullmatch(r"\d+", pmid):
            pmids[pmid] = paper
        doi_raw = str(paper.get("doi") or "").strip()
        if doi_raw:
            doi = _normalize_doi(doi_raw)
            if doi.startswith("10."):
                dois[doi.lower()] = paper
    return {"pmids": pmids, "dois": dois}


def _source_verified(type_: str, ident: str, index: Dict[str, Dict[str, Any]]) -> bool:
    if not _identifier_format_ok(type_, ident):
        return False
    if type_ == "pmid":
        return ident in index["pmids"]
    return ident.lower() in index["dois"]


def _line_bounds(text: str, pos: int) -> Tuple[int, int]:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end < 0:
        end = len(text)
    return start, end


def _previous_nonempty_line(text: str, pos: int) -> Tuple[int, int, str]:
    """Return (start, end, stripped text) of the nearest preceding non-empty line."""
    cursor = pos
    while cursor > 0:
        prev_nl = text.rfind("\n", 0, cursor)
        line_start = prev_nl + 1
        line = text[line_start:cursor]
        stripped = line.strip()
        if stripped:
            return line_start, cursor, stripped
        if prev_nl < 0:
            break
        cursor = prev_nl
    return pos, pos, ""


def _prose_of_line(line: str) -> str:
    """Strip HTML comments from a single line and collapse whitespace."""
    return re.sub(r"\s+", " ", HTML_COMMENT_RE.sub(" ", line)).strip()


def claim_text_at(manuscript: str, match_start: int) -> str:
    """Nearest authored prose bound to a claim comment (same line, else previous)."""
    line_start, line_end = _line_bounds(manuscript, match_start)
    same = _prose_of_line(manuscript[line_start:match_start])
    if same:
        return same
    _, _, prev = _previous_nonempty_line(manuscript, line_start)
    return _prose_of_line(prev)


def covered_span(manuscript: str, match_start: int, match_end: int) -> Tuple[int, int]:
    """Byte span owned by a claim comment: same-line prose or previous non-empty line."""
    line_start, _ = _line_bounds(manuscript, match_start)
    same = _prose_of_line(manuscript[line_start:match_start])
    if same:
        return line_start, match_end
    prev_start, _, prev = _previous_nonempty_line(manuscript, line_start)
    if prev:
        return prev_start, match_end
    return match_start, match_end


def output_present(manuscript: str, kind: str, ident: str) -> bool:
    """True when authored prose names this table/figure (caption, xref, or header).

    Looks for ``Table 2`` / ``Tbl. 2`` or ``Figure 1`` / ``Fig. 1`` (and
    unsuffixed Fig/Tbl) in comment-stripped markdown. Local, no network.
    """
    if kind not in ("table", "figure"):
        return False
    authored = _mask_comments(manuscript)
    ident_esc = re.escape(ident)
    if kind == "table":
        pattern = rf"\b(?:Tables?|Tbl\.?)\s*{ident_esc}\b"
    else:
        pattern = rf"\b(?:Figures?|Fig\.?)\s*{ident_esc}\b"
    return bool(re.search(pattern, authored, flags=re.IGNORECASE))


def extract_sources(kind: str, anchor_id: str, text: str,
                    index: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    seen = set()

    def add(type_: str, ident: str) -> None:
        ident = ident.strip()
        if not ident:
            return
        key = (type_, ident.lower() if type_ == "doi" else ident)
        if key in seen:
            return
        seen.add(key)
        sources.append({
            "type": type_,
            "id": ident,
            "verified": _source_verified(type_, ident, index),
        })

    if kind == "text":
        parsed = parse_identifier(anchor_id)
        if parsed:
            add(parsed[0], parsed[1])
    for pmid in PMID_IN_TEXT_RE.findall(text):
        add("pmid", pmid)
    for match in DOI_IN_TEXT_RE.finditer(text):
        add("doi", _normalize_doi(match.group(0)))
    return sources


def claim_status(kind: str, anchor_id: str, sources: List[Dict[str, Any]],
                 manuscript: str = "") -> str:
    """verified | unverified | mismatch — local, no network.

    verified   : every PMID/DOI is present in evidence and well-formed;
                 table/figure anchors are verified only when that output is
                 named in the manuscript (caption / xref / header).
    unverified : identifiers are well-formed but none exist in evidence
                 (hallucinated citation); a text anchor whose id is not a
                 PMID/DOI; or a table/figure id that the manuscript never names.
    mismatch   : at least one identifier hits evidence and another does not.
    """
    if kind == "text" and parse_identifier(anchor_id) is None:
        return "unverified"
    if kind in ("table", "figure") and not output_present(manuscript, kind, anchor_id):
        return "unverified"
    if not sources:
        if kind in ("table", "figure"):
            return "verified"
        return "unverified"
    flags = [bool(s["verified"]) for s in sources]
    if all(flags):
        return "verified"
    if any(flags):
        return "mismatch"
    return "unverified"


def _validate_claim_grammar(manuscript: str) -> None:
    """Fail-closed on claim comments that are not the declared grammar.

    Template placeholders (`<!-- claim: <kind>:<id> -->`) are ignored — they
    document the grammar and are not live anchors.
    """
    for match in CLAIM_COMMENT_RE.finditer(manuscript):
        body = match.group(1).strip()
        if "<" in body:
            continue
        if CLAIM_RE.fullmatch(match.group(0)):
            continue
        raise ClaimMapError(
            f"malformed claim anchor {match.group(0)!r}: "
            "expected <!-- claim: table|figure|text:<id> -->"
        )


def parse_claims(manuscript: str, index: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    _validate_claim_grammar(manuscript)
    claims: List[Dict[str, Any]] = []
    for match in CLAIM_RE.finditer(manuscript):
        kind = match.group(1).lower()
        ident = match.group(2)
        text = claim_text_at(manuscript, match.start())
        sources = extract_sources(kind, ident, text, index)
        claims.append({
            "text": text,
            "anchor": {"kind": kind, "id": ident},
            "sources": sources,
            "status": claim_status(kind, ident, sources, manuscript),
        })
    return claims


def _mask_comments(text: str) -> str:
    return HTML_COMMENT_RE.sub(lambda m: " " * (m.end() - m.start()), text)


def unmapped_estimates(manuscript: str, claim_matches: List[re.Match[str]]) -> List[Dict[str, str]]:
    """Effect-estimate spans (RR/OR/HR/CI numbers) not owned by a claim comment."""
    spans = [covered_span(manuscript, m.start(), m.end()) for m in claim_matches]
    masked = _mask_comments(manuscript)
    seen_lines = set()
    out: List[Dict[str, str]] = []
    for match in EFFECT_RE.finditer(masked):
        start, end = match.start(), match.end()
        if any(s <= start < e or s < end <= e for s, e in spans):
            continue
        line_start, line_end = _line_bounds(manuscript, start)
        if line_start in seen_lines:
            continue
        seen_lines.add(line_start)
        line = _prose_of_line(manuscript[line_start:line_end])
        if not line:
            continue
        out.append({"text": line})
    return out


def map_claims(manuscript: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
    require_schema_version(evidence)
    index = index_evidence(evidence)
    claims = parse_claims(manuscript, index)
    claim_matches = list(CLAIM_RE.finditer(manuscript))
    return {
        "claims": claims,
        "unmapped": unmapped_estimates(manuscript, claim_matches),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bind manuscript claims to evidence.json (PMID/DOI, local)"
    )
    parser.add_argument("--manuscript", required=True, help="Path to manuscript markdown")
    parser.add_argument("--evidence", required=True, help="Path to evidence-table JSON")
    args = parser.parse_args()

    if not os.path.exists(args.manuscript):
        print(f"ERROR: manuscript not found: {args.manuscript}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.evidence):
        print(f"ERROR: evidence not found: {args.evidence}", file=sys.stderr)
        sys.exit(1)

    try:
        evidence = load_evidence(args.evidence)
        with open(args.manuscript, encoding="utf-8") as f:
            manuscript = f.read()
        report = map_claims(manuscript, evidence)
    except ClaimMapError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(report, indent=2, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
