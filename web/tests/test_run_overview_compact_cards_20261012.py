"""Single scientific State and inline read-only active-account Codex quota."""
import unittest
from bs4 import BeautifulSoup
from tm_observer.run_views import _tui_summary,run_detail_body


_HEADER=("Process PAUSED    State PAUSED\n"
         "Cycle 1 / 40     Phase provider_quota_exhausted       Elapsed 09:44:42\n"
         "Started  10-12T02:46:39  Cycle start 10-12T10:44:43  Cost ~$23.44 estimated")


class CompactOverviewCardsTests(unittest.TestCase):
    def test_scientific_state_single_card_phase_removed_and_quota_inside_grid(self):
        soup=BeautifulSoup(_tui_summary(_HEADER,show_codex_quota=True),'html.parser')
        grid=soup.select_one('.tm-tui-kv-grid')
        self.assertIsNotNone(grid)
        cards=grid.select(':scope > .tm-tui-kv')
        self.assertEqual(len(cards),7)
        self.assertEqual([v.get_text(' ',strip=True) for v in grid.select('dt')],
            ['State','Cycle','Codex 额度 · 查询中…','Cost','Elapsed','Started','Cycle start'])
        self.assertIsNone(grid.select_one('[data-tui-process-actions]'))
        self.assertEqual(grid.select_one('.tm-tui-process').text,'PAUSED')
        self.assertNotIn('provider_quota_exhausted',soup.get_text())
        self.assertEqual(len(grid.select('[data-codex-quota]')),1)
        self.assertEqual(len(grid.select('.tm-run-quota [data-quota-week]')),1)
        self.assertEqual(grid.select_one('.tm-run-quota-card-link')['href'],'/services/')
        self.assertIsNotNone(grid.select_one('.tm-run-quota [role="progressbar"]'))

    def test_running_preserves_action_button_and_no_second_state_card(self):
        h=_HEADER.replace('Process PAUSED    State PAUSED','Process RUNNING    State RUNNING')
        soup=BeautifulSoup(_tui_summary(h,show_codex_quota=True),'html.parser')
        self.assertEqual(len(soup.select('dt')),7)
        self.assertIsNotNone(soup.select_one('[data-tui-process-actions]'))
        self.assertEqual(soup.select_one('[data-tui-process-actions]').get_text(' ',strip=True),'RUNNING ▾')

    def test_disabled_process_control_if_state_running_but_no_active_runtime(self):
        h=_HEADER.replace('Process PAUSED    State PAUSED','Process STOPPED   State RUNNING')
        soup=BeautifulSoup(_tui_summary(h,show_codex_quota=True),'html.parser')
        self.assertEqual(soup.select_one('.tm-tui-process').get_text(' ',strip=True),'RUNNING')
        self.assertIsNone(soup.select_one('[data-tui-process-actions]'))

    def test_actual_run_quota_unique_embedded_not_below_and_poll_preserved(self):
        page=run_detail_body({"observer_id":"a"*16,"task_name":"Fixture",
            "cycle":"70","state":"PAUSED"})
        soup=BeautifulSoup(page,'html.parser')
        card=soup.select_one('.tm-tui-kv-grid > .tm-run-quota')
        self.assertIsNotNone(card)
        self.assertEqual(len(soup.select('[data-codex-quota]')),1)
        self.assertFalse(soup.select('.tm-tui-header > .tm-run-quota'))
        self.assertIn('setInterval(loadCodexQuota,300000)',page)
        self.assertIn('paintCodexQuota();',page)
        self.assertIn('Codex 1',page)
        self.assertIn("const weekText=windows.length>1?windowLabel(windows[1])",page)
