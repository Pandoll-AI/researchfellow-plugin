"""Drift guard: the tables in references/state-machine.md MUST stay identical to
the constants in state_tool.py. This replaces the "cross-checked in review"
comment (state_tool.py:18-22) with a machine check, so doc/code divergence breaks
the build instead of silently rotting.
"""

from __future__ import annotations

import re

import state_tool
import rf_paths

DOC = "state-machine.md"

# | gate.x | anchor |
_ANCHOR_RE = re.compile(r"^\|\s*(gate\.[\w-]+)\s*\|\s*(\w+)\s*\|\s*$")
# | 9 | gate.qc | hard |
_V1MAP_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*(gate\.[\w-]+)\s*\|\s*(soft|hard)\s*\|")
_ARTIFACT_PATH_RE = re.compile(r"^\|\s*`([\w_]+)`\s*\|\s*`([^`]+)`\s*\|$")
_STEP_LABEL_RE = re.compile(r"^\|\s*(1[0-3]|[1-9])\s*\|\s*(.+?)\s*\|$")


def _doc_text(references_dir):
    return (references_dir / DOC).read_text(encoding="utf-8")


def test_gate_anchor_map_matches_doc(references_dir):
    doc_anchor = {}
    for line in _doc_text(references_dir).splitlines():
        m = _ANCHOR_RE.match(line.strip())
        if m:
            doc_anchor[m.group(1)] = m.group(2)
    assert doc_anchor, "no GATE_ANCHOR table rows parsed from doc"
    assert doc_anchor == state_tool.GATE_ANCHOR


def test_v1_gate_map_and_types_match_doc(references_dir):
    doc_v1_map = {}
    doc_gate_type = {}
    for line in _doc_text(references_dir).splitlines():
        m = _V1MAP_RE.match(line.strip())
        if m:
            num, gate, gtype = m.group(1), m.group(2), m.group(3)
            doc_v1_map[num] = gate
            doc_gate_type[gate] = gtype
    assert doc_v1_map, "no v1-map table rows parsed from doc"
    assert doc_v1_map == state_tool.V1_GATE_MAP
    # Every gate named in the doc map must carry the same type in code.
    for gate, gtype in doc_gate_type.items():
        assert state_tool.GATE_TYPE[gate] == gtype, gate


def test_real_data_gates_are_the_three_hard_gates():
    hard_in_code = {g for g, t in state_tool.GATE_TYPE.items() if t == "hard"}
    assert set(state_tool.REAL_DATA_GATES) == hard_in_code


def test_v3_layout_mirror_matches_rf_paths(references_dir):
    """The explicit v3 mirror table is a machine-checked view of rf_paths."""
    doc_paths = {}
    for line in _doc_text(references_dir).splitlines():
        match = _ARTIFACT_PATH_RE.match(line.strip())
        if match:
            artifact, path = match.groups()
            if artifact in rf_paths.ARTIFACT_DIRS:
                doc_paths[artifact] = path.rstrip("/")
    assert doc_paths == rf_paths.ARTIFACT_DIRS


def test_step_labels_match_entry_points_mirror(references_dir):
    """The user-facing Korean verb labels have one executable source."""
    labels = {}
    for line in (references_dir / "entry-points.md").read_text(encoding="utf-8").splitlines():
        match = _STEP_LABEL_RE.match(line.strip())
        if match:
            labels[int(match.group(1))] = match.group(2)
    assert labels == state_tool.STEP_LABELS_KO


# --- checklist JSON coherence (single-source integrity) ---

import json  # noqa: E402
import pathlib  # noqa: E402


def _checklists(references_dir):
    for path in sorted((references_dir / "checklists").glob("*.json")):
        yield path.stem, json.loads(path.read_text(encoding="utf-8"))


def test_checklist_ids_unique_and_well_formed(references_dir):
    seen_files = 0
    for name, cl in _checklists(references_dir):
        seen_files += 1
        ids = [i["id"] for i in cl["items"]]
        assert len(ids) == len(set(ids)), f"duplicate ids in {name}"
        for item in cl["items"]:
            assert isinstance(item.get("required"), bool), f"{item['id']} required must be bool"
            assert item.get("anchors"), f"{item['id']} needs anchors for coverage screening"
            assert item.get("section")
    assert seen_files >= 3  # strobe + record + tripod


def test_every_checklist_guideline_is_documented(references_dir):
    """Adding a checklist JSON without documenting it in checklist-templates.md
    (the human-readable source) breaks the build."""
    doc = (references_dir / "checklist-templates.md").read_text(encoding="utf-8").upper()
    for _name, cl in _checklists(references_dir):
        assert cl["guideline"].upper() in doc, f"{cl['guideline']} undocumented in checklist-templates.md"
