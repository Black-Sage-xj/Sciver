"""Regression: task switches (Supervisor merged) share the live quota bar; no duplicate panel."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from tm_observer.quota_auto_ui import quota_auto_card
from tm_observer.run_controls_ui import quota_auto_switch, settings_panel
from tm_observer.run_views import run_detail_body
from tm_observer.dashboard_ui import STYLE


class InlineSettingsToolbarTests(unittest.TestCase):
    def test_four_independent_switches_inside_quota_toolbar(self):
        run = {"research_root": "/tmp/unused-for-test"}
        with patch("tm_observer.run_controls_ui.run_monitor.data", return_value={
            "system_failure_actions_enabled": True,
            "cycle_audit_actions_enabled": False,
            "attention_takeover_enabled": False,
            "quota_auto_adjust_enabled": True,
        }), patch("tm_observer.run_controls_ui._read_operator_preferences",
                  return_value={"flags": {}, "models": {}}):
            controls = settings_panel(run)
            quota_toggle = quota_auto_switch(run)
        quota = {"status": "idle", "enabled": True, "selected_account": "1"}
        dom = BeautifulSoup(quota_auto_card(quota, settings_html=controls,
                                            quota_switch_html=quota_toggle), "html.parser")
        toolbar = dom.select_one(".tm-quota-auto-toolbar")
        self.assertIsNotNone(toolbar)
        self.assertIn("自动管理已开启", toolbar.get_text())
        self.assertIn("当前账号 Codex 1", toolbar.get_text(" ", strip=True))
        self.assertEqual(len(toolbar.select(".tm-setting-compact")), 1)
        nodes = {node["data-setting-key"]: node for node in toolbar.select('input[role="switch"]')}
        self.assertEqual(set(nodes), {"quota_auto_adjust", "supervisor"})
        self.assertEqual({k: n["data-setting-mode"] for k, n in nodes.items()},
                         {"quota_auto_adjust":"hot", "supervisor":"hot"})
        self.assertEqual({k: n.has_attr("checked") for k, n in nodes.items()},
                         {"quota_auto_adjust":True, "supervisor":False})
        names = list(nodes)
        self.assertIsNone(toolbar.select_one("details.tm-setting-advanced"))
        self.assertIsNone(toolbar.select_one("details.tm-quota-auto-more"))
        self.assertNotIn("查看账号与额度详情", toolbar.get_text())
        self.assertNotIn("设置说明与审查模型", toolbar.get_text())
        panel = toolbar.select_one("#tm-setting-detail-box")
        self.assertIsNotNone(panel)
        self.assertTrue(panel.has_attr("hidden"))
        self.assertEqual({x["data-setting-detail"] for x in panel.select("[data-setting-detail]")}, set(names))
        self.assertIsNone(toolbar.select_one("details.tm-setting-gear"))
        self.assertIsNone(toolbar.select_one('input[data-setting-key="cycle_audit"]'))
        self.assertEqual(len(toolbar.select("[data-setting-card]")), 2)
        self.assertEqual(len(toolbar.select(".tm-setting-compact")), 1)
        self.assertTrue(all(x.has_attr("hidden") for x in panel.select("[data-setting-detail]")))
        self.assertEqual(len(toolbar.select('input[role="switch"]')), 2)
        self.assertIsNotNone(toolbar.select_one('[data-setting-model="supervisor"]'))
        self.assertEqual(len(toolbar.select("[data-setting-model]")), 1)
        self.assertEqual(len(toolbar.select("[data-setting-save-model]")), 1)
        self.assertEqual(len(dom.select("#tm-web-settings")), 1)
        self.assertEqual(len(dom.select("[data-tui-pane=quota-settings]")), 1)
        self.assertIn("repeat(2,minmax(0,1fr))", STYLE)
        self.assertIn("repeat(2,minmax(0,1fr))", STYLE)

    def test_quota_status_matches_switch_cards_and_only_pill_is_a_label(self):
        from tm_observer.quota_auto_ui import _help_tooltip
        with patch("tm_observer.run_controls_ui.run_monitor.data", return_value={
            "system_failure_actions_enabled": True,
            "cycle_audit_actions_enabled": False,
            "attention_takeover_enabled": False,
            "quota_auto_adjust_enabled": True,
        }), patch("tm_observer.run_controls_ui._read_operator_preferences",
                  return_value={"flags": {}, "models": {}}):
            controls = settings_panel({"research_root": "/tmp/none"})
            quota_toggle = quota_auto_switch({"research_root": "/tmp/none"})
        dom = BeautifulSoup(quota_auto_card(
            {"enabled": True, "status": "idle", "selected_account": "1"},
            settings_html=controls, quota_switch_html=quota_toggle), "html.parser")
        status = dom.select_one(".tm-quota-auto-overview")
        cards = dom.select(".tm-setting-compact")
        self.assertIn("tm-setting-card-surface", status["class"])
        self.assertEqual(len(cards), 1)
        self.assertTrue(all("tm-setting-card-surface" in card["class"] for card in cards))
        self.assertEqual(len(dom.select(".tm-setting-help-wrap")), 2)
        self.assertEqual(len(dom.select(".tm-setting-tooltip[role=tooltip]")), 2)
        self.assertEqual(len(dom.select(".tm-setting-help[tabindex='0']")), 2)
        detail = dom.select_one('[data-setting-detail=supervisor]').get_text()
        self.assertIn("运行故障（FAILED）", detail)
        self.assertIn("NEEDS_ATTENTION", detail)
        self.assertIsNone(dom.select_one('[data-setting-detail=error_repair]'))
        self.assertIsNone(dom.select_one('[data-setting-detail=attention_takeover]'))
        self.assertIn("Supervisor", dom.select_one('[data-setting-detail=quota_auto_adjust]').get_text())
        self.assertEqual(len(status.select("input[role=switch]")), 1)
        self.assertEqual(status.select_one("input[role=switch]")["data-setting-key"], "quota_auto_adjust")
        self.assertEqual(len(status.select("label.tm-setting-switch-control")), 1)
        self.assertEqual(len(dom.select('[data-setting-key="quota_auto_adjust"]')), 1)
        for card in cards:
            with self.subTest(card=card.get_text(" ", strip=True)):
                self.assertEqual(card.name, "div")  # no whole-card label target
                labels = card.find_all("label")
                self.assertEqual(len(labels), 1)
                self.assertIn("tm-setting-switch-control", labels[0]["class"])
                self.assertIsNotNone(labels[0].select_one("input[role=switch]"))
                self.assertIsNone(card.select_one(".tm-setting-compact-copy label"))
                self.assertIsNone(card.select_one(".tm-setting-help-wrap label"))
        escaped = _help_tooltip("test", "<img src=x onerror=alert(1)>")
        self.assertNotIn("<img", escaped)
        self.assertIn("&lt;img", escaped)
        self.assertIn('role="tooltip"', escaped)
        self.assertIn("input:checked + .tm-setting-switch-track", STYLE)
        self.assertIn(".tm-setting-switch-control", STYLE)

    def test_controls_still_exist_if_controller_snapshot_is_unknown(self):
        with patch("tm_observer.run_controls_ui.run_monitor.data", return_value={}), \
             patch("tm_observer.run_controls_ui._read_operator_preferences",
                   return_value={"flags": {}, "models": {}}):
            html = settings_panel({"research_root":"/tmp/none"})
            quota_toggle = quota_auto_switch({"research_root":"/tmp/none"})
        soup = BeautifulSoup(quota_auto_card(None, settings_html=html,
                                             quota_switch_html=quota_toggle), "html.parser")
        self.assertEqual(len(soup.select("[data-setting-key]")), 2)
        self.assertIn("状态待确认", soup.get_text())
        self.assertNotIn("当前账号 Codex", soup.get_text())

    def test_full_run_and_fragment_have_one_live_settings_panel(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle = root / "70"
            cycle.mkdir()
            (cycle/"run_state.json").write_text(json.dumps({
                "cycle_id":"70", "status":"RUNNING", "max_cycles":75,
                "quota_auto_adjust_enabled":True,
                "quota_auto_adjust":{"status":"idle", "enabled":True,
                                     "selected_account":"1"},
            }))
            run = {"observer_id":"a"*16, "research_root":str(root),
                   "cycle_dir":str(cycle), "cycle":"70",
                   "task_name":"Test", "state":"RUNNING", "run_id":1}
            for fragment in (True,False):
                with self.subTest(fragment=fragment):
                    html = run_detail_body(run, fragment=fragment)
                    soup = BeautifulSoup(html,"html.parser")
                    switches = soup.select('.tm-quota-auto-toolbar input[role=switch]')
                    self.assertEqual(len(switches),2)
                    self.assertEqual(len(soup.select('[data-setting-key="quota_auto_adjust"]')),1)
                    self.assertEqual(len(soup.select('.tm-setting-compact')),1)
                    self.assertEqual(len(soup.select('[data-setting-detail]')),2)
                    self.assertIsNone(soup.select_one('.tm-setting-gear'))
                    gear = soup.select_one('.tm-tui-title-actions a.tm-run-settings-link')
                    self.assertIsNotNone(gear)
                    self.assertEqual(gear['href'], '/settings/?observer_id=' + 'a'*16)
                    self.assertEqual(gear['aria-label'], '运行设置')
                    self.assertIsNone(soup.select_one('.tm-quota-auto-more'))
                    self.assertEqual(len(soup.select('#tm-web-settings')),1)
                    self.assertEqual(len(soup.select('.tm-tui-settings')),1)
                    self.assertEqual(len(soup.select('.tm-quota-auto[data-tui-pane=quota-settings]')),1)
                    self.assertEqual(len(soup.select('.tm-run-detail > #tm-web-settings')),0)
                    if not fragment:
                        self.assertIn("document.addEventListener('change',event=>", html)
                        self.assertIn("settings.dataset.settingSaving='1'", html)
                        self.assertIn("oldPanes[n].dataset.tuiPane==='quota-settings'", html)
                        self.assertIn("document.addEventListener('click',event=>", html)


if __name__ == "__main__":
    unittest.main()
