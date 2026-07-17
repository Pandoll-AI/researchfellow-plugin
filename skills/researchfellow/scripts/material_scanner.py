#!/usr/bin/env python3
"""Material scanner for the ResearchFellow skill (FR-T5, FR-M pipeline Stage 0-1).

Offline, stdlib-only intake scanner. Detects file format (extension + magic
bytes), performs a lightweight format-specific structure scan, applies the
FR-M4 role heuristics, pre-computes lineage (duplicates / version groups),
copies originals into an immutable `00_materials/` store for v3 projects, and emits a scan-report
JSON that Stage 2 (host-LLM batch classification) consumes.

It NEVER extracts PDF text (that is delegated to the host LLM Read tool) and
NEVER calls the network. Optional `--phi-screen` shells out to phi_screener.py
for tabular files. Scanner-produced text excerpts (docx / md / txt / code,
headings included) are ALWAYS masked through the phi_detect engine before they
enter the scan report — independent of --phi-screen — because excerpts feed
the host-LLM Stage 2 batch classification.

Usage:
    python3 material_scanner.py \
        --input data/ --input notes.docx \
        --paste-refs "PMID:38812345, 10.1001/jama.2024.1234" \
        --project-dir research/ [--no-copy] [--phi-screen] \
        --output research/.system/scan-report.json

Exit codes:
    0  ok
    1  input error
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from rf_paths import MATERIALS_DIR, resolve_materials_dir, resolve_system_file

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# PHI engine — soft import, deliberately unlike phi_screener's hard import:
# a batch intake scan must not abort wholesale over one missing module. The
# fallback is still fail-closed PER FILE: without the engine, scanner-produced
# excerpts are withheld ("" + excerpt_source="unscreened"), never emitted raw.
sys.path.insert(0, SCRIPT_DIR)
try:
    import phi_detect
except ImportError:  # pragma: no cover - both files ship together
    phi_detect = None

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")
PMID_RE = re.compile(r"PMID[:\s]*?(\d{6,9})", re.IGNORECASE)
STAT_COL_RE = re.compile(r"(p[-_ ]?value|\bci\b|95\s*%|\bhr\b|\bor\b|estimate|hazard|odds)", re.IGNORECASE)
ID_NAME_RE = re.compile(r"(\bid\b|_id\b|^id|patient|subject|record|mrn|\bno\b|\bnum\b|key)", re.IGNORECASE)
SQL_RE = re.compile(r"\bSELECT\b[\s\S]{0,4000}?\bFROM\b", re.IGNORECASE)
ANALYSIS_LIB_RE = re.compile(
    r"\b(survival|coxph|lifelines|statsmodels|glm|lmer|lme4|sklearn|scikit|tableone|"
    r"proc\s+(logistic|phreg|glm|mixed)|geeglm|survfit|km\.fit|Surv\()\b",
    re.IGNORECASE,
)

TEXT_EXCERPT_LEN = 3000
MD_HEADER_RE = re.compile(r"^#{1,3}\s+(.*)$", re.MULTILINE)

# ---------------------------------------------------------------------------
# Extension -> (format, subtype)   format in {tabular,document,bibliographic,code,image,archive}
# ---------------------------------------------------------------------------
EXT_MAP: Dict[str, Tuple[str, str]] = {
    ".csv": ("tabular", "csv"), ".tsv": ("tabular", "csv"),
    ".xlsx": ("tabular", "xlsx"), ".xlsm": ("tabular", "xlsx"),
    ".xls": ("tabular", "xls"), ".sav": ("tabular", "spss"),
    ".dta": ("tabular", "stata"), ".parquet": ("tabular", "parquet"),
    ".pdf": ("document", "pdf"), ".docx": ("document", "docx"),
    ".doc": ("document", "doc"), ".hwp": ("document", "hwp"),
    ".md": ("document", "md"), ".markdown": ("document", "md"),
    ".txt": ("document", "txt"), ".rtf": ("document", "rtf"),
    ".ris": ("bibliographic", "ris"), ".bib": ("bibliographic", "bib"),
    ".nbib": ("bibliographic", "nbib"),
    ".py": ("code", "py"), ".r": ("code", "r"), ".sql": ("code", "sql"),
    ".do": ("code", "do"), ".ipynb": ("code", "ipynb"), ".sas": ("code", "sas"),
    ".png": ("image", "png"), ".jpg": ("image", "jpg"), ".jpeg": ("image", "jpg"),
    ".tif": ("image", "tiff"), ".tiff": ("image", "tiff"), ".gif": ("image", "gif"),
    ".zip": ("archive", "zip"), ".tar": ("archive", "tar"),
    ".gz": ("archive", "gz"), ".tgz": ("archive", "tar"),
}

CODE_EXTS = {".py", ".r", ".sql", ".do", ".ipynb", ".sas"}


# ---------------------------------------------------------------------------
# Stage 0 — format detection (extension + magic bytes)
# ---------------------------------------------------------------------------
def _read_head(path: str, n: int = 8) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def _looks_binary(path: str) -> bool:
    with open(path, "rb") as f:
        chunk = f.read(4096)
    if b"\x00" in chunk:
        return True
    try:
        chunk.decode("utf-8")
        return False
    except UnicodeDecodeError:
        # allow latin-1-ish text; treat undecodable as binary
        return True


def _zip_members(path: str) -> List[str]:
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.namelist()
    except Exception:
        return []


def detect_format(path: str) -> Tuple[str, str, Dict[str, Any]]:
    """Return (format, subtype, evidence)."""
    ext = os.path.splitext(path)[1].lower()
    head = _read_head(path, 8)
    evidence: Dict[str, Any] = {"extension": ext, "magic": head[:4].hex()}

    # PDF magic
    if head[:4] == b"%PDF":
        evidence["magic_match"] = "%PDF"
        return "document", "pdf", evidence

    # ZIP-container magic: could be xlsx / docx / pptx / plain zip
    if head[:4] == b"PK\x03\x04":
        members = _zip_members(path)
        evidence["magic_match"] = "PK\\x03\\x04"
        joined = "\n".join(members)
        if any(m.startswith("word/") for m in members):
            evidence["zip_hint"] = "word/"
            return "document", "docx", evidence
        if any(m.startswith("xl/") for m in members):
            evidence["zip_hint"] = "xl/"
            return "tabular", "xlsx", evidence
        if any(m.startswith("ppt/") for m in members):
            evidence["zip_hint"] = "ppt/"
            return "document", "pptx", evidence
        evidence["zip_hint"] = "generic"
        return "archive", "zip", evidence

    # tar magic (ustar at offset 257) — cheap check via ext, fall through otherwise
    if ext in (".tar", ".tgz", ".gz"):
        evidence["magic_match"] = "ext-archive"
        return "archive", EXT_MAP.get(ext, ("archive", "tar"))[1], evidence

    # extension-driven with text/binary sanity
    if ext in EXT_MAP:
        fmt, sub = EXT_MAP[ext]
        evidence["source"] = "extension"
        # a text-looking file with a binary ext (e.g. renamed) — trust ext but note
        return fmt, sub, evidence

    # unknown extension: decide by text/binary
    if _looks_binary(path):
        evidence["source"] = "binary-fallback"
        return "archive", "unknown-binary", evidence
    evidence["source"] = "text-fallback"
    return "document", "txt", evidence


# ---------------------------------------------------------------------------
# Helpers: hashing, text read
# ---------------------------------------------------------------------------
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_text(path: str, limit: Optional[int] = None) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read() if limit is None else f.read(limit)


# ---------------------------------------------------------------------------
# Tabular profiling (shared by csv + xlsx)
# ---------------------------------------------------------------------------
def _guess_dtype(values: List[str]) -> str:
    sample = [v for v in values if str(v).strip() != ""][:200]
    if not sample:
        return "empty"
    n = len(sample)
    ints = floats = dates = 0
    date_re = re.compile(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}$|^\d{8}$")
    for v in sample:
        s = str(v).strip()
        if date_re.match(s):
            dates += 1
            continue
        try:
            int(s)
            ints += 1
            continue
        except ValueError:
            pass
        try:
            float(s)
            floats += 1
        except ValueError:
            pass
    if dates / n >= 0.7:
        return "date"
    if ints / n >= 0.9:
        return "integer"
    if (ints + floats) / n >= 0.9:
        return "float"
    return "string"


def profile_columns(header: List[str], body: List[List[str]]) -> List[Dict[str, Any]]:
    cols: List[Dict[str, Any]] = []
    n = len(body)
    for i, name in enumerate(header):
        values = [(row[i] if i < len(row) else "") for row in body]
        nonempty = [v for v in values if str(v).strip() != ""]
        missing = n - len(nonempty)
        unique_ratio = round(len(set(nonempty)) / len(nonempty), 4) if nonempty else 0.0
        cols.append({
            "name": name,
            "dtype": _guess_dtype(values),
            "missing_rate": round(missing / n, 4) if n else 0.0,
            "unique_ratio": unique_ratio,
        })
    return cols


def _tabular_role_hint(n_rows: int, columns: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Apply FR-M4 heuristics. Returns (rule_role_hint, id_col_candidates, stat_col_hits)."""
    stat_col_hits = [c["name"] for c in columns if STAT_COL_RE.search(str(c["name"]))]
    id_col_candidates = [
        c["name"] for c in columns
        if c["unique_ratio"] > 0.95 and (ID_NAME_RE.search(str(c["name"])) or c["unique_ratio"] >= 0.999)
    ]

    hint: Dict[str, Any] = {"role": None, "rule": None, "certainty": "none"}
    if n_rows <= 200 and stat_col_hits:
        hint = {
            "role": "analysis_output",
            "rule": "n_rows<=200 & statistical result columns present",
            "certainty": "strong",
        }
    elif n_rows >= 500 and id_col_candidates:
        hint = {
            "role": "raw_dataset",
            "rule": "n_rows>=500 & high-uniqueness ID column present",
            "certainty": "strong",
        }
    return hint, id_col_candidates, stat_col_hits


