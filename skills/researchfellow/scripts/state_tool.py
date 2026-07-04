#!/usr/bin/env python3
"""Read-only state-machine judge for the ResearchFellow skill (state.json v2).

This tool NEVER writes state. All mutation is the host LLM's job; the script only
inspects `.research/state.json`, judges deterministic invariants, and reports the
verdict via exit codes + JSON on stdout. Audit-replay verification is out of scope
for P0 (see p0-implementation-design_2026-07-04.md §4).

Subcommands:
    state_tool.py validate   --project-dir .research/            # exit 0/1
    state_tool.py can-enter  --project-dir .research/ --step N   # exit 0/2
    state_tool.py gate-check --project-dir .research/ --for real-analysis  # exit 0/2
    state_tool.py cascade    --project-dir .research/ --changed <artifact>  # exit 0

Handles both v2 state.json (schema_version:2 + semantic gate ids) and legacy v1
state.json (no schema_version + numeric gate keys) via the mapping constants below.

IMPORTANT: The DAG table, invalidation adjacency list, gate-anchor map and v1->v2
gate mapping below MUST stay identical to the tables in
`references/state-machine.md`. Any edit here requires the same edit there
(cross-checked in review).

Usage examples:
    python3 state_tool.py validate --project-dir .research/
    python3 state_tool.py can-enter --project-dir .research/ --step 10
    python3 state_tool.py gate-check --project-dir .research/ --for real-analysis
    python3 state_tool.py cascade --project-dir .research/ --changed protocol
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Module constants — MUST mirror references/state-machine.md
# ---------------------------------------------------------------------------

# Human-readable step names (state.json steps.*.name).
STEP_NAMES: Dict[int, str] = {
    1: "PICO Structuring",
    2: "Literature Scoping",
    3: "Evidence Table",
    4: "Variable Definition",
    5: "Protocol",
    6: "SAP",
    7: "Table/Figure Shells",
    8: "Synthetic Dry-Run",
    9: "Data Prep & QC",
    10: "Real Analysis",
    11: "Manuscript",
    12: "Submission Package",
    13: "Revision Loop",
}

# Which step produces which artifact (state.json artifacts registry keys).
# Must match state-machine.md "산출" column.
ARTIFACT_PRODUCED_BY: Dict[str, int] = {
    "idea": 1,
    "literature": 2,
    "evidence_table": 3,
    "variables": 4,
    "protocol": 5,
    "sap": 6,
    "shells": 7,
    "synthetic_results": 8,
    "extraction_plan": 9,
    "qc_report": 9,
    "real_results": 10,
    "manuscript": 11,
    "checklist": 11,
    "submission_package": 12,
    "revision": 13,
}

# Artifact DAG — state-machine.md §2 table. `req` = hard requirement (absent/draft/
# invalidated blocks entry, deterministic). `rec` = recommended (warn, LLM may
# proceed after user confirmation). hard_gates block deterministically; soft_gates
# are LLM-conversational (surfaced as warnings here).
DAG: Dict[int, Dict[str, List[str]]] = {
    1:  {"req": [], "rec": [], "hard_gates": [], "soft_gates": [], "produces": ["idea"]},
    2:  {"req": ["idea"], "rec": [], "hard_gates": [], "soft_gates": ["gate.go-no-go"], "produces": ["literature"]},
    3:  {"req": ["idea"], "rec": ["literature"], "hard_gates": [], "soft_gates": [], "produces": ["evidence_table"]},
    4:  {"req": ["idea"], "rec": ["evidence_table"], "hard_gates": [], "soft_gates": ["gate.novelty"], "produces": ["variables"]},
    5:  {"req": ["idea", "variables"], "rec": ["evidence_table"], "hard_gates": [], "soft_gates": ["gate.endpoint"], "produces": ["protocol"]},
    6:  {"req": ["protocol", "variables"], "rec": [], "hard_gates": [], "soft_gates": [], "produces": ["sap"]},
    7:  {"req": ["sap"], "rec": [], "hard_gates": [], "soft_gates": [], "produces": ["shells"]},
    8:  {"req": ["sap", "variables"], "rec": ["shells"], "hard_gates": [], "soft_gates": [], "produces": ["synthetic_results"]},
    9:  {"req": ["protocol", "variables"], "rec": [], "hard_gates": ["gate.feasibility", "gate.protocol"], "soft_gates": [], "produces": ["extraction_plan", "qc_report"]},
    10: {"req": ["sap", "qc_report"], "rec": [], "hard_gates": ["gate.qc", "gate.feasibility", "gate.protocol"], "soft_gates": [], "produces": ["real_results"]},
    11: {"req": ["real_results", "protocol", "sap"], "rec": ["evidence_table"], "hard_gates": [], "soft_gates": ["gate.results"], "produces": ["manuscript", "checklist"]},
    12: {"req": ["manuscript", "checklist"], "rec": [], "hard_gates": [], "soft_gates": ["gate.manuscript"], "produces": ["submission_package"]},
    13: {"req": ["manuscript"], "rec": [], "hard_gates": [], "soft_gates": [], "produces": ["revision"]},
}

# Static invalidation adjacency — state-machine.md §2 (identical here and there).
# Maps a changed artifact to the downstream artifacts that must be invalidated.
# Only these seven artifacts act as cascade sources; others are leaves or feed
# only [rec] edges (handled by LLM conversation, not deterministic cascade).
INVALIDATION_ADJACENCY: Dict[str, List[str]] = {
    "idea": ["literature", "evidence_table", "variables", "protocol", "sap", "shells",
             "synthetic_results", "extraction_plan", "qc_report", "real_results",
             "manuscript", "checklist", "submission_package", "revision"],
    "variables": ["protocol", "sap", "shells", "synthetic_results", "extraction_plan",
                  "qc_report", "real_results", "manuscript", "checklist",
                  "submission_package", "revision"],
    "protocol": ["sap", "shells", "synthetic_results", "extraction_plan", "qc_report",
                 "real_results", "manuscript", "checklist", "submission_package", "revision"],
    "sap": ["shells", "synthetic_results", "real_results", "manuscript", "checklist",
            "submission_package", "revision"],
    "qc_report": ["real_results", "manuscript", "checklist", "submission_package", "revision"],
    "real_results": ["manuscript", "checklist", "submission_package", "revision"],
    "manuscript": ["checklist", "submission_package", "revision"],
}

# Gate metadata — state-machine.md gate table.
GATE_TYPE: Dict[str, str] = {
    "gate.go-no-go": "soft",
    "gate.novelty": "soft",
    "gate.endpoint": "soft",
    "gate.feasibility": "hard",
    "gate.protocol": "hard",
    "gate.qc": "hard",
    "gate.results": "soft",
    "gate.manuscript": "soft",
}

# Anchor artifact per gate — used by the invalidation cascade on gate reversal
# (state-machine.md §2 trigger ②).
GATE_ANCHOR: Dict[str, str] = {
    "gate.go-no-go": "idea",
    "gate.novelty": "evidence_table",
    "gate.endpoint": "variables",
    "gate.feasibility": "variables",
    "gate.protocol": "protocol",
    "gate.qc": "qc_report",
    "gate.results": "real_results",
    "gate.manuscript": "manuscript",
}

# v1 numeric gate key -> v2 semantic gate id (NFR-5 lazy-upgrade map).
V1_GATE_MAP: Dict[str, str] = {
    "1": "gate.go-no-go",
    "2": "gate.novelty",
    "3": "gate.endpoint",
    "4": "gate.feasibility",
    "5": "gate.protocol",
    "9": "gate.qc",
    "10": "gate.results",
    "11": "gate.manuscript",
}

# The three hard real-data gates (FR-G4). Legacy ids in comment for reference.
REAL_DATA_GATES: List[str] = ["gate.feasibility", "gate.protocol", "gate.qc"]  # v1: 4, 5, 9

# Candidate on-disk filenames per artifact — used ONLY for v1 reconstruction,
# which has no artifacts registry (see reconstruct rule in state-machine.md).
# Paths are relative to --project-dir.
ARTIFACT_FILES: Dict[str, List[str]] = {
    "idea": ["idea.json", "pico.json"],
    "literature": ["literature.json", "search-results.json", "literature"],
    "evidence_table": ["evidence-table.json", "evidence_table.json"],
    "variables": ["variables.json", "variable-definitions.json"],
    "protocol": ["protocol.md"],
    "sap": ["sap.md"],
    "shells": ["shells.md", "table-shells.md", "tables"],
    "synthetic_results": [os.path.join("analysis", "synthetic", "results.json")],
    "extraction_plan": ["extraction-plan.md", "extraction_plan.md"],
    "qc_report": ["qc-report.json"],
    "real_results": [os.path.join("analysis", "real", "results.json")],
    "manuscript": ["manuscript.md"],
    "checklist": ["checklist.json", "checklist.md"],
    "submission_package": ["submission", "submission-package"],
    "revision": ["revision"],
}


# ---------------------------------------------------------------------------
# Derived helpers
# ---------------------------------------------------------------------------

def _build_dependents() -> Dict[str, set]:
    """Transitive dependents per artifact, derived from DAG req+rec edges.

    Used by the draft-downstream invariant (validate): a valid artifact must not
    structurally depend on a draft one. This is DAG-derived (structural) and is
    intentionally distinct from INVALIDATION_ADJACENCY (the pinned cascade list,
    which e.g. couples manuscript->checklist by co-production).
    """
    direct: Dict[str, set] = {a: set() for a in ARTIFACT_PRODUCED_BY}
    for step, spec in DAG.items():
        inputs = list(spec["req"]) + list(spec["rec"])
        for produced in spec["produces"]:
            for inp in inputs:
                direct[inp].add(produced)

    dependents: Dict[str, set] = {}
    for artifact in ARTIFACT_PRODUCED_BY:
        seen: set = set()
        stack = list(direct.get(artifact, set()))
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(direct.get(node, set()))
        dependents[artifact] = seen
    return dependents


DEPENDENTS = _build_dependents()

COMPLETED_STATUSES = {"completed", "imported"}


# ---------------------------------------------------------------------------
# State loading + schema detection
# ---------------------------------------------------------------------------

def load_state(project_dir: str) -> Optional[dict]:
    """Load state.json from a project dir. Returns None if absent/unreadable."""
    state_path = os.path.join(project_dir, "state.json")
    if not os.path.exists(state_path):
        return None
    try:
        with open(state_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def detect_schema(state: dict) -> Tuple[str, dict]:
    """Detect schema flavor: 'v1', 'v2', or 'hybrid'.

    v1  = no schema_version && all gate keys numeric.
    v2  = schema_version >= 2 && all gate keys semantic ('gate.*').
    Anything else (mixed keys, version/key mismatch) = hybrid (a violation).
    """
    gates = state.get("gates", {}) or {}
    keys = list(gates.keys())
    has_version = "schema_version" in state
    version = state.get("schema_version")

    numeric = bool(keys) and all(re.fullmatch(r"\d+", str(k)) for k in keys)
    semantic = bool(keys) and all(str(k).startswith("gate.") for k in keys)

    try:
        version_ok = has_version and int(version) >= 2
    except (TypeError, ValueError):
        version_ok = False

    if not keys:
        # No gates block at all — infer purely from schema_version presence.
        return ("v2", {}) if has_version else ("v1", {})

    if version_ok and semantic:
        return "v2", {}
    if not has_version and numeric:
        return "v1", {}
    return "hybrid", {"has_version": has_version, "numeric": numeric, "semantic": semantic}


def _gate_status(state: dict, schema: str, semantic_id: str) -> str:
    """Return a gate's status, resolving the v1 numeric key when needed."""
    gates = state.get("gates", {}) or {}
    if schema == "v1":
        # Reverse-map semantic id to its v1 numeric key.
        for num, sem in V1_GATE_MAP.items():
            if sem == semantic_id and num in gates:
                return gates[num].get("status", "pending")
        return "pending"
    return gates.get(semantic_id, {}).get("status", "pending")


