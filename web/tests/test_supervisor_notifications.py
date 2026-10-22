"""Global Supervisor bell: historical inbox and current actionable count."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tm_observer.dashboard_ui import dashboard_shell
from tm_observer.supervisor_notifications import notification_feed
from tm_observer.supervisor_bell_ui import BELL_SCRIPT


class SupervisorBellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = {
            "research_root": str(self.root),
            "observer_id": "8dd38a9977eef7e5",
            "task_name": "Example project",
            "cycle": "2",
        }

    def add_session(self, cycle, sid, at, status, code="investigator_native_goal_blocked"):
        folder = self.root / str(cycle) / "supervisor"
        folder.mkdir(parents=True, exist_ok=True)
        base = {
            "schema": "theory-master/supervisor-session/v1",
            "profile": "attention", "session_id": sid,
            "code": code, "incident_id": "INC-" + sid[-16:],
        }
        with (folder / "session.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({**base, "kind": "opened", "at": at,
                                     "content": "Supervisor took over"}) + "\n")
            handle.write(json.dumps({**base, "kind": "decision", "at": at,
                                     "status": status, "action": "hold" if status == "hold" else "resume",
                                     "content": "Concrete diagnosis"}) + "\n")

    def feed(self, status="needs_attention", **kwargs):
        with patch("tm_observer.supervisor_notifications.monitor_data",
                   return_value={"status": status}):
            return notification_feed([self.run], **kwargs)

    def test_badge_only_for_current_stopped_hold_not_historical(self):
        self.add_session("1", "SUP-0000000000000001", "2026-10-12T12:00:00+08:00", "hold")
        self.add_session("2", "SUP-0000000000000002", "2026-10-13T12:00:00+08:00", "hold")
        data = self.feed()
        self.assertEqual(data["needs_attention"], 1)
        self.assertEqual(data["total"], 1)
        self.assertTrue(data["items"][0]["needs_attention"])
        self.assertIn("cycle=2#SUP-0000000000000002", data["items"][0]["url"])
        resumed = self.feed("ACTIVE")
        self.assertEqual(resumed["total"], 0)
        self.assertEqual(resumed["needs_attention"], 0)

    def test_returned_session_does_not_hide_current_stop(self):
        self.add_session("2", "SUP-0000000000000001", "2026-10-13T11:00:00+08:00", "hold")
        self.add_session("2", "SUP-0000000000000002", "2026-10-13T12:00:00+08:00", "returned")
        result = self.feed()
        self.assertEqual(result["needs_attention"], 1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["profile"], "runtime")
        self.assertEqual(result["items"][0]["status"], "current_stop")
        self.assertEqual(self.feed("ACTIVE")["total"], 0)

    def test_old_sessions_do_not_fill_notification_pagination(self):
        for i in range(1, 6):
            self.add_session(str(i), f"SUP-{i:016x}", f"2026-10-{i:02d}T10:00:00+08:00", "hold")
        self.run["cycle"] = "5"
        first = self.feed(offset=0, limit=2)
        second = self.feed(offset=2, limit=2)
        self.assertEqual(first["total"], 1)
        self.assertEqual(len(first["items"]), 1)
        self.assertIsNone(first["next_offset"])
        self.assertEqual(len(second["items"]), 0)

    def test_returned_runtime_failure_is_archived_not_notification(self):
        self.add_session("2", "SUP-0000000000000009", "2026-10-13T15:10:18+08:00",
                         "returned", code="run_failed")
        result = self.feed("RUNNING")
        self.assertEqual(result["needs_attention"], 0)
        self.assertEqual(result["total"], 0)

    def test_no_supervisor_still_reports_current_stopped_task(self):
        data=self.feed()
        self.assertEqual(data["needs_attention"], 1)
        self.assertEqual(data["items"][0]["profile"], "runtime")
        self.assertEqual(data["items"][0]["status"], "current_stop")
        self.assertEqual(self.feed("RUNNING")["needs_attention"], 0)

    def test_current_stop_uses_latest_event_time_not_updated_at(self):
        cycle = self.root / "2"
        cycle.mkdir()
        earlier = {"at": "2026-10-18T08:00:00+08:00", "kind": "needs_attention",
                   "incident_id": "INC-0000000000000001", "lifecycle": "registered"}
        latest = {"at": "2026-10-18T09:00:00+08:00", "kind": "system_failure",
                  "incident_id": "INC-0000000000000002", "lifecycle": "recurred"}
        (cycle / "important_events.jsonl").write_text(json.dumps(earlier) + "\n", encoding="utf-8")
        (cycle / "cycle_anomalies.jsonl").write_text(json.dumps(latest) + "\n", encoding="utf-8")
        with patch("tm_observer.supervisor_notifications.monitor_data", return_value={
            "status": "NEEDS_ATTENTION", "updated_at": "2026-10-19T20:01:00+08:00",
        }):
            result = notification_feed([self.run])
        self.assertEqual(result["items"][0]["at"], latest["at"])

    def test_missing_stop_event_time_is_not_fabricated_from_updated_at(self):
        with patch("tm_observer.supervisor_notifications.monitor_data", return_value={
            "status": "NEEDS_ATTENTION", "updated_at": "2026-10-19T20:01:00+08:00",
        }):
            item = notification_feed([self.run])["items"][0]
        self.assertEqual(item["at"], "")

    def test_notification_item_explicitly_labels_raised_time(self):
        self.assertIn("提出时间：", BELL_SCRIPT)
        self.assertIn(':"未知"', BELL_SCRIPT)

    def test_new_stop_occurrence_gets_new_notice_key(self):
        cycle = self.root / "2"
        cycle.mkdir()
        ledger = cycle / "important_events.jsonl"
        first = {"at": "2026-10-18T08:00:00+08:00", "kind": "needs_attention",
                 "incident_id": "INC-0000000000000001", "lifecycle": "registered"}
        again = {**first, "at": "2026-10-18T09:00:00+08:00", "lifecycle": "recurred"}
        ledger.write_text(json.dumps(first) + "\n", encoding="utf-8")
        original = self.feed()["items"][0]["notice_key"]
        self.assertEqual(self.feed()["active_keys"], [original])
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(again) + "\n")
        self.assertNotEqual(self.feed()["items"][0]["notice_key"], original)

    def test_current_returned_status_does_not_clear_real_stop(self):
        self.add_session("2", "SUP-0000000000000001", "2026-10-13T12:00:00+08:00", "hold")
        folder = self.root / "2" / "supervisor"
        (folder / "status.json").write_text(json.dumps({
            "schema": "theory-master/supervisor-status/v1",
            "session_id": "SUP-0000000000000001", "status": "returned",
        }), encoding="utf-8")
        result = self.feed()
        self.assertEqual(result["needs_attention"], 1)
        self.assertEqual(result["items"][0]["profile"], "runtime")
        self.assertEqual(self.feed("ACTIVE")["needs_attention"], 0)

    def test_all_workspace_routes_have_single_global_bell(self):
        for active in ("run", "explorer", "workflow", "architecture", "services", "settings"):
            with self.subTest(active=active):
                html = dashboard_shell("Test", "content", active=active)
                self.assertEqual(html.count('data-notices-toggle\n'), 1)
                self.assertEqual(html.count('data-supervisor-bell'), 1)
                self.assertIn('data-notices-count hidden', html)
                self.assertIn('/api/supervisor-notifications', html)


if __name__ == "__main__":
    unittest.main()