# ---------------------------------------------------------------------------
# XLSX reader (zipfile + xml, first sheet, capped rows) — pandas forbidden
# ---------------------------------------------------------------------------
def _xlsx_shared_strings(zf: zipfile.ZipFile) -> List[str]:
    strings: List[str] = []
    try:
        data = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return strings
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    root = ET.fromstring(data)
    for si in root.findall(f"{ns}si"):
        strings.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
    return strings


def _load_xlsx(path: str, max_rows: int = 200) -> Tuple[List[str], List[List[str]]]:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as zf:
        shared = _xlsx_shared_strings(zf)
        sheets = sorted(n for n in zf.namelist() if n.startswith("xl/worksheets/") and n.endswith(".xml"))
        sheet_path = sheets[0] if sheets else "xl/worksheets/sheet1.xml"
        root = ET.fromstring(zf.read(sheet_path))

    def col_index(ref: str) -> int:
        m = re.match(r"[A-Z]+", ref or "")
        if not m:
            return 0
        idx = 0
        for ch in m.group(0):
            idx = idx * 26 + (ord(ch) - ord("A") + 1)
        return idx - 1

    sheet_data = root.find(f"{ns}sheetData")
    if sheet_data is None:
        return [], []
    rows_out: List[List[str]] = []
    for row in sheet_data.findall(f"{ns}row"):
        cells: Dict[int, str] = {}
        max_c = -1
        for c in row.findall(f"{ns}c"):
            ci = col_index(c.get("r", ""))
            ctype = c.get("t")
            text = ""
            if ctype == "s":
                v = c.find(f"{ns}v")
                if v is not None and v.text is not None:
                    si = int(v.text)
                    if 0 <= si < len(shared):
                        text = shared[si]
            elif ctype == "inlineStr":
                is_el = c.find(f"{ns}is")
                if is_el is not None:
                    text = "".join(t.text or "" for t in is_el.iter(f"{ns}t"))
            else:
                v = c.find(f"{ns}v")
                if v is not None and v.text is not None:
                    text = v.text
            cells[ci] = text
            max_c = max(max_c, ci)
        rows_out.append([cells.get(i, "") for i in range(max_c + 1)])
        if len(rows_out) > max_rows:
            break
    if not rows_out:
        return [], []
    header = [str(c) for c in rows_out[0]]
    width = len(header)
    body = [([*(r), *([""] * width)])[:width] for r in rows_out[1:]]
    return header, body


