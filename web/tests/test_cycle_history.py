"""Regression tests for Task-global Recent Progress across Cycle/run restarts."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from tm_observer.cycle_history import cycle_catalog, cycle_events, cycle_summary, cycle_summary_page, selected_cycle
from tm_observer.run_views import _recent_timeline, run_detail_body


class TaskCycleHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def snapshot(self, cid: str, actions: list[dict], agents: list[dict] | None = None):
        folder = self.root / cid
        folder.mkdir()
        (folder / "run_state.json").write_text(json.dumps({
            "cycle_id": cid, "action_history": actions,
            "agent_timeline": agents or [],
        }), encoding="utf-8")

    @staticmethod
    def start(cid: str, minute: int):
        return {"event": "CYCLE_STARTED", "at": f"2026-10-19T14:{minute:02d}:00+08:00",
                "text": f"Cycle started · {cid}"}

    def test_old_cycles_remain_visible_when_latest_snapshot_contains_only_latest(self):
        self.snapshot("1", [self.start("1", 0), {"event": "JUDGE_VERDICT",
            "at": "2026-10-19T14:01:00+08:00", "text": "Judge verdict: supported"}])
        self.snapshot("8", [self.start("8", 8)])
        self.snapshot("22", [self.start("22", 22)])
        catalog = cycle_catalog(self.root)
        self.assertEqual([row["id"] for row in catalog], ["1", "8", "22"])
        self.assertEqual(selected_cycle(catalog), "22")
        raw, events = cycle_events(self.root, catalog, "1")
        self.assertIn("Judge verdict: supported", raw)
        self.assertEqual({e["cycle_id"] for e in events}, {"1"})
        summary = cycle_summary(self.root, catalog, "1")
        self.assertEqual(summary, {"time": "14:00:00", "verdict": "supported"})
        html = _recent_timeline(raw, events, cycle_catalog=catalog, selected_cycle_id="1",
                                cycle_summaries={"1": summary})
        self.assertIn("Judge · supported", html)
        self.assertIn("已显示 3 / 3 个 Cycle", html)
        self.assertIn('data-history-selected="1"', html)
        self.assertIn('data-cycle-history-lazy="8"', html)
        self.assertIn('data-cycle-history-lazy="22"', html)
        self.assertNotIn('tm-cycle-history-link', html)
        self.assertNotIn('查看记录 →', html)
        self.assertIn("Judge verdict: supported", html)
        self.assertEqual(html.count('class="tm-cycle-group"'), 3)

    def test_default_recent_progress_shows_latest_ten_collapsed_with_final_judge_result(self):
        for cid in range(1, 13):
            minute = cid
            actions = [
                self.start(str(cid), minute),
                {"event": "JUDGE_REWORK_REQUESTED", "at": f"2026-10-19T14:{minute:02d}:10+08:00",
                 "text": "Judge returned for rework"},
                {"event": "AGENT_COMPLETED", "at": f"2026-10-19T14:{minute:02d}:20+08:00",
                 "role": "judge", "text": "Judge · done"},
                {"event": "JUDGE_VERDICT", "at": f"2026-10-19T14:{minute:02d}:25+08:00",
                 "verdict": "open", "text": "Judge verdict: open"},
                {"event": "JUDGE_VERDICT", "at": f"2026-10-19T14:{minute:02d}:30+08:00",
                 "verdict": "supported" if cid == 12 else "rejected",
                 "text": f"Judge verdict: {'supported' if cid == 12 else 'rejected'}"},
            ]
            self.snapshot(str(cid), actions)
        catalog = cycle_catalog(self.root)
        summaries = {item["id"]: cycle_summary(self.root, catalog, item["id"])
                     for item in catalog[-10:]}
        html = _recent_timeline("", [], cycle_catalog=catalog, cycle_summaries=summaries)
        soup = BeautifulSoup(html, "html.parser")
        groups = soup.select("details.tm-cycle-group")
        self.assertEqual([group["data-cycle-group-key"] for group in groups],
                         [f"cycle-{cid}" for cid in range(3, 13)])
        self.assertTrue(all(not group.has_attr("open") for group in groups))
        self.assertNotIn('data-cycle-history-lazy="1"', html)
        self.assertNotIn('data-cycle-history-lazy="2"', html)
        latest = groups[-1].select_one("summary.tm-cycle-summary")
        latest_text = latest.get_text(" ", strip=True)
        self.assertIn("14:12:00", latest_text)
        self.assertIn("Judge · supported", latest_text)
        self.assertLess(latest_text.index("14:12:00"), latest_text.index("Judge · supported"))

    def test_top_scroll_page_returns_ten_older_cycles_without_duplicate_or_misattribution(self):
        for cid in range(1, 26):
            self.snapshot(str(cid), [
                self.start(str(cid), cid),
                {"event": "JUDGE_VERDICT", "at": f"2026-10-19T14:{cid:02d}:30+08:00",
                 "verdict": "supported" if cid == 11 else "open",
                 "text": f"Judge verdict: {'supported' if cid == 11 else 'open'}"},
            ])
        catalog = cycle_catalog(self.root)
        initial = _recent_timeline("", [], cycle_catalog=catalog)
        soup = BeautifulSoup(initial, "html.parser")
        scroller = soup.select_one(".tm-run-events-scroll")
        self.assertEqual(scroller["data-cycle-earliest"], "16")
        self.assertEqual(scroller["data-cycle-has-more"], "1")
        self.assertEqual(scroller["data-cycle-total"], "25")
        self.assertEqual([x["data-cycle-history-lazy"] for x in
                          scroller.select("[data-cycle-history-lazy]")],
                         [str(i) for i in range(16, 26)])
        page1 = cycle_summary_page(self.root, "16")
        self.assertEqual([r["id"] for r in page1["cycles"]], [str(i) for i in range(6, 16)])
        self.assertEqual(page1["total"], 25)
        self.assertTrue(page1["has_more"])
        self.assertEqual(page1["cycles"][5]["verdict"], "supported")
        page2 = cycle_summary_page(self.root, "6")
        self.assertEqual([r["id"] for r in page2["cycles"]], [str(i) for i in range(1, 6)])
        self.assertFalse(page2["has_more"])
        self.assertEqual(cycle_summary_page(self.root, "1")["cycles"], [])
        with self.assertRaises(ValueError):
            cycle_summary_page(self.root, "../16")
        with self.assertRaises(ValueError):
            cycle_summary_page(self.root, "-1")
        with self.assertRaises(ValueError):
            cycle_summary_page(self.root, "0" * 9)

    def test_rework_after_verdict_suppresses_stale_final_summary(self):
        self.snapshot("5", [
            self.start("5", 5),
            {"event": "JUDGE_VERDICT", "at": "2026-10-19T14:05:20+08:00",
             "verdict": "supported", "text": "Judge verdict: supported"},
            {"event": "JUDGE_REWORK_REQUESTED", "at": "2026-10-19T14:05:30+08:00",
             "text": "Judge returned for rework"},
        ])
        catalog = cycle_catalog(self.root)
        self.assertEqual(cycle_summary(self.root, catalog, "5"),
                         {"time": "14:05:00", "verdict": ""})

    def test_closed_cycle_uses_science_not_goal_completion(self):
        self.snapshot("174", [
            {"event": "CYCLE_STARTED", "at": "2026-10-21T10:23:21+08:00",
             "text": "Cycle started · 174"},
            {"event": "JUDGE_COMPLETED", "at": "2026-10-21T10:50:11+08:00",
             "role": "judge", "text": "Judge · Goal Completion · Completed · not_passed"},
        ])
        folder = self.root / "174"
        (folder / "research_state.json").write_text(json.dumps({
            "cycle_id": "174", "status": "over", "assessment_state": "terminal",
            "scientific_verdict": "supported",
        }), encoding="utf-8")
        catalog = cycle_catalog(self.root)
        summary = cycle_summary(self.root, catalog, "174")
        self.assertEqual(summary, {"time": "10:23:21", "verdict": "supported"})
        html = _recent_timeline("", [], cycle_catalog=catalog,
                                cycle_summaries={"174": summary})
        self.assertIn("Judge · supported", html)
        self.assertNotIn("Judge · not_passed", html)

        self.snapshot("175", [{"event": "CYCLE_STARTED",
            "at": "2026-10-21T10:51:00+08:00", "text": "Cycle started · 175"}])
        state = self.root / "175" / "research_state.json"
        state.write_text(json.dumps({"cycle_id": "175", "status": "over",
            "assessment_state": "not_adjudicated", "scientific_verdict": None}),
            encoding="utf-8")
        catalog = cycle_catalog(self.root)
        summary = cycle_summary(self.root, catalog, "175")
        self.assertEqual(summary["verdict"], "unadjudicated")
        html = _recent_timeline("", [], cycle_catalog=catalog,
                                cycle_summaries={"175": summary})
        self.assertIn("未裁决", html)
        self.assertNotIn("Judge · unadjudicated", html)

        state.write_text(json.dumps({"cycle_id": "174", "status": "over",
            "assessment_state": "terminal", "scientific_verdict": "rejected"}),
            encoding="utf-8")
        self.assertEqual(cycle_summary(self.root, catalog, "175")["verdict"], "")

    def test_persisted_native_events_expose_judge_and_lead_stages(self):
        self.snapshot("176", [
            {"event": "CYCLE_STARTED", "at": "2026-10-21T11:13:35+08:00",
             "text": "Cycle started · 176"},
            {"event": "JUDGE_COMPLETED", "at": "2026-10-21T11:25:48+08:00",
             "role": "judge", "text": "Judge · done"},
        ], agents=[
            {"role": "judge", "started_at": "2026-10-21T11:25:30+08:00"},
            {"role": "research_lead", "started_at": "2026-10-21T11:25:48+08:00"},
        ])
        events = [
            ("role.started", "11:25:30", "judge", "terminal_assessment", None),
            ("role.completed", "11:25:48", "judge", "terminal_assessment", "open"),
            ("role.started", "11:25:48", "research_lead", "cycle_synthesis", None),
            ("role.completed", "11:26:29", "research_lead", "cycle_synthesis", None),
            ("role.started", "11:26:29", "research_lead", "frontier_maintenance", None),
            ("role.completed", "11:26:57", "research_lead", "frontier_maintenance", None),
        ]
        with (self.root / "176" / "events.jsonl").open("w", encoding="utf-8") as handle:
            for kind, clock, role, stage, verdict in events:
                payload = {"role": role, "stage": stage}
                if verdict:
                    payload["output"] = {"verdict": verdict}
                handle.write(json.dumps({
                    "type": kind, "at": f"2026-10-21T{clock}+08:00",
                    "payload": payload,
                }) + "\n")
        raw, source = cycle_events(self.root, cycle_catalog(self.root), "176")
        self.assertIn("11:25:48  Judge · Terminal Assessment · Completed · open", raw)
        self.assertIn("11:26:29  Research Lead · Cycle Synthesis · done", raw)
        self.assertIn("11:26:57  Research Lead · Frontier Maintenance · done", raw)
        self.assertNotIn("Judge · done", raw)
        self.assertEqual(len(source), len(raw.splitlines()))
        html = _recent_timeline(raw, source)
        self.assertIn("Terminal Assessment · Completed · open", html)
        self.assertIn("Frontier Maintenance · done", html)

    def test_completion_labels_keep_science_and_goal_separate(self):
        from theory_master.observability.collector_projection import CollectorProjectionMixin
        action = CollectorProjectionMixin._action_text
        cases = [
            ("terminal_assessment", "supported", "Terminal Assessment · Completed · supported"),
            ("terminal_assessment", "rejected", "Terminal Assessment · Completed · rejected"),
            ("terminal_assessment", "open", "Terminal Assessment · Completed · open"),
            ("goal_completion", "not_passed", "Goal Completion · Completed · not_passed"),
            ("goal_completion", "passed", "Goal Completion · Completed · passed"),
            ("terminal_assessment", "needs_rework", "Terminal Assessment · Rework requested"),
        ]
        for stage, verdict, expected in cases:
            with self.subTest(stage=stage, verdict=verdict):
                event = {"event": "JUDGE_COMPLETED", "agent": "judge",
                         "stage": stage, "output": {"verdict": verdict}}
                self.assertIn(expected, action(event))
                self.assertTrue(CollectorProjectionMixin._keep_action_history(event, action(event)))
        for stage, label in (("frontier_proposal", "Frontier Proposal"),
                             ("cycle_synthesis", "Cycle Synthesis"),
                             ("frontier_maintenance", "Frontier Maintenance")):
            event = {"event": "AGENT_COMPLETED", "agent": "research_lead", "stage": stage}
            self.assertEqual(action(event), f"Research Lead · {label} · done")
            self.assertTrue(CollectorProjectionMixin._keep_action_history(event, action(event)))

    def test_cumulative_old_actions_not_misattributed_to_new_cycle(self):
        self.snapshot("80", [
            self.start("74", 0),
            {"event": "JUDGE_VERDICT", "at": "2026-10-19T14:01:00+08:00",
             "text": "old verdict"},
            self.start("80", 20),
            {"event": "SKILL_LOADED", "at": "2026-10-19T14:21:00+08:00",
             "text": "Investigator · loaded"},
            self.start("80", 22),
        ], [
            {"started_at": "2026-10-19T14:02:00+08:00", "role": "judge"},
            {"started_at": "2026-10-19T14:21:30+08:00", "role": "investigator"},
        ])
        catalog = cycle_catalog(self.root)
        raw, events = cycle_events(self.root, catalog, "80")
        self.assertNotIn("74", raw)
        self.assertNotIn("old verdict", raw)
        self.assertEqual(len(events), 4)
        self.assertTrue(all(e["at"] >= "2026-10-19T14:20" for e in events))
        self.assertEqual(raw.count("Cycle started · 80"), 2)

    def test_legacy_names_and_invalid_selections_do_not_escape_root(self):
        self.snapshot("cycle-0003", [self.start("3", 3)])
        self.snapshot("5", [self.start("5", 5)])
        (self.root / "cycle-0005").mkdir()
        (self.root / "9").symlink_to(self.root / "5", target_is_directory=True)
        catalog = cycle_catalog(self.root)
        self.assertEqual(catalog, [{"id": "3", "dir": "cycle-0003"},
                                   {"id": "5", "dir": "5"}])
        self.assertEqual(selected_cycle(catalog, "../5"), "5")
        self.assertEqual(selected_cycle(catalog, "99999"), "5")
        self.assertEqual(selected_cycle(catalog, "0003"), "3")
        self.assertIn("Cycle started · 3", cycle_events(self.root, catalog, "3")[0])
        self.assertEqual(cycle_events(self.root, catalog, "9"), ("", []))

    def test_full_page_poll_keeps_explicit_history_selection(self):
        self.snapshot("1", [self.start("1", 1)])
        self.snapshot("12", [
            self.start("12", 12),
            {"event": "JUDGE_VERDICT", "at": "2026-10-19T14:12:30+08:00",
             "verdict": "supported", "text": "Judge verdict: supported"},
        ])
        run = {"observer_id": "a" * 16, "run_id": 1,
               "task_name": "Test", "cycle": "12", "state": "OVER",
               "research_root": str(self.root)}
        historical = run_detail_body(run, history_cycle="1")
        self.assertIn('data-history-selected="1"', historical)
        self.assertIn('data-cycle-history-lazy="12"', historical)
        self.assertNotIn('href="?history_cycle=12"', historical)
        self.assertNotIn('查看记录 →', historical)
        self.assertIn('const selectedHistoryCycle="1"', historical)
        self.assertIn("selectedHistoryCycle?'&history_cycle='", historical)
        self.assertIn("const cycleHistoryLoads=new Set()", historical)
        self.assertIn("Cycle history request failed", historical)
        self.assertIn("loadedCycleHtml", historical)
        latest = run_detail_body(run)
        self.assertNotIn('data-history-selected="12"', latest)
        latest_group = next(group for group in BeautifulSoup(latest, "html.parser").select("details.tm-cycle-group")
                            if group.get("data-cycle-group-key") == "cycle-12")
        self.assertFalse(latest_group.has_attr("open"))
        self.assertIn('data-cycle-history-lazy="12"', latest)
        self.assertIn("14:12:00", latest)
        self.assertIn("Judge · supported", latest)
        self.assertIn('const selectedHistoryCycle=""', latest)

    def test_missing_and_untrustworthy_history_is_not_invented(self):
        self.snapshot("12", [self.start("11", 2)])
        catalog = cycle_catalog(self.root)
        raw, rows = cycle_events(self.root, catalog, "12")
        self.assertEqual((raw, rows), ("", []))
        html = _recent_timeline(raw, rows, cycle_catalog=catalog, selected_cycle_id="12")
        self.assertIn("记录暂不可用", html)
        self.assertIn('data-history-selected="12"', html)


if __name__ == "__main__":
    unittest.main()
