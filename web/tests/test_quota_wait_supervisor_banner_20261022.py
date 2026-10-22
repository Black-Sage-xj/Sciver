"""Quota waiting card uses explicit scientific pause + recent account evidence."""
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

from bs4 import BeautifulSoup

from tm_observer.supervisor_view import banner_html, quota_wait_banner


def _snapshot(ts):
    return {"accounts": [
        {"account_home": "account-1", "ok": True, "queried_at": int(time.time()),
         "primary": {"remaining_percent": 0, "resets_at": ts},
         "secondary": {"remaining_percent": 50, "resets_at": ts + 86400}},
        {"account_home": "account-2", "ok": True, "queried_at": int(time.time()),
         "primary": {"remaining_percent": 0, "resets_at": ts + 1200},
         "secondary": {"remaining_percent": 20, "resets_at": ts + 86400}},
    ]}


def test_verified_quota_pause_shows_reset_but_not_old_supervisor_hold(tmp_path):
    cycle = tmp_path / "184"
    (cycle / "supervisor").mkdir(parents=True)
    (cycle / "research_state.json").write_text(json.dumps({
        "status": "paused", "phase": "provider_quota_exhausted",
        "resumable": True, "recovery": {"code": "provider_quota_exhausted"},
    }))
    (cycle / "supervisor" / "status.json").write_text(json.dumps({
        "schema": "theory-master/supervisor-status/v1", "status": "hold",
        "code": "native_goal_runtime_failure", "profile": "runtime",
    }))
    run = {"research_root": str(tmp_path), "cycle": "184",
           "observer_id": "8dd38a9977eef7e5", "state": "PAUSED"}
    now = int(time.time()) + 1200
    html = quota_wait_banner(run, {"status": "PAUSED"}, _snapshot(now), "2")
    dom = BeautifulSoup(html, "html.parser")
    assert dom.select_one('[data-quota-wait="true"]')
    assert "额度等待中" in dom.get_text()
    assert "账号 2" in dom.get_text()
    dt = datetime.fromtimestamp(now, timezone(timedelta(hours=8)))
    assert dt.strftime("%m月%d日 %H:%M") in dom.get_text()
    assert "Supervisor 已停下" not in banner_html(run, {"status": "PAUSED"})
    assert 'data-quota-wait="true"' in banner_html(run, {"status": "PAUSED"})


def test_unknown_and_stale_quota_never_invent_resets(tmp_path):
    cycle = tmp_path / "185"
    cycle.mkdir()
    (cycle / "research_state.json").write_text(json.dumps({
        "status": "paused", "phase": "provider_quota_exhausted",
        "resumable": True, "recovery": {"code": "provider_quota_exhausted"},
    }))
    run = {"research_root": str(tmp_path), "cycle": "185", "state": "PAUSED"}
    stale = _snapshot(int(time.time()) + 600)
    for entry in stale["accounts"]:
        entry["queried_at"] = int(time.time()) - 3600
    html = quota_wait_banner(run, None, stale, "2")
    assert "重置时间待服务端确认" in html
    assert "预计 " not in html


def test_operator_pause_and_actual_failure_keep_distinct_supervisor_semantics(tmp_path):
    cycle = tmp_path / "184"
    cycle.mkdir()
    run = {"research_root": str(tmp_path), "cycle": "184", "state": "PAUSED"}
    (cycle / "research_state.json").write_text(json.dumps({
        "status": "paused", "phase": "paused", "resumable": True,
        "recovery": {"reason": "multi_worker_operator"},
    }))
    assert not quota_wait_banner(run, {"status": "PAUSED"}, _snapshot(int(time.time())+600))
    assert 'data-quota-wait="true"' not in banner_html(run, {"status": "PAUSED"})
    (cycle / "research_state.json").write_text(json.dumps({
        "status": "failed", "phase": "failed", "resumable": True,
        "recovery": {"kind": "AuthenticationError"},
    }))
    assert not quota_wait_banner(run, {"status": "FAILED"}, _snapshot(int(time.time())+600))
    assert 'data-supervisor-phase="stopped"' in banner_html(run, {"status": "FAILED"})
