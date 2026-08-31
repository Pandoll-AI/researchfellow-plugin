#!/usr/bin/env python3
"""Multi-source literature search for the Research Assistant skill.

Searches PubMed (NCBI E-utilities) plus PMC, Europe PMC, medRxiv, OpenAlex,
and Crossref. Stdlib only (urllib). No API keys.

Existing PubMed CLI flags keep working:

    python3 pubmed_search.py --query "diabetes AND metformin" \\
        --email user@example.com --output .research/literature/

Multi-source (default `--sources all`) writes the same legacy files plus
`literature.json` (envelope: items, source logs, failed_sources, degraded).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import phi_detect  # noqa: E402  — import reuse only; this file does not modify it

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUROPEPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
MEDRXIV_DETAILS = "https://api.biorxiv.org/details/medrxiv"
OPENALEX_WORKS = "https://api.openalex.org/works"
CROSSREF_WORKS = "https://api.crossref.org/works"
TOOL_NAME = "researchfellow"

EVIDENCE_TEMPLATE_PATH = os.path.normpath(
    os.path.join(_SCRIPTS_DIR, "..", "templates", "evidence-table-template.json")
)

ALL_SOURCES = ("pubmed", "pmc", "europepmc", "medrxiv", "openalex", "crossref")
# PMC shares NCBI with PubMed and does not count toward extra-source coverage.
INDEPENDENT_EXTRAS = frozenset({"europepmc", "medrxiv", "openalex", "crossref"})
# Outbound search-string keys only. Never screen email/id/filter/contact.
_SEARCH_PARAM_KEYS = frozenset({"term", "query", "search", "q"})
ITEM_SCHEMA_KEYS = (
    "pmid",
    "doi",
    "pmcid",
    "title",
    "authors",
    "year",
    "journal",
    "abstract",
    "pubdate",
    "fulltext_url",
    "sources",
    "direction",
)

# Incremental backoff gaps (seconds) per CLAUDE.md API rules — legacy PubMed path
BACKOFF_GAPS = [1, 3, 5, 10, 10]

_DOI_PREFIX_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.I)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_TOKEN_RE = re.compile(r"[A-Za-z]{3,}")
_STOP_TOKENS = frozenset({"and", "or", "not", "the", "for", "with", "from"})

# Optional test injection; live CLI uses UrlLibTransport.
_DEFAULT_TRANSPORT = None


# ---------------------------------------------------------------------------
# Legacy PubMed item (CLI backward compatible)
# ---------------------------------------------------------------------------
@dataclass
class PubMedItem:
    pmid: str
    title: str
    pubdate: str
    journal: str
    abstract: str = ""


# ---------------------------------------------------------------------------
# Errors / HTTP
# ---------------------------------------------------------------------------
class QueryRejectedError(ValueError):
    """Query matched a PHI pattern — the request must not be sent.

    The exception message and `rule_ids` never contain the matched value.
    """

    def __init__(self, rule_ids: Sequence[str]):
        self.rule_ids = list(rule_ids)
        super().__init__(
            "query rejected: PHI pattern(s) detected "
            f"({', '.join(self.rule_ids)}); request not sent"
        )


class SchemaError(ValueError):
    """Source payload did not match the expected response shape."""


class SourceFailure(Exception):
    def __init__(self, source: str, kind: str, log: Optional[Dict[str, Any]] = None):
        self.source = source
        self.kind = kind
        self.log = log or {}
        super().__init__(kind)


@dataclass
class HttpResponse:
    status: int
    body: bytes
    accessed: str


class UrlLibTransport:
    """stdlib urllib GET. No retries — source isolation is the caller's job."""

    def get(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 15,
    ) -> HttpResponse:
        accessed = _now()
        req = Request(url, headers=dict(headers or {}))
        try:
            with urlopen(req, timeout=timeout) as response:
                status = int(getattr(response, "status", 200) or 200)
                return HttpResponse(status=status, body=response.read(), accessed=accessed)
        except HTTPError as exc:
            body = b""
            try:
                body = exc.read() or b""
            except Exception:
                body = b""
            return HttpResponse(status=int(exc.code), body=body, accessed=accessed)
        except Exception as exc:
            if _is_timeout(exc):
                raise TimeoutError("timeout") from exc
            raise


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError):
            return True
        if reason is not None and reason.__class__.__name__ in {"timeout", "TimeoutError"}:
            return True
        text = str(reason or exc).lower()
        return "timed out" in text or "timeout" in text
    name = exc.__class__.__name__.lower()
    return "timeout" in name


def _active_transport(override: Optional[Any]) -> Any:
    if override is not None:
        return override
    if _DEFAULT_TRANSPORT is not None:
        return _DEFAULT_TRANSPORT
    return UrlLibTransport()


def _polite_headers(contact: Optional[str]) -> Dict[str, str]:
    """User-Agent with mailto for Crossref/OpenAlex polite pool.

    No contact → header omitted (caller sends no User-Agent).
    """
    email = (contact or "").strip()
    if not email:
        return {}
    return {"User-Agent": f"{TOOL_NAME}/1.0 (mailto:{email})"}