# ---------------------------------------------------------------------------
# Artifact validity resolution (v2 registry vs v1 reconstruction)
# ---------------------------------------------------------------------------

def _artifact_validity_v2(state: dict, artifact: str) -> str:
    """Return 'valid' | 'draft' | 'invalidated' | 'absent' from the registry."""
    entry = (state.get("artifacts", {}) or {}).get(artifact)
    if not entry:
        return "absent"
    validity = entry.get("validity", "absent")
    if validity not in ("valid", "draft", "invalidated"):
        return "absent"
    return validity


def _artifact_file_exists(project_dir: str, artifact: str) -> bool:
    for candidate in ARTIFACT_FILES.get(artifact, []):
        if os.path.exists(os.path.join(project_dir, candidate)):
            return True
    return False


def _artifact_validity_v1(state: dict, project_dir: str, artifact: str) -> str:
    """Reconstruct validity for a v1 project (no registry).

    Rule (state-machine.md §v1): producing step is completed AND a produced
    artifact file exists on disk => 'valid'; otherwise 'absent'. v1 has no notion
    of 'draft'.
    """
    step = ARTIFACT_PRODUCED_BY.get(artifact)
    if step is None:
        return "absent"
    status = (state.get("steps", {}) or {}).get(str(step), {}).get("status", "pending")
    if status in COMPLETED_STATUSES and _artifact_file_exists(project_dir, artifact):
        return "valid"
    return "absent"