# ---------------------------------------------------------------------------
# Stage 1 scanners
# ---------------------------------------------------------------------------
def scan_csv(path: str) -> Dict[str, Any]:
    import csv

    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        # sniff delimiter for .tsv
        sample = f.read(8192)
        f.seek(0)
        delim = "\t" if path.lower().endswith(".tsv") or sample.count("\t") > sample.count(",") else ","
        reader = csv.reader(f, delimiter=delim)
        rows = list(reader)
    if not rows:
        return {"structure": _empty_structure(), "identifiers": _empty_ids(),
                "rule_role_hint": {"role": None, "rule": None, "certainty": "none"},
                "needs_llm": True, "excerpt_source": None}
    header = [str(c) for c in rows[0]]
    body = [[str(c) for c in r] for r in rows[1:]]
    columns = profile_columns(header, body)
    hint, id_cands, stat_hits = _tabular_role_hint(len(body), columns)
    structure = {
        "n_rows": len(body),
        "n_cols": len(header),
        "columns": columns,
        "id_col_candidates": id_cands,
        "stat_col_hits": stat_hits,
    }
    return {
        "structure": structure,
        "identifiers": _empty_ids(),
        "rule_role_hint": hint,
        "needs_llm": hint["certainty"] != "strong",
        "excerpt_source": None,
    }


