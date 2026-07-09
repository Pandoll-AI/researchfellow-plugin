"""Drift guard: the tables in references/state-machine.md MUST stay identical to
the constants in state_tool.py. This replaces the "cross-checked in review"
comment (state_tool.py:18-22) with a machine check, so doc/code divergence breaks
the build instead of silently rotting.
"""

from __future__ import annotations

import re

import state_tool

DOC = "state-machine.md"

# | gate.x | anchor |
_ANCHOR_RE = re.compile(r"^\|\s*(gate\.[\w-]+)\s*\|\s*(\w+)\s*\|\s*$")
# | 9 | gate.qc | hard |
_V1MAP_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*(gate\.[\w-]+)\s*\|\s*(soft|hard)\s*\|")


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
