"""A historical Supervisor hold is not an active hold for a LIVE task."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tm_observer.supervisor_view import home_status, page_body

class HistoricalHoldTests(unittest.TestCase):
    def test_live_cycle_labels_past_hold_historically(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "87" / "supervisor"
            folder.mkdir(parents=True)
            session_id = "SUP-0123456789abcdef"
            rows = [
                {"schema": "theory-master/supervisor-session/v1", "seq": 1,
                 "session_id": session_id, "profile": "runtime", "code": "run_failed",
                 "kind": "opened", "at": "2026-10-13T10:00:00Z", "content": "ValueError"},
                {"schema": "theory-master/supervisor-session/v1", "seq": 2,
                 "session_id": session_id, "profile": "runtime", "code": "run_failed",
                 "kind": "decision", "status": "hold", "action": "hold",
                 "at": "2026-10-13T10:00:01Z", "content": "old incident"}
            ]
            (folder/"session.jsonl").write_text(
                "".join(json.dumps(r)+"\n" for r in rows))
            (folder/"status.json").write_text(json.dumps({
                "schema": "theory-master/supervisor-status/v1",
                "status": "hold", "session_id": session_id, "profile": "runtime",
                "code": "run_failed", "summary": "old incident"}))
            run = {"research_root": str(root), "cycle": "87", "raw_status": "RUNNING",
                   "observer_id": "demo", "task_name": "one"}
            self.assertIsNone(home_status(run, {"status": "RUNNING"}))
            html = page_body(run, "87", {"status": "RUNNING"})
            self.assertIn("历史曾停下 · 当前已恢复运行", html)
            self.assertIn("old incident", html)
            stopped = page_body(run, "87", {"status": "FAILED"})
            self.assertNotIn("历史曾停下 · 当前已恢复运行", stopped)
            self.assertIn("已停下", stopped)


if __name__ == "__main__":
    unittest.main()