def artifact_validity(state: dict, project_dir: str, schema: str, artifact: str) -> str:
    if schema == "v1":
        return _artifact_validity_v1(state, project_dir, artifact)
    return _artifact_validity_v2(state, artifact)


# ---------------------------------------------------------------------------
# check_real_data_gates — shared with analysis_runner.py (FR-G4 last line)
# ---------------------------------------------------------------------------

def check_real_data_gates(state: dict) -> Tuple[bool, List[str]]:
    """Return (ok, missing) for the three hard real-data gates.

    Works on both v2 (semantic ids) and v1 (numeric keys 4/5/9). `missing` is a
    list of semantic gate ids not in 'approved' status. analysis_runner.py imports
    and calls this so an LLM cannot bypass the gate by editing prose.
    """
    schema, _ = detect_schema(state)
    missing: List[str] = []
    for gate in REAL_DATA_GATES:
        if _gate_status(state, schema, gate) != "approved":
            missing.append(gate)
    return (len(missing) == 0, missing)


# ---------------------------------------------------------------------------
# Subcommand: validate
# ---------------------------------------------------------------------------

def do_validate(state: dict, project_dir: str) -> Tuple[dict, int]:
    schema, schema_detail = detect_schema(state)
    violations: List[dict] = []

    # Invariant: schema must not be a v1/v2 hybrid.
    if schema == "hybrid":
        violations.append({
            "invariant": "schema_consistency",
            "detail": "state.json mixes v1 and v2 conventions (gate keys vs schema_version)",
            "context": schema_detail,
        })

    gates = state.get("gates", {}) or {}
    steps = state.get("steps", {}) or {}

    # Invariant: hard gates may never be retroactive.
    for key, info in gates.items():
        semantic = V1_GATE_MAP.get(key, key) if schema == "v1" else key
        gtype = info.get("type") or GATE_TYPE.get(semantic)
        if gtype == "hard" and info.get("retroactive") is True:
            violations.append({
                "invariant": "hard_gate_not_retroactive",
                "detail": f"hard gate '{semantic}' has retroactive:true",
                "gate": semantic,
            })

    # Invariant: at most one step in_progress.
    in_progress = [s for s, info in steps.items() if info.get("status") == "in_progress"]
    if len(in_progress) > 1:
        violations.append({
            "invariant": "single_in_progress",
            "detail": f"{len(in_progress)} steps in_progress: {sorted(in_progress, key=lambda x: int(x))}",
            "steps": sorted(in_progress, key=lambda x: int(x)),
        })

    # Invariant: a draft artifact must not have a valid downstream (v2 registry only).
    if schema == "v2":
        registry = state.get("artifacts", {}) or {}
        for artifact, entry in registry.items():
            if entry.get("validity") != "draft":
                continue
            for dependent in DEPENDENTS.get(artifact, set()):
                dep_entry = registry.get(dependent)
                if dep_entry and dep_entry.get("validity") == "valid":
                    violations.append({
                        "invariant": "draft_has_no_valid_downstream",
                        "detail": f"draft artifact '{artifact}' has valid downstream '{dependent}'",
                        "artifact": artifact,
                        "downstream": dependent,
                    })

    report = {"schema": schema, "violations": violations}
    return report, (0 if not violations else 1)


