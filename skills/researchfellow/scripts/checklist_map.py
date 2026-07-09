#!/usr/bin/env python3
"""Reporting-checklist coverage mapper (FREE integrity guardrail).

Selects the design-appropriate reporting guideline(s), then does a DETERMINISTIC,
keyword-anchored coverage pass over a manuscript draft and reports which required
items are covered / unclear / missing. This is a *coverage screen*, not a quality
review — the host LLM fills the gaps, and deep critique is the paid tier
(methodology_advisor / journal_fit).

Design -> guidelines:
    cohort | case_control | cross_sectional  -> STROBE (+ RECORD if routinely-collected)
    prediction                               -> TRIPOD

Usage:
    python3 checklist_map.py --design cohort --manuscript manuscript.md \
        --output .research/checklist-report.json [--routinely-collected]

Exit codes:
    0  no REQUIRED items missing/unclear
    2  at least one required reporting item is missing or unclear
    1  input error
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List

CHECKLIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "checklists")

DESIGN_GUIDELINES = {
    "cohort": ["strobe"],
    "case_control": ["strobe"],
    "cross_sectional": ["strobe"],
    "prediction": ["tripod"],
}

# Phrases that imply routinely-collected data and so pull in the RECORD extension.
# Matched on word boundaries and kept specific — bare "claims" is avoided because
# it collides with "numeric claims" etc. (data-source claims are "claims data").
ROUTINELY_COLLECTED_HINTS = (
    "electronic health record", "ehr", "claims data", "claims database",
    "insurance claims", "registry", "registries", "administrative data",
    "administrative database", "routinely collected", "icd-9", "icd-10",
    "icd code", "atc code",
)


def _mentions_routinely_collected(text_lc: str) -> bool:
    return any(re.search(r"\b" + re.escape(h) + r"\b", text_lc) for h in ROUTINELY_COLLECTED_HINTS)


def _strip_comments(text: str) -> str:
    """Remove HTML comments before scanning.

    The manuscript template embeds `<!-- REPORTING: ... -->` anchors that contain
    reporting keywords. Those must NOT count as coverage — otherwise an unfilled
    template would score as fully reported. Coverage is judged on authored prose
    only.
    """
    return re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)


def _load_checklist(name: str) -> Dict[str, Any]:
    path = os.path.join(CHECKLIST_DIR, f"{name}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _section_headers(manuscript: str) -> List[str]:
    """Lowercased markdown header texts, for section-presence heuristics."""
    return [m.group(1).strip().lower() for m in re.finditer(r"^#{1,6}\s*(.+)$", manuscript, re.MULTILINE)]


def _item_status(item: Dict[str, Any], text_lc: str, headers: List[str]) -> str:
    anchors = [a.lower() for a in item.get("anchors", [])]
    if any(a in text_lc for a in anchors):
        return "covered"
    section = item.get("section", "").lower()
    if section and any(section in h for h in headers):
        return "unclear"  # section exists but no anchor phrase found
    return "missing"


def screen(manuscript: str, guideline_names: List[str]) -> Dict[str, Any]:
    authored = _strip_comments(manuscript)  # anchors in comments must not count
    text_lc = authored.lower()
    headers = _section_headers(authored)

    guidelines_out: List[Dict[str, Any]] = []
    required_missing: List[str] = []
    covered = unclear = missing = 0

    for name in guideline_names:
        cl = _load_checklist(name)
        item_reports = []
        for item in cl["items"]:
            status = _item_status(item, text_lc, headers)
            if status == "covered":
                covered += 1
            elif status == "unclear":
                unclear += 1
            else:
                missing += 1
            if item.get("required") and status != "covered":
                required_missing.append(item["id"])
            item_reports.append({
                "id": item["id"],
                "label": item["label"],
                "section": item.get("section"),
                "required": bool(item.get("required")),
                "status": status,
            })
        guidelines_out.append({
            "guideline": cl["guideline"],
            "items": item_reports,
        })

    total = covered + unclear + missing
    return {
        "guidelines": guidelines_out,
        "summary": {
            "total_items": total,
            "covered": covered,
            "unclear": unclear,
            "missing": missing,
            "required_missing": required_missing,
        },
        "note": "Deterministic keyword-anchored coverage screen — a required item "
                "flagged missing/unclear needs the author's attention, not proof of "
                "absence. Quality critique is out of scope (paid tier).",
        "generated_at": datetime.now().isoformat(),
    }


def select_guidelines(design: str, manuscript: str, routinely_collected: bool) -> List[str]:
    names = list(DESIGN_GUIDELINES.get(design, []))
    is_rc = routinely_collected or _mentions_routinely_collected(_strip_comments(manuscript).lower())
    if is_rc and "strobe" in names:
        names.append("record")
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description="Map a manuscript against a reporting checklist (coverage screen)")
    parser.add_argument("--design", required=True, choices=sorted(DESIGN_GUIDELINES))
    parser.add_argument("--manuscript", required=True, help="Path to manuscript markdown")
    parser.add_argument("--output", required=True, help="Output path for checklist report JSON")
    parser.add_argument("--routinely-collected", action="store_true",
                        help="Force the RECORD extension (auto-detected from EMR/claims wording otherwise)")
    args = parser.parse_args()

    if not os.path.exists(args.manuscript):
        print(f"ERROR: manuscript not found: {args.manuscript}", file=sys.stderr)
        sys.exit(1)

    with open(args.manuscript, encoding="utf-8") as f:
        manuscript = f.read()

    guideline_names = select_guidelines(args.design, manuscript, args.routinely_collected)
    if not guideline_names:
        print(f"ERROR: no checklist for design '{args.design}'", file=sys.stderr)
        sys.exit(1)

    report = screen(manuscript, guideline_names)
    report["design"] = args.design
    report["guidelines_applied"] = [g["guideline"] for g in report["guidelines"]]

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    s = report["summary"]
    print(f"Checklist coverage ({', '.join(report['guidelines_applied'])}, design={args.design}):")
    print(f"  covered={s['covered']} unclear={s['unclear']} missing={s['missing']} / {s['total_items']}")
    if s["required_missing"]:
        print(f"  REQUIRED items needing attention: {', '.join(s['required_missing'])}")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
