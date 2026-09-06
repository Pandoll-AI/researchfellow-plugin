#!/usr/bin/env python3
"""Preflight environment check for ResearchFellow (`/rf doctor`).

Reports interpreter version, optional stats packages, plugin version, and MCP
reachability as JSON on stdout. Stdlib only — package presence is probed with
importlib, never imported for use.

Usage:
    python3 env_check.py [--project-dir DIR] [--mcp-url URL]
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request

DEFAULT_MCP_URL = "https://researchfellow-mcp.vercel.app/api/mcp"
MCP_TIMEOUT_SECONDS = 3
PACKAGE_NAMES = ("pandas", "statsmodels", "lifelines")
PLUGIN_JSON = Path(__file__).resolve().parents[3] / ".claude-plugin" / "plugin.json"

DEFAULT_PROJECT_DIR_NAME = "research"
STATE_RELATIVE = Path(".system") / "state.json"
NEXT_ACTION_REAL = "실데이터 분석 가능. 합성 데모부터 시작하려면 /rf 아이디어 한 줄"
NEXT_ACTION_SYNTHETIC = (
    "합성 데모부터 시작: /rf <아이디어 한 줄>. 실데이터 분석 전 pip install -r requirements.txt"
)
NEXT_ACTION_PROJECT = "프로젝트가 있습니다. /rf 로 이어서 진행"

PING_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "ping", "arguments": {}},
}


def _python_info() -> Dict[str, Any]:
    version_info = sys.version_info
    return {
        "version": f"{version_info.major}.{version_info.minor}.{version_info.micro}",
        "ok": version_info >= (3, 10),
    }


def _package_version(name: str) -> Optional[str]:
    if importlib.util.find_spec(name) is None:
        return None
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _plugin_version() -> Optional[str]:
    try:
        payload = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = payload.get("version")
    if version is None:
        return None
    return str(version)


def _parse_sse(text: str) -> Any:
    chunks: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        chunks.append(data)
    if not chunks:
        raise ValueError("SSE response had no data")
    parsed = None
    last_error: Optional[Exception] = None
    for chunk in chunks:
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(parsed, dict) and (
            "result" in parsed or "server_version" in parsed
        ):
            return parsed
    if parsed is not None:
        return parsed
    if last_error is not None:
        raise last_error
    raise ValueError("SSE data was not JSON")


def _extract_server_version(obj: Any) -> Optional[str]:
    if isinstance(obj, dict):
        if obj.get("server_version") is not None:
            return str(obj["server_version"])
        result = obj.get("result")
        if isinstance(result, dict):
            found = _extract_server_version(result)
            if found is not None:
                return found
            structured = result.get("structuredContent") or result.get("structured_content")
            found = _extract_server_version(structured)
            if found is not None:
                return found
            for item in result.get("content") or []:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                text = item.get("text") or ""
                try:
                    nested = json.loads(text)
                except (TypeError, json.JSONDecodeError):
                    continue
                found = _extract_server_version(nested)
                if found is not None:
                    return found
    return None


def _mcp_post(url: str, payload: Dict[str, Any]) -> Any:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=MCP_TIMEOUT_SECONDS) as res:
        raw = res.read()
        content_type = (res.headers.get("Content-Type") or "").lower()
    text = raw.decode("utf-8", errors="replace")
    if "text/event-stream" in content_type or text.lstrip().startswith("data:"):
        return _parse_sse(text)
    return json.loads(text)


def check_mcp(url: str) -> Dict[str, Any]:
    try:
        parsed = _mcp_post(url, PING_PAYLOAD)
        return {
            "url": url,
            "reachable": True,
            "server_version": _extract_server_version(parsed),
        }
    except Exception:
        return {"url": url, "reachable": False, "server_version": None}


def _resolve_project_dir(project_dir: Optional[str]) -> Path:
    if project_dir:
        return Path(project_dir).expanduser().resolve()
    return (Path.cwd() / DEFAULT_PROJECT_DIR_NAME).resolve()


def _read_schema_version(state_path: Path) -> Optional[int]:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("schema_version")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def _project_info(project_dir: Optional[str] = None) -> Dict[str, Any]:
    resolved = _resolve_project_dir(project_dir)
    state_path = resolved / STATE_RELATIVE
    if not state_path.is_file():
        return {"dir": str(resolved), "exists": False, "schema_version": None}
    return {
        "dir": str(resolved),
        "exists": True,
        "schema_version": _read_schema_version(state_path),
    }


def _next_action(*, exists: bool, synthetic: bool, real: bool) -> str:
    if exists and synthetic:
        return NEXT_ACTION_PROJECT
    if real:
        return NEXT_ACTION_REAL
    return NEXT_ACTION_SYNTHETIC


def build_report(
    *,
    mcp_url: str = DEFAULT_MCP_URL,
    project_dir: Optional[str] = None,
) -> Dict[str, Any]:
    python = _python_info()
    packages = {name: _package_version(name) for name in PACKAGE_NAMES}
    synthetic = bool(python["ok"])
    real = bool(python["ok"] and all(packages.values()))
    project = _project_info(project_dir)
    return {
        "python": python,
        "packages": packages,
        "plugin_version": _plugin_version(),
        "mcp": check_mcp(mcp_url),
        "modes": {"synthetic": synthetic, "real": real},
        "next_action": _next_action(
            exists=bool(project["exists"]),
            synthetic=synthetic,
            real=real,
        ),
        "project": project,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ResearchFellow environment preflight")
    parser.add_argument("--project-dir", help="Path to the project directory")
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL, help="MCP server URL")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(mcp_url=args.mcp_url, project_dir=args.project_dir)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["python"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
