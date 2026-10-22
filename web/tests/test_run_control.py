"""Web operator integration tests run ONLY isolated fixtures and mock SDK writes."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tm_observer.run_control import OperatorError, execute
from tm_observer.collector import _proc_start_ticks
from tm_observer.run_controls_ui import control_panel


class OperatorTests(unittest.TestCase):
    def test_idempotent_command_and_cycle_launch_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'.theory-master').mkdir()
            cycle=root/'1'
            cycle.mkdir()
            (cycle/'research_state.json').write_text(json.dumps({'status':'RUNNING', 'cycle_id':'1'}))
            run={'observer_id':'a'*16,'research_root':str(root),'task_root':str(root),
                 'cycle_dir':str(cycle),'cycle':'1','state':'LIVE','run_id':1}
            request={'action':'pause','value':None,'expected_cycle':'1',
                     'expected_status':'RUNNING','expected_launch_id':'',
                     'request_id':uuid4().hex}
            with patch('tm_observer.run_control._sdk_apply',return_value={'requested':'pause'}) as action:
                first=execute(run,request)
                second=execute(run,request)
                self.assertTrue(first['ok'])
                self.assertFalse(first['duplicate'])
                self.assertTrue(second['duplicate'])
                action.assert_called_once()
                with self.assertRaises(OperatorError):
                    execute(run, {**request,'action':'kill'})
                self.assertEqual((root/'.theory-master'/'web-operator-audit.jsonl').read_text().count('\n'),1)
                bad={**request,'request_id':uuid4().hex,'expected_cycle':'2'}
                with self.assertRaisesRegex(OperatorError,'Cycle'):
                    execute(run,bad)
                (root/'.theory-master'/'runtime.json').write_text(json.dumps({'launch_id':'new_runner'}))
                with self.assertRaisesRegex(OperatorError,'Runner'):
                    execute(run,{**request,'request_id':uuid4().hex})

    def test_add_worker_idempotent_and_legacy_runner_interlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.theory-master').mkdir()
            cycle = root / '126'
            cycle.mkdir()
            (cycle / 'research_state.json').write_text(json.dumps({'status': 'RUNNING', 'cycle_id': '126'}))
            run = {'observer_id': 'a'*16, 'research_root': str(root),
                   'task_root': str(root), 'cycle_dir': str(cycle),
                   'cycle': '126', 'state': 'RUNNING', 'run_id': 1}
            request = {'action': 'add_worker', 'value': None, 'expected_cycle': '126',
                       'expected_status': 'RUNNING', 'expected_launch_id': '',
                       'request_id': uuid4().hex}
            with patch('tm_observer.run_control._sdk_apply') as backend:
                with self.assertRaisesRegex(OperatorError, '旧版单 Agent'):
                    execute(run, request)
                backend.assert_not_called()
            registered = root / '.theory-master' / 'workers' / 'worker-1'
            # Only a verified *live* worker may satisfy the concurrency
            # interlock; a stale RUNNING label is not sufficient.
            (cycle / 'research_state.json').write_text(json.dumps({
                'status':'RUNNING', 'cycle_id':'126', 'worker_id':'worker-1'}))
            registered.mkdir(parents=True)
            (registered / 'runtime.json').write_text(json.dumps({
                'schema': 'theory-master/worker-runtime/v1', 'worker_id': 'worker-1',
                'status': 'RUNNING', 'current_cycle': '126',
                'pid': os.getpid(), 'pid_start_ticks': _proc_start_ticks(os.getpid())}))
            with patch('tm_observer.run_control._sdk_apply', return_value={'requested_agents': 2}) as backend:
                one = execute(run, request)
                replay = execute(run, request)
                self.assertTrue(one['ok'])
                self.assertTrue(replay['duplicate'])
                backend.assert_called_once()
                with self.assertRaisesRegex(OperatorError, 'request_id'):
                    execute(run, {**request, 'action': 'set_worker_mode',
                                  'value': {'enabled': True, 'desired_agents': 2}})

    def test_released_state_can_start_worker_scheduler(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.theory-master').mkdir()
            cycle = root / '126'
            cycle.mkdir()
            (cycle / 'research_state.json').write_text(json.dumps({
                'status': 'RELEASED', 'cycle_id': '126'}))
            run = {'observer_id': 'a'*16, 'research_root': str(root),
                   'task_root': str(root), 'cycle_dir': str(cycle),
                   'cycle': '126', 'state': 'RELEASED', 'run_id': 1}
            request = {'action': 'start_worker_scheduler', 'value': None,
                       'expected_cycle': '126', 'expected_status': 'RELEASED',
                       'expected_launch_id': '', 'request_id': uuid4().hex}
            with patch('tm_observer.run_control._sdk_apply',
                       return_value={'status': 'starting'}) as backend:
                result = execute(run, request)
            self.assertTrue(result['ok'])
            self.assertEqual(backend.call_args.args[1], 'start_worker_scheduler')

    def test_public_web_operator_rejects_interrupt_without_sdk_mutation(self):
        from tm_observer.run_control import ACTIONS, HIGH_RISK
        self.assertNotIn("interrupt", ACTIONS)
        self.assertNotIn("interrupt", HIGH_RISK)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/".theory-master").mkdir()
            cycle=root/"1"
            cycle.mkdir()
            (cycle/"research_state.json").write_text(json.dumps(
                {"status":"RUNNING","cycle_id":"1"}))
            run={"observer_id":"a"*16,"research_root":str(root),
                 "task_root":str(root),"cycle_dir":str(cycle),
                 "cycle":"1","state":"RUNNING","run_id":1}
            payload={"action":"interrupt","value":None,
                     "expected_cycle":"1","expected_status":"RUNNING",
                     "expected_launch_id":"","request_id":uuid4().hex}
            with patch("tm_observer.run_control._sdk_apply") as backend:
                with self.assertRaises(OperatorError):
                    execute(run,payload)
                backend.assert_not_called()
            self.assertFalse((root/".theory-master"/"web-operator-receipts").exists())

    def test_paused_kill_uses_existing_idempotent_operator_bridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/".theory-master").mkdir()
            cycle=root/"71"
            cycle.mkdir()
            (cycle/"research_state.json").write_text(json.dumps({
                "status":"paused", "cycle_id":"71", "phase":"provider_quota_exhausted",
                "execution_active":False,"resumable":True
            }))
            run={"observer_id":"a"*16,"research_root":str(root),"task_root":str(root),
                 "cycle_dir":str(cycle),"cycle":"71","state":"PAUSED","run_id":1}
            request={"action":"kill","value":None,"expected_cycle":"71",
                     "expected_status":"PAUSED","expected_launch_id":"",
                     "request_id":uuid4().hex}
            with patch("tm_observer.run_control._sdk_apply",
                       return_value={"requested":"kill","status":"cancelled"}) as backend:
                first=execute(run,request)
                replay=execute(run,request)
            self.assertTrue(first["ok"])
            self.assertTrue(replay["duplicate"])
            backend.assert_called_once()
            self.assertEqual(backend.call_args.args[1],"kill")
            saved=(root/".theory-master"/"web-operator-audit.jsonl").read_text()
            self.assertIn('"action": "kill"',saved)

    def test_paused_cycle_with_live_execution_rejects_kill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/".theory-master").mkdir()
            cycle=root/"71"
            cycle.mkdir()
            (cycle/"research_state.json").write_text(json.dumps({
                "status":"paused","cycle_id":"71","execution_active":True
            }))
            run={"observer_id":"a"*16,"research_root":str(root),"task_root":str(root),
                 "cycle_dir":str(cycle),"cycle":"71","state":"PAUSED","run_id":1}
            with patch("tm_observer.run_control._sdk_apply") as backend:
                with self.assertRaises(OperatorError):
                    execute(run,{"action":"kill","value":None,"expected_cycle":"71",
                                 "expected_status":"PAUSED","expected_launch_id":"",
                                 "request_id":uuid4().hex})
                backend.assert_not_called()

    def test_validation_and_pending_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'.theory-master').mkdir()
            cycle=root/'cycle-0001'
            cycle.mkdir()
            (cycle/'research_state.json').write_text(json.dumps({'status':'NEEDS_ATTENTION','cycle_id':'cycle-0001'}))
            run={'observer_id':'b'*16,'research_root':str(root),'task_root':str(root),
                 'cycle_dir':str(cycle),'cycle':'cycle-0001','state':'NEEDS_ATTENTION'}
            base={'action':'attention_guidance','value':'Proceed with bounded test',
                  'expected_cycle':'cycle-0001','expected_status':'NEEDS_ATTENTION',
                  'expected_launch_id':'','request_id':uuid4().hex}
            with patch('tm_observer.run_control._sdk_apply',side_effect=RuntimeError('unknown write')) as action:
                with self.assertRaises(OperatorError):
                    execute(run,base)
                with self.assertRaisesRegex(OperatorError,'结果尚不确定'):
                    execute(run,base)
                action.assert_called_once()
            with self.assertRaises(OperatorError):
                execute(run,{**base,'request_id':uuid4().hex,'action':'max_cycles','value':True})
            with self.assertRaises(OperatorError):
                execute(run,{**base,'request_id':uuid4().hex,'value':''})
            with self.assertRaises(OperatorError):
                execute(run,{**base,'request_id':'invalid'})
            with self.assertRaises(OperatorError):
                execute(run,{**base,'request_id':uuid4().hex,'action':[]})

    def test_control_html_uses_source_truth_and_safe_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'.theory-master').mkdir()
            cycle=root/'cycle-0001'
            cycle.mkdir()
            (cycle/'run_state.json').write_text(json.dumps({'status':'RUNNING','max_cycles':20,'attention_takeover_enabled':True}))
            (root/'.theory-master'/'runtime.json').write_text(json.dumps({'launch_id':'trusted-episode'}))
            run={'observer_id':'a'*16,'research_root':str(root),'cycle_dir':str(cycle),'cycle':'cycle-0001','state':'RUNNING'}
            html=control_panel(run)
            self.assertIn('data-action',html.replace('data-operator-action','data-action'))
            self.assertIn('data-launch="trusted-episode"',html)
            self.assertIn('data-operator-action="pause"',html)
            self.assertNotIn('data-operator-action="interrupt"',html)
            self.assertNotIn('data-tui-quick-action="interrupt"',html)
            self.assertNotIn("interrupt:'中断'",html)
            self.assertIn('data-operator-action="kill"',html)
            self.assertIn('data-tui-quick-action="pause"',html)
            self.assertIn("action==='kill'?2:1",html)
            self.assertIn('X-TM-Operator-Intent',html)
            self.assertNotIn('canvas-md',html)


if __name__=='__main__':
    unittest.main()


class ManagedLaunchTests(unittest.TestCase):
    def test_control_bridge_uses_fixed_worker_not_shell(self):
        from types import SimpleNamespace
        from tm_observer.run_control import _sdk_apply
        with tempfile.TemporaryDirectory() as tmp:
            run={'task_root':tmp,'research_root':tmp,'run_id':1}
            success=json.dumps({'ok':True,'outcome':{'launch_status':'started','launch_id':'mock-launch'}})
            with patch('tm_observer.run_control.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=success)) as proc:
                result=_sdk_apply(run,'resume',{'cycle_audit':True})
                self.assertEqual(result['launch_id'],'mock-launch')
                args=proc.call_args.args[0]
                self.assertEqual(args[-2:], ['-m','theory_master.operator_control'])
                self.assertFalse(proc.call_args.kwargs['shell'])
                self.assertIn('"action": "resume"',proc.call_args.kwargs['input'])


class OperatorHttpTests(unittest.TestCase):
    def test_post_requires_same_origin_and_intent(self):
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        from tm_observer.web import make_handler
        with tempfile.TemporaryDirectory() as tmp:
            task_id = 'abcdef0123456789'
            task = {'observer_id': task_id, 'research_root': tmp, 'cycle': '1'}
            class MockCache:
                search_root = Path(tmp)
                def get(self):
                    return {'runs': [task]}
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(MockCache()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host = '127.0.0.1:' + str(server.server_address[1])
                url = 'http://' + host + '/api/run/' + task_id + '/action'
                body = json.dumps({'action':'pause'}).encode()
                def request(headers):
                    req = Request(url, data=body, headers=headers, method='POST')
                    try:
                        with urlopen(req, timeout=3) as response: return response.status
                    except HTTPError as error: return error.code
                self.assertEqual(request({}), 403)
                self.assertEqual(request({'Origin': 'https://not-same-origin.example', 'X-TM-Operator-Intent':'1'}), 403)
                with patch('tm_observer.web.execute_run_action', return_value={'ok':True}) as handler:
                    self.assertEqual(request({'Origin':'http://'+host, 'X-TM-Operator-Intent':'1', 'Content-Type':'application/json'}), 200)
                    handler.assert_called_once()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


class RoleRouteActionTests(unittest.TestCase):
    def test_role_model_selection_uses_existing_checked_http_operator(self):
        from tm_observer.run_control import _validated_value, OperatorError
        valid={'role':'investigator','model':'gpt-6-sol','provider':'CodexHTTPS','reasoning_effort':'high'}
        self.assertEqual(_validated_value('set_role_route',valid),valid)
        for invalid in (
            {**valid,'role':'root'}, {**valid,'reasoning_effort':'superhigh'},
            {**valid,'model':['gpt-6-sol']}, {**valid,'command':'rm -rf /'},
        ):
            with self.assertRaises(OperatorError):_validated_value('set_role_route',invalid)
