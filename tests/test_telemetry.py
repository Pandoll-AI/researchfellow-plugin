"""telemetry.py — funnel-only usage client. Contracts under test:
  1. every command exits 0 no matter what the network does (TM-8),
  2. no consent record -> nothing is ever queued or sent,
  3. queue append happens BEFORE any network attempt (durability),
  4. grace recovery replaces the local token on every queued event (D4),
  5. events carry counters only — no free-text field exists.
"""

from __future__ import annotations

import json
import uuid

import pytest

import telemetry


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".researchfellow"
    monkeypatch.setattr(telemetry, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(telemetry, "CONFIG_PATH", cfg_dir / "config.json")
    monkeypatch.setattr(telemetry, "QUEUE_PATH", cfg_dir / "queue.jsonl")
    return cfg_dir


def _mock_http(monkeypatch, script):
    """script: list of (status, body) returned per call; records requests."""
    calls = []

    def fake(url, payload, method="POST", timeout=None):
        calls.append({"url": url, "payload": payload, "method": method})
        return script[min(len(calls) - 1, len(script) - 1)]

    monkeypatch.setattr(telemetry, "_http", fake)
    return calls


def _args(**kw):
    import argparse
    ns = argparse.Namespace(base_url=None, **kw)
    return ns


def _make_project(tmp_path):
    pdir = tmp_path / ".research"
    pdir.mkdir()
    (pdir / "state.json").write_text(json.dumps({"project_id": str(uuid.uuid4())}))
    return str(pdir)


def test_register_success_saves_real_token(tmp_path, monkeypatch):
    _mock_http(monkeypatch, [(201, {"token": "tl_abc", "issued_at": "x"})])
    assert telemetry.cmd_register(_args(plugin_version="0.2.0")) == 0
    cfg = json.loads(telemetry.CONFIG_PATH.read_text())
    assert cfg == {"token": "tl_abc", "status": "registered",
                   "consent_at": cfg["consent_at"], "plugin_version": "0.2.0"}


def test_register_failure_grants_grace_and_exits_zero(tmp_path, monkeypatch):
    _mock_http(monkeypatch, [(None, None)])
    assert telemetry.cmd_register(_args(plugin_version="0.2.0")) == 0
    cfg = json.loads(telemetry.CONFIG_PATH.read_text())
    assert cfg["status"] == "pending"
    assert cfg["token"].startswith("local:")


def test_emit_without_consent_records_nothing(tmp_path, monkeypatch):
    calls = _mock_http(monkeypatch, [(202, {})])
    pdir = _make_project(tmp_path)
    assert telemetry.cmd_emit(_args(event="step_entered", step=1,
                                    entry_point=None, project_dir=pdir)) == 0
    assert not telemetry.QUEUE_PATH.exists()
    assert calls == []  # no consent -> not even a network attempt


def test_emit_queues_before_network_and_survives_offline(tmp_path, monkeypatch):
    _mock_http(monkeypatch, [(None, None)])  # registration fails -> pending
    telemetry.cmd_register(_args(plugin_version="0.2.0"))
    _mock_http(monkeypatch, [(None, None)])  # still offline for emit
    pdir = _make_project(tmp_path)
    assert telemetry.cmd_emit(_args(event="step_entered", step=3,
                                    entry_point="S1", project_dir=pdir)) == 0
    queued = [json.loads(l) for l in telemetry.QUEUE_PATH.read_text().splitlines()]
    assert len(queued) == 1
    e = queued[0]
    assert e["event"] == "step_entered" and e["step"] == 3 and e["entry_point"] == "S1"
    assert set(e) == {"token", "project_hash", "event", "step", "entry_point",
                      "plugin_version", "schema_version", "ts"}  # no content field
    assert e["token"].startswith("local:")


def test_recovery_replaces_token_on_all_queued_events(tmp_path, monkeypatch):
    _mock_http(monkeypatch, [(None, None)])
    telemetry.cmd_register(_args(plugin_version="0.2.0"))
    _mock_http(monkeypatch, [(None, None)])
    pdir = _make_project(tmp_path)
    for step in (1, 2):
        telemetry.cmd_emit(_args(event="step_entered", step=step,
                                 entry_point="S1", project_dir=pdir))
    # network recovers: token issuance succeeds, then the batch is accepted
    calls = _mock_http(monkeypatch, [(201, {"token": "tl_real"}), (202, {"accepted": 2})])
    assert telemetry.cmd_flush(_args()) == 0
    assert json.loads(telemetry.CONFIG_PATH.read_text())["status"] == "registered"
    assert not telemetry.QUEUE_PATH.read_text().strip()  # drained
    batch = calls[1]["payload"]["events"]
    assert len(batch) == 2 and all(e["token"] == "tl_real" for e in batch)


def test_unknown_token_response_demotes_to_pending_and_keeps_queue(tmp_path, monkeypatch):
    _mock_http(monkeypatch, [(201, {"token": "tl_old"})])
    telemetry.cmd_register(_args(plugin_version="0.2.0"))
    pdir = _make_project(tmp_path)
    _mock_http(monkeypatch, [(401, {"error": "unknown_token"})])
    telemetry.cmd_emit(_args(event="project_created", step=None,
                             entry_point=None, project_dir=pdir))
    assert json.loads(telemetry.CONFIG_PATH.read_text())["status"] == "pending"
    assert len(telemetry.QUEUE_PATH.read_text().splitlines()) == 1


def test_queue_cap_drops_oldest(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry, "MAX_QUEUE_LINES", 5)
    telemetry._write_queue([{"i": n} for n in range(5)])
    telemetry._append_queue({"i": 99})
    q = telemetry._read_queue()
    assert len(q) == 5 and q[-1] == {"i": 99} and q[0] == {"i": 1}


def test_emit_without_project_id_skips(tmp_path, monkeypatch):
    _mock_http(monkeypatch, [(201, {"token": "tl_x"})])
    telemetry.cmd_register(_args(plugin_version="0.2.0"))
    pdir = tmp_path / ".research"
    pdir.mkdir()
    (pdir / "state.json").write_text(json.dumps({"project_name": "환자군 연구"}))
    calls = _mock_http(monkeypatch, [(202, {})])
    telemetry.cmd_emit(_args(event="step_entered", step=1,
                             entry_point=None, project_dir=str(pdir)))
    assert not telemetry.QUEUE_PATH.exists()
    assert calls == []  # nothing attributable -> nothing sent


def test_revoke_deletes_local_state(tmp_path, monkeypatch):
    _mock_http(monkeypatch, [(201, {"token": "tl_x"})])
    telemetry.cmd_register(_args(plugin_version="0.2.0"))
    telemetry._write_queue([{"i": 1}])
    _mock_http(monkeypatch, [(200, {"revoked": True})])
    assert telemetry.cmd_revoke(_args()) == 0
    assert not telemetry.CONFIG_PATH.exists() and not telemetry.QUEUE_PATH.exists()


def test_new_project_id_prints_uuid(capsys):
    assert telemetry.cmd_new_project_id(_args()) == 0
    out = capsys.readouterr().out.strip()
    assert uuid.UUID(out)
