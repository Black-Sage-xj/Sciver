"""Five-hour and seven-day Codex usage must sit beside each other."""
import unittest
from bs4 import BeautifulSoup
from tm_observer.run_views import _codex_quota_metric_card, run_detail_body
from tm_observer.dashboard_ui import STYLE


class CodexQuotaUsageRowTests(unittest.TestCase):
    def test_five_hour_left_and_seven_day_right_same_row(self):
        fragment=BeautifulSoup(_codex_quota_metric_card(),"html.parser")
        row=fragment.select_one(".tm-run-quota-usage-row")
        self.assertIsNotNone(row)
        children=row.find_all(recursive=False)
        self.assertEqual([c.get("data-quota-value") is not None for c in children],
                         [True,False])
        self.assertEqual([c.get("data-quota-week") is not None for c in children],
                         [False,True])
        self.assertIn("额度窗口剩余",children[0].text)
        self.assertTrue(children[1].has_attr('hidden'))
        self.assertIn("tm-run-quota-track",row.find_next_sibling().get("class",[]))
        self.assertIn("display:flex",STYLE.split(".tm-run-quota-usage-row",1)[1][:130])
        self.assertIn("justify-content:space-between",STYLE.split(".tm-run-quota-usage-row",1)[1][:130])
        self.assertIn("flex-wrap:wrap",STYLE.split(".tm-run-quota-usage-row",1)[1][:180])

    def test_live_run_has_one_codex_quota_card_and_existing_poll(self):
        run={"observer_id":"a"*16,"cycle":"70","state":"PAUSED","task_name":"Fixture"}
        page=run_detail_body(run)
        soup=BeautifulSoup(page,"html.parser")
        self.assertEqual(len(soup.select("[data-codex-quota]")),1)
        self.assertEqual(len(soup.select(".tm-run-quota-usage-row")),1)
        self.assertTrue(soup.select_one(".tm-tui-kv-grid > .tm-run-quota"))
        self.assertEqual(soup.select_one(".tm-run-quota-card-link").get("href"),"/services/")
        self.assertIn("w.window_duration_mins===300?'5 小时'",page)
        self.assertIn("w.window_duration_mins===10080?'7 天'",page)
