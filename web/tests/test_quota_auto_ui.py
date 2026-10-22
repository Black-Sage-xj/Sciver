"""Semantic read-only Supervisor quota status in the Run overview."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from theory_master.tui.web_bridge import project
from tm_observer.quota_auto_ui import quota_auto_card
from tm_observer.run_views import _tui_summary, run_detail_body


class QuotaAutoPresentationTests(unittest.TestCase):
    def _quota(self, **updates):
        state = {
            "schema": "theory-master/quota-auto-adjust/v1",
            "status": "waiting_refresh",
            "enabled": True,
            "updated_at": "2026-10-12T14:46:16+08:00",
            "selected_account": "1",
            "failed_account": "1",
            "next_check_in_s": 60,
            "accounts": {
                "1": {"account": "1", "ordinary_usage_allowed": False,
                      "primary": {"remaining_percent": 0, "resets_at": 1791796540},
                      "secondary": {"remaining_percent": 48, "resets_at": 1792301261}},
                "2": {"account": "2", "ordinary_usage_allowed": False,
                      "primary": {"remaining_percent": 0, "resets_at": 1791790919},
                      "secondary": {"remaining_percent": 70, "resets_at": 1792332153}},
            },
        }
        state.update(updates)
        return state

    def test_semantic_card_has_account_ids_not_failure_counts(self):
        from tm_observer.dashboard_ui import STYLE
        quota = self._quota()
        dom = BeautifulSoup(quota_auto_card(quota), "html.parser")
        card = dom.select_one(".tm-quota-auto")
        self.assertIsNotNone(card)
        self.assertIn("额度自调", card.get_text())
        self.assertIn("等待额度恢复", card.select_one(".tm-quota-auto-state").get_text())
        self.assertIn("当前账号 Codex 1", card.get_text(" ", strip=True))
        self.assertIn("触发额度暂停 Codex 1", card.get_text(" ", strip=True))
        self.assertIn("自动检查间隔 约 60 秒", card.get_text(" ", strip=True))
        self.assertIsNone(card.select_one("details.tm-quota-auto-more"))
        self.assertNotIn("查看账号与额度详情", card.get_text())
        self.assertFalse(card.select(".tm-quota-auto-account"))
        self.assertNotIn("48%", card.get_text())
        self.assertNotIn("70%", card.get_text())
        self.assertIsNone(card.select_one(".tm-quota-auto-link"))
        self.assertIn("display:grid", STYLE)

    def test_idle_and_disabled_are_not_waiting_and_no_fake_countdown(self):
        idle = self._quota(status="idle", failed_account=None, next_check_in_s=None)
        dom = BeautifulSoup(quota_auto_card(idle), "html.parser")
        self.assertIn("自动管理已开启", dom.get_text())
        self.assertNotIn("触发额度暂停", dom.get_text())
        self.assertNotIn("60 秒", dom.get_text())
        off = self._quota(status="disabled", enabled=False)
        self.assertIn("已关闭", quota_auto_card(off))
        stale_off = self._quota(status="waiting_refresh", enabled=False)
        self.assertIn("已关闭", quota_auto_card(stale_off))
        self.assertNotIn("等待额度恢复", quota_auto_card(stale_off))
        self.assertEqual(quota_auto_card(None), "")
        self.assertEqual(quota_auto_card({"status": "", "enabled": False}), "")
        self.assertIn("当前 Provider 不支持",
                      quota_auto_card(self._quota(status="unsupported_provider")))

    def test_web_bridge_only_exposes_bounded_structured_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "2"
            cycle.mkdir()
            record = {
                "cycle_id": "2", "status": "PAUSED",
                "quota_auto_adjust_enabled": True,
                "quota_auto_adjust": self._quota(
                    selected_account="1", failed_account="2",
                    accounts={**self._quota()["accounts"],
                              "password": "private-token"}),
            }
            (cycle / "run_state.json").write_text(json.dumps(record), encoding="utf-8")
            with patch("theory_master.tui.web_bridge.FileSource") as source:
                source.return_value.snapshot.return_value = record
                source.return_value.run_dir = None
                result = project(root)
            quota = result["quota_auto_adjust"]
            self.assertEqual(quota["status"], "waiting_refresh")
            self.assertEqual(quota["selected_account"], "1")
            self.assertEqual(quota["failed_account"], "2")
            self.assertEqual(quota["next_check_in_s"], 60)
            self.assertEqual(quota["accounts"]["1"]["secondary"]["remaining_percent"], 48)
            self.assertNotIn("password", json.dumps(quota))
            self.assertNotIn("private-token", json.dumps(result))
            self.assertEqual(len(quota["accounts"]), 2)

    def test_web_overview_hides_raw_tui_quota_line_in_all_fallbacks(self):
        header = ("Process PAUSED   State PAUSED\n"
                  "Cycle 70 / 80     Phase investigator:running       Elapsed 12:00\n"
                  "Started  10-12T12:00:00  Cycle start 10-12T12:00:00  Cost —\n"
                  "Quota auto waiting_refresh  selected=1  failed=1")
        summary = _tui_summary(header, show_codex_quota=True)
        self.assertIn("tm-tui-header-stats", summary)
        self.assertNotIn("Quota auto waiting_refresh", summary)
        fallback = _tui_summary("unparseable\nQuota auto waiting_refresh  selected=1  failed=1")
        self.assertNotIn("Quota auto waiting_refresh", fallback)

    def test_actual_run_renders_quota_card_next_to_existing_usage_and_refresh_preserves_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "2"
            cycle.mkdir()
            (cycle / "run_state.json").write_text(json.dumps({
                "cycle_id": "2", "status": "PAUSED",
                "quota_auto_adjust_enabled": True,
                "quota_auto_adjust": self._quota(),
            }), encoding="utf-8")
            run = {"observer_id": "a"*16, "research_root": str(root),
                   "cycle_dir": str(cycle), "cycle": "2",
                   "task_name": "Test", "state": "PAUSED", "run_id": 1}
            html = run_detail_body(run)
            soup = BeautifulSoup(html, "html.parser")
            card = soup.select_one(".tm-quota-auto")
            self.assertIsNotNone(card)
            self.assertIn("等待额度恢复", card.get_text())
            self.assertIsNone(card.select_one("details.tm-quota-auto-more"))
            self.assertNotIn("查看账号与额度详情", card.get_text())
            self.assertEqual(len(card.select('[data-setting-key="quota_auto_adjust"]')),1)
            self.assertEqual(len(card.select('[data-setting-detail]')),2)
            self.assertIsNotNone(card.select_one('[data-setting-key="supervisor"]'))
            self.assertNotIn("Quota auto waiting_refresh", soup.get_text())
            # Generic refresh preserves <details> by index, including quota card.
            self.assertIn("if(openDetails.has(index))detail.open=true", html)
            self.assertIn("setInterval(refresh,8000)", html)

    def test_bad_data_account_ids_and_percentages_not_echoed_as_markup(self):
        unsafe = self._quota(selected_account="<script>", failed_account="9",
                             next_check_in_s="now", accounts={"1": {
                                 "primary": {"remaining_percent": 999},
                                 "secondary": {"remaining_percent": "evil"}
                             }})
        text = quota_auto_card(unsafe)
        self.assertNotIn("<script>", text)
        self.assertNotIn("999%", text)
        self.assertNotIn("width:999%", text)
        self.assertNotIn("now 秒", text)


if __name__ == "__main__":
    unittest.main()