def _complete_log(endpoint: str, params: Dict[str, Any], accessed: str) -> Dict[str, Any]:
    if not endpoint or not params or not accessed:
        raise RuntimeError("source log fields must be non-empty")
    return {"endpoint": endpoint, "params": dict(params), "accessed": accessed}


# ---------------------------------------------------------------------------
# Evidence-table schema (consumed, not owned)
# ---------------------------------------------------------------------------
def load_evidence_defaults() -> Dict[str, Any]:
    try:
        with open(EVIDENCE_TEMPLATE_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        paper = (data.get("papers") or [{}])[0]
        return {
            "schema_version": data.get("schema_version") or "1",
            "direction": paper.get("direction", None),
        }
    except OSError:
        return {"schema_version": "1", "direction": None}


def blank_item(source: str, defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    meta = defaults or load_evidence_defaults()
    return {
        "pmid": "",
        "doi": "",
        "pmcid": "",
        "title": "",
        "authors": "",
        "year": "",
        "journal": "",
        "abstract": "",
        "pubdate": "",
        "fulltext_url": "",
        "sources": [source],
        "direction": meta.get("direction", None),
    }


# ---------------------------------------------------------------------------
# PHI screen — never echo the matched value
# ---------------------------------------------------------------------------
def screen_query(query: str) -> None:
    findings = phi_detect.detect_text(query or "")
    if findings:
        rule_ids = sorted({f.get("rule_id") or "unknown" for f in findings})
        raise QueryRejectedError(rule_ids)


def _screen_search_params(params: Optional[Dict[str, Any]]) -> None:
    """Screen outbound search-string params at the HTTP boundary.

    Matched original values are never included in the exception.
    Contact/id/filter fields are not search strings and are not screened.
    """
    for key, value in (params or {}).items():
        if str(key).lower() not in _SEARCH_PARAM_KEYS:
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and item:
                    screen_query(item)
        elif isinstance(value, str) and value:
            screen_query(value)


# ---------------------------------------------------------------------------
# ID / text helpers
# ---------------------------------------------------------------------------
def normalize_doi(doi: str) -> str:
    value = (doi or "").strip()
    value = _DOI_PREFIX_RE.sub("", value)
    return value.lower().rstrip("/")


def _year_from(text: str) -> str:
    match = _YEAR_RE.search(text or "")
    return match.group(0) if match else ""


def _iso_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return value.replace("/", "-")[:10]


def _query_tokens(query: str) -> List[str]:
    return [tok for tok in _TOKEN_RE.findall((query or "").lower()) if tok not in _STOP_TOKENS]


def _tokens_match(query: str, item: Dict[str, Any]) -> bool:
    tokens = _query_tokens(query)
    if not tokens:
        return True
    blob = " ".join(
        str(item.get(k) or "") for k in ("title", "abstract", "journal", "authors")
    ).lower()
    return all(tok in blob for tok in tokens)


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").replace("  ", " ").strip()


def _pmid_from_url(value: str) -> str:
    text = (value or "").strip()
    match = re.search(r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|PMID:)\s*(\d{5,9})", text, re.I)
    if match:
        return match.group(1)
    if text.isdigit() and 5 <= len(text) <= 9:
        return text
    return ""


def _normalize_pmcid(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    match = re.search(r"(?:PMC)?(\d+)", text, re.I)
    if not match:
        return text.upper() if text.upper().startswith("PMC") else ""
    return f"PMC{match.group(1)}"


def refresh_fulltext(item: Dict[str, Any]) -> Dict[str, Any]:
    pmcid = _normalize_pmcid(item.get("pmcid") or "")
    if pmcid:
        item["pmcid"] = pmcid
        item["fulltext_url"] = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"
    elif item.get("pmid"):
        item["fulltext_url"] = f"https://pubmed.ncbi.nlm.nih.gov/{item['pmid']}/"
    elif item.get("doi"):
        item["fulltext_url"] = f"https://doi.org/{normalize_doi(item['doi'])}"
    return item


def finalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    if item.get("doi"):
        item["doi"] = normalize_doi(item["doi"])
    if not item.get("year"):
        item["year"] = _year_from(item.get("pubdate") or "")
    return refresh_fulltext(item)


# ---------------------------------------------------------------------------
# Merge / degraded
# ---------------------------------------------------------------------------
def merge_records(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    by_doi: Dict[str, Dict[str, Any]] = {}
    by_pmid: Dict[str, Dict[str, Any]] = {}

    def _index(rec: Dict[str, Any]) -> None:
        doi = normalize_doi(rec.get("doi") or "")
        pmid = (rec.get("pmid") or "").strip()
        if doi:
            by_doi[doi] = rec
        if pmid:
            by_pmid[pmid] = rec

    for rec in items:
        doi = normalize_doi(rec.get("doi") or "")
        pmid = (rec.get("pmid") or "").strip()
        existing = None
        if doi and doi in by_doi:
            existing = by_doi[doi]
        elif pmid and pmid in by_pmid:
            existing = by_pmid[pmid]
        if existing is None:
            merged.append(rec)
            _index(rec)
            continue
        _merge_into(existing, rec)
        _index(existing)
    for rec in merged:
        finalize_item(rec)
    return merged


def _merge_into(base: Dict[str, Any], extra: Dict[str, Any]) -> None:
    for key, value in extra.items():
        if key == "sources":
            for source in value or []:
                if source not in base["sources"]:
                    base["sources"].append(source)
            continue
        if key == "direction":
            continue
        if not base.get(key) and value:
            base[key] = value


def evaluate_degraded(
    successful_sources: Sequence[str],
    requested_sources: Optional[Sequence[str]] = None,
) -> Tuple[bool, str]:
    """True when pubmed is missing or fewer than 2 independent extras succeeded.

    Independent extras are europepmc, medrxiv, openalex, crossref. PMC is
    excluded (NCBI double-count). When fewer than 2 independent extras were
    requested (pubmed-only / NCBI-only), degraded is False and the reason
    field records that this is the expected result.
    """
    successful = list(successful_sources)
    requested = list(ALL_SOURCES if requested_sources is None else requested_sources)
    pubmed_ok = "pubmed" in successful
    extras_ok = [s for s in successful if s in INDEPENDENT_EXTRAS]
    extras_requested = [s for s in requested if s in INDEPENDENT_EXTRAS]
    if len(extras_requested) < 2:
        return False, (
            f"requested independent extras: {len(extras_requested)} "
            "(standalone NCBI search is an expected result)"
        )
    if pubmed_ok and len(extras_ok) >= 2:
        return False, ""
    parts: List[str] = []
    if not pubmed_ok:
        parts.append("pubmed did not succeed")
    if len(extras_ok) < 2:
        parts.append(
            f"independent extra sources succeeded: {len(extras_ok)} (need >= 2)"
        )
    return True, "; ".join(parts)


# ---------------------------------------------------------------------------
# JSON / XML helpers
# ---------------------------------------------------------------------------
def _load_json(body: bytes) -> Dict[str, Any]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SchemaError("invalid json") from exc
    if not isinstance(data, dict):
        raise SchemaError("json root must be an object")
    return data


def _parse_xml(body: bytes) -> ET.Element:
    try:
        return ET.fromstring(body)
    except ET.ParseError as exc:
        raise SchemaError("invalid xml") from exc


def _flatten_docsum(doc: ET.Element) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for child in list(doc):
        if child.tag == "Id":
            out["id"] = (child.text or "").strip()
            continue
        if child.tag != "Item":
            continue
        name = child.attrib.get("Name", "")
        typ = child.attrib.get("Type", "")
        if typ == "List":
            for sub in child.findall("./Item"):
                subname = sub.attrib.get("Name", "")
                out[subname] = (sub.text or "").strip()
        else:
            out[name] = (child.text or "").strip()
    return out


def parse_esearch_ids(body: bytes) -> List[str]:
    root = _parse_xml(body)
    return [node.text.strip() for node in root.findall("./IdList/Id") if node.text]


# ---------------------------------------------------------------------------
# Per-source parsers (fixture → unified schema)
# ---------------------------------------------------------------------------
def parse_pubmed_esummary(body: bytes, defaults: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    root = _parse_xml(body)
    items: List[Dict[str, Any]] = []
    for doc in root.findall("./DocSum"):
        raw = _flatten_docsum(doc)
        pmid = raw.get("id") or raw.get("pubmed") or ""
        item = blank_item("pubmed", defaults)
        item["pmid"] = pmid
        item["title"] = raw.get("Title") or ""
        item["pubdate"] = raw.get("PubDate") or ""
        item["journal"] = raw.get("FullJournalName") or raw.get("Source") or ""
        item["doi"] = raw.get("DOI") or raw.get("doi") or ""
        item["pmcid"] = _normalize_pmcid(raw.get("pmcid") or raw.get("PMCID") or "")
        items.append(finalize_item(item))
    return items


def parse_pubmed_efetch_abstracts(body: bytes) -> Dict[str, str]:
    root = _parse_xml(body)
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


def parse_pmc_esummary(body: bytes, defaults: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    root = _parse_xml(body)
    items: List[Dict[str, Any]] = []
    for doc in root.findall("./DocSum"):
        raw = _flatten_docsum(doc)
        item = blank_item("pmc", defaults)
        item["pmcid"] = _normalize_pmcid(raw.get("pmcid") or raw.get("id") or "")
        item["pmid"] = raw.get("pmid") or raw.get("pubmed") or ""
        item["title"] = raw.get("Title") or ""
        item["pubdate"] = raw.get("PubDate") or ""
        item["journal"] = raw.get("FullJournalName") or raw.get("Source") or ""
        item["doi"] = raw.get("DOI") or raw.get("doi") or ""
        items.append(finalize_item(item))
    return items


def parse_europepmc(body: bytes, defaults: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    data = _load_json(body)
    result_list = data.get("resultList")
    if not isinstance(result_list, dict) or "result" not in result_list:
        raise SchemaError("europepmc missing resultList.result")
    rows = result_list.get("result") or []
    if not isinstance(rows, list):
        raise SchemaError("europepmc result is not a list")
    items: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = blank_item("europepmc", defaults)
        item["pmid"] = str(row.get("pmid") or "")
        item["doi"] = str(row.get("doi") or "")
        item["pmcid"] = _normalize_pmcid(str(row.get("pmcid") or ""))
        item["title"] = str(row.get("title") or "")
        item["authors"] = str(row.get("authorString") or "")
        item["journal"] = str(row.get("journalTitle") or "")
        item["year"] = str(row.get("pubYear") or "")
        item["pubdate"] = str(row.get("firstPublicationDate") or item["year"])
        item["abstract"] = str(row.get("abstractText") or "")
        items.append(finalize_item(item))
    return items


def parse_medrxiv(body: bytes, defaults: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    data = _load_json(body)
    if "collection" not in data:
        raise SchemaError("medrxiv missing collection")
    rows = data.get("collection") or []
    if not isinstance(rows, list):
        raise SchemaError("medrxiv collection is not a list")
    items: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = blank_item("medrxiv", defaults)
        item["doi"] = str(row.get("doi") or "")
        item["title"] = str(row.get("title") or "")
        item["authors"] = str(row.get("authors") or "")
        item["pubdate"] = str(row.get("date") or "")
        item["year"] = _year_from(item["pubdate"])
        item["journal"] = "medRxiv"
        item["abstract"] = str(row.get("abstract") or "")
        items.append(finalize_item(item))
    return items


def parse_openalex(body: bytes, defaults: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    data = _load_json(body)
    if "results" not in data:
        raise SchemaError("openalex missing results")
    rows = data.get("results") or []
    if not isinstance(rows, list):
        raise SchemaError("openalex results is not a list")
    items: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ids = row.get("ids") if isinstance(row.get("ids"), dict) else {}
        loc = row.get("primary_location") if isinstance(row.get("primary_location"), dict) else {}
        source = loc.get("source") if isinstance(loc.get("source"), dict) else {}
        authorships = row.get("authorships") if isinstance(row.get("authorships"), list) else []
        authors = []
        for auth in authorships:
            if not isinstance(auth, dict):
                continue
            person = auth.get("author") if isinstance(auth.get("author"), dict) else {}
            name = person.get("display_name") or ""
            if name:
                authors.append(str(name))
        item = blank_item("openalex", defaults)
        item["doi"] = str(ids.get("doi") or row.get("doi") or "")
        item["pmid"] = _pmid_from_url(str(ids.get("pmid") or ""))
        item["pmcid"] = _normalize_pmcid(str(ids.get("pmcid") or ""))
        item["title"] = str(row.get("display_name") or row.get("title") or "")
        item["authors"] = ", ".join(authors)
        item["year"] = str(row.get("publication_year") or "")
        item["pubdate"] = str(row.get("publication_date") or item["year"])
        item["journal"] = str(source.get("display_name") or "")
        item["abstract"] = _openalex_abstract(row.get("abstract_inverted_index"))
        items.append(finalize_item(item))
    return items


def _openalex_abstract(inverted: Any) -> str:
    if not isinstance(inverted, dict) or not inverted:
        return ""
    positions: List[Tuple[int, str]] = []
    for word, idxs in inverted.items():
        if not isinstance(idxs, list):
            continue
        for idx in idxs:
            if isinstance(idx, int):
                positions.append((idx, str(word)))
    positions.sort()
    return " ".join(word for _, word in positions)


def parse_crossref(body: bytes, defaults: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    data = _load_json(body)
    message = data.get("message")
    if not isinstance(message, dict) or "items" not in message:
        raise SchemaError("crossref missing message.items")
    rows = message.get("items") or []
    if not isinstance(rows, list):
        raise SchemaError("crossref items is not a list")
    items: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title_val = row.get("title")
        if isinstance(title_val, list):
            title = str(title_val[0]) if title_val else ""
        else:
            title = str(title_val or "")
        container = row.get("container-title")
        if isinstance(container, list):
            journal = str(container[0]) if container else ""
        else:
            journal = str(container or "")
        year, pubdate = _crossref_date(row)
        item = blank_item("crossref", defaults)
        item["doi"] = str(row.get("DOI") or "")
        item["pmid"] = _crossref_pmid(row)
        item["title"] = title
        item["authors"] = _crossref_authors(row.get("author"))
        item["journal"] = journal
        item["year"] = year
        item["pubdate"] = pubdate
        item["abstract"] = _strip_tags(str(row.get("abstract") or ""))
        items.append(finalize_item(item))
    return items


def _crossref_authors(authors: Any) -> str:
    if not isinstance(authors, list):
        return ""
    names: List[str] = []
    for author in authors:
        if not isinstance(author, dict):
            continue
        given = str(author.get("given") or "").strip()
        family = str(author.get("family") or "").strip()
        name = " ".join(part for part in (given, family) if part)
        if name:
            names.append(name)
    return ", ".join(names)


def _crossref_date(row: Dict[str, Any]) -> Tuple[str, str]:
    for key in ("published-print", "published-online", "published", "created"):
        block = row.get(key)
        if not isinstance(block, dict):
            continue
        parts = block.get("date-parts") or []
        if not parts or not isinstance(parts[0], list) or not parts[0]:
            continue
        nums = [n for n in parts[0] if isinstance(n, int)]
        if not nums:
            continue
        year = str(nums[0])
        chunks = [year]
        if len(nums) >= 2:
            chunks.append(f"{nums[1]:02d}")
        if len(nums) >= 3:
            chunks.append(f"{nums[2]:02d}")
        return year, "-".join(chunks)
    return "", ""


def _crossref_pmid(row: Dict[str, Any]) -> str:
    alt = row.get("alternative-id")
    if isinstance(alt, list):
        for value in alt:
            text = str(value)
            if text.isdigit() and 5 <= len(text) <= 9:
                return text
    pmid = row.get("PMID")
    if pmid:
        return str(pmid)
    return ""


PARSERS = {
    "pubmed": parse_pubmed_esummary,
    "pmc": parse_pmc_esummary,
    "europepmc": parse_europepmc,
    "medrxiv": parse_medrxiv,
    "openalex": parse_openalex,
    "crossref": parse_crossref,
}


# ---------------------------------------------------------------------------
# HTTP dispatch for one source call
# ---------------------------------------------------------------------------
def _http_get(
    transport: Any,
    endpoint: str,
    params: Dict[str, Any],
    *,
    source: str,
    timeout: int,
    headers: Optional[Dict[str, str]] = None,
    path_url: Optional[str] = None,
) -> Tuple[HttpResponse, Dict[str, Any]]:
    _screen_search_params(params)
    url = path_url if path_url is not None else f"{endpoint}?{urlencode(params)}"
    accessed = _now()
    log = _complete_log(endpoint, params, accessed)
    try:
        response = transport.get(url, headers=headers or {}, timeout=timeout)
    except TimeoutError:
        raise SourceFailure(source, "timeout", log=log)
    except SourceFailure:
        raise
    except Exception:
        raise SourceFailure(source, "network", log=log)
    log["accessed"] = response.accessed or accessed
    if response.status >= 400:
        kind = "http_4xx" if response.status < 500 else "http_5xx"
        raise SourceFailure(source, kind, log=log)
    return response, log


def _ncbi_pause(transport: Any) -> None:
    if isinstance(transport, UrlLibTransport):
        time.sleep(0.34)


# ---------------------------------------------------------------------------
# Per-source search
# ---------------------------------------------------------------------------
def _pubmed_params(
    query: str,
    *,
    email: str,
    retmax: int,
    mindate: Optional[str],
    maxdate: Optional[str],
    db: str = "pubmed",
) -> Dict[str, str]:
    params = {
        "db": db,
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
    return params


def search_pubmed(
    query: str,
    *,
    email: str,
    retmax: int,
    mindate: Optional[str],
    maxdate: Optional[str],
    timeout: int,
    transport: Any,
    defaults: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    source = "pubmed"
    params = _pubmed_params(
        query, email=email, retmax=retmax, mindate=mindate, maxdate=maxdate, db="pubmed"
    )
    response, log = _http_get(
        transport, f"{EUTILS_BASE}/esearch.fcgi", params, source=source, timeout=timeout
    )
    try:
        ids = parse_esearch_ids(response.body)
    except SchemaError:
        raise SourceFailure(source, "schema", log=log)
    if not ids:
        return [], log
    _ncbi_pause(transport)
    sum_params = {
        "db": "pubmed",
        "id": ",".join(ids),
        "retmode": "xml",
        "tool": TOOL_NAME,
        "email": email,
    }
    sum_resp, _ = _http_get(
        transport, f"{EUTILS_BASE}/esummary.fcgi", sum_params, source=source, timeout=timeout
    )
    try:
        items = parse_pubmed_esummary(sum_resp.body, defaults)
    except SchemaError:
        raise SourceFailure(source, "schema", log=log)
    _ncbi_pause(transport)
    fetch_params = {
        "db": "pubmed",
        "id": ",".join(ids),
        "retmode": "xml",
        "tool": TOOL_NAME,
        "email": email,
    }
    try:
        fetch_resp, _ = _http_get(
            transport, f"{EUTILS_BASE}/efetch.fcgi", fetch_params, source=source, timeout=timeout
        )
        abstracts = parse_pubmed_efetch_abstracts(fetch_resp.body)
        for item in items:
            if item.get("pmid") in abstracts:
                item["abstract"] = abstracts[item["pmid"]]
    except SourceFailure:
        # Search itself succeeded; abstracts are enrichment.
        pass
    return items, log


def search_pmc(
    query: str,
    *,
    email: str,
    retmax: int,
    mindate: Optional[str],
    maxdate: Optional[str],
    timeout: int,
    transport: Any,
    defaults: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    source = "pmc"
    params = _pubmed_params(
        query, email=email, retmax=retmax, mindate=mindate, maxdate=maxdate, db="pmc"
    )
    response, log = _http_get(
        transport, f"{EUTILS_BASE}/esearch.fcgi", params, source=source, timeout=timeout
    )
    try:
        ids = parse_esearch_ids(response.body)
    except SchemaError:
        raise SourceFailure(source, "schema", log=log)
    if not ids:
        return [], log
    _ncbi_pause(transport)
    sum_params = {
        "db": "pmc",
        "id": ",".join(ids),
        "retmode": "xml",
        "tool": TOOL_NAME,
        "email": email,
    }
    sum_resp, _ = _http_get(
        transport, f"{EUTILS_BASE}/esummary.fcgi", sum_params, source=source, timeout=timeout
    )
    try:
        return parse_pmc_esummary(sum_resp.body, defaults), log
    except SchemaError:
        raise SourceFailure(source, "schema", log=log)


def search_europepmc(
    query: str,
    *,
    retmax: int,
    mindate: Optional[str],
    maxdate: Optional[str],
    timeout: int,
    transport: Any,
    defaults: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    term = query
    start = _iso_date(mindate)
    end = _iso_date(maxdate)
    if start or end:
        term = f"{query} AND FIRST_PDATE:[{start or '0000-01-01'} TO {end or '3000-01-01'}]"
    params = {
        "query": term,
        "format": "json",
        "pageSize": str(retmax),
        "resultType": "lite",
    }
    response, log = _http_get(
        transport, EUROPEPMC_SEARCH, params, source="europepmc", timeout=timeout
    )
    try:
        return parse_europepmc(response.body, defaults), log
    except SchemaError:
        raise SourceFailure("europepmc", "schema", log=log)


def search_medrxiv(
    query: str,
    *,
    retmax: int,
    mindate: Optional[str],
    maxdate: Optional[str],
    timeout: int,
    transport: Any,
    defaults: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    today = datetime.now(timezone.utc).date()
    start = _iso_date(mindate) or (today - timedelta(days=730)).isoformat()
    end = _iso_date(maxdate) or today.isoformat()
    params = {"server": "medrxiv", "from": start, "to": end, "cursor": "0", "query": query}
    path_url = f"{MEDRXIV_DETAILS}/{start}/{end}/0"
    response, log = _http_get(
        transport,
        MEDRXIV_DETAILS,
        params,
        source="medrxiv",
        timeout=timeout,
        path_url=path_url,
    )
    try:
        parsed = parse_medrxiv(response.body, defaults)
    except SchemaError:
        raise SourceFailure("medrxiv", "schema", log=log)
    items = [it for it in parsed if _tokens_match(query, it)]
    return items[:retmax], log


def search_openalex(
    query: str,
    *,
    retmax: int,
    mindate: Optional[str],
    maxdate: Optional[str],
    timeout: int,
    transport: Any,
    defaults: Dict[str, Any],
    contact: Optional[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    params: Dict[str, Any] = {"search": query, "per_page": str(min(retmax, 200))}
    filters: List[str] = []
    if mindate:
        filters.append(f"from_publication_date:{_iso_date(mindate)}")
    if maxdate:
        filters.append(f"to_publication_date:{_iso_date(maxdate)}")
    if filters:
        params["filter"] = ",".join(filters)
    response, log = _http_get(
        transport,
        OPENALEX_WORKS,
        params,
        source="openalex",
        timeout=timeout,
        headers=_polite_headers(contact),
    )
    try:
        return parse_openalex(response.body, defaults), log
    except SchemaError:
        raise SourceFailure("openalex", "schema", log=log)


def search_crossref(
    query: str,
    *,
    retmax: int,
    mindate: Optional[str],
    maxdate: Optional[str],
    timeout: int,
    transport: Any,
    defaults: Dict[str, Any],
    contact: Optional[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    params: Dict[str, Any] = {"query": query, "rows": str(min(retmax, 1000))}
    filters: List[str] = []
    if mindate:
        filters.append(f"from-pub-date:{_iso_date(mindate)}")
    if maxdate:
        filters.append(f"until-pub-date:{_iso_date(maxdate)}")
    if filters:
        params["filter"] = ",".join(filters)
    response, log = _http_get(
        transport,
        CROSSREF_WORKS,
        params,
        source="crossref",
        timeout=timeout,
        headers=_polite_headers(contact),
    )
    try:
        return parse_crossref(response.body, defaults), log
    except SchemaError:
        raise SourceFailure("crossref", "schema", log=log)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def parse_sources(raw: str) -> List[str]:
    text = (raw or "").strip().lower()
    if not text or text == "all":
        return list(ALL_SOURCES)
    requested: List[str] = []
    unknown: List[str] = []
    for part in text.split(","):
        name = part.strip().lower()
        if not name:
            continue
        if name not in ALL_SOURCES:
            unknown.append(name)
        elif name not in requested:
            requested.append(name)
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown sources: {', '.join(unknown)} (allowed: {', '.join(ALL_SOURCES)})"
        )
    if not requested:
        raise argparse.ArgumentTypeError("at least one source is required")
    return requested


def run_multi_search(
    query: str,
    *,
    email: str,
    retmax: int = 20,
    mindate: Optional[str] = None,
    maxdate: Optional[str] = None,
    sources: Optional[Sequence[str]] = None,
    contact: Optional[str] = None,
    timeout: int = 15,
    transport: Optional[Any] = None,
) -> Dict[str, Any]:
    screen_query(query)
    defaults = load_evidence_defaults()
    wanted = list(sources) if sources is not None else list(ALL_SOURCES)
    client = _active_transport(transport)

    collected: List[Dict[str, Any]] = []
    failed: List[Dict[str, str]] = []
    source_logs: Dict[str, Dict[str, Any]] = {}
    successful: List[str] = []

    for source in wanted:
        try:
            if source == "pubmed":
                items, log = search_pubmed(
                    query,
                    email=email,
                    retmax=retmax,
                    mindate=mindate,
                    maxdate=maxdate,
                    timeout=timeout,
                    transport=client,
                    defaults=defaults,
                )
            elif source == "pmc":
                items, log = search_pmc(
                    query,
                    email=email,
                    retmax=retmax,
                    mindate=mindate,
                    maxdate=maxdate,
                    timeout=timeout,
                    transport=client,
                    defaults=defaults,
                )
            elif source == "europepmc":
                items, log = search_europepmc(
                    query,
                    retmax=retmax,
                    mindate=mindate,
                    maxdate=maxdate,
                    timeout=timeout,
                    transport=client,
                    defaults=defaults,
                )
            elif source == "medrxiv":
                items, log = search_medrxiv(
                    query,
                    retmax=retmax,
                    mindate=mindate,
                    maxdate=maxdate,
                    timeout=timeout,
                    transport=client,
                    defaults=defaults,
                )
            elif source == "openalex":
                items, log = search_openalex(
                    query,
                    retmax=retmax,
                    mindate=mindate,
                    maxdate=maxdate,
                    timeout=timeout,
                    transport=client,
                    defaults=defaults,
                    contact=contact,
                )
            elif source == "crossref":
                items, log = search_crossref(
                    query,
                    retmax=retmax,
                    mindate=mindate,
                    maxdate=maxdate,
                    timeout=timeout,
                    transport=client,
                    defaults=defaults,
                    contact=contact,
                )
            else:
                continue
            source_logs[source] = log
            collected.extend(items)
            successful.append(source)
        except QueryRejectedError:
            raise
        except SourceFailure as exc:
            failed.append({"source": source, "error": exc.kind})
            if exc.log:
                source_logs[source] = exc.log
        except TimeoutError:
            failed.append({"source": source, "error": "timeout"})
        except Exception:
            failed.append({"source": source, "error": "error"})

    try:
        merged = merge_records(collected)
    except Exception:
        merged = []
        for rec in collected:
            try:
                merged.append(finalize_item(rec))
            except Exception:
                merged.append(rec)
        failed.append({"source": "merge", "error": "error"})
    degraded, reason = evaluate_degraded(successful, requested_sources=wanted)
    return {
        "schema_version": defaults["schema_version"],
        "query": query,
        "items": merged,
        "failed_sources": failed,
        "source_logs": source_logs,
        "degraded": degraded,
        "degraded_reason": reason,
        "successful_sources": successful,
    }


# ---------------------------------------------------------------------------
# Legacy NCBI helpers (CLI backward compatible)
# ---------------------------------------------------------------------------
def _request_xml(path: str, params: dict, timeout: int = 15, attempt: int = 0) -> ET.Element:
    _screen_search_params(params)
    url = f"{EUTILS_BASE}/{path}?{urlencode(params)}"
    try:
        with urlopen(url, timeout=timeout) as response:
            body = response.read()
        return ET.fromstring(body)
    except QueryRejectedError:
        raise
    except Exception as exc:
        if attempt < len(BACKOFF_GAPS):
            wait = BACKOFF_GAPS[attempt]
            status = getattr(exc, "code", None)
            if not isinstance(status, int):
                status = getattr(exc, "status", None)
            if isinstance(status, int):
                print(
                    f"  Retry {attempt + 1} after {wait}s: pubmed HTTP {status}",
                    file=sys.stderr,
                )
            else:
                print(f"  Retry {attempt + 1} after {wait}s: pubmed", file=sys.stderr)
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
    params = _pubmed_params(
        query, email=email, retmax=retmax, mindate=mindate, maxdate=maxdate, db="pubmed"
    )
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
    return parse_pubmed_efetch_abstracts(ET.tostring(root, encoding="utf-8"))


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------
def _item_filename(item: Dict[str, Any]) -> str:
    if item.get("pmid"):
        return f"{item['pmid']}.json"
    if item.get("pmcid"):
        return f"{item['pmcid']}.json"
    doi = normalize_doi(item.get("doi") or "")
    if doi:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", doi)[:80]
        return f"{slug}.json"
    return "item.json"


def write_search_outputs(
    output_dir: str,
    args: argparse.Namespace,
    result: Dict[str, Any],
    *,
    pmids: Optional[List[str]] = None,
    summaries: Optional[Dict[str, PubMedItem]] = None,
) -> None:
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, "items"), exist_ok=True)

    envelope_path = os.path.join(output_dir, "literature.json")
    with open(envelope_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)

    if summaries:
        payload = [asdict(summaries[pmid]) for pmid in (pmids or []) if pmid in summaries]
        for pmid, item in summaries.items():
            with open(os.path.join(output_dir, "items", f"{pmid}.json"), "w", encoding="utf-8") as handle:
                json.dump(asdict(item), handle, indent=2, ensure_ascii=False)
    else:
        payload = result.get("items") or []
        used_names = set()
        for item in payload:
            name = _item_filename(item)
            if name in used_names:
                stem, ext = os.path.splitext(name)
                suffix = 2
                while f"{stem}_{suffix}{ext}" in used_names:
                    suffix += 1
                name = f"{stem}_{suffix}{ext}"
            used_names.add(name)
            with open(os.path.join(output_dir, "items", name), "w", encoding="utf-8") as handle:
                json.dump(item, handle, indent=2, ensure_ascii=False)

    summary_path = os.path.join(output_dir, "search-results.json")
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)

    queries_path = os.path.join(output_dir, "queries.json")
    existing_queries: List[Any] = []
    if os.path.exists(queries_path):
        with open(queries_path, encoding="utf-8") as handle:
            existing_queries = json.load(handle)

    existing_queries.append({
        "query": args.query,
        "retmax": args.retmax,
        "mindate": args.mindate,
        "maxdate": args.maxdate,
        "executed_at": datetime.now().isoformat(),
        "result_count": len(pmids) if pmids is not None else len(payload),
        "pmids": pmids if pmids is not None else [
            it.get("pmid") for it in payload if it.get("pmid")
        ],
        "sources": getattr(args, "sources", list(ALL_SOURCES)),
        "degraded": result.get("degraded"),
        "failed_sources": result.get("failed_sources") or [],
    })
    with open(queries_path, "w", encoding="utf-8") as handle:
        json.dump(existing_queries, handle, indent=2, ensure_ascii=False)


def run_legacy_pubmed(args: argparse.Namespace) -> Dict[str, Any]:
    """Existing PubMed-only path: esearch → esummary → efetch, same files as before."""
    print("Searching pubmed")
    pmids = search_pmids(
        args.query,
        email=args.email,
        retmax=args.retmax,
        mindate=args.mindate,
        maxdate=args.maxdate,
    )
    summaries: Dict[str, PubMedItem] = {}
    if pmids:
        print(f"Fetching summaries for {len(pmids)} articles...")
        time.sleep(1)
        summaries = fetch_summaries(pmids, email=args.email)
        print("Fetching abstracts...")
        time.sleep(1)
        abstracts = fetch_abstracts(pmids, email=args.email)
        for pmid, abstract in abstracts.items():
            if pmid in summaries:
                summaries[pmid].abstract = abstract

    defaults = load_evidence_defaults()
    items = []
    for pmid in pmids:
        rec = summaries.get(pmid)
        if rec is None:
            continue
        item = blank_item("pubmed", defaults)
        item["pmid"] = rec.pmid
        item["title"] = rec.title
        item["pubdate"] = rec.pubdate
        item["journal"] = rec.journal
        item["abstract"] = rec.abstract
        items.append(finalize_item(item))

    result = {
        "schema_version": defaults["schema_version"],
        "query": args.query,
        "items": items,
        "failed_sources": [],
        "source_logs": {
            "pubmed": _complete_log(
                f"{EUTILS_BASE}/esearch.fcgi",
                _pubmed_params(
                    args.query,
                    email=args.email,
                    retmax=args.retmax,
                    mindate=args.mindate,
                    maxdate=args.maxdate,
                ),
                _now(),
            )
        },
        "successful_sources": ["pubmed"],
    }
    degraded, reason = evaluate_degraded(
        result["successful_sources"], requested_sources=["pubmed"]
    )
    result["degraded"] = degraded
    result["degraded_reason"] = reason
    write_search_outputs(args.output, args, result, pmids=pmids, summaries=summaries)
    print(f"Saved {len(summaries)} articles to {args.output}")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search PubMed and save results")
    parser.add_argument("--query", required=True, help="PubMed search query")
    parser.add_argument("--email", required=True, help="Email for NCBI E-utilities")
    parser.add_argument("--retmax", type=int, default=20, help="Max results (default: 20)")
    parser.add_argument("--mindate", help="Minimum publication date (YYYY/MM/DD)")
    parser.add_argument("--maxdate", help="Maximum publication date (YYYY/MM/DD)")
    parser.add_argument("--output", required=True, help="Output directory for results")
    parser.add_argument(
        "--sources",
        default="all",
        type=parse_sources,
        help="Comma-separated sources or 'all' (default). Use 'pubmed' for the legacy path.",
    )
    parser.add_argument(
        "--contact",
        default=None,
        help="Contact email for OpenAlex/Crossref polite-pool User-Agent (omitted if unset)",
    )
    parser.add_argument("--timeout", type=int, default=15, help="Per-request timeout seconds")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        screen_query(args.query)
    except QueryRejectedError as exc:
        print(
            json.dumps({"error": "phi_query_rejected", "rule_ids": exc.rule_ids}),
            file=sys.stderr,
        )
        return 2

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(os.path.join(args.output, "items"), exist_ok=True)

    sources: List[str] = list(args.sources)
    pubmed_only = sources == ["pubmed"]

    if pubmed_only:
        run_legacy_pubmed(args)
        return 0

    result = run_multi_search(
        args.query,
        email=args.email,
        retmax=args.retmax,
        mindate=args.mindate,
        maxdate=args.maxdate,
        sources=sources,
        contact=args.contact,
        timeout=args.timeout,
    )
    write_search_outputs(args.output, args, result)
    n_ok = len(result.get("successful_sources") or [])
    print(
        f"Saved {len(result.get('items') or [])} articles "
        f"({n_ok} sources ok) to {args.output}"
    )
    if result.get("degraded"):
        print(f"degraded: {result.get('degraded_reason')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
