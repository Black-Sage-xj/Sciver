"""State PAUSED exposes Resume and destructive Kill through confirmed operator."""
import subprocess
import unittest
from unittest.mock import patch
from bs4 import BeautifulSoup
from tm_observer.run_controls_ui import can_resume_state, control_panel
from tm_observer.run_views import _tui_summary, run_detail_body

_PAUSED=("Process PAUSED    State PAUSED\n"
         "Cycle 2 / 40     Phase provider_quota_exhausted       Elapsed 02:14:32\n"
         "Started  10-12T02:46:39  Cycle start 10-12T18:45:30  Cost ~$38.35 estimated")
_RUNNING=_PAUSED.replace('PAUSED','RUNNING').replace('provider_quota_exhausted','investigator:running')


class PausedStateResumeDialogTests(unittest.TestCase):
    def test_paused_clickable_only_when_same_operator_supports_resume(self):
        for allowed in (True,False):
            with self.subTest(allowed=allowed):
                root=BeautifulSoup(_tui_summary(_PAUSED,resume_available=allowed),"html.parser")
                button=root.select_one('[data-tui-process-actions]')
                self.assertEqual(bool(button),allowed)
                if allowed:
                    self.assertEqual(button.text.strip(),'PAUSED▾')
                    self.assertEqual(button.get('aria-haspopup'),'dialog')
                    self.assertIn('Resume',button.get('title'))
                else:
                    self.assertIsNotNone(root.select_one('span.tm-tui-process-amber'))
        for state in ('OVER','CANCELLED','STOPPED'):
            header=_PAUSED.replace('Process PAUSED    State PAUSED',f'Process STOPPED    State {state}')
            root=BeautifulSoup(_tui_summary(header,resume_available=True),'html.parser')
            self.assertFalse(root.select('[data-tui-process-actions]'),state)
        for state in ('FAILED','NEEDS_ATTENTION','INTERRUPTED'):
            header=_PAUSED.replace('Process PAUSED    State PAUSED',f'Process STOPPED    State {state}')
            root=BeautifulSoup(_tui_summary(header,resume_available=True),'html.parser')
            self.assertIsNotNone(root.select_one('[data-tui-process-actions]'))

    def test_modal_contains_only_resume_for_paused_cycle(self):
        state={'status':'paused','phase':'provider_quota_exhausted',
               'cycle_id':'71','resumable':True}
        run={'observer_id':'a'*16,'cycle':'71','state':'PAUSED','task_name':'Fixture'}
        with patch('tm_observer.run_controls_ui.run_monitor.data',return_value=state):
            page=control_panel(run)
        root=BeautifulSoup(page,'html.parser')
        quick=root.select_one('#tm-operator-quick-dialog')
        self.assertEqual([b['data-tui-quick-action'] for b in quick.select('[data-tui-quick-action]')],['resume','kill'])
        self.assertIn('继续之前暂停的科研任务',quick.get_text())
        self.assertIsNotNone(quick.select_one('.tm-operator-resume-options'))
        self.assertFalse(root.select_one('[data-operator-action="resume"]').has_attr('disabled'))
        self.assertTrue(root.select_one('[data-operator-action="pause"]').has_attr('disabled'))
        self.assertFalse(root.select_one('[data-operator-action="kill"]').has_attr('disabled'))
        self.assertIn("永久作废当前 Cycle",page)
        self.assertIn("const confirmations=action===\'kill\'?2:1",page)
        # The single shared delegated-click handler lives in run_views, not
        # inside the control panel itself.
        self.assertIn("const dialog=confirmDialog",page)
        self.assertIn("dialog.showModal();",page)
        self.assertIn("action==='resume'",page)
        self.assertIn('从之前的检查点恢复当前科研 Cycle',page)
        self.assertNotIn("window.confirm(",page)

    def test_running_and_abandoned_preserve_existing_state_controls(self):
        run={'observer_id':'a'*16,'cycle':'71','state':'RUNNING'}
        with patch('tm_observer.run_controls_ui.run_monitor.data',
                   return_value={'status':'running','cycle_id':'71','sdk_runtime_capabilities':{'abandon_cycle_v1':True}}):
            root=BeautifulSoup(control_panel(run),'html.parser')
        actions=[b['data-tui-quick-action'] for b in root.select('[data-tui-quick-action]')]
        self.assertEqual(actions,['pause','kill'])
        running=BeautifulSoup(_tui_summary(_RUNNING,resume_available=False),'html.parser')
        self.assertIsNotNone(running.select_one('[data-tui-process-actions]'))
        self.assertFalse(can_resume_state({'status':'interrupted','phase':'abandon_requested'}))
        self.assertTrue(can_resume_state({'status':'paused','phase':'provider_quota_exhausted'}))

    def test_complete_run_page_binds_paused_badge_to_real_status(self):
        run={'observer_id':'a'*16,'cycle':'71','state':'PAUSED','task_name':'Fixture'}
        projection={'status':'PAUSED','cycle_id':'71','parts':{'header':_PAUSED}}
        state={'status':'paused','phase':'provider_quota_exhausted',
               'cycle_id':'71','resumable':True}
        with patch('tm_observer.run_tui_bridge.read_tui',return_value=projection),\
             patch('tm_observer.run_views.run_monitor.data',return_value=state),\
             patch('tm_observer.run_controls_ui.run_monitor.data',return_value=state):
            page=run_detail_body(run)
        soup=BeautifulSoup(page,'html.parser')
        self.assertIsNotNone(soup.select_one('.tm-tui-header [data-tui-process-actions]'))
        self.assertEqual([b['data-tui-quick-action'] for b in soup.select('#tm-operator-quick-dialog [data-tui-quick-action]')],['resume','kill'])
        self.assertIn("box.after(quickDialog,confirmDialog)",page)
        self.assertIn("existing.click();",page)
        self.assertIn("if(existing&&!existing.disabled)",page)
        self.assertIn("present.dataset.tuiStatus!==incoming.dataset.tuiStatus",page)
        self.assertIn("present.dataset.tuiCycle!==incoming.dataset.tuiCycle",page)
        self.assertIn("window.location.reload();",page)
        # Rendering never issues an operator request.
        self.assertEqual(page.count('id="tm-operator-quick-dialog"'),1)
        self.assertIsNotNone(soup.select_one('#tm-start-progress-dialog'))
        self.assertIn('正在开启运行',soup.select_one('#tm-start-progress-dialog').get_text(' ',strip=True))
        self.assertIn('window.tmStartProgress',page)
        self.assertIn('不会重复提交启动请求',page)
        self.assertEqual(soup.select_one('.tm-tui-screen')['data-runtime-verified'],'false')
        for script in soup.select('script'):
            js=script.get_text()
            if not js.strip():
                continue
            p=subprocess.run(['node','--check','--input-type=commonjs'],input=js,text=True,capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr)
