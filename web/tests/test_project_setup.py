from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from tm_observer.collector import collect_state
from tm_observer.project_setup import source_inventory
from tm_observer.research_chat import ResearchChatError, apply_action, create, send, snapshot
from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler


class ProjectSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.state_dir = self.root / 'state'
        self.original = self.root / 'original'
        self.original.mkdir()
        (self.original / 'research_plan.md').write_text(
            '# Goal\n\n旧目标\n\n# Scientific Model\n\n旧模型猜想仍需检验。\n', encoding='utf-8')
        (self.original / 'measurements.csv').write_text('time,value\n0,1\n', encoding='utf-8')

    def make_preview(self, *, name='新项目', source=True):
        payload = {'mode': 'setup', 'name': name}
        if source:
            payload.update(source_directory=str(self.original), source_files=['research_plan.md', 'measurements.csv'], read_source=True)
        ident = create(self.state_dir, {'runs': []}, payload)['session_id']
        for answer in ('检验扩散系数是否随温度变化', '报告估计值与置信区间', '只使用已授权数据'):
            send(self.state_dir, self.workspace, {'runs': []}, ident, {'content': answer})
        row = snapshot(self.state_dir, {'runs': []}, ident)['session']
        return ident, row['pending_action']

    def test_confirmed_import_is_visible_without_starting_research(self):
        ident, action = self.make_preview()
        self.assertFalse((self.workspace / 'projects').exists())
        self.assertIn('旧模型猜想仍需检验', action['details'])
        self.assertIn('报告估计值与置信区间', action['details'])
        with self.assertRaises(ResearchChatError):
            apply_action(self.state_dir, self.workspace, {'runs': []}, ident,
                         {'action_id': action['id'], 'decision': 'confirm', 'details': action['details'] + 'unsaved'})
        result = apply_action(self.state_dir, self.workspace, {'runs': []}, ident,
                              {'action_id': action['id'], 'decision': 'confirm', 'details': action['details']})
        self.assertEqual(result['redirect'], '/run/')
        self.assertEqual(result, apply_action(self.state_dir, self.workspace, {'runs': []}, ident,
                         {'action_id': action['id'], 'decision': 'confirm'}))
        task = self.workspace / 'projects' / '新项目'
        config = json.loads((task / 'theory-master.config.json').read_text())
        benchmark = Path(config['benchmark_root'])
        self.assertEqual((benchmark / 'source/measurements.csv').read_text(), 'time,value\n0,1\n')
        self.assertEqual((self.original / 'measurements.csv').read_text(), 'time,value\n0,1\n')
        self.assertFalse((task / 'benchmark').exists())
        self.assertIn('旧模型猜想仍需检验', (task / 'research_plan.md').read_text())
        runs = collect_state(self.workspace)['runs']
        self.assertTrue(any(run['observer_id'] == result['task_id'] for run in runs))
        self.assertFalse(any(run.get('execution_active') for run in runs))

    def test_source_change_before_confirmation_is_rejected_without_creating_project(self):
        ident, action = self.make_preview(name='changed')
        (self.original / 'measurements.csv').write_text('time,value\n0,99\n')
        with self.assertRaises(ResearchChatError):
            apply_action(self.state_dir, self.workspace, {'runs': []}, ident,
                         {'action_id': action['id'], 'decision': 'confirm'})
        self.assertFalse((self.workspace / 'projects' / 'changed').exists())

    def test_secret_and_symlink_files_are_not_imported(self):
        (self.original / 'api_token.txt').write_text('sensitive')
        (self.original / 'linked.csv').symlink_to(self.original / 'measurements.csv')
        names = {row['path'] for row in source_inventory(str(self.original))['files']}
        self.assertIn('measurements.csv', names)
        self.assertNotIn('api_token.txt', names)
        self.assertNotIn('linked.csv', names)

    def test_existing_project_is_preserved(self):
        ident, action = self.make_preview(name='occupied', source=False)
        target = self.workspace / 'projects' / 'occupied'
        target.mkdir(parents=True)
        (target / 'keep.txt').write_text('keep')
        with self.assertRaises(ResearchChatError):
            apply_action(self.state_dir, self.workspace, {'runs': []}, ident,
                         {'action_id': action['id'], 'decision': 'confirm'})
        self.assertEqual((target / 'keep.txt').read_text(), 'keep')

    def test_failed_bootstrap_confirmation_can_retry(self):
        ident, action = self.make_preview(name='retry')
        with patch('tm_observer.project_setup.subprocess.run') as run:
            run.return_value.returncode = 1
            with self.assertRaises(ResearchChatError):
                apply_action(self.state_dir, self.workspace, {'runs': []}, ident,
                             {'action_id': action['id'], 'decision': 'confirm'})
        result = apply_action(self.state_dir, self.workspace, {'runs': []}, ident,
                              {'action_id': action['id'], 'decision': 'confirm'})
        self.assertTrue(result['ok'])

    def test_optional_model_draft_retains_human_and_prior_inputs(self):
        class Backend:
            prompts = []
            def invoke(self, role, prompt, thread, model, provider):
                self.prompts.append(prompt)
                return 'fake-thread', ('# Goal\n\n检验扩散系数是否随温度变化\n\n'
                    '# Research Contract\n\n比较扩散模型。\n\n# Final Gate\n\n报告证据。\n\n'
                    '# Required Context\n\n检查资料。\n\n# Scientific Model\n\n模型初稿。\n')
            def drop_thread(self, thread):
                pass

        backend = Backend()
        ident = create(self.state_dir, {'runs': []}, {'mode': 'setup', 'name': 'assisted',
            'source_directory': str(self.original), 'source_files': ['research_plan.md', 'measurements.csv'],
            'read_source': True, 'use_model': True})['session_id']
        for answer in ('检验扩散系数是否随温度变化', '报告估计值与置信区间', '只使用已授权数据'):
            send(self.state_dir, self.workspace, {'runs': []}, ident, {'content': answer}, backend=backend)
        details = snapshot(self.state_dir, {'runs': []}, ident)['session']['pending_action']['details']
        self.assertIn('模型初稿', details)
        self.assertIn('旧模型猜想仍需检验', details)
        self.assertIn('报告估计值与置信区间', details)
        self.assertIn('只使用已授权数据', details)
        self.assertIn('measurements.csv', details)
        self.assertIn('Do not ask whether to start research', backend.prompts[0])

    def test_reject_or_back_does_not_materialize_project(self):
        ident, action = self.make_preview(name='cancelled', source=False)
        apply_action(self.state_dir, self.workspace, {'runs': []}, ident,
                     {'action_id': action['id'], 'decision': 'reject'})
        send(self.state_dir, self.workspace, {'runs': []}, ident, {'content': '/back'})
        row = snapshot(self.state_dir, {'runs': []}, ident)['session']
        self.assertNotIn('constraints', row['setup']['answers'])
        self.assertFalse((self.workspace / 'projects').exists())

    def test_http_confirmation_refreshes_task_list_immediately(self):
        cache = StateCache(self.workspace, ttl_s=3600)
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(cache, field_notes_state_dir=self.state_dir))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(lambda: worker.join(timeout=3))
        base = f'http://127.0.0.1:{server.server_address[1]}'
        with urlopen(base + '/research-chat/', timeout=5) as response:
            self.assertIn('data-setup-welcome', response.read().decode())

        def post(path, payload):
            request = Request(base + path, data=json.dumps(payload).encode(), headers={
                'Origin': base, 'Content-Type': 'application/json', 'X-TM-Operator-Intent': '1'})
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read())

        ident = post('/api/research-chat/session', {'mode': 'setup', 'name': 'http-project'})['session_id']
        for answer in ('研究目的', '验收标准', '暂无'):
            send(self.state_dir, self.workspace, {'runs': []}, ident, {'content': answer})
        action = snapshot(self.state_dir, {'runs': []}, ident)['session']['pending_action']
        result = post(f'/api/research-chat/{ident}/action', {'decision': 'confirm', 'action_id': action['id']})
        self.assertEqual(result['redirect'], '/run/')
        with urlopen(base + '/run/', timeout=5) as response:
            page = response.read().decode()
        self.assertIn('http-project', page)
        with urlopen(base + '/research-chat/?session=' + ident, timeout=5) as response:
            self.assertIn('项目已建立', response.read().decode())


if __name__ == '__main__':
    unittest.main()
