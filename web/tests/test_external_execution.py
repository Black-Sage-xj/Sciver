"""Read-only executor evidence, queue transitions and shared Web projections."""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from tm_observer import external_execution as execution
from tm_observer.collector import collect_run
from tm_observer.execution_probe import observe
from tm_observer.run_monitor import data
from tm_observer.run_tui_bridge import read_tui
from tm_observer.run_views import run_index_body


class ExternalExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = self.root / 'task'
        self.research = self.root / 'research'
        self.task.mkdir(); self.research.mkdir()
        self.config = {'task_id': 12, 'research_root': str(self.research)}
        self.write(self.task/'config.json', self.config)
        self.write(self.task/'.theory-master/task.json', {'config_path': str(self.task/'config.json')})
        self.pid = os.getpid()
        self.identity = execution.process_identity(self.pid)
        self.cid = 'a'*64
        self.record = {'task_id': 12, 'task_home': str(self.task), 'research_root': str(self.research),
                       'container_id': self.cid, 'image_id': 'sha256:abc', 'selector': 'T1',
                       'display_name': 'T1 示例', 'status_file': str(self.root/'status.json'),
                       'controller_file': str(self.root/'controller.json'), 'controller_pid': self.pid,
                       'controller_start_ticks': self.identity['start_ticks']}
        self.write(self.root/'registry.json', {'schema': execution.REGISTRY_SCHEMA,
                   'observations_file': str(self.root/'evidence.json'), 'tasks': {str(self.research): self.record}})
        self.env = patch.dict(os.environ, {execution.REGISTRY_ENV: str(self.root/'registry.json')})
        self.env.start(); self.addCleanup(self.env.stop)
        self.runtime = {'runner_pid': 7, 'runner_start_ticks': self.identity['start_ticks']}
        self.process = {**self.identity, 'namespace_pids': [self.pid, 7]}
        self.evidence()
        self.write(self.root/'controller.json', {'pending': ['T1'], 'running': []})
        self.write(self.root/'status.json', {'status': 'repair_ready'})

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def evidence(self, *, processes=None, age=0, image='sha256:abc', mounts=True):
        self.write(self.root/'evidence.json', {'schema': execution.EVIDENCE_SCHEMA,
                   'observed_at': (datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat(),
                   'containers': {self.cid: {'id': self.cid, 'image_id': image, 'running': True,
                       'mounts': [{'Source': str(self.root), 'Destination': str(self.root)}] if mounts else [],
                       'processes': [] if processes is None else processes}}})

    def project(self, scientific=None):
        return execution.project(self.research, self.task, self.config, self.runtime,
                                 scientific or {'status': 'running', 'phase': 'investigator:running'},
                                 {'current_agent': 'investigator'})

    def test_queue_then_resume_uses_verified_namespace_owner_without_edits(self):
        self.assertEqual(self.project()['state'], 'QUEUED')
        self.assertEqual(self.project()['queue_position'], 1)
        self.write(self.root/'controller.json', {'pending': [], 'running': ['T1']})
        self.write(self.root/'status.json', {'status': 'resuming_sciver'})
        self.assertEqual(self.project()['state'], 'RECOVERING')
        self.evidence(processes=[self.process])
        result = self.project()
        self.assertEqual(result['state'], 'LIVE')
        self.assertEqual(result['pid'], self.pid)
        self.assertTrue(result['execution_active'])
        self.assertIn('已恢复', result['message'])
        self.evidence(processes=[{**self.process, 'start_ticks': 1}])
        self.assertEqual(self.project()['state'], 'RECOVERING')

    def test_container_running_alone_never_means_task_running(self):
        self.write(self.root/'controller.json', {'pending': [], 'running': []})
        self.assertEqual(self.project()['state'], 'ORPHANED')
        self.evidence(processes=[{**self.process, 'namespace_pids': [self.pid, 1]}])
        self.assertFalse(self.project()['execution_active'])

    def test_stale_evidence_and_wrong_identity_fail_visibly(self):
        self.evidence(age=30)
        self.assertEqual(self.project()['state'], 'OBSERVATION_STALE')
        self.evidence(image='sha256:wrong')
        self.assertEqual(self.project()['state'], 'UNKNOWN')
        self.evidence(mounts=False)
        self.assertEqual(self.project()['state'], 'UNKNOWN')
        self.config['task_id'] = 13
        self.assertIn('身份不匹配', self.project()['message'])

    def test_old_failure_does_not_hide_queue_but_controller_death_does(self):
        self.assertEqual(self.project({'status': 'failed'})['state'], 'QUEUED')
        with patch.object(execution, 'alive', return_value=False):
            self.assertEqual(self.project()['state'], 'PAUSED')

    def test_budget_limit_and_scientific_attention_survive_process_completion(self):
        self.write(self.root/'status.json', {'status': 'time_limit'})
        self.assertEqual(self.project()['state'], 'TIME_LIMIT')
        self.assertIn('评分排队', self.project()['message'])
        self.write(self.root/'controller.json', {'pending': [], 'running': []})
        self.write(self.root/'status.json', {'status': 'complete', 'official_pass': False})
        self.assertEqual(self.project({'status': 'needs_attention'})['state'], 'NEEDS_ATTENTION')
        self.assertEqual(self.project({'status': 'finished'})['state'], 'STOPPED')

    def test_list_detail_monitor_share_projection_and_preserve_raw_science(self):
        cycle = self.research/'1'
        self.write(cycle/'research_state.json', {'status': 'failed', 'phase': 'lead:failed'})
        self.write(cycle/'run_state.json', {'status': 'failed', 'phase': 'lead:failed', 'master_id': 12})
        self.write(self.research/'.theory-master/runtime.json', self.runtime)
        before = (cycle/'research_state.json').read_bytes()
        run = collect_run(self.task)
        self.assertEqual(run['state'], 'QUEUED')
        self.assertEqual(data(run)['status'], 'QUEUED')
        tui = read_tui(run)
        self.assertEqual(tui.get('status'), 'QUEUED', tui.get('unavailable'))
        self.assertFalse(tui['recovery_info']['execution_active'])
        html = run_index_body({'runs': [run]})
        self.assertIn('排队中', html)
        self.assertIn('第 1', html)
        self.assertIn('window.setInterval(refreshTasks,5000)', html)
        # A prior Goal seal is scientific history, not an execution override.
        self.assertIn('排队中', run_index_body({'runs': [{**run, 'sealed_goal_cycle': 1}]}))
        self.assertEqual((cycle/'research_state.json').read_bytes(), before)
        from tm_observer.run_control import execute, OperatorError
        with self.assertRaises(OperatorError) as denied:
            execute(run, {'action': 'resume'})
        self.assertEqual(denied.exception.status, 409)

    def test_probe_keeps_other_containers_when_one_top_fails(self):
        second = 'b'*64
        registry = {'tasks': {'first': {'container_id': self.cid}, 'second': {'container_id': second}}}
        def command(argv, **kwargs):
            if argv[1] == 'inspect':
                return subprocess.CompletedProcess(argv, 0, json.dumps([
                    {'Id': cid, 'Image': 'sha256:abc', 'State': {'Running': True}, 'Mounts': []}
                    for cid in (self.cid, second)]), '')
            if argv[2] == self.cid:
                raise subprocess.CalledProcessError(1, argv)
            return subprocess.CompletedProcess(argv, 0, 'PID\n'+str(self.pid)+'\n', '')
        with patch('tm_observer.execution_probe.subprocess.run', side_effect=command):
            result = observe(registry, ['docker'])
        self.assertEqual(len(result['containers']), 2)
        self.assertEqual(len(result['containers'][second]['processes']), 1)
        self.assertEqual(len(result['errors']), 1)


if __name__ == '__main__':
    unittest.main()