def scan_xlsx(path: str) -> Dict[str, Any]:
    try:
        header, body = _load_xlsx(path)
    except Exception:
        return {"structure": _empty_structure(), "identifiers": _empty_ids(),
                "rule_role_hint": {"role": None, "rule": None, "certainty": "none"},
                "needs_llm": True, "excerpt_source": None}
    columns = profile_columns(header, body)
    hint, id_cands, stat_hits = _tabular_role_hint(len(body), columns)
    structure = {
        "n_rows": len(body),
        "n_cols": len(header),
        "columns": columns,
        "id_col_candidates": id_cands,
        "stat_col_hits": stat_hits,
        "note": "first sheet, first 200 rows (zipfile+xml)",
    }
    return {
        "structure": structure,
        "identifiers": _empty_ids(),
        "rule_role_hint": hint,
        "needs_llm": hint["certainty"] != "strong",
        "excerpt_source": None,
    }


def scan_pdf(path: str) -> Dict[str, Any]:
    """latin-1 byte scan only. NO text extraction — excerpt delegated to host LLM."""
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("latin-1", errors="replace")
    dois = sorted(set(m.group(0).rstrip(").,;") for m in DOI_RE.finditer(text)))
    pmids = sorted(set(m.group(1) for m in PMID_RE.finditer(text)))
    pages = len(re.findall(r"/Type\s*/Page[^s]", text)) or len(re.findall(r"/Type\s*/Page\b", text))
    structure = _empty_structure()
    structure["pages"] = pages
    return {
        "structure": structure,
        "identifiers": {"doi": dois[0] if dois else None, "doi_all": dois,
                        "pmid": pmids[0] if pmids else None, "pmid_all": pmids},
        "rule_role_hint": {"role": None, "rule": "pdf: role deferred to host read", "certainty": "none"},
        "needs_llm": True,
        "excerpt_source": "host_llm_read",
    }


def _mask_excerpt_text(raw: str) -> Tuple[str, Dict[str, Any]]:
    """Mask PHI in scanner-produced text BEFORE excerpt slicing (mask-then-slice:
    an identifier straddling the cutoff can then only lose placeholder chars,
    never leave original fragments behind). Fail-closed: if the engine is
    unavailable or errors — including an unsupported RF_PHI_BACKEND — the text
    is withheld entirely rather than emitted unscreened. Only the exception
    type name is recorded, never content."""
    if phi_detect is None:
        return "", {"screened": False, "reason": "phi_engine_unavailable"}
    try:
        backend = phi_detect.get_backend()
        masked, findings = phi_detect.redact_text(raw, backend=backend)
    except Exception as exc:
        return "", {"screened": False, "reason": type(exc).__name__}
    return masked, {
        "screened": True,
        "backend": backend.name,
        "target": "excerpt",
        "severity": phi_detect.max_severity(findings),
        "finding_count": len(findings),
    }


def _mask_headings(headings: List[str]) -> List[str]:
    """Headings are stored as a raw list separate from the excerpt, so they get
    their own masking pass ("" per heading when the engine is unavailable)."""
    return [_mask_excerpt_text(h)[0] for h in headings]


def scan_docx(path: str) -> Dict[str, Any]:
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    text_parts: List[str] = []
    headings: List[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("word/document.xml"))
        for p in root.iter(f"{ns}p"):
            style = None
            ppr = p.find(f"{ns}pPr")
            if ppr is not None:
                pstyle = ppr.find(f"{ns}pStyle")
                if pstyle is not None:
                    style = pstyle.get(f"{ns}val")
            para = "".join(t.text or "" for t in p.iter(f"{ns}t"))
            if para.strip():
                text_parts.append(para)
                if style and "heading" in style.lower():
                    headings.append(para.strip())
    except Exception:
        pass
    full_text = "\n".join(text_parts)
    # DOI/PMID extraction keeps its original (pre-mask) scope — bibliographic
    # identifiers are the scanner's own feature, not PHI.
    ids = _scan_identifiers_text(full_text[:TEXT_EXCERPT_LEN])
    masked_full, phi_record = _mask_excerpt_text(full_text)
    structure = _empty_structure()
    structure["excerpt"] = masked_full[:TEXT_EXCERPT_LEN]
    structure["headings"] = _mask_headings(headings[:30])
    return {
        "structure": structure,
        "identifiers": ids,
        "rule_role_hint": {"role": None, "rule": None, "certainty": "none"},
        "needs_llm": True,
        "excerpt_source": "scanner" if phi_record.get("screened") else "unscreened",
        "phi": phi_record,
    }


