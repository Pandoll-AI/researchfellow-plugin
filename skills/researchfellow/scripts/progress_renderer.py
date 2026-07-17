#!/usr/bin/env python3
"""Deterministically render ResearchFellow's persistent human-readable views.

The renderer is deliberately a projection, not a state-machine judge: it reads
state.json and audit.jsonl through rf_paths and replaces the two Markdown views
at the project root.  It is fire-and-forget like telemetry.py, so every error is
reported as one stderr line and the process always exits zero.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from rf_paths import resolve_state_path, resolve_system_file
from state_tool import GATE_TYPE, STEP_LABELS_KO, STEP_NAMES, V1_GATE_MAP, detect_schema


STEP_STATUS_LABELS = {
    "completed": "done",
    "in_progress": "in progress",
    "pending": "pending",
    "skipped": "skipped",
    "imported": "imported",
}
GATE_STATUSES = {"pending", "approved", "rejected", "changes_requested"}
KNOWN_EVENTS = {
    "PROJECT_INIT", "STEP_STARTED", "STEP_COMPLETED", "GATE_APPROVED",
    "GATE_REJECTED", "GATE_CHANGES_REQUESTED", "ARTIFACT_CREATED",
    "ARTIFACT_UPDATED", "ENTRY_POINT", "ARTIFACT_IMPORTED",
    "ARTIFACT_REVERSE_FILLED", "GATE_RETROACTIVE", "MATERIAL_RECLASSIFIED",
    "PROVENANCE_ATTESTED", "ARTIFACT_INVALIDATED", "SCHEMA_UPGRADED",
    "PHI_DETECTED", "SESSION_RESUMED", "SYNTHETIC_DATA_GENERATED",
}
ARTIFACT_NAMES_KO = {
    "idea": "연구 질문", "literature": "문헌 검색 결과", "evidence_table": "근거표",
    "variables": "변수 정의", "protocol": "프로토콜", "sap": "SAP",
    "shells": "표·그림 틀", "synthetic_results": "합성 결과",
    "extraction_plan": "추출 계획", "qc_report": "QC 보고서",
    "real_results": "실분석 결과", "manuscript": "원고", "checklist": "체크리스트",
    "submission_package": "제출 패키지", "revision": "리뷰 대응",
}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


class _ArgumentParser(argparse.ArgumentParser):
    """Keep malformed command lines within the renderer's exit-zero contract."""

    def error(self, message: str) -> None:
        raise ValueError(message)


def _error(message: str) -> None:
    print(f"progress_renderer: {message}", file=sys.stderr)


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError("state.json must contain an object")
    return value


