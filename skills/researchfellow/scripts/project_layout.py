#!/usr/bin/env python3
"""Create and safely migrate the visible ResearchFellow schema-v3 layout."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, Iterable, List, Tuple

from rf_paths import ARTIFACT_DIRS, MATERIALS_DIR, REHEARSAL_DIR, STEP_DIRS, SYSTEM_DIR, detect_layout

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = SCRIPT_DIR.parent / "templates"

STEP_READMES: Dict[int, Tuple[str, str]] = {
    1: ("PICO Structuring", "연구 질문을 PICO 구조로 정리하는 단계입니다.\n확정된 질문과 핵심 가정은 idea.json에 기록됩니다."),
    2: ("Literature Scoping", "관련 문헌의 범위와 검색 과정을 정리하는 단계입니다.\n검색 결과와 근거 자료가 이 폴더에 쌓입니다."),
    3: ("Evidence Table", "문헌 근거를 비교 가능한 표로 정리하는 단계입니다.\n연구별 핵심 결과와 근거 요약이 생성됩니다."),
    4: ("Variable Definition", "노출, 결과, 공변량과 측정 기준을 정의하는 단계입니다.\n변수 정의와 데이터 사전이 이곳에 생성됩니다."),
    5: ("Protocol", "연구 설계와 분석 전 계획을 프로토콜로 확정하는 단계입니다.\n검토용 protocol.md가 이 폴더에 생성됩니다."),
    6: ("Statistical Analysis Plan", "추정 대상과 분석 방법을 사전에 명시하는 단계입니다.\n재현 가능한 SAP 문서가 이 폴더에 생성됩니다."),
    7: ("Table and Figure Shells", "결과를 담을 표와 그림의 뼈대를 준비하는 단계입니다.\n표와 그림 shell 파일이 이 폴더에 생성됩니다."),
    8: ("Synthetic Dry-Run", "합성 자료로 분석 흐름을 점검하는 단계입니다.\nNOT REAL DATA로 표시된 synthetic_results가 이 폴더에 생성됩니다."),
    9: ("Data Preparation and QC", "실제 자료의 추출 계획과 품질 점검을 준비하는 단계입니다.\nextraction plan과 QC report가 이곳에 생성됩니다."),
    10: ("Real Analysis", "승인된 계획에 따라 실제 자료를 분석하는 단계입니다.\nreal results, analysis plan, 재현 가능한 분석 스크립트가 이 폴더에 생성됩니다."),
    11: ("Manuscript", "검증된 결과를 원고와 보고 체크리스트로 정리하는 단계입니다.\nmanuscript와 checklist가 이 폴더에 생성됩니다."),
    12: ("Submission Package", "제출에 필요한 원고와 부속 문서를 묶는 단계입니다.\nsubmission package가 이 폴더에 생성됩니다."),
    13: ("Revision Loop", "심사 의견에 맞춰 수정과 응답을 반복하는 단계입니다.\n각 수정 라운드는 round-N 폴더에 정리됩니다."),
}


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _ensure_gitignore(path: Path) -> None:
    required = ("00_materials/", ".system/desk/")
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [line for line in required if line not in existing]
    if missing:
        prefix = "\n" if existing and existing[-1] != "" else ""
        path.write_text("\n".join(existing) + prefix + "\n".join(missing) + "\n", encoding="utf-8")


def _init_tree(project_dir: Path, create_state: bool = True) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    for step, dirname in STEP_DIRS.items():
        folder = project_dir / dirname
        folder.mkdir(exist_ok=True)
        title, prose = STEP_READMES[step]
        _write_if_missing(folder / "README.md", f"# {title}\n\n{prose}\n")
    rehearsal = project_dir / REHEARSAL_DIR
    rehearsal.mkdir(exist_ok=True)
    _write_if_missing(rehearsal / "README.md", "# Rehearsal\n\nNOT REAL DATA 전용 연습 공간입니다.\n이 폴더의 산출물은 연구 DAG와 실제 분석 결과에 포함되지 않습니다.\n")
    (project_dir / MATERIALS_DIR).mkdir(exist_ok=True)
    (project_dir / SYSTEM_DIR / "desk").mkdir(parents=True, exist_ok=True)
    _ensure_gitignore(project_dir / ".gitignore")
    if create_state:
        state_path = project_dir / SYSTEM_DIR / "state.json"
        if not state_path.exists():
            state = json.loads((TEMPLATE_DIR / "project-init.json").read_text(encoding="utf-8"))
            state["schema_version"] = 3
            state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_if_missing(project_dir / SYSTEM_DIR / "audit.jsonl", "")
    _write_if_missing(project_dir / SYSTEM_DIR / "materials.json", json.dumps({
        "schema_version": 1, "materials": [], "version_groups": {},
    }, indent=2) + "\n")
    compliance_path = project_dir / SYSTEM_DIR / "compliance-checklist.json"
    if not compliance_path.exists():
        shutil.copyfile(TEMPLATE_DIR / "compliance-checklist-template.json", compliance_path)


def init(project_dir: str) -> int:
    _init_tree(Path(project_dir))
    return 0


def _relative_target(artifact: str, old_path: str) -> str:
    """Map a registry path to its v3 step directory, retaining its basename."""
    normalized = old_path.replace("\\", "/").rstrip("/")
    base = os.path.basename(normalized)
    target_dir = ARTIFACT_DIRS[artifact]
    if artifact in {"literature", "shells", "synthetic_results", "real_results", "submission_package", "revision"}:
        return target_dir
    if base == os.path.basename(target_dir):
        return target_dir
    return os.path.join(target_dir, base).replace("\\", "/")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_relative(root: Path, raw: str, label: str) -> str:
    """Validate a declared relative path before joining it to a project root."""
    normalized = raw.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(normalized)
    if (not normalized or posix.is_absolute() or windows.is_absolute() or ".." in posix.parts):
        raise RuntimeError(f"unsafe migration path for {label}: {raw!r}")
    # The resolve check is deliberate: a path can otherwise escape through a symlink.
    candidate = (root / Path(*posix.parts)).resolve(strict=False)
    if not _is_within(candidate, root.resolve()):
        raise RuntimeError(f"unsafe migration path for {label}: {raw!r}")
    return str(posix)


def _assert_no_external_symlinks(source: Path) -> None:
    root = source.resolve()
    for current, dirs, files in os.walk(source, followlinks=False):
        for name in list(dirs) + list(files):
            path = Path(current) / name
            if path.is_symlink() and not _is_within(path.resolve(strict=False), root):
                raise RuntimeError(f"external symlink in migration source: {path}")


def _load_v2_state(source: Path) -> dict:
    state_path = source / "state.json"
    if not state_path.is_file():
        raise RuntimeError("legacy state.json is missing")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("legacy state.json is invalid JSON") from exc
    gates = state.get("gates")
    semantic_gates = isinstance(gates, dict) and all(
        isinstance(key, str) and key.startswith("gate.") for key in gates
    )
    if state.get("schema_version") != 2 or not semantic_gates:
        if "schema_version" not in state and isinstance(gates, dict) and gates and all(str(key).isdigit() for key in gates):
            raise RuntimeError("v1 project detected: open it once with /rf to lazy-upgrade to v2, then migrate to v3")
        raise RuntimeError("--from must be an exact v2 state (schema_version: 2 with semantic gate.* keys)")
    return state


def _add_move(moves: List[Dict[str, str]], source: str, target: str) -> None:
    item = {"from": source, "to": target}
    if item not in moves:
        moves.append(item)


def _validate_plan(source: Path, moves: Iterable[Dict[str, str]], registry_labels: Dict[str, str]) -> List[Dict[str, str]]:
    """Normalize every move and reject target aliases before any move can run."""
    normalized: List[Dict[str, str]] = []
    target_sources: Dict[str, List[str]] = {}
    for move in moves:
        source_rel = _safe_relative(source, move["from"], registry_labels.get(move["from"], f"path {move['from']}"))
        target_rel = _safe_relative(source, move["to"], f"migration target {move['to']}")
        if source_rel == target_rel:
            continue
        item = {"from": source_rel, "to": target_rel}
        if item not in normalized:
            normalized.append(item)
            target_sources.setdefault(target_rel, []).append(source_rel)
    collisions = {
        target: sorted(set(sources)) for target, sources in target_sources.items()
        if len(set(sources)) > 1
    }
    if collisions:
        rendered = "; ".join(f"{target} <- {', '.join(sources)}" for target, sources in sorted(collisions.items()))
        raise RuntimeError(f"migration target collisions: {rendered}")
    return normalized


def _build_migration_plan(source: Path) -> Tuple[dict, List[Dict[str, str]]]:
    """Purely calculate the full migration plan against the unmodified source."""
    state = _load_v2_state(source)
    moves: List[Dict[str, str]] = []
    labels: Dict[str, str] = {}
    if (source / "materials").exists():
        _add_move(moves, "materials", MATERIALS_DIR)

    legacy_moves = {
        "idea.json": "01_pico/idea.json", "pico.json": "01_pico/pico.json",
        "literature": "02_literature/literature",
        "evidence-table.json": "03_evidence_table/evidence-table.json",
        "evidence_table.json": "03_evidence_table/evidence_table.json",
        "variables.json": "04_variables/variables.json",
        "variable-definitions.json": "04_variables/variable-definitions.json",
        "protocol.md": "05_protocol/protocol.md", "sap.md": "06_sap/sap.md",
        "shells": "07_shells/shells", "analysis/synthetic": "08_dry_run/synthetic_results",
        "extraction-plan.md": "09_data_qc/extraction-plan.md",
        "extraction_plan.md": "09_data_qc/extraction_plan.md",
        "extraction-plan.dsl": "09_data_qc/extraction-plan.dsl",
        "extraction-plan.sql": "09_data_qc/extraction-plan.sql",
        "qc-report.json": "09_data_qc/qc-report.json", "analysis/real": "10_analysis/real_results",
        "analysis/scripts": "10_analysis/scripts", "analysis-plan.json": "10_analysis/analysis-plan.json",
        "manuscript.md": "11_manuscript/manuscript.md", "checklist.json": "11_manuscript/checklist.json",
        "checklist.md": "11_manuscript/checklist.md", "submission": "12_submission/submission_package",
        "submission-package": "12_submission/submission_package", "revision": "13_revision",
    }
    for source_rel, target_rel in legacy_moves.items():
        if (source / source_rel).exists():
            _add_move(moves, source_rel, target_rel)

    for artifact, entry in (state.get("artifacts") or {}).items():
        if artifact not in ARTIFACT_DIRS or not isinstance(entry, dict):
            continue
        old_path = entry.get("path")
        if not isinstance(old_path, str) or not old_path:
            continue
        # Validate every registry declaration even when its file no longer exists.
        source_rel = _safe_relative(source, old_path, f"artifacts.{artifact}.path")
        labels[source_rel] = f"artifacts.{artifact}.path"
        new_path = _relative_target(artifact, source_rel)
        _add_move(moves, source_rel, new_path)

    for name in ("state.json", "audit.jsonl", "materials.json", "scan-report.json", "compliance-checklist.json"):
        if (source / name).exists():
            _add_move(moves, name, f"{SYSTEM_DIR}/{name}")
    for path in source.glob("phi-report_*.json"):
        _add_move(moves, path.name, f"{SYSTEM_DIR}/{path.name}")
    if (source / "desk").exists():
        _add_move(moves, "desk", f"{SYSTEM_DIR}/desk")
    return state, _validate_plan(source, moves, labels)


def _move_in_staging(root: Path, source_rel: str, target_rel: str) -> None:
    source = root / source_rel
    target = root / target_rel
    if not source.exists():
        return
    if target.exists():
        raise RuntimeError(f"migration target already exists: {target_rel}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))


def _rewrite_stored_as(value: Any, stage: Path, label: str) -> Any:
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if key == "stored_as" and isinstance(item, str):
                rewritten = f"{MATERIALS_DIR}/{item[len('materials/'):]}" if item.startswith("materials/") else item
                rel = _safe_relative(stage, rewritten, label)
                if not (stage / rel).exists():
                    raise RuntimeError(f"rewritten {label} does not exist: {rewritten}")
                value[key] = rel
            else:
                _rewrite_stored_as(item, stage, label)
    elif isinstance(value, list):
        for item in value:
            _rewrite_stored_as(item, stage, label)
    return value


def _rewrite_round_paths(state: dict) -> None:
    rounds = ((state.get("steps") or {}).get("13") or {}).get("rounds") or []
    for index, entry in enumerate(rounds):
        if not isinstance(entry, dict):
            continue
        for key in ("response_letter", "diff"):
            value = entry.get(key)
            if isinstance(value, str) and value.startswith("revision/"):
                entry[key] = f"13_revision/{value[len('revision/'):]}"


def _rewrite_material_registries(stage: Path) -> None:
    for name in ("materials.json", "scan-report.json"):
        path = stage / SYSTEM_DIR / name
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{name} is invalid JSON") from exc
        _rewrite_stored_as(payload, stage, f"{SYSTEM_DIR}/{name}.stored_as")
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _append_upgrade_audit(stage: Path, source: Path, source_state: dict) -> None:
    receipt = {
        "project_id": source_state.get("project_id"),
        "source_path": str(source.resolve()),
    }
    audit_path = stage / SYSTEM_DIR / "audit.jsonl"
    with audit_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "event": "SCHEMA_UPGRADED",
            "details": {"from_schema": 2, "to_schema": 3, "migration_receipt": receipt},
        }, ensure_ascii=False) + "\n")


def _validate_promoted_state(stage: Path) -> None:
    from state_tool import do_validate

    state_path = stage / SYSTEM_DIR / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    report, code = do_validate(state, str(stage))
    if code:
        raise RuntimeError(f"migrated v3 state failed validation: {json.dumps(report['violations'], ensure_ascii=False)}")


def _execute_migration(stage: Path, source: Path, source_state: dict, plan: List[Dict[str, str]]) -> None:
    for move in plan:
        _move_in_staging(stage, move["from"], move["to"])
    analysis_dir = stage / "analysis"
    if analysis_dir.is_dir() and not any(analysis_dir.iterdir()):
        analysis_dir.rmdir()

    state = source_state.copy()
    state["schema_version"] = 3
    for artifact, entry in (state.get("artifacts") or {}).items():
        if artifact in ARTIFACT_DIRS and isinstance(entry, dict) and isinstance(entry.get("path"), str) and entry["path"]:
            entry["path"] = _relative_target(artifact, _safe_relative(source, entry["path"], f"artifacts.{artifact}.path"))
    _rewrite_round_paths(state)
    system = stage / SYSTEM_DIR
    system.mkdir(exist_ok=True)
    (system / "state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _init_tree(stage, create_state=False)
    _rewrite_material_registries(stage)
    _append_upgrade_audit(stage, source, source_state)
    _validate_promoted_state(stage)


def _matching_receipt(target: Path, source: Path) -> bool:
    source_state = _load_v2_state(source)
    project_id = source_state.get("project_id")
    if not project_id:
        return False
    audit_path = target / SYSTEM_DIR / "audit.jsonl"
    try:
        events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError):
        return False
    return any(
        event.get("event") == "SCHEMA_UPGRADED"
        and isinstance(event.get("details"), dict)
        and (event["details"].get("migration_receipt") or {}).get("project_id") == project_id
        and (event["details"].get("migration_receipt") or {}).get("source_path") == str(source.resolve())
        for event in events
    )


def migrate(source: str, target: str, dry_run: bool = False) -> int:
    source_path, target_path = Path(source), Path(target)
    if target_path.exists() and detect_layout(str(target_path)) == "v3":
        if source_path.exists() and detect_layout(str(source_path)) == "legacy":
            if not _matching_receipt(target_path, source_path):
                raise RuntimeError("--to is an existing v3 project, but its migration receipt does not match --from; legacy source was preserved for manual cleanup")
            shutil.rmtree(source_path)
        return 0
    if not source_path.is_dir() or detect_layout(str(source_path)) != "legacy":
        raise RuntimeError("--from must be a legacy project containing state.json")
    if target_path.exists():
        raise RuntimeError("--to already exists and is not a v3 project")

    _assert_no_external_symlinks(source_path)
    source_state, plan = _build_migration_plan(source_path)
    if dry_run:
        print(json.dumps({"from": str(source_path), "to": str(target_path), "moves": plan}, indent=2))
        return 0

    with tempfile.TemporaryDirectory(prefix="rf-v3-", dir=str(target_path.parent)) as temp:
        stage = Path(temp) / target_path.name
        shutil.copytree(source_path, stage, symlinks=True)
        _execute_migration(stage, source_path, source_state, plan)
        shutil.move(str(stage), str(target_path))
    # A promoted target is never enough evidence to delete the source; receipt
    # identity must agree with the source that remains on disk.
    if not _matching_receipt(target_path, source_path):
        raise RuntimeError("v3 project was promoted, but migration receipt cannot prove source identity; legacy source was preserved for manual cleanup")
    shutil.rmtree(source_path)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage ResearchFellow schema-v3 project layout")
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("--project-dir", default="research")
    p_migrate = sub.add_parser("migrate")
    p_migrate.add_argument("--from", dest="source", default=".research")
    p_migrate.add_argument("--to", dest="target", default="research")
    p_migrate.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        code = init(args.project_dir) if args.command == "init" else migrate(args.source, args.target, args.dry_run)
    except (OSError, RuntimeError, shutil.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
