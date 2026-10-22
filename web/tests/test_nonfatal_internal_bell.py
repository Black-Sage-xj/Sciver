"""Nonfatal operational incidents surface in the existing header bell."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tm_observer.supervisor_notifications import notification_feed
from tm_observer.supervisor_bell_ui import BELL_SCRIPT, BELL_HTML


def _run(tmp_path: Path):
    cycle=tmp_path/"88"
    cycle.mkdir()
    return {"research_root": str(tmp_path), "cycle_dir": str(cycle),
            "observer_id": "8dd38a9977eef7e5", "task_name": "TheoryMaster task",
            "cycle": "88"}, cycle


def test_duplicate_transient_failures_are_archived_but_not_notifications(tmp_path):
    run, cycle=_run(tmp_path)
    at=datetime.now(timezone.utc).isoformat()
    events=[
        {"at":at,"lifecycle":"registered","severity":"error","kind":"system_failure",
         "code":"run_failed","summary":"Codex model HTTP 400 unsupported", "incident_id":"INC-1111111111111111"},
        {"at":at,"lifecycle":"recurred","severity":"error","kind":"system_failure",
         "code":"run_failed","summary":"Codex model HTTP 400 unsupported", "incident_id":"INC-1111111111111111"},
    ]
    (cycle/"important_events.jsonl").write_text("".join(json.dumps(x)+"\n" for x in events))
    with patch("tm_observer.supervisor_notifications.monitor_data",return_value={"status":"ACTIVE"}):
        result=notification_feed([run])
    assert result["needs_attention"]==0
    assert result["total"]==0
    assert result["internal_alert_ids"]==[]
    from tm_observer.important_events import incident_cases, read_important_events
    cases=incident_cases(read_important_events(run))
    assert len(cases)==1
    assert len(cases[0]["records"])==2


def test_old_incidents_not_a_new_red_dot(tmp_path):
    run, cycle=_run(tmp_path)
    (cycle/"important_events.jsonl").write_text(json.dumps({
       "at":"2020-01-01T10:00:00+00:00","lifecycle":"registered",
       "severity":"error","kind":"system_failure","code":"run_failed","summary":"old error",
    })+"\n")
    with patch("tm_observer.supervisor_notifications.monitor_data",return_value={"status":"ACTIVE"}):
        result=notification_feed([run])
    assert result["total"]==0
    assert result["internal_alert_ids"]==[]


def test_bell_only_counts_actionable_stops():
    assert "tm.internal.incidents.seen.v1" not in BELL_SCRIPT
    assert "tm.supervisor.notices.v1" in BELL_SCRIPT
    assert "const unread=visible.filter(key=>!read.has(key)).length" in BELL_SCRIPT
    assert "clear.textContent=\"清除\"" in BELL_SCRIPT
    assert "待处理通知" in BELL_HTML
    assert "已查阅" in BELL_SCRIPT


def test_newer_failure_after_supervisor_return_is_actionable(tmp_path):
    run, cycle=_run(tmp_path)
    from datetime import timedelta
    folder=cycle/"supervisor"
    folder.mkdir()
    at=(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat()
    later=datetime.now(timezone.utc).isoformat()
    with (folder/"session.jsonl").open("w") as output:
        for kind in ("opened", "decision"):
            output.write(json.dumps({
                "schema":"theory-master/supervisor-session/v1", "profile":"attention",
                "session_id":"SUP-0000000000000012",
                "code":"investigator_native_goal_blocked",
                "incident_id":"INC-0000000000000012", "kind":kind,
                "at":at,"content":"Supervisor chose preserve",
                **({"status":"returned","action":"preserve"} if kind=="decision" else {}),
            })+"\n")
    (cycle/"important_events.jsonl").write_text(json.dumps({
        "at":later, "lifecycle":"registered", "severity":"attention",
        "kind":"needs_attention","code":"investigator_preservation_failed",
        "summary":"PreservationGuard: source file absent"
    })+"\n")
    with patch("tm_observer.supervisor_notifications.monitor_data",
               return_value={"status":"NEEDS_ATTENTION","attention":{
                   "code":"investigator_preservation_failed",
                   "reason":"PreservationGuard: source file absent",
               }}):
        data=notification_feed([run])
    assert data["needs_attention"] == 1
    assert data["internal_alert_ids"] == []
    assert data["items"][0]["profile"] == "runtime"
    assert "source file absent" in data["items"][0]["summary"]
    assert data["items"][0]["needs_attention"] is True