# ---------------------------------------------------------------------------
# Subcommand: can-enter
# ---------------------------------------------------------------------------

def do_can_enter(state: dict, project_dir: str, step: int) -> Tuple[dict, int]:
    schema, _ = detect_schema(state)

    if step not in DAG:
        report = {
            "step": step,
            "allowed": False,
            "error": f"unknown step {step} (valid: 1-13)",
        }
        return report, 2

    spec = DAG[step]
    missing_artifacts: List[str] = []
    draft_artifacts: List[str] = []
    warnings: List[str] = []

    # Required artifacts must resolve to 'valid'.
    for artifact in spec["req"]:
        validity = artifact_validity(state, project_dir, schema, artifact)
        if validity == "draft":
            draft_artifacts.append(artifact)
        elif validity != "valid":
            missing_artifacts.append(artifact)

    # Recommended artifacts -> warning only.
    for artifact in spec["rec"]:
        validity = artifact_validity(state, project_dir, schema, artifact)
        if validity != "valid":
            warnings.append(f"recommended artifact '{artifact}' is {validity}")

    # Hard gates block deterministically.
    missing_hard_gates: List[str] = []
    for gate in spec["hard_gates"]:
        if _gate_status(state, schema, gate) != "approved":
            missing_hard_gates.append(gate)

    # Soft gates -> warning only.
    for gate in spec["soft_gates"]:
        if _gate_status(state, schema, gate) != "approved":
            warnings.append(f"soft gate '{gate}' not approved")

    allowed = not (missing_artifacts or draft_artifacts or missing_hard_gates)
    report = {
        "step": step,
        "step_name": STEP_NAMES.get(step),
        "schema": schema,
        "allowed": allowed,
        "missing_artifacts": missing_artifacts,
        "draft_artifacts": draft_artifacts,
        "missing_hard_gates": missing_hard_gates,
        "warnings": warnings,
    }
    return report, (0 if allowed else 2)


