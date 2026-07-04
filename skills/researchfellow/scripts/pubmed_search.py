#!/usr/bin/env python3
"""PubMed search script for the Research Assistant skill.

Searches PubMed via NCBI E-utilities and saves results as JSON.
No external dependencies required (stdlib only).

Usage:
    python3 pubmed_search.py --query "diabetes AND metformin" --email user@example.com --output .research/literature/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlencode
from urllib.request import urlopen

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL_NAME = "researchfellow"

# Incremental backoff gaps (seconds) per CLAUDE.md API rules
BACKOFF_GAPS = [1, 3, 5, 10, 10]


@dataclass
class PubMedItem:
    pmid: str
    title: str
    pubdate: str
    journal: str
    abstract: str = ""


def _request_xml(path: str, params: dict, timeout: int = 15, attempt: int = 0) -> ET.Element:
    url = f"{EUTILS_BASE}/{path}?{urlencode(params)}"
    try:
        with urlopen(url, timeout=timeout) as response:
            body = response.read()
        return ET.fromstring(body)
    except Exception as exc:
        if attempt < len(BACKOFF_GAPS):
            wait = BACKOFF_GAPS[attempt]
            print(f"  Retry {attempt + 1} after {wait}s: {exc}", file=sys.stderr)
            time.sleep(wait)
            return _request_xml(path, params, timeout, attempt + 1)
        raise


def search_pmids(
    query: str,
    *,
    email: str,
    retmax: int = 20,
    mindate: Optional[str] = None,
    maxdate: Optional[str] = None,
) -> List[str]:
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": str(retmax),
        "retmode": "xml",
        "sort": "relevance",
        "tool": TOOL_NAME,
        "email": email,
    }
    if mindate:
        params["mindate"] = mindate
        params["datetype"] = "pdat"
    if maxdate:
        params["maxdate"] = maxdate
        params["datetype"] = "pdat"

    root = _request_xml("esearch.fcgi", params)
    count_el = root.find("./Count")
    total = int(count_el.text) if count_el is not None and count_el.text else 0
    print(f"  Found {total} results (returning up to {retmax})")
    return [node.text for node in root.findall("./IdList/Id") if node.text]


def fetch_summaries(pmids: List[str], *, email: str) -> Dict[str, PubMedItem]:
    if not pmids:
        return {}

    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "tool": TOOL_NAME,
        "email": email,
    }
    root = _request_xml("esummary.fcgi", params)

    results: Dict[str, PubMedItem] = {}
    for doc in root.findall("./DocSum"):
        pmid = title = pubdate = journal = ""
        for child in doc:
            if child.tag == "Id":
                pmid = child.text or ""
            if child.tag == "Item":
                name = child.attrib.get("Name", "")
                if name == "Title":
                    title = child.text or ""
                elif name == "PubDate":
                    pubdate = child.text or ""
                elif name == "FullJournalName":
                    journal = child.text or ""
        if pmid:
            results[pmid] = PubMedItem(pmid=pmid, title=title, pubdate=pubdate, journal=journal)
    return results


def fetch_abstracts(pmids: List[str], *, email: str) -> Dict[str, str]:
    if not pmids:
        return {}

    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "tool": TOOL_NAME,
        "email": email,
    }
    root = _request_xml("efetch.fcgi", params)

    abstracts: Dict[str, str] = {}
    for article in root.findall(".//PubmedArticle"):
        pmid_node = article.find(".//MedlineCitation/PMID")
        if pmid_node is None or not pmid_node.text:
            continue
        pmid = pmid_node.text.strip()

        parts: List[str] = []
        for ab_node in article.findall(".//Abstract/AbstractText"):
            text = "".join(ab_node.itertext()).strip()
            label = ab_node.attrib.get("Label")
            if not text:
                continue
            parts.append(f"{label}: {text}" if label else text)

        abstracts[pmid] = " ".join(parts).strip()
    return abstracts


def main():
    parser = argparse.ArgumentParser(description="Search PubMed and save results")
    parser.add_argument("--query", required=True, help="PubMed search query")
    parser.add_argument("--email", required=True, help="Email for NCBI E-utilities")
    parser.add_argument("--retmax", type=int, default=20, help="Max results (default: 20)")
    parser.add_argument("--mindate", help="Minimum publication date (YYYY/MM/DD)")
    parser.add_argument("--maxdate", help="Maximum publication date (YYYY/MM/DD)")
    parser.add_argument("--output", required=True, help="Output directory for results")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(os.path.join(args.output, "items"), exist_ok=True)

    print(f"Searching PubMed: {args.query}")
    pmids = search_pmids(
        args.query,
        email=args.email,
        retmax=args.retmax,
        mindate=args.mindate,
        maxdate=args.maxdate,
    )

    if not pmids:
        print("No results found.")
        return

    print(f"Fetching summaries for {len(pmids)} articles...")
    time.sleep(1)  # Rate limit
    summaries = fetch_summaries(pmids, email=args.email)

    print("Fetching abstracts...")
    time.sleep(1)  # Rate limit
    abstracts = fetch_abstracts(pmids, email=args.email)

    # Merge abstracts into summaries
    for pmid, abstract in abstracts.items():
        if pmid in summaries:
            summaries[pmid].abstract = abstract

    # Save individual items
    for pmid, item in summaries.items():
        item_path = os.path.join(args.output, "items", f"{pmid}.json")
        with open(item_path, "w") as f:
            json.dump(asdict(item), f, indent=2, ensure_ascii=False)

    # Save query log
    queries_path = os.path.join(args.output, "queries.json")
    existing_queries = []
    if os.path.exists(queries_path):
        with open(queries_path) as f:
            existing_queries = json.load(f)

    existing_queries.append({
        "query": args.query,
        "retmax": args.retmax,
        "mindate": args.mindate,
        "maxdate": args.maxdate,
        "executed_at": datetime.now().isoformat(),
        "result_count": len(pmids),
        "pmids": pmids,
    })

    with open(queries_path, "w") as f:
        json.dump(existing_queries, f, indent=2, ensure_ascii=False)

    # Save summary list
    summary_path = os.path.join(args.output, "search-results.json")
    with open(summary_path, "w") as f:
        json.dump(
            [asdict(summaries[pmid]) for pmid in pmids if pmid in summaries],
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(f"Saved {len(summaries)} articles to {args.output}")


if __name__ == "__main__":
    main()
