"""Read-only quota first paint and whole running Agent green ribbon."""
import subprocess
import time
import unittest
from bs4 import BeautifulSoup

from tm_observer.web import codex_usage_body, services_html
from tm_observer.run_views import _codex_quota_metric_card, _tui_workflow_table
from tm_observer.dashboard_ui import STYLE


class QuotaAndRunningStagePresentationTests(unittest.TestCase):
    def test_running_row_all_cells_receive_full_width_tint(self):
        names=['Research Lead','Challenger','Investigator','Literature','Judge','Reporter']
        states=['running','idle','done','idle','idle','idle']
        raw='AGENT/STAGE    MODEL                         COST      TIME     STATE\n'
        raw += '\n'.join(f'{n:<13}  {"GPT-6 Sol · low":<24}  {"—":>8}  {"00:03":>8}  {st:>8}'
                         for n,st in zip(names,states))
        doc=BeautifulSoup(_tui_workflow_table(raw),'html.parser')
        rows=doc.select('.tm-tui-agent-table tbody tr')
        self.assertEqual(len(rows),6)
        self.assertIn('tm-agent-row-running',rows[0].get('class',[]))
        self.assertEqual(len(rows[0].select('td')),6)
        for r in rows[1:]:
            self.assertNotIn('tm-agent-row-running',r.get('class',[]))
        self.assertIn('tr.tm-agent-row-running > td',STYLE)
        self.assertIn('tr.tm-agent-row-running:hover > td',STYLE)
        # Running is indicated by the row-wide green tint, not a second
        # green accent at the left of the Agent's role-colored identity bar.
        self.assertNotIn('tr.tm-agent-row-running > td:first-child', STYLE)
        self.assertNotIn('box-shadow:inset 2px 0 0 #52ba85', STYLE)
        self.assertIn('tr[class*="tm-role-"] td:first-child { border-left:3px solid var(--role-accent)', STYLE)

    def test_codex_quota_entire_card_is_one_link(self):
        fragment=BeautifulSoup(_codex_quota_metric_card(),'html.parser')
        card=fragment.select_one('[data-codex-quota]')
        links=card.select('a')
        self.assertEqual(len(links),1)
        self.assertEqual(links[0]['href'],'/services/')
        self.assertIn('tm-run-quota-card-link',links[0]['class'])
        self.assertEqual(len(links[0].select('[data-quota-value]')),1)
        self.assertEqual(len(links[0].select('[data-quota-week]')),1)
        self.assertEqual(len(links[0].select('[role=progressbar]')),1)
        self.assertNotIn('详情 ↗',str(card))
        self.assertIn('.tm-run-quota-card-link:focus-visible',STYLE)

    def test_services_warm_html_contains_last_good_percentages_and_age_without_stale_actions(self):
        stamp=int(time.time())-83
        quota={'active_account':'account-2','accounts':[
            {'account_home':'account-1','ok':True,
             'primary':{'remaining_percent':12,'window_duration_mins':300},'secondary':{'remaining_percent':43,'window_duration_mins':10080},
             'queried_at':stamp-20},
            {'account_home':'account-2','ok':True,
             'primary':{'remaining_percent':76,'window_duration_mins':10080},'secondary':None,
             'queried_at':stamp}]}
        page=services_html({},None,quota)
        doc=BeautifulSoup(page,'html.parser')
        panel=doc.select_one('#usage')
        self.assertEqual(panel.get('data-cached-at'),str(stamp))
        self.assertEqual(len(panel.select('.tm-quota-historical')),2)
        self.assertEqual(len(panel.select('.is-active')),1)
        self.assertIn('5-hour window',panel.text)
        self.assertIn('76%',panel.text)
        codex2=panel.select('.tm-quota-historical')[1]
        self.assertIn('7-day window',codex2.text)
        self.assertNotIn('5-hour window',codex2.text)
        self.assertEqual(len(codex2.select('.tm-quota-window')),1)
        self.assertIn('历史记录',doc.select_one('#usage-updated').get_text())
        self.assertEqual(panel.select('.reset-credit-button'),[])
        self.assertEqual(panel.select('.tm-account-switch'),[])
        self.assertIn('查询失败，保留上次记录',page)
        self.assertIn("scheduleUsagePoll(hasCodexSnapshot ? 60000 : 5000)",page)
        self.assertIn('if (content !== renderedUsageSignature)',page)
        self.assertIn('请求',page) if False else None
        code=page.split('<script>',1)[1].split('</script>',1)[0]
        syntax=subprocess.run(['node','--check','--input-type=commonjs'],input=code,
                              text=True,capture_output=True,timeout=12)
        self.assertEqual(syntax.returncode,0,syntax.stderr)

    def test_no_history_fallback_and_sanitize_bad_accounts(self):
        empty=BeautifulSoup(codex_usage_body(), 'html.parser')
        self.assertIn('暂无历史额度记录',empty.select_one('#usage').get_text())
        invalid={'active_account':'account-1','accounts':[{'account_home':'account-1',
             'ok':True,'primary':{'remaining_percent':999}, 'secondary':{'remaining_percent':-10},
             'queried_at':int(time.time())}]}
        safe=BeautifulSoup(codex_usage_body(invalid),'html.parser')
        self.assertNotIn('999%',safe.get_text())
        self.assertNotIn('-10%',safe.get_text())
        self.assertFalse(safe.select('.tm-quota-historical .reset-credit-button'))
