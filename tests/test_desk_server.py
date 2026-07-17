"""desk_server.py — one-shot local HTML interface. Contracts under test:
  1. exactly one authorized submission ends the server with exit 0,
  2. a wrong token gets 403 and the session SURVIVES,
  3. free-text answers are masked before touching disk,
  4. timeout/fallback produce their distinct exit codes + session status,
  5. port conflicts fall through to the next candidate,
  6. headless detection follows the desk-interface.md decision table.
"""

from __future__ import annotations

import http.client
import json
import socket
import subprocess
import sys
import time

import pytest

import desk_server

_RRN_WEIGHTS = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
PHONE = "010-1234-5678"


def _payload(**over):
    base = {"free_text_seed": "패혈증 환자 연구", "pico_prefill": {},
            "candidates": [], "free_text_keys": ["idea_text"]}
    base.update(over)
    return base


def _launch(tmp_path, scripts_dir, *, view="s1_interview", payload=None,
            timeout_min="5", port_start="4321", env_extra=None):
    import os
    pdir = tmp_path / ".research"
    pdir.mkdir(exist_ok=True)
    ppath = tmp_path / "payload.json"
    ppath.write_text(json.dumps(payload if payload is not None else _payload()),
                     encoding="utf-8")
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen(
        [sys.executable, str(scripts_dir / "desk_server.py"),
         "--view", view, "--payload", str(ppath), "--project-dir", str(pdir),
         "--session-id", "t1", "--no-browser",
         "--timeout-min", timeout_min, "--port-start", port_start],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    session_path = pdir / "desk" / "session-t1.json"
    for _ in range(200):  # bounded startup wait (max ~10s)
        if session_path.exists():
            data = json.loads(session_path.read_text(encoding="utf-8"))
            if data.get("status") == "serving":
                return proc, data, pdir
        if proc.poll() is not None:
            error = proc.stderr.read()
            if "no available port" in error:
                pytest.skip("local socket binding is unavailable in this environment")
            raise AssertionError(f"server died early: {error}")
        time.sleep(0.05)
    proc.kill()
    raise AssertionError("server did not reach 'serving'")


def _request(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path,
                 body=json.dumps(body) if body is not None else None,
                 headers={"Content-Type": "application/json"})
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, data


def test_submit_writes_masked_answers_and_exits_zero(tmp_path, scripts_dir):
    proc, session, pdir = _launch(tmp_path, scripts_dir)
    port, url = session["port"], session["url"]
    token = url.split("t=")[1]

    status, html = _request(port, "GET", f"/?t={token}")
    assert status == 200 and "rf-payload".encode() in html

    status, _ = _request(port, "POST", f"/submit?t={token}", {
        "view": "s1_interview",
        "idea_text": f"보호자 연락처 {PHONE} 포함 메모",
        "pico": {"population": {"value": "sepsis adults", "confidence": "stated"}},
    })
    assert status == 200
    assert proc.wait(timeout=10) == 0

    answers = json.loads((pdir / "desk" / "answers-t1.json").read_text(encoding="utf-8"))
    assert PHONE not in json.dumps(answers, ensure_ascii=False)
    assert "[MASKED:phone_kr]" in answers["answers"]["idea_text"]
    assert answers["answers"]["pico"]["population"]["value"] == "sepsis adults"  # structured field untouched
    assert answers["_phi"]["screened"] is True and answers["_phi"]["finding_count"] >= 1
    assert json.loads((pdir / "desk" / "session-t1.json").read_text())["status"] == "submitted"


def test_wrong_token_is_rejected_and_session_survives(tmp_path, scripts_dir):
    proc, session, pdir = _launch(tmp_path, scripts_dir)
    port, token = session["port"], session["url"].split("t=")[1]

    assert _request(port, "GET", "/?t=wrong")[0] == 403
    assert _request(port, "POST", "/submit?t=wrong", {"x": 1})[0] == 403
    assert proc.poll() is None  # still serving
    assert not (pdir / "desk" / "answers-t1.json").exists()

    _request(port, "POST", f"/submit?t={token}", {"view": "s1_interview", "idea_text": "ok"})
    assert proc.wait(timeout=10) == 0


def test_timeout_exits_3(tmp_path, scripts_dir):
    proc, session, pdir = _launch(tmp_path, scripts_dir, timeout_min="0.03")  # ~1.8s
    assert proc.wait(timeout=15) == 3
    assert json.loads((pdir / "desk" / "session-t1.json").read_text())["status"] == "timeout"


def test_fallback_button_exits_4(tmp_path, scripts_dir):
    proc, session, pdir = _launch(tmp_path, scripts_dir)
    port, token = session["port"], session["url"].split("t=")[1]
    _request(port, "POST", f"/submit?t={token}", {"fallback_requested": True})
    assert proc.wait(timeout=10) == 4
    assert json.loads((pdir / "desk" / "session-t1.json").read_text())["status"] == "fallback_requested"
    assert not (pdir / "desk" / "answers-t1.json").exists()


def test_port_conflict_falls_through(tmp_path, scripts_dir):
    blocker = socket.socket()
    try:
        blocker.bind(("127.0.0.1", 0))
    except PermissionError:
        blocker.close()
        pytest.skip("local socket binding is unavailable in this environment")
    taken = blocker.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) and blocker.getsockname()[1]
    try:
        proc, session, _ = _launch(tmp_path, scripts_dir, port_start=str(taken))
        assert session["port"] != taken
        port, token = session["port"], session["url"].split("t=")[1]
        _request(port, "POST", f"/submit?t={token}", {"view": "s1_interview"})
        assert proc.wait(timeout=10) == 0
    finally:
        blocker.close()


def test_probe_headless_ssh_signal(tmp_path, scripts_dir):
    out = subprocess.run(
        [sys.executable, str(scripts_dir / "desk_server.py"), "--probe-headless"],
        capture_output=True, text=True,
        env={**__import__("os").environ, "SSH_CONNECTION": "10.0.0.1 22"},
    )
    assert out.returncode == 0
    verdict = json.loads(out.stdout)
    assert verdict["likely_headless"] is True
    assert any(s.startswith("env:SSH") for s in verdict["signals"])


def test_headless_rules_per_platform(monkeypatch):
    for var in ("SSH_CONNECTION", "SSH_TTY", "SSH_CLIENT", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(desk_server.platform, "system", lambda: "Darwin")
    assert desk_server.is_likely_headless()[0] is False  # macOS never checks DISPLAY
    monkeypatch.setattr(desk_server.platform, "system", lambda: "Linux")
    assert desk_server.is_likely_headless()[0] is True   # Linux without DISPLAY
    monkeypatch.setenv("DISPLAY", ":0")
    assert desk_server.is_likely_headless()[0] is False


def test_mask_answers_fail_closed_without_engine(monkeypatch):
    monkeypatch.setattr(desk_server, "phi_detect", None)
    masked, phi = desk_server._mask_answers({"idea_text": f"전화 {PHONE}"}, ["idea_text"])
    assert masked["idea_text"] == ""            # withheld, never raw
    assert phi["screened"] is False
