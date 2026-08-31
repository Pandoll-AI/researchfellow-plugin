"""pubmed_search.py — multi-source literature search.

Verification (network-free; fixture responses only):
  (1) per-source parse → unified schema
  (2) failure isolation: one source errors, others survive
  (3) repro log fields endpoint/params/accessed are non-empty
  (4) degraded=true when only PubMed + 1 extra source succeed
  (5) PHI query (RRN) is blocked, never echoed
  (6) DOI-based cross-source merge
  (7) legacy PubMed-only CLI args still work
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

import pytest

from pathlib import Path

import pubmed_search

ROOT = Path(__file__).resolve().parent
FIXTURE_DIR = ROOT / "fixtures"

DOI_SHARED = "10.1000/rf-lit-001"
QUERY = "sepsis statin mortality"

_RRN_WEIGHTS = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]


def _valid_rrn(first12: str) -> str:
    total = sum(int(first12[i]) * _RRN_WEIGHTS[i] for i in range(12))
    check = (11 - (total % 11)) % 10
    return first12 + str(check)


RRN = _valid_rrn("900101123456")
RRN_DISPLAY = f"{RRN[:6]}-{RRN[6:]}"


class FixtureTransport:
    """Dispatch by URL to litsearch_* fixtures. `errors` maps source → status int or kind."""

    accessed = "2026-09-01T00:00:00Z"

    def __init__(self, errors: Optional[Dict[str, Any]] = None):
        self.errors = dict(errors or {})
        self.calls: list = []

    def get(self, url: str, *, headers: Optional[Dict[str, str]] = None, timeout: int = 15):
        self.calls.append({"url": url, "headers": dict(headers or {}), "timeout": timeout})
        source, kind = classify_url(url)
        err = self.errors.get(source)
        if err == "timeout":
            raise TimeoutError("timeout")
        if isinstance(err, int):
            return pubmed_search.HttpResponse(err, b"", self.accessed)
        if err == "schema":
            body = (FIXTURE_DIR / "litsearch_openalex_bad.json").read_bytes()
            return pubmed_search.HttpResponse(200, body, self.accessed)
        body = load_fixture(source, kind)
        return pubmed_search.HttpResponse(200, body, self.accessed)


def classify_url(url: str):
    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path
    qs = parse_qs(parsed.query)
    db = (qs.get("db") or [""])[0]
    if "europepmc" in host or "europepmc" in path:
        return "europepmc", "search"
    if "openalex.org" in host:
        return "openalex", "works"
    if "crossref.org" in host:
        return "crossref", "works"
    if "biorxiv.org" in host or "medrxiv.org" in host:
        return "medrxiv", "details"
    if "eutils.ncbi.nlm.nih.gov" in host:
        source = "pmc" if db == "pmc" else "pubmed"
        if "esearch" in path:
            return source, "esearch"
        if "esummary" in path:
            return source, "esummary"
        if "efetch" in path:
            return source, "efetch"
        return source, "other"
    return "unknown", "other"


def load_fixture(source: str, kind: str) -> bytes:
    names = {
        ("pubmed", "esearch"): "litsearch_pubmed_esearch.xml",
        ("pubmed", "esummary"): "litsearch_pubmed_esummary.xml",
        ("pubmed", "efetch"): "litsearch_pubmed_efetch.xml",
        ("pmc", "esearch"): "litsearch_pmc_esearch.xml",
        ("pmc", "esummary"): "litsearch_pmc_esummary.xml",
        ("europepmc", "search"): "litsearch_europepmc.json",
        ("medrxiv", "details"): "litsearch_medrxiv.json",
        ("openalex", "works"): "litsearch_openalex.json",
        ("crossref", "works"): "litsearch_crossref.json",
    }
    filename = names.get((source, kind))
    if not filename:
        raise AssertionError(f"no fixture for {source}/{kind}")
    return (FIXTURE_DIR / filename).read_bytes()


def run_search(**kwargs):
    params = dict(
        query=QUERY,
        email="dev@example.com",
        retmax=20,
        transport=kwargs.pop("transport", FixtureTransport()),
    )
    params.update(kwargs)
    return pubmed_search.run_multi_search(**params)


# ---------------------------------------------------------------------------
# (1) per-source parse / normalize contract
# ---------------------------------------------------------------------------
PARSE_CASES = (
    ("pubmed", "litsearch_pubmed_esummary.xml", pubmed_search.parse_pubmed_esummary),
    ("pmc", "litsearch_pmc_esummary.xml", pubmed_search.parse_pmc_esummary),
    ("europepmc", "litsearch_europepmc.json", pubmed_search.parse_europepmc),
    ("medrxiv", "litsearch_medrxiv.json", pubmed_search.parse_medrxiv),
    ("openalex", "litsearch_openalex.json", pubmed_search.parse_openalex),
    ("crossref", "litsearch_crossref.json", pubmed_search.parse_crossref),
)


@pytest.mark.parametrize("source,filename,parser", PARSE_CASES)
def test_1_source_parse_normalize_contract(source, filename, parser):
    body = (FIXTURE_DIR / filename).read_bytes()
    items = parser(body)
    assert items, f"{source} parser returned no items"
    for item in items:
        missing = [k for k in pubmed_search.ITEM_SCHEMA_KEYS if k not in item]
        assert not missing, f"{source} missing keys {missing}"
        assert item["sources"] == [source]
        assert item["direction"] is None
        assert item["title"], f"{source} title empty"
        # identifiers: at least one of doi/pmid/pmcid
        assert item["doi"] or item["pmid"] or item["pmcid"]


def test_1_evidence_template_fields_consumed():
    defaults = pubmed_search.load_evidence_defaults()
    assert defaults["schema_version"] == "1"
    assert "direction" in defaults
    item = pubmed_search.blank_item("pubmed", defaults)
    assert item["direction"] == defaults["direction"]


# ---------------------------------------------------------------------------
# (2) failure isolation
# ---------------------------------------------------------------------------
def test_2_failure_isolation_http_error_keeps_other_sources():
    transport = FixtureTransport(errors={"openalex": 429})
    result = run_search(transport=transport)
    failed_names = [row["source"] for row in result["failed_sources"]]
    assert failed_names == ["openalex"]
    assert "pubmed" in result["successful_sources"]
    assert "crossref" in result["successful_sources"]
    assert result["items"], "remaining sources should still yield items"
    assert any(DOI_SHARED in (it.get("doi") or "") for it in result["items"])


def test_2_failure_isolation_schema_error():
    transport = FixtureTransport(errors={"openalex": "schema"})
    result = run_search(transport=transport)
    assert any(row["source"] == "openalex" and row["error"] == "schema" for row in result["failed_sources"])
    assert "europepmc" in result["successful_sources"]
    assert result["items"]


def test_2_failure_isolation_timeout():
    transport = FixtureTransport(errors={"medrxiv": "timeout"})
    result = run_search(transport=transport)
    assert any(row["source"] == "medrxiv" and row["error"] == "timeout" for row in result["failed_sources"])
    assert "pubmed" in result["successful_sources"]
    assert result["items"]


# ---------------------------------------------------------------------------
# (3) repro log
# ---------------------------------------------------------------------------
def test_3_repro_log_three_fields_nonempty():
    result = run_search()
    assert result["source_logs"], "expected a log entry per attempted source"
    for source, log in result["source_logs"].items():
        assert log.get("endpoint"), f"{source} endpoint empty"
        assert log.get("params"), f"{source} params empty"
        assert log.get("accessed"), f"{source} accessed empty"


def test_3_repro_log_present_for_failed_source():
    transport = FixtureTransport(errors={"crossref": 404})
    result = run_search(transport=transport)
    log = result["source_logs"]["crossref"]
    assert log["endpoint"]
    assert log["params"]
    assert log["accessed"]


# ---------------------------------------------------------------------------
# (4) degraded
# ---------------------------------------------------------------------------
def test_4_degraded_when_only_pubmed_plus_one_extra():
    transport = FixtureTransport(
        errors={
            "europepmc": 500,
            "medrxiv": "timeout",
            "openalex": 429,
            "crossref": "schema",
        }
    )
    result = run_search(transport=transport)
    assert set(result["successful_sources"]) == {"pubmed", "pmc"}
    assert result["degraded"] is True
    assert result["degraded_reason"]


def test_4_not_degraded_when_pubmed_plus_two_extra():
    transport = FixtureTransport(
        errors={"medrxiv": 500, "openalex": 500, "crossref": 500}
    )
    result = run_search(transport=transport)
    assert "pubmed" in result["successful_sources"]
    extra = [s for s in result["successful_sources"] if s != "pubmed"]
    assert len(extra) >= 2
    assert result["degraded"] is False


# ---------------------------------------------------------------------------
# (5) PHI screening
# ---------------------------------------------------------------------------
def test_5_rrn_query_is_blocked_not_sent_not_echoed():
    transport = FixtureTransport()
    query = f"{QUERY} patient {RRN_DISPLAY}"
    with pytest.raises(pubmed_search.QueryRejectedError) as caught:
        run_search(query=query, transport=transport)
    assert transport.calls == []
    message = str(caught.value)
    assert RRN not in message
    assert RRN_DISPLAY not in message
    assert RRN[:6] not in message
    assert "phi" in message.lower() or "PHI" in message
    assert "krn_rrn" in caught.value.rule_ids


def test_5_cli_phi_reject_does_not_echo(tmp_path, capsys):
    query = f"{QUERY} {RRN_DISPLAY}"
    rc = pubmed_search.main(
        ["--query", query, "--email", "dev@example.com", "--output", str(tmp_path)]
    )
    assert rc == 2
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    assert RRN not in blob
    assert RRN_DISPLAY not in blob
    payload = json.loads(captured.err.strip().splitlines()[-1])
    assert payload["error"] == "phi_query_rejected"
    assert "krn_rrn" in payload["rule_ids"]
    assert not (tmp_path / "search-results.json").exists()


# ---------------------------------------------------------------------------
# (6) DOI merge
# ---------------------------------------------------------------------------
def test_6_doi_dedup_merges_sources():
    result = run_search()
    matches = [it for it in result["items"] if pubmed_search.normalize_doi(it.get("doi") or "") == DOI_SHARED]
    assert len(matches) == 1, f"expected one merged record, got {len(matches)}"
    sources = set(matches[0]["sources"])
    assert {"pubmed", "europepmc", "openalex", "crossref"} <= sources
    dois = [pubmed_search.normalize_doi(it.get("doi") or "") for it in result["items"] if it.get("doi")]
    assert dois.count(DOI_SHARED) == 1


def test_6_distinct_doi_not_collapsed():
    result = run_search()
    dois = {pubmed_search.normalize_doi(it.get("doi") or "") for it in result["items"] if it.get("doi")}
    assert DOI_SHARED in dois
    assert "10.1000/rf-lit-002" in dois
    assert "10.1101/2020.01.01.20001001" in dois


# ---------------------------------------------------------------------------
# (7) legacy PubMed-only path
# ---------------------------------------------------------------------------
def test_7_legacy_cli_flags_still_parse():
    parser = pubmed_search.build_parser()
    args = parser.parse_args(
        [
            "--query", "diabetes AND metformin",
            "--email", "user@example.com",
            "--retmax", "20",
            "--output", "/tmp/literature",
        ]
    )
    assert args.query == "diabetes AND metformin"
    assert args.email == "user@example.com"
    assert args.retmax == 20
    assert args.output == "/tmp/literature"
    assert args.mindate is None
    assert args.maxdate is None


def test_7_legacy_search_pmids_summaries_abstracts(monkeypatch):
    def fake_request(path, params, timeout=15, attempt=0):
        if path == "esearch.fcgi":
            return ET.fromstring((FIXTURE_DIR / "litsearch_pubmed_esearch.xml").read_bytes())
        if path == "esummary.fcgi":
            return ET.fromstring((FIXTURE_DIR / "litsearch_pubmed_esummary.xml").read_bytes())
        if path == "efetch.fcgi":
            return ET.fromstring((FIXTURE_DIR / "litsearch_pubmed_efetch.xml").read_bytes())
        raise AssertionError(path)

    monkeypatch.setattr(pubmed_search, "_request_xml", fake_request)
    pmids = pubmed_search.search_pmids("sepsis", email="user@example.com", retmax=20)
    assert pmids == ["11111111"]
    summaries = pubmed_search.fetch_summaries(pmids, email="user@example.com")
    assert "11111111" in summaries
    item = summaries["11111111"]
    assert item.pmid == "11111111"
    assert item.title.startswith("Statins")
    abstracts = pubmed_search.fetch_abstracts(pmids, email="user@example.com")
    assert "sepsis" in abstracts["11111111"].lower()


def test_7_legacy_cli_pubmed_only_writes_original_files(tmp_path, monkeypatch):
    def fake_request(path, params, timeout=15, attempt=0):
        mapping = {
            "esearch.fcgi": "litsearch_pubmed_esearch.xml",
            "esummary.fcgi": "litsearch_pubmed_esummary.xml",
            "efetch.fcgi": "litsearch_pubmed_efetch.xml",
        }
        return ET.fromstring((FIXTURE_DIR / mapping[path]).read_bytes())

    monkeypatch.setattr(pubmed_search, "_request_xml", fake_request)
    monkeypatch.setattr(pubmed_search.time, "sleep", lambda *_a, **_k: None)
    rc = pubmed_search.main(
        [
            "--query", QUERY,
            "--email", "user@example.com",
            "--retmax", "20",
            "--output", str(tmp_path),
            "--sources", "pubmed",
        ]
    )
    assert rc == 0
    results = json.loads((tmp_path / "search-results.json").read_text())
    assert isinstance(results, list)
    assert results[0]["pmid"] == "11111111"
    assert set(results[0]) >= {"pmid", "title", "pubdate", "journal", "abstract"}
    queries = json.loads((tmp_path / "queries.json").read_text())
    assert queries[-1]["query"] == QUERY
    assert queries[-1]["pmids"] == ["11111111"]
    assert (tmp_path / "items" / "11111111.json").is_file()


def test_7_default_args_without_new_flags_still_run(tmp_path, monkeypatch):
    transport = FixtureTransport()
    monkeypatch.setattr(pubmed_search, "_DEFAULT_TRANSPORT", transport)
    rc = pubmed_search.main(
        [
            "--query", QUERY,
            "--email", "user@example.com",
            "--output", str(tmp_path),
        ]
    )
    assert rc == 0
    assert (tmp_path / "search-results.json").is_file()
    assert (tmp_path / "queries.json").is_file()
    assert (tmp_path / "literature.json").is_file()
    assert transport.calls, "multi-source default should hit the fixture transport"


def test_contact_header_omitted_without_contact():
    transport = FixtureTransport()
    run_search(transport=transport, contact=None)
    oa = [c for c in transport.calls if "openalex.org" in c["url"]]
    cr = [c for c in transport.calls if "crossref.org" in c["url"]]
    assert oa and "User-Agent" not in oa[0]["headers"]
    assert cr and "User-Agent" not in cr[0]["headers"]


def test_contact_header_present_when_set():
    transport = FixtureTransport()
    run_search(transport=transport, contact="lab@example.com")
    oa = [c for c in transport.calls if "openalex.org" in c["url"]][0]
    assert "mailto:lab@example.com" in oa["headers"]["User-Agent"]
