#!/usr/bin/env python3
"""Canonical filesystem layout for ResearchFellow projects (schema v3).

This module is stdlib-only.  Every script that needs to locate project state or
a user-visible artifact imports these constants rather than spelling paths.
"""

from __future__ import annotations

import os
from typing import Dict

SYSTEM_DIR = ".system"
MATERIALS_DIR = "00_materials"
REHEARSAL_DIR = "rehearsal"

# The v3 step folders are the one canonical folder vocabulary for this plugin.
STEP_DIRS: Dict[int, str] = {
    1: "01_pico", 2: "02_literature", 3: "03_evidence_table",
    4: "04_variables", 5: "05_protocol", 6: "06_sap",
    7: "07_shells", 8: "08_dry_run", 9: "09_data_qc",
    10: "10_analysis", 11: "11_manuscript", 12: "12_submission",
    13: "13_revision",
}

# Artifact registry key -> visible artifact directory. A file keeps its filename
# when moved; only its containing directory changes during v2 -> v3 migration.
ARTIFACT_DIRS: Dict[str, str] = {
    "idea": STEP_DIRS[1], "literature": f"{STEP_DIRS[2]}/literature",
    "evidence_table": STEP_DIRS[3], "variables": STEP_DIRS[4],
    "protocol": STEP_DIRS[5], "sap": STEP_DIRS[6], "shells": f"{STEP_DIRS[7]}/shells",
    "synthetic_results": f"{STEP_DIRS[8]}/synthetic_results",
    "extraction_plan": STEP_DIRS[9], "qc_report": STEP_DIRS[9],
    "real_results": f"{STEP_DIRS[10]}/real_results", "manuscript": STEP_DIRS[11],
    "checklist": STEP_DIRS[11], "submission_package": f"{STEP_DIRS[12]}/submission_package",
    "revision": STEP_DIRS[13],
}

SYSTEM_FILES = {
    "state": "state.json", "audit": "audit.jsonl", "materials": "materials.json",
    "scan_report": "scan-report.json", "compliance": "compliance-checklist.json",
}


def detect_layout(project_dir: str) -> str:
    """Return ``v3``, ``legacy``, or ``unknown`` for a project directory."""
    if os.path.isfile(os.path.join(project_dir, SYSTEM_DIR, "state.json")):
        return "v3"
    if os.path.isfile(os.path.join(project_dir, "state.json")):
        return "legacy"
    return "unknown"


def resolve_state_path(project_dir: str) -> str:
    """Return the active state path, preferring schema-v3 layout when present."""
    if detect_layout(project_dir) == "v3":
        return os.path.join(project_dir, SYSTEM_DIR, "state.json")
    return os.path.join(project_dir, "state.json")


def resolve_system_dir(project_dir: str) -> str:
    """Return the v3 system directory, or the legacy project root."""
    return os.path.join(project_dir, SYSTEM_DIR) if detect_layout(project_dir) == "v3" else project_dir


def resolve_desk_dir(project_dir: str) -> str:
    return os.path.join(resolve_system_dir(project_dir), "desk")


def resolve_materials_dir(project_dir: str) -> str:
    return os.path.join(project_dir, MATERIALS_DIR if detect_layout(project_dir) == "v3" else "materials")


def resolve_system_file(project_dir: str, name: str) -> str:
    return os.path.join(resolve_system_dir(project_dir), SYSTEM_FILES[name])


def resolve_analysis_scripts_dir(project_dir: str) -> str:
    """Return the canonical directory for generated analysis scripts."""
    return os.path.join(project_dir, STEP_DIRS[10], "scripts") if detect_layout(project_dir) == "v3" else os.path.join(project_dir, "analysis", "scripts")


def resolve_qc_report_path(project_dir: str) -> str:
    """Return the QC report path for either supported project layout."""
    return os.path.join(project_dir, STEP_DIRS[9], "qc-report.json") if detect_layout(project_dir) == "v3" else os.path.join(project_dir, "qc-report.json")


def resolve_analysis_plan_report_path(project_dir: str) -> str:
    return os.path.join(project_dir, STEP_DIRS[10], "plan-report.json") if detect_layout(project_dir) == "v3" else os.path.join(project_dir, "analysis", "plan-report.json")


def resolve_analysis_output_dir(project_dir: str, mode: str) -> str:
    if detect_layout(project_dir) != "v3":
        return os.path.join(project_dir, "analysis", mode)
    if mode == "synthetic":
        return os.path.join(project_dir, STEP_DIRS[8], "synthetic_results")
    return os.path.join(project_dir, STEP_DIRS[10], "real_results")


def resolve_rehearsal_analysis_dir(project_dir: str) -> str:
    return os.path.join(project_dir, REHEARSAL_DIR, "analysis")
