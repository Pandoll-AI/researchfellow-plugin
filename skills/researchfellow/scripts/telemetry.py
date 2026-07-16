#!/usr/bin/env python3
"""Usage telemetry client for the ResearchFellow skill (FR-P; server: TM-1..9).

Funnel counters ONLY: which step a project entered at, which step it reached,
where progress stalls. No content field exists in the event schema — research
ideas, data, manuscripts and conversation never leave the machine through this
channel. Collection happens only after the user consented (the SKILL.md consent
gate); without a config file this script records nothing.

Network exception #2 under FR-T7 (the first is pubmed_search.py). Fire-and-
forget by design: `register`/`emit`/`flush` ALWAYS exit 0 — a telemetry failure
must never block the research workflow (TM-8). Offline/closed-network grace:
registration failure stores a `local:<uuid4>` token with status "pending",
events accumulate in a local queue, and a later successful registration
replaces the token on every queued event before sending (D4).

Subcommands:
    telemetry.py register --plugin-version X
    telemetry.py emit --event E [--step N] [--entry-point S1..S5] --project-dir DIR
    telemetry.py flush
    telemetry.py revoke
    telemetry.py new-project-id      # prints a real uuid4 for state.json.project_id

Config:  ~/.researchfellow/config.json   (install-scoped consent + token)
Queue:   ~/.researchfellow/queue.jsonl   (pending events, capped)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, request

DEFAULT_BASE_URL = "https://researchfellow-mcp.vercel.app"
TIMEOUT_SECONDS = 1.5
CONFIG_DIR = Path.home() / ".researchfellow"
CONFIG_PATH = CONFIG_DIR / "config.json"
QUEUE_PATH = CONFIG_DIR / "queue.jsonl"
MAX_QUEUE_LINES = 500  # closed-network longevity cap — oldest events drop first
BATCH_SIZE = 50        # server-side eventBatchSchema cap
SCHEMA_VERSION = 1

VALID_EVENTS = (
    "project_created", "entry_point_selected", "step_entered", "step_completed",
    "gate_approved", "gate_rejected", "gate_changes_requested", "session_resumed",
)
VALID_ENTRY_POINTS = ("S1", "S2", "S3", "S4", "S5")


def _base_url(args: argparse.Namespace) -> str:
    return (getattr(args, "base_url", None)
            or os.environ.get("RF_TELEMETRY_URL", "").strip()
            or DEFAULT_BASE_URL).rstrip("/")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _http(url: str, payload: Dict[str, Any], method: str = "POST",
          timeout: float = TIMEOUT_SECONDS) -> Tuple[Optional[int], Optional[dict]]:
    """POST/DELETE JSON. Swallows every exception — telemetry never raises."""
    try:
        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "User-Agent": "ResearchFellow-Plugin/telemetry"},
            method=method,
        )
        with request.urlopen(req, timeout=timeout) as res:
            body = res.read()
            return res.status, (json.loads(body) if body else {})
    except error.HTTPError as exc:  # non-2xx still carries a status we act on
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except Exception:
            return exc.code, None
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Config + queue (atomic writes; queue append happens BEFORE any network try)
# ---------------------------------------------------------------------------
def _load_config() -> Optional[dict]:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def _read_queue() -> List[dict]:
    try:
        lines = QUEUE_PATH.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return []
    out: List[dict] = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue  # drop a corrupt line rather than wedge the queue
    return out


def _write_queue(events: List[dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_PATH.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    os.replace(tmp, QUEUE_PATH)


def _append_queue(event: dict) -> None:
    events = _read_queue()
    events.append(event)
    if len(events) > MAX_QUEUE_LINES:
        events = events[-MAX_QUEUE_LINES:]
    _write_queue(events)


def _project_hash(project_dir: str) -> Optional[str]:
    """sha256(state.json.project_id) — a UUID, never the user-named title."""
    try:
        with open(os.path.join(project_dir, "state.json"), encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    project_id = str(state.get("project_id") or "").strip()
    if not project_id:
        return None
    return hashlib.sha256(project_id.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Flush machinery (token replacement on recovery — D4)
# ---------------------------------------------------------------------------
def _try_flush(cfg: dict, base_url: str) -> dict:
    """Attempt to promote a pending token and drain the queue. Never raises."""
    result = {"registered": cfg.get("status") == "registered", "sent": 0, "queued": 0}

    if cfg.get("status") == "pending":
        status, body = _http(f"{base_url}/api/token",
                             {"consent": True,
                              "plugin_version": cfg.get("plugin_version", "unknown")})
        if status == 201 and body and body.get("token"):
            cfg["token"] = body["token"]
            cfg["status"] = "registered"
            _save_config(cfg)
            events = _read_queue()
            for e in events:  # retroactive token replacement — the grace contract
                e["token"] = cfg["token"]
            _write_queue(events)
        else:
            result["queued"] = len(_read_queue())
            return result
    result["registered"] = True

    while True:
        events = _read_queue()
        if not events:
            break
        batch = events[:BATCH_SIZE]
        status, body = _http(f"{base_url}/api/events", {"events": batch})
        if status == 202:
            _write_queue(events[len(batch):])
            result["sent"] += len(batch)
            continue
        if status == 401:  # unknown_token — DB reset etc.: re-register next time
            cfg["status"] = "pending"
            _save_config(cfg)
        break
    result["queued"] = len(_read_queue())
    return result


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
def cmd_register(args: argparse.Namespace) -> int:
    cfg = _load_config()
    if cfg and cfg.get("token"):
        out = _try_flush(cfg, _base_url(args))  # idempotent: promote/drain if possible
        print(json.dumps({"status": _load_config().get("status", "unknown"), **out}))
        return 0
    status, body = _http(f"{_base_url(args)}/api/token",
                         {"consent": True, "plugin_version": args.plugin_version})
    if status == 201 and body and body.get("token"):
        cfg = {"token": body["token"], "status": "registered",
               "consent_at": _now_iso(), "plugin_version": args.plugin_version}
    else:  # grace mode (D4): local temporary identity, promoted later
        cfg = {"token": f"local:{uuid.uuid4()}", "status": "pending",
               "consent_at": _now_iso(), "plugin_version": args.plugin_version}
    _save_config(cfg)
    print(json.dumps({"status": cfg["status"]}))
    return 0


def cmd_emit(args: argparse.Namespace) -> int:
    cfg = _load_config()
    if not cfg or not cfg.get("token"):
        # No consent record -> no collection, ever. Still exit 0 (never blocks).
        print(json.dumps({"status": "not_registered", "recorded": False}))
        return 0
    project_hash = _project_hash(args.project_dir)
    if not project_hash:
        print(json.dumps({"status": "no_project_id", "recorded": False}))
        return 0
    event = {
        "token": cfg["token"],
        "project_hash": project_hash,
        "event": args.event,
        "step": args.step,
        "entry_point": args.entry_point,
        "plugin_version": cfg.get("plugin_version", "unknown"),
        "schema_version": SCHEMA_VERSION,
        "ts": _now_iso(),
    }
    _append_queue(event)  # durability first — network comes after
    out = _try_flush(cfg, _base_url(args))
    print(json.dumps({"status": "recorded", **out}))
    return 0


def cmd_flush(args: argparse.Namespace) -> int:
    cfg = _load_config()
    if not cfg:
        print(json.dumps({"status": "not_registered"}))
        return 0
    print(json.dumps(_try_flush(cfg, _base_url(args))))
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    cfg = _load_config()
    revoked_remote = False
    if cfg and cfg.get("status") == "registered":
        status, body = _http(f"{_base_url(args)}/api/token", {"token": cfg["token"]},
                             method="DELETE")
        revoked_remote = status == 200 and bool(body and body.get("revoked"))
    for p in (CONFIG_PATH, QUEUE_PATH):
        try:
            p.unlink()
        except OSError:
            pass
    print(json.dumps({"status": "revoked", "server_erased": revoked_remote}))
    return 0


def cmd_new_project_id(_args: argparse.Namespace) -> int:
    print(str(uuid.uuid4()))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="ResearchFellow usage telemetry (funnel counters only)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("register")
    p.add_argument("--plugin-version", required=True)
    p.add_argument("--base-url")
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("emit")
    p.add_argument("--event", required=True, choices=VALID_EVENTS)
    p.add_argument("--step", type=int, choices=range(1, 14), default=None)
    p.add_argument("--entry-point", choices=VALID_ENTRY_POINTS, default=None)
    p.add_argument("--project-dir", required=True)
    p.add_argument("--base-url")
    p.set_defaults(func=cmd_emit)

    p = sub.add_parser("flush")
    p.add_argument("--base-url")
    p.set_defaults(func=cmd_flush)

    p = sub.add_parser("revoke")
    p.add_argument("--base-url")
    p.set_defaults(func=cmd_revoke)

    p = sub.add_parser("new-project-id")
    p.set_defaults(func=cmd_new_project_id)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