def scan_text_document(path: str, subtype: str) -> Dict[str, Any]:
    text = _read_text(path, TEXT_EXCERPT_LEN * 4)
    headings = [h.strip() for h in MD_HEADER_RE.findall(text)] if subtype == "md" else []
    ids = _scan_identifiers_text(text)
    masked, phi_record = _mask_excerpt_text(text)
    structure = _empty_structure()
    structure["excerpt"] = masked[:TEXT_EXCERPT_LEN]
    structure["headings"] = _mask_headings(headings[:30])
    return {
        "structure": structure,
        "identifiers": ids,
        "rule_role_hint": {"role": None, "rule": None, "certainty": "none"},
        "needs_llm": True,
        "excerpt_source": "scanner" if phi_record.get("screened") else "unscreened",
        "phi": phi_record,
    }


def scan_code(path: str, subtype: str) -> Dict[str, Any]:
    text = _read_text(path, TEXT_EXCERPT_LEN * 6)
    signatures: List[str] = []
    role = None
    certainty = "none"
    rule = None
    if SQL_RE.search(text) or subtype == "sql":
        if SQL_RE.search(text):
            signatures.append("SELECT...FROM")
            role, certainty, rule = "extraction_query", "strong", "SQL SELECT...FROM signature"
        else:
            role, certainty, rule = "extraction_query", "medium", ".sql file"
    if ANALYSIS_LIB_RE.search(text):
        sig = ANALYSIS_LIB_RE.search(text).group(0)
        signatures.append(sig)
        # analysis library signature wins if no SQL, else keep extraction_query but note both
        if role is None:
            role, certainty, rule = "analysis_code", "strong", f"statistical library signature ({sig})"
    if role is None:
        # generic code file — let LLM decide
        role, certainty, rule = None, "none", None
    # Signature/role detection above ran on the ORIGINAL text — masking must
    # never disturb role judgement. Only the stored excerpt is masked.
    masked, phi_record = _mask_excerpt_text(text)
    structure = _empty_structure()
    structure["excerpt"] = masked[:TEXT_EXCERPT_LEN]
    structure["signatures"] = signatures
    return {
        "structure": structure,
        "identifiers": _scan_identifiers_text(text),
        "rule_role_hint": {"role": role, "rule": rule, "certainty": certainty},
        "needs_llm": certainty != "strong",
        "excerpt_source": "scanner" if phi_record.get("screened") else "unscreened",
        "phi": phi_record,
    }


def scan_bib(path: str, subtype: str) -> Dict[str, Any]:
    text = _read_text(path)
    if subtype == "ris":
        records = max(len(re.findall(r"^ER\s{2}-", text, re.MULTILINE)),
                      len(re.findall(r"^TY\s{2}-", text, re.MULTILINE)))
    elif subtype == "nbib":
        records = len(re.findall(r"^PMID\s*-", text, re.MULTILINE))
    else:  # bib
        records = len(re.findall(r"@\w+\s*\{", text))
    dois = sorted(set(m.group(0).rstrip(").,;") for m in DOI_RE.finditer(text)))
    pmids = sorted(set(m.group(1) for m in PMID_RE.finditer(text)))
    structure = _empty_structure()
    structure["records"] = records
    return {
        "structure": structure,
        "identifiers": {"doi": dois[0] if dois else None, "doi_all": dois,
                        "pmid": pmids[0] if pmids else None, "pmid_all": pmids},
        "rule_role_hint": {"role": "bibliographic", "rule": f"{subtype} reference file ({records} records)",
                           "certainty": "strong"},
        "needs_llm": False,
        "excerpt_source": "scanner",
    }


def scan_archive(path: str, subtype: str) -> Dict[str, Any]:
    members: List[str] = []
    try:
        if subtype == "zip" or zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                members = zf.namelist()
        elif tarfile.is_tarfile(path):
            with tarfile.open(path) as tf:
                members = tf.getnames()
    except Exception:
        members = []
    structure = _empty_structure()
    structure["members"] = members[:200]
    structure["member_count"] = len(members)
    return {
        "structure": structure,
        "identifiers": _empty_ids(),
        "rule_role_hint": {"role": None, "rule": "archive: members listed, not extracted", "certainty": "none"},
        "needs_llm": True,
        "excerpt_source": None,
    }