# ---------------------------------------------------------------------------
# Subcommand: gate-check
# ---------------------------------------------------------------------------

def do_gate_check(state: dict, for_what: str) -> Tuple[dict, int]:
    schema, _ = detect_schema(state)
    if for_what == "real-analysis":
        ok, missing = check_real_data_gates(state)
        report = {"for": for_what, "schema": schema, "ok": ok, "missing": missing}
        return report, (0 if ok else 2)

    report = {"for": for_what, "schema": schema, "ok": False,
              "error": f"unknown gate-check target '{for_what}'"}
    return report, 2


# ---------------------------------------------------------------------------
# Subcommand: cascade
# ---------------------------------------------------------------------------

def do_cascade(state: dict, changed: str) -> Tuple[dict, int]:
    """Compute the invalidation fan-out for a changed artifact (trigger ①).

    Descendants become 'invalidated', their producing steps reset to 'pending',
    and soft gates whose anchor artifact falls in the invalidated set reset to
    'pending'. Read-only: emits the plan, applies nothing.
    """
    invalidate = list(INVALIDATION_ADJACENCY.get(changed, []))
    note = None
    if changed not in INVALIDATION_ADJACENCY:
        if changed in ARTIFACT_PRODUCED_BY:
            note = f"'{changed}' is a leaf/rec-only artifact — no deterministic cascade"
        else:
            note = f"unknown artifact '{changed}'"

    reset_steps = sorted({ARTIFACT_PRODUCED_BY[a] for a in invalidate if a in ARTIFACT_PRODUCED_BY})

    invalidate_set = set(invalidate)
    reset_gates = sorted(
        gate for gate, anchor in GATE_ANCHOR.items()
        if GATE_TYPE.get(gate) == "soft" and anchor in invalidate_set
    )

    report = {
        "changed": changed,
        "invalidate_artifacts": invalidate,
        "reset_steps": reset_steps,
        "reset_gates": reset_gates,
    }
    if note:
        report["note"] = note
    return report, 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _emit(report: dict, exit_code: int) -> None:
    print(json.dumps(report, indent=2, ensure_ascii=False))
    sys.exit(exit_code)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only state-machine judge (state.json v2)")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("validate", "can-enter", "gate-check", "cascade"):
        sp = sub.add_parser(name)
        sp.add_argument("--project-dir", required=True, help="Path to .research/ directory")
        if name == "can-enter":
            sp.add_argument("--step", required=True, type=int, help="Target step number (1-13)")
        if name == "gate-check":
            sp.add_argument("--for", dest="for_what", required=True, help="Gate-check target, e.g. real-analysis")
        if name == "cascade":
            sp.add_argument("--changed", required=True, help="Artifact whose version changed")

    args = parser.parse_args()

    state = load_state(args.project_dir)
    if state is None:
        _emit({"error": f"state.json not found or unreadable in {args.project_dir}",
                "schema": None}, 1)

    if args.command == "validate":
        report, code = do_validate(state, args.project_dir)
    elif args.command == "can-enter":
        report, code = do_can_enter(state, args.project_dir, args.step)
    elif args.command == "gate-check":
        report, code = do_gate_check(state, args.for_what)
    elif args.command == "cascade":
        report, code = do_cascade(state, args.changed)
    else:  # pragma: no cover - argparse enforces choices
        parser.error(f"unknown command {args.command}")
        return

    _emit(report, code)


if __name__ == "__main__":
    main()
