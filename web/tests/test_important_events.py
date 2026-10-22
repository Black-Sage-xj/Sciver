"""Important-event ledger read-only timeline and Run tab regression tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from tm_observer.important_events import read_important_events, incident_cases
from tm_observer.run_views import _important_timeline, _incident_timeline, run_detail_body


class ImportantEventTimelineTests(unittest.TestCase):
    def test_reads_task_and_cycle_ledgers_in_time_order_without_lifecycle_dedup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "research"
            earlier = root / "1"
            current = root / "cycle-0002"
            earlier.mkdir(parents=True)
            current.mkdir(parents=True)
            rows = [
                {"at":"2026-10-11T14:00:00+08:00","incident_id":"INC-1",
                 "severity":"attention","kind":"needs_attention","lifecycle":"registered",
                 "code":"budget_required","summary":"Budget approval required"},
                {"at":"2026-10-11T14:01:00+08:00","incident_id":"INC-1",
                 "severity":"attention","kind":"needs_attention","lifecycle":"resume_attempt",
                 "summary":"Retry does NOT mean fixed"},
                {"at":"2026-10-11T14:03:00+08:00","incident_id":"INC-1",
                 "severity":"attention","kind":"needs_attention","lifecycle":"recurred",
                 "summary":"Again"},
            ]
            (earlier/"cycle_anomalies.jsonl").write_text(json.dumps(rows[0])+"\n")
            (current/"important_events.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows[1:])+"\n")
            focus = {"at":"2026-10-11T05:05:30+00:00","incident_id":"EVT-focus",
                     "kind":"preferred_focus_started","severity":"important",
                     "lifecycle":"registered","summary":"Preferred research focus started"}
            (root/"important_events.jsonl").write_text(json.dumps(focus)+"\n")
            run={"research_root":str(root),"cycle_dir":str(current)}
            found=read_important_events(run)
            self.assertEqual([event["incident_id"] for event in found],
                             ["EVT-focus","INC-1","INC-1","INC-1"])
            self.assertEqual([event["_display_cycle"] for event in found],
                             ["任务级","1","0002","0002"])
            self.assertEqual([event["lifecycle"] for event in found][2:],
                             ["resume_attempt","recurred"])
            milestones=BeautifulSoup(_important_timeline(found),"html.parser")
            self.assertEqual(len(milestones.select("li.tm-important-item")),1)
            self.assertIn("Preferred Focus 开始", milestones.get_text())
            cases=incident_cases(found)
            self.assertEqual(len(cases),2)  # Cycle identity prevents cross-cycle merging
            self.assertEqual(len(cases[1]["records"]),2)
            self.assertEqual([x["lifecycle"] for x in cases[1]["records"]],
                             ["resume_attempt","recurred"])
            archive=BeautifulSoup(_incident_timeline(found),"html.parser")
            self.assertEqual(len(archive.select_one(".tm-important-scroll > ol").find_all("li",recursive=False)),2)
            self.assertIn("2 条原始记录",archive.get_text())
            self.assertIn("Retry does NOT mean fixed",archive.get_text())
            self.assertEqual(archive.select_one(".tm-important-scroll")["role"],"region")

    def test_invalid_files_and_symlinks_do_not_escape_allowed_task(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            root=base/"research"
            active=root/"4"
            active.mkdir(parents=True)
            outside=base/"outside.jsonl"
            outside.write_text(json.dumps({"at":"2026-10-12T00:00:00+08:00",
                                            "severity":"error","summary":"exfiltrate"})+"\n")
            (active/"important_events.jsonl").symlink_to(outside)
            (root/"cycle-0003").symlink_to(base, target_is_directory=True)
            (root/"important_events.jsonl").write_text("{broken}\n")
            self.assertEqual(read_important_events({"research_root":str(root),
                                                    "cycle_dir":str(active)}), [])
            self.assertEqual(read_important_events({"research_root":str(root),
                                                    "cycle_dir":str(base)}), [])

    def test_escapes_untrusted_values_and_scrubs_secret_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            cycle=root/"1"
            cycle.mkdir()
            payload={"at":"2026-10-12T02:00:00+08:00","kind":"system_failure",
                     "severity":"error","lifecycle":"notification_failed",
                     "code":'bad" onclick="alert(1)',
                     "summary":"Error <img src=x onerror=alert(1)>",
                     "password":"private-pw"}
            (cycle/"important_events.jsonl").write_text(json.dumps(payload))
            found=read_important_events({"research_root":str(root),"cycle_dir":str(cycle)})
            html=_incident_timeline(found)
            self.assertNotIn("<img",html)
            self.assertIn("&lt;img",html)
            self.assertNotIn("private-pw",html)
            self.assertIn("[redacted]",html)
            self.assertIn("通知失败",html)
            self.assertIn("系统执行失败",html)
            # Raw code still exists only in the collapsed full-record JSON.
            self.assertIn("onclick", html)
            rendered = BeautifulSoup(html, "html.parser")
            self.assertIsNone(rendered.select_one(".tm-important-code"))
            self.assertIn("bad", rendered.select_one("details.tm-important-more pre").get_text())
            self.assertNotIn("onclick", rendered.select_one(".tm-important-meta").get_text())

    def test_kind_first_readable_rows_no_grid_overlap_or_placeholder_noise(self):
        from tm_observer.dashboard_ui import STYLE
        events = [
            {"at":"2026-10-10T15:43:46+08:00", "_display_cycle":"49",
             "severity":"attention", "kind":"needs_attention", "lifecycle":"registered",
             "code":"investigator_native_goal_usageLimited",
             "summary":"NEEDS_ATTENTION", "incident_id":"INC-x"},
            {"at":"2026-10-10T15:43:48+08:00", "_display_cycle":"49",
             "severity":"attention", "kind":"needs_attention", "lifecycle":"notification_sent",
             "code":"investigator_native_goal_usageLimited",
             "incident_id":"INC-x"},
            {"at":"2026-10-10T17:04:27+08:00", "_display_cycle":"49",
             "severity":"error", "kind":"system_failure", "lifecycle":"registered",
             "code":"codex_network_unreachable", "incident_id":"INC-y",
             "summary":("Master1 Cycle49 managed resume with Investigator GPT-6 Luna "
                        "failed before SDK research: codex-net no reachable HTTPS route "
                        "through current Mihomo proxies; original state unchanged.")},
        ]
        dom = BeautifulSoup(_incident_timeline(events),"html.parser")
        cards = dom.select_one(".tm-important-scroll > ol").find_all("li",recursive=False)
        self.assertEqual(len(cards),2)  # two incidents, not three lifecycle messages
        self.assertEqual([x.select_one(".tm-important-kind").get_text()
                          for x in cards], ["需要关注", "系统执行失败"])
        self.assertIn("2 条原始记录", cards[0].get_text())
        self.assertIn("Investigator 模型原生使用额度受限", cards[0].get_text())
        self.assertIn("Codex 网络无法连接", cards[1].get_text())
        self.assertIn("Mihomo proxies", cards[1].get_text())
        self.assertIn(".tm-log-item.tm-important-item { display:block; grid-template-columns:none",STYLE)
        self.assertIn(".tm-important-item .tm-important-meta { display:flex;",STYLE)
        self.assertIn("flex-wrap:wrap",STYLE)

    def test_run_header_tabs_share_card_and_keep_existing_event_links(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            cycle=root/"1"
            cycle.mkdir()
            (cycle/"run_state.json").write_text(json.dumps({
                "cycle_id":"1","status":"RUNNING",
                "action_history":[{"at":"2026-10-11T22:00:00+08:00",
                                   "event":"CYCLE_STARTED","text":"Cycle started · 1"}]}))
            (cycle/"important_events.jsonl").write_text(json.dumps({
                "at":"2026-10-11T22:01:00+08:00","severity":"attention",
                "kind":"needs_attention","code":"hold","summary":"Approval required",
                "lifecycle":"registered"})+"\n")
            run={"observer_id":"a"*16,"research_root":str(root),"cycle_dir":str(cycle),
                 "task_name":"Test","cycle":"1","state":"LIVE","run_id":1}
            page=run_detail_body(run)
            soup=BeautifulSoup(page,"html.parser")
            history=soup.select_one('[data-tui-pane="last"]')
            self.assertIsNotNone(history)
            self.assertEqual([tab.get_text() for tab in history.select('[data-history-view]')],
                             ["最近进展","重要事件","异常记录"])
            self.assertEqual([tab["aria-selected"] for tab in history.select('[data-history-view]')],
                             ["true","false","false"])
            self.assertIsNotNone(history.select_one('[data-history-panel="recent"] .tm-cycle-group'))
            important=history.select_one('[data-history-panel="important"]')
            self.assertTrue(important.has_attr("hidden"))
            self.assertNotIn("Approval required",important.get_text())
            archive=history.select_one('[data-history-panel="incidents"]')
            self.assertIn("Approval required",archive.get_text())
            self.assertIn("historyView='recent'",page)
            self.assertIn("applyHistoryView()",page)
            self.assertIn("historyScroll=new Map",page)
            self.assertIn("expandedImportant",page)
            self.assertIsNotNone(soup.select_one('.tm-tui-center'))

    def test_cycle_127_three_lifecycle_rows_are_one_incident_case(self):
        records=[
            {"at":"2026-10-17T22:29:27+08:00","_display_cycle":"127",
             "severity":"error","kind":"system_failure","code":"run_failed",
             "incident_id":"INC-100401027bfde406","lifecycle":"registered",
             "summary":"Unknown frontier H: historical-c4-decision"},
            {"at":"2026-10-17T22:29:27+08:00","_display_cycle":"127",
             "severity":"error","kind":"system_failure","code":"run_failed",
             "incident_id":"INC-100401027bfde406","lifecycle":"recurred",
             "summary":"Unknown frontier H: historical-c4-decision"},
            {"at":"2026-10-17T22:31:29+08:00","_display_cycle":"127",
             "severity":"error","kind":"system_failure","code":"run_failed",
             "incident_id":"INC-100401027bfde406","lifecycle":"resume_attempt",
             "summary":"Unknown frontier H: historical-c4-decision"},
        ]
        self.assertEqual(len(incident_cases(records)),1)
        self.assertEqual(len(BeautifulSoup(_important_timeline(records),"html.parser").select("li.tm-important-item")),0)
        html=BeautifulSoup(_incident_timeline(records),"html.parser")
        self.assertEqual(len(html.select_one(".tm-important-scroll > ol").find_all("li",recursive=False)),1)
        self.assertIn("3 条原始记录", html.get_text())
        self.assertIn("恢复尝试", html.get_text())
        self.assertEqual(len(incident_cases(records)[0]["records"]),3)


if __name__=="__main__":
    unittest.main()