def scan_generic(path: str) -> Dict[str, Any]:
    return {
        "structure": _empty_structure(),
        "identifiers": _empty_ids(),
        "rule_role_hint": {"role": None, "rule": None, "certainty": "none"},
        "needs_llm": True,
        "excerpt_source": None,
    }


def _scan_identifiers_text(text: str) -> Dict[str, Any]:
    dois = sorted(set(m.group(0).rstrip(").,;") for m in DOI_RE.finditer(text)))
    pmids = sorted(set(m.group(1) for m in PMID_RE.finditer(text)))
    return {"doi": dois[0] if dois else None, "doi_all": dois,
            "pmid": pmids[0] if pmids else None, "pmid_all": pmids}


def _empty_structure() -> Dict[str, Any]:
    return {"n_rows": None, "n_cols": None, "columns": [],
            "id_col_candidates": [], "stat_col_hits": []}


def _empty_ids() -> Dict[str, Any]:
    return {"doi": None, "doi_all": [], "pmid": None, "pmid_all": []}


def dispatch_scan(path: str, fmt: str, subtype: str) -> Dict[str, Any]:
    if fmt == "tabular":
        if subtype == "csv":
            return scan_csv(path)
        if subtype == "xlsx":
            return scan_xlsx(path)
        return scan_generic(path)  # sav/dta/parquet/xls — cannot parse offline
    if fmt == "document":
        if subtype == "pdf":
            return scan_pdf(path)
        if subtype == "docx":
            return scan_docx(path)
        if subtype in ("md", "txt"):
            return scan_text_document(path, subtype)
        return scan_generic(path)
    if fmt == "bibliographic":
        return scan_bib(path, subtype)
    if fmt == "code":
        return scan_code(path, subtype)
    if fmt == "archive":
        return scan_archive(path, subtype)
    return scan_generic(path)


# ---------------------------------------------------------------------------
# Lineage pre-computation
# ---------------------------------------------------------------------------
_VERSION_TOKEN_RE = re.compile(r"[ _\-]*v\d+", re.IGNORECASE)
_NOISE_TOKEN_RE = re.compile(r"[ _\-]*(final|copy|draft|revised|rev)\b", re.IGNORECASE)
_PAREN_NUM_RE = re.compile(r"\(\d+\)")
_STRIP_RE = re.compile(r"[\s_\-]+")


def normalize_stem(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename))[0]
    s = stem.lower()
    s = _VERSION_TOKEN_RE.sub("", s)
    s = _NOISE_TOKEN_RE.sub("", s)
    s = _PAREN_NUM_RE.sub("", s)
    s = _STRIP_RE.sub("", s)
    return s


