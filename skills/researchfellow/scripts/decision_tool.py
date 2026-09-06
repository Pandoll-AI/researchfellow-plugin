#!/usr/bin/env python3
"""Record, list, and check research decisions (decisions.jsonl).

Stdlib only. JSON on stdout; exit codes are the contract.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import phi_detect
from rf_paths import resolve_state_path, resolve_system_dir, resolve_system_file

POST_HOC_FIELDS = frozenset({"primary_outcome", "primary_analysis"})
SOURCES = ("user", "recommended_accepted", "autonomous")
LEVELS = ("A", "B", "C")
KINDS = ("decision", "knowledge_check", "exploratory")
SLUG_RE = re.compile(r"^[a-z0-9_]{1,40}$")
T0_UNKNOWN_NOTE = (
    "실제 분석 결과는 등록되어 있으나 산출 시점을 확인할 수 없습니다. "
    "verified_at, 감사 기록의 ARTIFACT_CREATED, 결과 파일 시각이 모두 없습니다."
)


def _emit(payload: Any, exit_code: int) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def _require_project_dir(project_dir: str) -> None:
    if not os.path.isdir(project_dir):
        _emit({"error": f"project directory not found: {project_dir}"}, 1)


def _decisions_path(project_dir: str) -> str:
    return os.path.join(resolve_system_dir(project_dir), "decisions.jsonl")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.isfile(path):
        return []
    records: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def _needs_leading_newline(path: str) -> bool:
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return False
    with open(path, "rb") as file:
        file.seek(-1, os.SEEK_END)
        return file.read(1) != b"\n"


def _append_jsonl(path: str, record: Dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    prefix = "\n" if _needs_leading_newline(path) else ""
    with open(path, "a", encoding="utf-8") as file:
        file.write(prefix + json.dumps(record, ensure_ascii=False) + "\n")


def _require_slug(value: Optional[str]) -> None:
    if not isinstance(value, str) or not SLUG_RE.fullmatch(value):
        _emit({"error": "field must be a slug"}, 1)


def _next_decision_id(path: str) -> str:
    count = 0
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    count += 1
    return f"d-{count + 1:04d}"


def _redact(text: str) -> Tuple[str, List[str]]:
    if not text:
        return text, []
    try:
        masked, findings = phi_detect.redact_text(text)
    except Exception:
        return "", []
    rules: List[str] = []
    for finding in findings:
        rule_id = finding.get("rule_id")
        if isinstance(rule_id, str) and rule_id not in rules:
            rules.append(rule_id)
    return masked, rules


def _merge_rules(*groups: Iterable[str]) -> List[str]:
    merged: List[str] = []
    for group in groups:
        for rule_id in group:
            if rule_id not in merged:
                merged.append(rule_id)
    return merged


def _load_state(project_dir: str) -> Dict[str, Any]:
    path = resolve_state_path(project_dir)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _real_result_ids_from_registry() -> Optional[set]:
    try:
        from state_tool import ARTIFACT_PRODUCED_BY
    except Exception:
        return None
    if not isinstance(ARTIFACT_PRODUCED_BY, dict):
        return None
    return {
        key for key in ARTIFACT_PRODUCED_BY
        if isinstance(key, str) and (key == "real_results" or "real_results" in key)
    }


def _is_real_result_key(key: str, known: Optional[set]) -> bool:
    if known is not None:
        return key in known
    return "real_results" in key or "analysis_output" in key


def _real_result_artifacts(state: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    known = _real_result_ids_from_registry()
    found: List[Tuple[str, Dict[str, Any]]] = []
    for key, entry in artifacts.items():
        if not isinstance(key, str) or not _is_real_result_key(key, known):
            continue
        found.append((key, entry if isinstance(entry, dict) else {}))
    return found


def _artifact_created_times(events: List[Dict[str, Any]], artifact_id: str) -> List[datetime]:
    times: List[datetime] = []
    for event in events:
        if event.get("event") != "ARTIFACT_CREATED":
            continue
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        if details.get("artifact") != artifact_id and details.get("artifact_key") != artifact_id:
            continue
        parsed = _parse_iso(event.get("timestamp"))
        if parsed is not None:
            times.append(parsed)
    return times


def _mtime_utc(project_dir: str, rel_path: Any) -> Optional[datetime]:
    if not isinstance(rel_path, str) or not rel_path:
        return None
    full = rel_path if os.path.isabs(rel_path) else os.path.join(project_dir, rel_path)
    if not os.path.isfile(full):
        return None
    return datetime.fromtimestamp(os.path.getmtime(full), tz=timezone.utc)


def _valid_real_results(state: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    return [
        (artifact_id, entry)
        for artifact_id, entry in _real_result_artifacts(state)
        if entry.get("validity") == "valid"
    ]


def _resolve_t0(state: Dict[str, Any], project_dir: str) -> Tuple[Optional[datetime], Optional[str]]:
    real_results = _valid_real_results(state)
    if not real_results:
        return None, None
    events = _read_jsonl(resolve_system_file(project_dir, "audit"))
    candidates: List[datetime] = []
    for artifact_id, entry in real_results:
        verified = _parse_iso(entry.get("verified_at"))
        if verified is not None:
            candidates.append(verified)
            continue
        created = _artifact_created_times(events, artifact_id)
        if created:
            candidates.extend(created)
            continue
        mtime = _mtime_utc(project_dir, entry.get("path"))
        if mtime is not None:
            candidates.append(mtime)
    if not candidates:
        return None, "t0_unknown"
    return min(candidates), None


def cmd_record(args: argparse.Namespace) -> None:
    _require_project_dir(args.project_dir)
    kind = args.kind
    level = args.level
    field = args.field
    if kind == "knowledge_check":
        if not level:
            level = "A"
        if not field:
            field = "knowledge_check"
    elif not level or not field:
        _emit({"error": "--level and --field are required when --kind is decision"}, 1)
    _require_slug(field)
    if args.artifact_ref is not None:
        _require_slug(args.artifact_ref)

    path = _decisions_path(args.project_dir)
    decision_id = _next_decision_id(path)

    chosen, chosen_rules = _redact(args.chosen)
    alternatives: List[str] = []
    alternative_rules: List[str] = []
    for part in (args.alternatives or "").split("|"):
        item = part.strip()
        if not item:
            continue
        masked, rules = _redact(item)
        alternatives.append(masked)
        alternative_rules.extend(rules)
    rationale, rationale_rules = _redact(args.rationale or "")
    impact, impact_rules = _redact(args.impact or "")
    rules = _merge_rules(chosen_rules, alternative_rules, rationale_rules, impact_rules)

    record: Dict[str, Any] = {
        "id": decision_id,
        "at": _utc_now(),
        "step": args.step,
        "level": level,
        "field": field,
        "chosen": chosen,
        "alternatives": alternatives,
        "rationale": rationale,
        "impact": impact,
        "source": args.source,
        "artifact_ref": args.artifact_ref,
        "kind": kind,
    }
    if rules:
        record["_phi"] = {"masked": True, "rules": rules}

    _append_jsonl(path, record)
    _append_jsonl(resolve_system_file(args.project_dir, "audit"), {
        "timestamp": record["at"],
        "event": "DECISION_RECORDED",
        "details": {
            "decision_id": decision_id,
            "step": args.step,
            "level": level,
            "field": field,
            "kind": kind,
        },
    })
    _emit(record, 0)


def cmd_list(args: argparse.Namespace) -> None:
    _require_project_dir(args.project_dir)
    records = _read_jsonl(_decisions_path(args.project_dir))
    if args.field is not None:
        records = [item for item in records if item.get("field") == args.field]
    if args.step is not None:
        records = [item for item in records if item.get("step") == args.step]
    _emit(records, 0)


def _last_pre_t0_chosen(records: List[Dict[str, Any]], field: Any, t0: datetime) -> Optional[str]:
    last: Optional[str] = None
    last_at: Optional[datetime] = None
    for record in records:
        if record.get("field") != field:
            continue
        at = _parse_iso(record.get("at"))
        if at is None or at > t0:
            continue
        chosen = record.get("chosen")
        if last_at is None or at >= last_at:
            last_at = at
            last = chosen if isinstance(chosen, str) else None
    return last


def _post_hoc_ids(records: List[Dict[str, Any]], t0: datetime) -> List[str]:
    """Return ids of post-T0 primary_* changes that are still in effect.

    A field is judged by its *latest* post-T0 decision record: if that record
    reverts to the last pre-T0 value, the whole field is treated as restored and
    none of its earlier post-hoc records are reported. Exploratory and
    knowledge_check records never count.
    """
    by_field: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        if record.get("kind") != "decision":
            continue
        field = record.get("field")
        if field not in POST_HOC_FIELDS:
            continue
        at = _parse_iso(record.get("at"))
        if at is None or not (at > t0):
            continue
        by_field.setdefault(field, []).append(record)

    ids: List[str] = []
    for field, post in by_field.items():
        baseline = _last_pre_t0_chosen(records, field, t0)
        post.sort(key=lambda r: (_parse_iso(r.get("at")) or t0, str(r.get("id"))))
        latest = post[-1].get("chosen")
        if baseline is not None and isinstance(latest, str) and latest == baseline:
            continue  # reverted — field restored to its pre-specified value
        for record in post:
            chosen = record.get("chosen")
            if baseline is not None and isinstance(chosen, str) and chosen == baseline:
                continue
            decision_id = record.get("id")
            if isinstance(decision_id, str):
                ids.append(decision_id)
    return ids


def cmd_check(args: argparse.Namespace) -> None:
    _require_project_dir(args.project_dir)
    state = _load_state(args.project_dir)
    t0, t0_reason = _resolve_t0(state, args.project_dir)
    if t0 is None:
        if t0_reason == "t0_unknown":
            _emit({"ok": False, "reason": "t0_unknown", "note": T0_UNKNOWN_NOTE}, 0)
        _emit({"ok": True}, 0)
    ids = _post_hoc_ids(_read_jsonl(_decisions_path(args.project_dir)), t0)
    if not ids:
        _emit({"ok": True}, 0)
    _emit({
        "ok": False,
        "blocker": {
            "what": "실제 분석 결과를 확인한 뒤에 주요 결과 지표 또는 주분석이 바뀌었습니다",
            "why": "결과를 본 뒤 주요 지표나 주분석을 바꾸면, 미리 정해 둔 분석이 아니라 결과에 맞춰 해석을 고른 것으로 읽힙니다",
            "unlock": "원래 사전 지정한 지표로 되돌리거나, 이번 변경을 탐색적 분석으로 기록하면 진행할 수 있습니다",
            "meanwhile": "탐색적 분석과 민감도 분석은 따로 정리할 수 있습니다",
        },
        "decisions": ids,
    }, 2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record and check ResearchFellow decisions.")
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record")
    record.add_argument("--project-dir", required=True)
    record.add_argument("--step", required=True, type=int)
    record.add_argument("--level", choices=LEVELS)
    record.add_argument("--field")
    record.add_argument("--chosen", required=True)
    record.add_argument("--alternatives", default="")
    record.add_argument("--rationale", default="")
    record.add_argument("--impact", default="")
    record.add_argument("--source", required=True, choices=SOURCES)
    record.add_argument("--artifact-ref", dest="artifact_ref", default=None)
    record.add_argument("--kind", choices=KINDS, default="decision")

    listing = sub.add_parser("list")
    listing.add_argument("--project-dir", required=True)
    listing.add_argument("--field")
    listing.add_argument("--step", type=int)

    check = sub.add_parser("check")
    check.add_argument("--project-dir", required=True)
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "record":
        cmd_record(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "check":
        cmd_check(args)
    else:  # pragma: no cover - argparse enforces choices
        _emit({"error": f"unknown command {args.command}"}, 1)


if __name__ == "__main__":
    main()
