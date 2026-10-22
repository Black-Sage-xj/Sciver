"""New operator control remains guarded and opens independent warning dialog."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from bs4 import BeautifulSoup
from tm_observer.run_control import OperatorError, execute
from tm_observer.run_controls_ui import control_panel
from tm_observer.run_views import _tui_summary


class AbandonOperatorTests(unittest.TestCase):
    def fixture(self, path: Path, status='RUNNING'):
        (path/'.theory-master').mkdir()
        cycle=path/'cycle-0001'
        cycle.mkdir()
        (cycle/'research_state.json').write_text(json.dumps({'status':status,'cycle_id':'cycle-0001','sdk_runtime_capabilities':{'abandon_cycle_v1':True}}))
        return {'observer_id':'a'*16,'research_root':str(path),'task_root':str(path),
                'cycle_dir':str(cycle),'cycle':'cycle-0001','state':status,'run_id':1}

    def test_new_abandon_web_action_uses_same_idempotent_safe_bridge(self):
        with tempfile.TemporaryDirectory() as temporary:
            run=self.fixture(Path(temporary))
            payload={'action':'abandon_cycle','value':None,'expected_cycle':'cycle-0001',
                     'expected_status':'RUNNING','expected_launch_id':'','request_id':uuid4().hex}
            with patch('tm_observer.run_control._sdk_apply',return_value={
                'requested':'abandon_cycle','effective_at':'next_sdk_safe_boundary'}) as backend:
                first=execute(run,payload)
                replay=execute(run,payload)
            self.assertTrue(first['ok'])
            self.assertTrue(replay['duplicate'])
            backend.assert_called_once()
            self.assertEqual(backend.call_args.args[1],'abandon_cycle')
            with self.assertRaises(OperatorError):
                execute(run,{**payload,'request_id':uuid4().hex,'expected_status':'OVER'})
            state=(Path(temporary)/'.theory-master'/'web-operator-audit.jsonl').read_text()
            self.assertIn('"action": "abandon_cycle"',state)

    def test_old_running_process_is_never_sent_unrecognized_abandon(self):
        with tempfile.TemporaryDirectory() as temporary:
            run=self.fixture(Path(temporary))
            state_path=Path(run['cycle_dir'])/'research_state.json'
            state=json.loads(state_path.read_text())
            state.pop('sdk_runtime_capabilities',None)
            state_path.write_text(json.dumps(state))
            page=BeautifulSoup(control_panel(run),'html.parser')
            self.assertTrue(page.select_one('[data-operator-action="kill"]').has_attr('disabled'))
            self.assertTrue(page.select_one('[data-tui-quick-action="kill"]').has_attr('disabled'))
            self.assertIn('尚未加载安全结束协议',str(page))
            payload={'action':'abandon_cycle','value':None,
                     'expected_cycle':'cycle-0001','expected_status':'RUNNING',
                     'expected_launch_id':'','request_id':uuid4().hex}
            with patch('tm_observer.run_control._sdk_apply') as backend:
                with self.assertRaisesRegex(OperatorError,'尚未加载'):
                    execute(run,payload)
                backend.assert_not_called()

    def test_stopped_cycle_cannot_be_abandoned(self):
        with tempfile.TemporaryDirectory() as temporary:
            run=self.fixture(Path(temporary),status='OVER')
            with self.assertRaises(OperatorError):
                execute(run,{'action':'abandon_cycle','value':None,
                  'expected_cycle':'cycle-0001','expected_status':'OVER',
                  'expected_launch_id':'','request_id':uuid4().hex})

    def test_independent_modal_warns_and_requires_acknowledgement(self):
        panel=BeautifulSoup(control_panel({'observer_id':'a'*16,'cycle':'1',
            'state':'RUNNING'}),'html.parser')
        self.assertIsNotNone(panel.select_one('[data-operator-action="kill"]'))
        self.assertIsNone(panel.select_one('[data-operator-action="abandon_cycle"]'))
        self.assertIsNotNone(panel.select_one('#tm-operator-quick-dialog'))
        self.assertIsNotNone(panel.select_one('#tm-operator-confirm-dialog'))
        for kind in ('pause','kill'):
            self.assertIsNotNone(panel.select_one(f'[data-tui-quick-action="{kind}"]'))
        self.assertIsNone(panel.select_one('[data-tui-quick-action="interrupt"]'))
        self.assertIsNone(panel.select_one('[data-operator-action="interrupt"]'))
        text=str(panel)
        self.assertIn("kill:'Kill · 结束当前 Cycle'",text)
        self.assertIn('ack.checked=false',text)
        self.assertIn('submit.disabled=destructive',text)
        self.assertNotIn('window.confirm(',text)
        self.assertIn('dialog.showModal();',text)
        head=BeautifulSoup(_tui_summary("Process RUNNING   State RUNNING\nCycle 1 / 40    Phase investigator:running  Elapsed 10:00\nStarted 10-12T00:00:00  Cycle start 10-12T00:00:00  Cost —"),'html.parser')
        self.assertIsNotNone(head.select_one('[data-tui-process-actions]'))
        self.assertFalse(head.select('.tm-process-action-menu'))