def compute_lineage(entries: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Populate lineage_pre on each entry. Returns version_groups map."""
    # duplicates by hash
    by_hash: Dict[str, str] = {}
    for e in entries:
        h = e["hash"]
        if h in by_hash:
            e["lineage_pre"]["duplicate_of"] = by_hash[h]
        else:
            by_hash[h] = e["material_id"]

    # version groups by normalized stem (only groups with >1 distinct file)
    by_stem: Dict[str, List[Dict[str, Any]]] = {}
    for e in entries:
        by_stem.setdefault(normalize_stem(e["filename"]), []).append(e)

    version_groups: Dict[str, Dict[str, Any]] = {}
    vg_counter = 1
    for stem, group in by_stem.items():
        if len(group) < 2:
            continue
        # skip trivial groups that are pure duplicates of one another only if ALL share one hash
        distinct_hashes = {g["hash"] for g in group}
        vg_id = f"vg-{vg_counter}"
        vg_counter += 1
        version_groups[vg_id] = {
            "normalized_stem": stem,
            "members": [g["material_id"] for g in group],
            "all_identical": len(distinct_hashes) == 1,
        }
        for g in group:
            g["lineage_pre"]["version_group_candidate"] = vg_id
    return version_groups


# ---------------------------------------------------------------------------
# paste-refs parsing
# ---------------------------------------------------------------------------
def parse_paste_refs(raw: str) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    if not raw:
        return refs
    for tok in re.split(r"[,\n;]+", raw):
        t = tok.strip()
        if not t:
            continue
        doi_m = DOI_RE.search(t)
        pmid_m = re.search(r"PMID[:\s]*?(\d{6,9})", t, re.IGNORECASE)
        # PubMed article URLs carry the PMID as the path segment.
        pmid_url_m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{6,9})", t, re.IGNORECASE)
        if pmid_m:
            refs.append({"raw": t, "type": "pmid", "value": pmid_m.group(1)})
        elif pmid_url_m:
            refs.append({"raw": t, "type": "pmid", "value": pmid_url_m.group(1)})
        elif doi_m:
            refs.append({"raw": t, "type": "doi", "value": doi_m.group(0).rstrip(").,;")})
        elif re.fullmatch(r"\d{6,9}", t):
            refs.append({"raw": t, "type": "pmid", "value": t})
        elif re.match(r"https?://", t, re.IGNORECASE):
            refs.append({"raw": t, "type": "url", "value": t})
        else:
            refs.append({"raw": t, "type": "unknown", "value": t})
    return refs


# ---------------------------------------------------------------------------
# Input expansion + copy
# ---------------------------------------------------------------------------
def expand_inputs(inputs: List[str]) -> List[str]:
    files: List[str] = []
    for inp in inputs:
        if os.path.isdir(inp):
            for root, _dirs, names in os.walk(inp):
                for name in sorted(names):
                    if name.startswith("."):
                        continue
                    files.append(os.path.join(root, name))
        elif os.path.isfile(inp):
            files.append(inp)
        else:
            print(f"WARNING: input not found, skipped: {inp}", file=sys.stderr)
    # stable, de-duplicated by path
    seen = set()
    out = []
    for f in files:
        ap = os.path.abspath(f)
        if ap not in seen:
            seen.add(ap)
            out.append(f)
    return out


def copy_material(path: str, file_hash: str, materials_dir: str) -> str:
    os.makedirs(materials_dir, exist_ok=True)
    base = os.path.basename(path)
    stored_name = f"{file_hash[:12]}_{base}"
    dest = os.path.join(materials_dir, stored_name)
    if not os.path.exists(dest):
        with open(path, "rb") as src, open(dest, "wb") as dst:
            for chunk in iter(lambda: src.read(1 << 16), b""):
                dst.write(chunk)
    return stored_name


# ---------------------------------------------------------------------------
# existing registry — continue material_id numbering
# ---------------------------------------------------------------------------
def next_material_index(project_dir: str) -> int:
    reg_path = resolve_system_file(project_dir, "materials")
    if not os.path.exists(reg_path):
        return 1
    try:
        with open(reg_path, encoding="utf-8") as f:
            reg = json.load(f)
        mx = 0
        for m in reg.get("materials", []):
            mid = str(m.get("id", ""))
            mo = re.match(r"m-0*(\d+)", mid)
            if mo:
                mx = max(mx, int(mo.group(1)))
        return mx + 1
    except Exception:
        return 1


# ---------------------------------------------------------------------------
# PHI integration
# ---------------------------------------------------------------------------
def run_phi_screen(original_path: str, material_id: str, project_dir: str) -> Dict[str, Any]:
    out_path = os.path.join(os.path.dirname(resolve_system_file(project_dir, "state")), f"phi-report_{material_id}.json")
    phi_script = os.path.join(SCRIPT_DIR, "phi_screener.py")
    try:
        proc = subprocess.run(
            [sys.executable, phi_script, "--data-path", original_path, "--output", out_path],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:
        return {"screened": False, "error": type(exc).__name__}
    severity_map = {0: "clean", 1: "warning", 2: "critical"}
    finding_count = 0
    severity = severity_map.get(proc.returncode, "unknown")
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8") as f:
                rep = json.load(f)
            finding_count = rep.get("finding_count", 0)
            severity = rep.get("max_severity", severity)
        except Exception:
            pass
    return {
        "screened": True,
        "finding_count": finding_count,
        "severity": severity,
        "report": os.path.relpath(out_path, project_dir) if os.path.exists(out_path) else None,
    }


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------
def scan(inputs: List[str], paste_refs: str, project_dir: str,
         no_copy: bool, phi_screen: bool) -> Dict[str, Any]:
    files = expand_inputs(inputs)
    materials_dir = resolve_materials_dir(project_dir)
    start_idx = next_material_index(project_dir)

    entries: List[Dict[str, Any]] = []
    for offset, path in enumerate(files):
        idx = start_idx + offset
        material_id = f"m-{idx:03d}"
        fmt, subtype, evidence = detect_format(path)
        file_hash = sha256_file(path)
        size_bytes = os.path.getsize(path)

        stored_as = None
        if not no_copy:
            stored_as = (MATERIALS_DIR if os.path.basename(materials_dir) == MATERIALS_DIR else "materials") + "/" + copy_material(path, file_hash, materials_dir)

        scan_result = dispatch_scan(path, fmt, subtype)

        entry: Dict[str, Any] = {
            "material_id": material_id,
            "filename": os.path.basename(path),
            "source_path": path,
            "stored_as": stored_as,
            "hash": file_hash,
            "size_bytes": size_bytes,
            "format": fmt,
            "subtype": subtype,
            "format_evidence": evidence,
            "structure": scan_result["structure"],
            "identifiers": scan_result["identifiers"],
            "rule_role_hint": scan_result["rule_role_hint"],
            "lineage_pre": {"duplicate_of": None, "version_group_candidate": None},
            "needs_llm": scan_result["needs_llm"],
            "excerpt_source": scan_result["excerpt_source"],
            "phi": scan_result.get("phi", {"screened": False}),
        }
        entries.append(entry)

    # lineage across the whole batch
    version_groups = compute_lineage(entries)

    # PHI screening on tabular files
    if phi_screen:
        for e in entries:
            if e["format"] == "tabular" and e["subtype"] in ("csv", "xlsx"):
                target = e["source_path"]
                e["phi"] = run_phi_screen(target, e["material_id"], project_dir)

    # Flags are format-agnostic: tabular phi comes from the subprocess screen
    # above, document/code phi from the inline excerpt masking — either way the
    # same channel (entry["phi"] + entry["flags"]) reaches Stage 2.
    for e in entries:
        phi_info = e.get("phi") or {}
        if phi_info.get("severity") in ("warning", "critical"):
            e.setdefault("flags", [])
            if "phi_suspect" not in e["flags"]:
                e["flags"].append("phi_suspect")
        if phi_info.get("screened") is False and phi_info.get("reason"):
            e.setdefault("flags", [])
            if "needs_full_read" not in e["flags"]:
                e["flags"].append("needs_full_read")

    llm_batch_needed = [e["material_id"] for e in entries if e["needs_llm"]]

    return {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(),
        "project_dir": project_dir,
        "n_materials": len(entries),
        "entries": entries,
        "pasted_refs": parse_paste_refs(paste_refs),
        "version_groups": version_groups,
        "llm_batch_needed": llm_batch_needed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan and pre-classify research materials (offline)")
    parser.add_argument("--input", action="append", default=[], help="File or directory (repeatable)")
    parser.add_argument("--paste-refs", default="", help='Pasted refs, e.g. "PMID:38812345, 10.1001/jama..."')
    parser.add_argument("--project-dir", default="research", help="Project directory (default research)")
    parser.add_argument("--no-copy", action="store_true", help="Do not copy originals into materials/")
    parser.add_argument("--phi-screen", action="store_true",
                        help="Run phi_screener.py on tabular files (docx/md/txt/code excerpt "
                             "masking is always on, regardless of this flag)")
    parser.add_argument("--output", required=True, help="Output path for scan-report JSON")
    args = parser.parse_args()

    if not args.input and not args.paste_refs:
        print("ERROR: at least one --input or --paste-refs is required", file=sys.stderr)
        sys.exit(1)

    for inp in args.input:
        if not os.path.exists(inp):
            print(f"ERROR: input path not found: {inp}", file=sys.stderr)
            sys.exit(1)

    os.makedirs(args.project_dir, exist_ok=True)

    report = scan(args.input, args.paste_refs, args.project_dir, args.no_copy, args.phi_screen)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"Scan report: {args.output}")
    print(f"  Materials: {report['n_materials']}")
    for e in report["entries"]:
        hint = e["rule_role_hint"]
        role = hint.get("role") or "?"
        cert = hint.get("certainty")
        phi = e["phi"]
        phi_txt = ""
        if phi.get("screened"):
            phi_txt = f"  phi={phi.get('severity')}({phi.get('finding_count')})"
        print(f"  [{e['material_id']}] {e['filename']}  fmt={e['format']}/{e['subtype']}  "
              f"role={role}({cert})  needs_llm={e['needs_llm']}{phi_txt}")
    if report["pasted_refs"]:
        print(f"  Pasted refs: {len(report['pasted_refs'])}")
    if report["version_groups"]:
        print(f"  Version groups: {list(report['version_groups'].keys())}")
    sys.exit(0)


if __name__ == "__main__":
    main()