def _read_audit(path: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
    return events


def _step_number(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number in STEP_NAMES else None


def _status_label(step: Dict[str, Any]) -> str:
    status = step.get("status")
    if status == "blocked":
        reason = step.get("blocked_reason", step.get("reason"))
        return f"blocked ({reason})" if isinstance(reason, str) and reason.strip() else "blocked"
    return STEP_STATUS_LABELS.get(status, "pending")


def _step_rows(state: Dict[str, Any]) -> Iterable[Tuple[int, str, str]]:
    steps = state.get("steps") if isinstance(state.get("steps"), dict) else {}
    for number in range(1, 14):
        entry = steps.get(str(number))
        entry = entry if isinstance(entry, dict) else {}
        name = entry.get("name")
        yield number, name if isinstance(name, str) and name else STEP_NAMES[number], _status_label(entry)


def _completed_count(state: Dict[str, Any]) -> int:
    steps = state.get("steps") if isinstance(state.get("steps"), dict) else {}
    return sum(
        1 for number in range(1, 13)
        if isinstance(steps.get(str(number)), dict)
        and steps[str(number)].get("status") == "completed"
    )


def _imported_count(state: Dict[str, Any]) -> int:
    steps = state.get("steps") if isinstance(state.get("steps"), dict) else {}
    return sum(
        1 for number in range(1, 13)
        if isinstance(steps.get(str(number)), dict)
        and steps[str(number)].get("status") == "imported"
    )


def _active_revision_round(state: Dict[str, Any]) -> Optional[int]:
    steps = state.get("steps") if isinstance(state.get("steps"), dict) else {}
    revision = steps.get("13") if isinstance(steps.get("13"), dict) else {}
    rounds = revision.get("rounds") if isinstance(revision.get("rounds"), list) else []
    active = revision.get("status") == "in_progress"
    active_rounds = [
        _step_number(item.get("round"))
        for item in rounds if isinstance(item, dict) and item.get("status") == "in_progress"
    ]
    active = active or bool(active_rounds)
    if not active:
        return None
    all_rounds = [_step_number(item.get("round")) for item in rounds if isinstance(item, dict)]
    values = [value for value in all_rounds if value is not None]
    return max(values) if values else 1


def _gate_entries(state: Dict[str, Any]) -> Iterable[Tuple[str, str, str]]:
    schema, _ = detect_schema(state)
    raw = state.get("gates") if isinstance(state.get("gates"), dict) else {}
    for gate_id, gate_type in GATE_TYPE.items():
        entry: Any = raw.get(gate_id)
        if schema == "v1":
            legacy_id = next((key for key, value in V1_GATE_MAP.items() if value == gate_id), None)
            entry = raw.get(legacy_id) if legacy_id else None
        status = entry.get("status") if isinstance(entry, dict) else "pending"
        yield gate_id, gate_type, status if status in GATE_STATUSES else "pending"


def _blockers(state: Dict[str, Any]) -> List[str]:
    values = state.get("blockers") if isinstance(state.get("blockers"), list) else []
    rendered: List[str] = []
    for value in values:
        if isinstance(value, str) and value.strip():
            rendered.append(value.strip())
        elif isinstance(value, dict):
            reason = value.get("reason")
            if isinstance(reason, str) and reason.strip():
                rendered.append(reason.strip())
    return rendered


def render_progress(state: Dict[str, Any]) -> str:
    project_name = state.get("project_name")
    title = project_name if isinstance(project_name, str) and project_name.strip() else "ResearchFellow"
    lines = [f"# {title}", ""]
    card = state.get("research_card")
    if isinstance(card, str) and card.strip():
        lines.extend([card.strip(), ""])
    lines.extend(["## Progress", "", "| Step | Name | Status |", "|---|---|---|"])
    for number, name, status in _step_rows(state):
        lines.append(f"| {number} | {name} | {status} |")
    completion = f"완료 {_completed_count(state)}/12"
    imported = _imported_count(state)
    lines.extend(["", f"{completion} · 반입 {imported}단계" if imported else completion])
    revision_round = _active_revision_round(state)
    if revision_round is not None:
        lines.append(f"Revision round {revision_round} 진행 중")
    lines.extend(["", "## Gates", "", "| Gate | Type | Status |", "|---|---|---|"])
    for gate_id, gate_type, status in _gate_entries(state):
        lines.append(f"| {gate_id} | {gate_type} | {status} |")
    lines.extend(["", "## Blockers", ""])
    blockers = _blockers(state)
    lines.extend([f"- {item}" for item in blockers] if blockers else ["- 없음"])
    lines.extend(["", "## Next action", ""])
    next_action = state.get("next_action") if isinstance(state.get("next_action"), dict) else {}
    step = _step_number(next_action.get("step"))
    label = STEP_LABELS_KO.get(step) if step is not None else None
    if label is None:
        candidate = next_action.get("label")
        label = candidate.strip() if isinstance(candidate, str) and candidate.strip() else "없음"
    lines.append(f"- {label}" + (f" (Step {step})" if step is not None else ""))
    return "\n".join(lines) + "\n"


def _safe_artifact(details: Dict[str, Any]) -> Optional[str]:
    for key in ("artifact", "artifact_key"):
        artifact = details.get(key)
        if artifact in ARTIFACT_NAMES_KO:
            return artifact
    return None


def _safe_gate(details: Dict[str, Any]) -> Optional[str]:
    for key in ("gate", "gate_id"):
        gate = details.get(key)
        if gate in GATE_TYPE:
            return gate
    return None


def _safe_path_version(details: Dict[str, Any]) -> str:
    parts: List[str] = []
    path = details.get("path")
    if isinstance(path, str) and path:
        parts.append(path)
    version = details.get("version")
    if isinstance(version, int) and not isinstance(version, bool) and version >= 0:
        parts.append(f"v{version}")
    return f" ({' '.join(parts)})" if parts else ""


def _event_message(event: Dict[str, Any]) -> Optional[str]:
    event_type = event.get("event")
    if event_type not in KNOWN_EVENTS:
        return None
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    step = _step_number(event.get("step")) or _step_number(details.get("step"))
    artifact = _safe_artifact(details)
    gate = _safe_gate(details)
    subject = ARTIFACT_NAMES_KO.get(artifact) if artifact else STEP_LABELS_KO.get(step)
    messages = {
        "PROJECT_INIT": "프로젝트가 시작되었습니다",
        "STEP_STARTED": f"{subject}을 시작했습니다" if subject else "단계를 시작했습니다",
        "STEP_COMPLETED": f"{subject}이 완료되었습니다" if subject else "단계가 완료되었습니다",
        "GATE_APPROVED": f"{gate} gate가 승인되었습니다" if gate else "gate가 승인되었습니다",
        "GATE_REJECTED": f"{gate} gate가 반려되었습니다" if gate else "gate가 반려되었습니다",
        "GATE_CHANGES_REQUESTED": f"{gate} gate에 수정이 요청되었습니다" if gate else "gate에 수정이 요청되었습니다",
        "ARTIFACT_CREATED": f"{subject}이 생성되었습니다" if subject else "산출물이 생성되었습니다",
        "ARTIFACT_UPDATED": f"{subject}이 갱신되었습니다" if subject else "산출물이 갱신되었습니다",
        "ENTRY_POINT": "연구 시작 경로가 기록되었습니다",
        "ARTIFACT_IMPORTED": f"{subject}이 반입되어 확정되었습니다" if subject else "산출물이 반입되어 확정되었습니다",
        "ARTIFACT_REVERSE_FILLED": f"{subject}이 기존 자료에서 채워졌습니다" if subject else "기존 자료에서 산출물이 채워졌습니다",
        "GATE_RETROACTIVE": f"{gate} gate가 소급 승인되었습니다" if gate else "gate가 소급 승인되었습니다",
        "MATERIAL_RECLASSIFIED": "자료 분류가 갱신되었습니다",
        "PROVENANCE_ATTESTED": "분석 결과의 출처가 확인되었습니다",
        "ARTIFACT_INVALIDATED": f"{subject}은 다시 검토가 필요합니다" if subject else "산출물은 다시 검토가 필요합니다",
        "SCHEMA_UPGRADED": "프로젝트 구조가 업데이트되었습니다",
        "PHI_DETECTED": "개인정보 위험 신호가 감지되어 검토가 필요합니다",
        "SESSION_RESUMED": "연구를 다시 이어서 진행했습니다",
        "SYNTHETIC_DATA_GENERATED": "합성 데이터 드라이런이 준비되었습니다",
    }
    return messages[event_type] + _safe_path_version(details)


def render_research_log(events: Iterable[Dict[str, Any]]) -> str:
    lines = ["# RESEARCH_LOG", ""]
    current_date: Optional[str] = None
    rendered = 0
    for event in events:
        timestamp = event.get("timestamp")
        if not isinstance(timestamp, str) or not _DATE_RE.match(timestamp):
            continue
        message = _event_message(event)
        if message is None:
            continue
        date = timestamp[:10]
        if date != current_date:
            if current_date is not None:
                lines.append("")
            lines.append(f"## {date}")
            current_date = date
        lines.append(f"- {date} — {message}")
        rendered += 1
    if rendered == 0:
        lines.append("기록된 진행 이벤트가 없습니다.")
    return "\n".join(lines) + "\n"


def render(project_dir: str) -> None:
    state_path = resolve_state_path(project_dir)
    audit_path = resolve_system_file(project_dir, "audit")
    state = _load_json(state_path)
    events = _read_audit(audit_path)
    root = Path(project_dir)
    (root / "PROGRESS.md").write_text(render_progress(state), encoding="utf-8")
    (root / "RESEARCH_LOG.md").write_text(render_research_log(events), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description="Render deterministic ResearchFellow progress views.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("render")
    command.add_argument("--project-dir", required=True)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        render(args.project_dir)
    except SystemExit as exc:
        if exc.code not in (0, None):
            _error("invalid command")
    except Exception as exc:  # fire-and-forget contract: no renderer error blocks work
        _error(str(exc).splitlines()[0] or exc.__class__.__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
