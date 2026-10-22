"""Supervisor takeover page, home banner and task-list chip (read-only projection)."""
import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from tm_observer.supervisor_view import (banner_html, home_status, index_chip, page_body,
                                         sessions, supervisor_enabled_for)

SID = "SUP-0123456789abcdef"


def _session_row(seq, kind, content, **extra):
    row = {"schema": "theory-master/supervisor-session/v1", "session_id": SID, "seq": seq,
           "at": f"2026-10-13T10:00:0{seq}Z", "profile": "attention", "code": "qualification_failed",
           "incident_id": "INC-1", "kind": kind, "content": content}
    row.update(extra)
    return row


class SupervisorViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cycle = self.root / "cycle-002"
        (self.cycle / "supervisor").mkdir(parents=True)
        (self.root / "cycle-001" / "attention").mkdir(parents=True)
        self.run = {"observer_id": "8dd38a9977eef7e5", "research_root": str(self.root),
                    "cycle": "cycle-002", "task_name": "demo", "state": "RUNNING"}

    def tearDown(self):
        self.tmp.cleanup()

    def _write_session(self, status):
        rows = [_session_row(1, "opened", "qualification_failed"),
                _session_row(2, "prompt", "P" * 2000, role="attention_reviewer"),
                _session_row(3, "model_output", '{"action":"retry_qualification"}', thread_id="t-1")]
        if status != "active":
            rows.append(_session_row(4, "decision", "补齐证据后重试", action="retry_qualification"
                                     if status == "returned" else "hold", status=status))
        (self.cycle / "supervisor" / "session.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        (self.cycle / "supervisor" / "status.json").write_text(json.dumps({
            "schema": "theory-master/supervisor-status/v1", "session_id": SID, "status": status,
            "profile": "attention", "code": "qualification_failed", "summary": "Qualification 未通过",
            "model": "gpt-6-luna", "at": "2026-10-13T10:00:05Z"}), encoding="utf-8")

    def test_banner_phases(self):
        empty = BeautifulSoup(banner_html(self.run, {"status": "RUNNING"}), "html.parser")
        pane = empty.select_one('[data-tui-pane="supervisor"]')
        self.assertIsNotNone(pane)
        self.assertEqual(pane.get_text(strip=True), "")
        stopped = BeautifulSoup(banner_html(self.run, {"status": "NEEDS_ATTENTION"}), "html.parser")
        self.assertEqual(stopped.select_one("a")["data-supervisor-phase"], "stopped")
        self.assertEqual(stopped.select_one("a")["href"], "/run/8dd38a9977eef7e5/supervisor/?cycle=cycle-002")
        self._write_session("active")
        dom = BeautifulSoup(banner_html(self.run, {"status": "RUNNING"}), "html.parser")
        link = dom.select_one("a.tm-supervisor-banner")
        self.assertEqual(link["data-supervisor-phase"], "active")
        self.assertTrue(link["href"].endswith("?cycle=cycle-002#" + SID))
        self._write_session("returned")
        returned = BeautifulSoup(banner_html(self.run, {"status": "RUNNING"}), "html.parser")
        self.assertIsNone(returned.select_one("a.tm-supervisor-banner"))
        self.assertEqual(returned.select_one('[data-tui-pane="supervisor"]').get_text(strip=True), "")
        self.assertIsNone(home_status(self.run, {"status": "RUNNING"}))
        self.assertEqual(index_chip(self.run), "")
        self._write_session("hold")
        self.assertIsNone(home_status(self.run, {"status": "RUNNING"}))
        self.assertEqual(home_status(self.run, {"status": "NEEDS_ATTENTION"})["phase"], "hold")
        self.assertIsNone(home_status(self.run, {"status": "OVER"}))

    def test_banner_selects_exact_cycle_bound_sis_not_updated_historical_chat(self):
        self._write_session("hold")
        sis = "SIS-26856b5d29d14904"
        other = "SIS-8a9696b8290c4a69"
        records = self.root / ".theory-master" / "supervisor_incidents" / "sessions"
        records.mkdir(parents=True)
        # An older incident may be the most recently updated conversation.
        (records / f"{other}.json").write_text(json.dumps({
            "schema": "theory-master/supervisor-incident-session/v1", "id": other,
            "cycle_id": "cycle-001", "status": "active", "audit": {},
        }), encoding="utf-8")
        (records / f"{sis}.json").write_text(json.dumps({
            "schema": "theory-master/supervisor-incident-session/v1", "id": sis,
            "cycle_id": "cycle-002", "status": "active",
            "audit": {"supervisor_session_id": SID},
        }), encoding="utf-8")
        link = self.cycle / "supervisor" / "incident_link.json"
        link.write_text(json.dumps({
            "schema": "theory-master/supervisor-incident-link/v1",
            "session_id": sis, "cycle_id": "cycle-002", "status": "active",
        }), encoding="utf-8")
        current = BeautifulSoup(banner_html(self.run, {"status": "NEEDS_ATTENTION"}), "html.parser")
        self.assertEqual(current.select_one("a.tm-supervisor-banner")["href"],
                         f"/run/8dd38a9977eef7e5/supervisor/?session={sis}")
        # A mismatched or missing SIS binding must fail to an explicit Cycle
        # history, not silently select an unrelated Supervisor conversation.
        record = json.loads((records / f"{sis}.json").read_text(encoding="utf-8"))
        record["audit"]["supervisor_session_id"] = "SUP-0000000000000001"
        (records / f"{sis}.json").write_text(json.dumps(record), encoding="utf-8")
        wrong = BeautifulSoup(banner_html(self.run, {"status": "NEEDS_ATTENTION"}), "html.parser")
        self.assertEqual(wrong.select_one("a.tm-supervisor-banner")["href"],
                         f"/run/8dd38a9977eef7e5/supervisor/?cycle=cycle-002#{SID}")

    def test_legacy_incident_link_never_adds_manual_takeover_button(self):
        # A stale active incident may remain after both workers are RUNNING.
        # It must not create a separate manual takeover entry in Task Console.
        sis = "SIS-33e4d29e24274c1e"
        (self.cycle / "supervisor" / "incident_link.json").write_text(json.dumps({
            "schema": "theory-master/supervisor-incident-link/v1",
            "session_id": sis, "status": "active", "cycle_id": "cycle-002",
        }), encoding="utf-8")
        for status in ("RUNNING", "NEEDS_ATTENTION"):
            dom = BeautifulSoup(banner_html(self.run, {"status": status}), "html.parser")
            self.assertIsNone(dom.select_one("a.tm-incident-open"))
            self.assertNotIn("人工接管会话", dom.get_text())
            self.assertFalse(any(sis in a.get("href", "") for a in dom.select("a")))
        running = BeautifulSoup(banner_html(self.run, {"status": "RUNNING"}), "html.parser")
        self.assertEqual(running.select_one('[data-tui-pane="supervisor"]').get_text(strip=True), "")
        self._write_session("active")
        with_supervisor = BeautifulSoup(banner_html(self.run, {"status": "RUNNING"}), "html.parser")
        self.assertIsNotNone(with_supervisor.select_one("a.tm-supervisor-banner"))
        self.assertIsNone(with_supervisor.select_one("a.tm-incident-open"))

    def test_old_supervisor_hold_is_not_current_preservation_alert(self):
        self._write_session("hold")
        old = {"status": "NEEDS_ATTENTION",
               "attention": {"code": "qualification_failed"}}
        current = {"status": "NEEDS_ATTENTION",
                   "attention": {"code": "investigator_budget_review_rejected"}}
        self.assertEqual(home_status(self.run, old)["phase"], "hold")
        self.assertIsNone(home_status(self.run, current))
        # Keep access to the historical Supervisor dialogue without displaying
        # its stale red hold banner as a new preservation failure.
        from bs4 import BeautifulSoup
        page = BeautifulSoup(banner_html(self.run, current), "html.parser")
        self.assertIsNone(page.select_one("a.tm-supervisor-banner"))

    def test_index_chip(self):
        self.assertEqual(index_chip(self.run), "")
        self._write_session("active")
        chip = BeautifulSoup(index_chip(self.run), "html.parser").select_one("span")
        self.assertEqual(chip["data-supervisor-phase"], "active")
        self.assertIn("接管中", chip.get_text())

    def test_page_renders_session_and_legacy_receipt(self):
        self._write_session("returned")
        (self.root / "cycle-001" / "attention" / "sdk_internal_review_001.json").write_text(json.dumps({
            "schema": "theory-master/internal-attention-review/v1", "at": "2026-10-12T09:00:00Z",
            "attention_code": "needs_attention", "action": "hold", "reason": "历史停下原因",
            "exchange": {}}), encoding="utf-8")
        data = sessions(self.run)
        self.assertEqual(data["cycle"], "cycle-002")
        self.assertEqual(data["cycles"], ["cycle-001", "cycle-002"])
        self.assertEqual(data["sessions"][0]["status"], "returned")
        self.assertEqual(data["sessions"][0]["model"], "gpt-6-luna")
        dom = BeautifulSoup(page_body(self.run, "", {"status": "RUNNING"}), "html.parser")
        section = dom.select_one(f"#{SID}")
        roles = [b["data-role"] for b in section.select("[data-role]")]
        self.assertEqual(roles, ["supervisor", "supervisor", "supervisor"])
        self.assertEqual(section["aria-label"], "Supervisor Incident")
        self.assertNotIn("P" * 80, section.get_text())
        self.assertIsNone(section.select_one("textarea"))
        self.assertIsNone(dom.select_one('[data-tui-pane]'))
        legacy = BeautifulSoup(page_body(self.run, "cycle-001", {}), "html.parser")
        self.assertIn("历史回放", legacy.get_text())
        self.assertIn("历史停下原因", legacy.get_text())
        with self.assertRaises(ValueError):
            sessions(self.run, "../etc")
        with self.assertRaises(ValueError):
            sessions(self.run, "cycle-009")

    def test_sup_page_shows_linked_incident_boxes(self):
        self._write_session("hold")
        sis = "SIS-0123456789abcdef"
        (self.cycle / "supervisor" / "incident_link.json").write_text(json.dumps({
            "schema": "theory-master/supervisor-incident-link/v1",
            "session_id": sis, "status": "active", "cycle_id": "cycle-002",
            "trigger_type": "hold",
        }), encoding="utf-8")
        folder = self.root / ".theory-master" / "supervisor_incidents" / "sessions"
        folder.mkdir(parents=True)
        (folder / f"{sis}.json").write_text(json.dumps({
            "schema": "theory-master/supervisor-incident-session/v1",
            "id": sis, "status": "active", "cycle_id": "cycle-002",
            "trigger_event": "protocol_health=violation",
            "audit": {"supervisor_session_id": SID},
        }), encoding="utf-8")
        (folder / f"{sis}.messages.jsonl").write_text(
            json.dumps({"role": "supervisor", "content": "检测到 cycle-002:\n开始分析原因。"}) + "\n"
            + json.dumps({"role": "tool", "content": "read workflow_state.py"}) + "\n"
            + json.dumps({"role": "system", "content": "确定性 hold"}) + "\n"
            + json.dumps({"role": "human", "content": "看图", "metadata": {
                "images": [{"id": "0123456789abcdef", "name": "shot.png", "media_type": "image/png"}]}}) + "\n",
            encoding="utf-8")
        dom = BeautifulSoup(page_body(self.run, "", {"status": "NEEDS_ATTENTION"}), "html.parser")
        section = dom.select_one(f"#{SID}")
        self.assertEqual(
            [node["data-role"] for node in section.select("[data-role]")],
            ["supervisor", "tool", "system", "human", "system"],
        )
        self.assertEqual(
            section.select_one("img")["src"],
            f"/api/supervisor-session/{sis}/image/0123456789abcdef",
        )
        self.assertIn("开始分析原因。", section.get_text())
        self.assertIn("read workflow_state.py", section.get_text())
        self.assertIn("protocol_health=violation", section.get_text())
        composer = section.select_one("loquix-chat-composer")
        self.assertEqual(composer["data-session"], sis)
        self.assertIsNotNone(section.select_one("loquix-attachment-panel"))
        self.assertIsNone(section.select_one("textarea"))

    def test_empty_run_page(self):
        run = dict(self.run, cycle="")
        dom = BeautifulSoup(page_body(run, "", {}), "html.parser")
        self.assertIn("没有 Supervisor 接管记录", dom.get_text())

    def test_switch_legacy_off_and_new_key(self):
        env = self.root / ".theory-master"
        env.mkdir()
        self.assertTrue(supervisor_enabled_for(self.run, {}))
        (env / "env").write_text("THEORY_MASTER_ATTENTION_TAKEOVER=0\n", encoding="utf-8")
        self.assertFalse(supervisor_enabled_for(self.run, {}))
        (env / "env").write_text("THEORY_MASTER_ATTENTION_TAKEOVER=0\nTHEORY_MASTER_SUPERVISOR=1\n",
                                 encoding="utf-8")
        self.assertTrue(supervisor_enabled_for(self.run, {}))


if __name__ == "__main__":
    unittest.main()
