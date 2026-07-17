#!/usr/bin/env python3
"""ResearchFellow Desk — one-shot local HTML interface server.

Serves a single interactive page (S1 PICO interview or the 13-step dashboard,
templates/desk/desk.html) on 127.0.0.1, waits for exactly one submission, writes
the answers to <project-dir>/desk/answers-<session>.json, and exits. The host
LLM launches this in the background, the browser opens, and the LLM continues
when the process ends. The server never touches state.json or audit.jsonl —
rendering and collecting only; all state writing stays with the host LLM.

Enhancement, not dependency: if this can't run (headless SSH, no port, timeout,
or the user clicks "그냥 채팅으로 할게요"), the chat procedures in
references/entry-points.md take over unchanged. Contract: references/desk-interface.md.

Usage:
    desk_server.py --probe-headless
    desk_server.py --view {s1_interview|dashboard} --payload <payload.json> \
        --project-dir .research --session-id <id> \
        [--port-start 4321] [--timeout-min 8] [--no-browser]

Exit codes:
    0  submitted (answers file written)
    1  input error (payload missing/unreadable)
    2  no available port
    3  timeout (no submission)
    4  user requested chat fallback
Status is double-written to <project-dir>/desk/session-<id>.json so the exit
code never has to be the only signal.

Security: binds 127.0.0.1 only; every GET/POST must carry the one-shot session
token (?t=...); free-text answer fields are masked through phi_detect before
they touch disk; the token is masked in access logs.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import platform
import re
import secrets
import socketserver
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from rf_paths import resolve_desk_dir

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(SCRIPT_DIR, "..", "templates", "desk", "desk.html")
PORT_CANDIDATES = [4321] + list(range(3000, 5000))
DEFAULT_TIMEOUT_MIN = 8.0

# PHI engine — soft import (same rationale as material_scanner.py): a missing
# engine must not kill the Desk, but the fallback is fail-closed per field —
# free-text answers are withheld, never stored unscreened.
sys.path.insert(0, SCRIPT_DIR)
try:
    import phi_detect
except ImportError:  # pragma: no cover - both files ship together
    phi_detect = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Headless detection (see desk-interface.md for the decision table)
# ---------------------------------------------------------------------------
def is_likely_headless() -> Tuple[bool, List[str]]:
    signals: List[str] = []
    for var in ("SSH_CONNECTION", "SSH_TTY", "SSH_CLIENT"):
        if os.environ.get(var):
            signals.append(f"env:{var}")
    # DISPLAY absence only means something on Linux — macOS/Windows desktop
    # sessions never set it.
    if platform.system() == "Linux":
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            signals.append("linux:no-display")
    return (len(signals) > 0, signals)


# ---------------------------------------------------------------------------
# Answer masking (free-text fields only — structured PICO terms are short
# clinical vocabulary; the free_text_keys list in the payload marks the rest)
# ---------------------------------------------------------------------------
def _mask_answers(answers: Any, free_text_keys: List[str]) -> Tuple[Any, Dict[str, Any]]:
    findings_total = 0
    screened = phi_detect is not None

    def mask_value(text: str) -> str:
        nonlocal findings_total, screened
        if phi_detect is None:
            return ""  # fail-closed: unscreened free text never touches disk
        try:
            masked, findings = phi_detect.redact_text(text)
            findings_total += len(findings)
            return masked
        except Exception:
            screened = False
            return ""

    def walk(node: Any, key: Optional[str] = None) -> Any:
        if isinstance(node, dict):
            return {k: walk(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, key) for v in node]
        if isinstance(node, str) and key in free_text_keys:
            return mask_value(node)
        return node

    masked = walk(answers)
    return masked, {"screened": screened, "finding_count": findings_total,
                    "free_text_keys": free_text_keys}


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
class DeskHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    # populated by serve():
    token: str = ""
    html: str = ""
    free_text_keys: List[str] = []
    answers_path: str = ""
    session_path: str = ""
    view: str = ""
    exit_code: int = 3  # default: timeout unless something else happens


class DeskHandler(http.server.BaseHTTPRequestHandler):
    server: DeskHTTPServer  # type: ignore[assignment]

    def log_message(self, fmt: str, *args: Any) -> None:  # mask the token
        line = fmt % args
        print(re.sub(r"t=[^\s&\"]+", "t=***", line), file=sys.stderr)

    def _token_ok(self) -> bool:
        qs = parse_qs(urlparse(self.path).query)
        return qs.get("t", [""])[0] == self.server.token

    def _send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _shutdown_with(self, code: int, status: str) -> None:
        self.server.exit_code = code
        _write_session(self.server.session_path, {"status": status})
        # BaseServer.shutdown() must run outside the serve_forever thread.
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        path = urlparse(self.path).path
        if path == "/health":
            self._send(200, b'{"status":"ok"}', "application/json")
            return
        if not self._token_ok():
            self._send(403, "403 — 세션 토큰이 올바르지 않습니다.".encode())
            return
        self._send(200, self.server.html.encode("utf-8"))

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/submit" or not self._token_ok():
            self._send(403, b"403")
            return  # keep serving — a stray POST must not kill the session
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, b'{"error":"invalid_json"}', "application/json")
            return

        if body.get("fallback_requested") is True:
            self._send(200, "채팅으로 이어갑니다 — 이 창을 닫고 Claude Code로 돌아가세요.".encode())
            self._shutdown_with(4, "fallback_requested")
            return

        masked, phi_record = _mask_answers(body, self.server.free_text_keys)
        record = {
            "view": self.server.view,
            "submitted_at": _now_iso(),
            "answers": masked,
            "_phi": phi_record,
        }
        tmp = self.server.answers_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.server.answers_path)
        self._send(200, "저장되었습니다 — 이 창을 닫고 Claude Code로 돌아가세요.".encode())
        self._shutdown_with(0, "submitted")


def _write_session(path: str, patch: Dict[str, Any]) -> None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    data.update(patch, updated_at=_now_iso())
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def render_template(payload: Dict[str, Any]) -> str:
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        template = f.read()
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return template.replace("__RF_PAYLOAD_JSON__", blob)


def find_server(host: str, ports: List[int]) -> Optional[DeskHTTPServer]:
    for port in ports:
        try:
            return DeskHTTPServer((host, port), DeskHandler)
        except OSError:
            continue
    return None


def serve(args: argparse.Namespace) -> int:
    try:
        with open(args.payload, encoding="utf-8") as f:
            payload = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        print("ERROR: payload file missing or unreadable", file=sys.stderr)
        return 1

    desk_dir = resolve_desk_dir(args.project_dir)
    os.makedirs(desk_dir, exist_ok=True)
    session_path = os.path.join(desk_dir, f"session-{args.session_id}.json")
    answers_path = os.path.join(desk_dir, f"answers-{args.session_id}.json")

    payload["view"] = args.view
    payload["session"] = args.session_id

    server = find_server("127.0.0.1", [args.port_start] + PORT_CANDIDATES)
    if server is None:
        _write_session(session_path, {"status": "no_port"})
        print("ERROR: no available port in 3000-4999", file=sys.stderr)
        return 2

    server.token = secrets.token_urlsafe(16)
    server.html = render_template(payload)
    server.free_text_keys = list(payload.get("free_text_keys", []))
    server.answers_path = answers_path
    server.session_path = session_path
    server.view = args.view

    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/?t={server.token}"
    _write_session(session_path, {"status": "serving", "port": port, "url": url,
                                  "view": args.view, "answers_path": answers_path})
    # Startup line for the launching shell (token intentionally included — it
    # only ever travels localhost, and the LLM may need it for a headless user).
    print(json.dumps({"url": url, "port": port, "session": args.session_id}), flush=True)

    headless, _signals = is_likely_headless()
    if not args.no_browser and not headless:
        try:
            webbrowser.open(url)
        except Exception:
            pass  # the URL is in the session file either way

    timer = threading.Timer(args.timeout_min * 60, lambda: (
        setattr(server, "exit_code", 3),
        _write_session(session_path, {"status": "timeout"}),
        threading.Thread(target=server.shutdown, daemon=True).start(),
    ))
    timer.daemon = True
    timer.start()

    try:
        server.serve_forever()
    finally:
        timer.cancel()
        server.server_close()
    return server.exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description="ResearchFellow Desk — one-shot local HTML interface")
    parser.add_argument("--probe-headless", action="store_true",
                        help="Print {likely_headless, signals} as JSON and exit 0")
    parser.add_argument("--view", choices=("s1_interview", "dashboard"))
    parser.add_argument("--payload", help="Path to the payload JSON the LLM prepared")
    parser.add_argument("--project-dir", default="research")
    parser.add_argument("--session-id", help="Caller-chosen id — answer/session file names derive from it")
    parser.add_argument("--port-start", type=int, default=4321)
    parser.add_argument("--timeout-min", type=float, default=DEFAULT_TIMEOUT_MIN)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if args.probe_headless:
        headless, signals = is_likely_headless()
        print(json.dumps({"likely_headless": headless, "signals": signals}))
        sys.exit(0)

    if not (args.view and args.payload and args.session_id):
        parser.error("--view, --payload and --session-id are required (or use --probe-headless)")
    sys.exit(serve(args))


if __name__ == "__main__":
    main()
