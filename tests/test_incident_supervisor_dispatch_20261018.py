"""No paid models: deterministic incident routing and isolated Worker dispatch."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from theory_master.observability.incident_routing import classify_incident
from theory_master.observability.incident_registry import (
    record_incident_verification, register_cycle_incident,
)
from theory_master.worker_supervisor_dispatch import (
    _fingerprint, dispatch_pending, review_worker,
)
from theory_master.worker_runtime import save_worker, worker_dir


class RoutingTests(unittest.TestCase):
    def test_cause_not_severity_owns_runtime_decision(self):
        samples = [
            ('error', 'system_failure', 'RUN_PAUSED You have hit your usage limit', 'quota_controller'),
            ('error', 'system_failure', 'Unknown frontier H: historical-c4-decision', 'supervisor_runtime'),
            ('minor', 'json_normalization', 'strip_suffix_prose', 'sdk'),
            ('attention', 'needs_attention', 'investigator_native_goal_blocked', 'supervisor_attention'),
            ('error', 'system_failure', 'selected model is not supported', 'supervisor_runtime'),
            ('important', 'preferred_focus_started', 'started', 'observability'),
        ]
        for severity, kind, summary, expected in samples:
            with self.subTest(summary=summary):
                classified = classify_incident(severity=severity, kind=kind, summary=summary)
                self.assertEqual(classified['owner'], expected)
                self.assertEqual(classified['dispatch_status'], 'classified_only')

    def test_persist_initial_classification_and_only_real_activity(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / '127'
            directory.mkdir()
            incident, created = register_cycle_incident(
                directory, severity='error', kind='system_failure',
                source_event='test', code='run_failed', summary='bad Canvas projection')
            self.assertTrue(created)
            self.assertEqual(incident['routing']['owner'], 'supervisor_runtime')
            verified = record_incident_verification(
                directory, incident_id=incident['incident_id'],
                status='runtime_activity_verified', detail='LLM_MESSAGE after resume')
            self.assertFalse(verified['verification']['root_cause_resolved'])
            self.assertEqual(len((directory/'important_events.jsonl').read_text().splitlines()), 2)
            record_incident_verification(
                directory, incident_id=incident['incident_id'],
                status='runtime_activity_verified', detail='duplicate callback')
            self.assertEqual(len((directory/'important_events.jsonl').read_text().splitlines()), 2)


class WorkerDispatchTests(unittest.TestCase):
    def fixture(self, root: Path):
        cycle = root / '127'
        cycle.mkdir(parents=True)
        state = {'status':'failed','phase':'reporter', 'worker_id':'worker-1',
                 'hypothesis_lock':{'id':'H-1'},
                 'recovery':{'kind':'ValueError','message':'Canvas projection traceback',
                             'at':'2026-10-18T01:00:00+08:00'}}
        (cycle/'research_state.json').write_text(json.dumps(state))
        save_worker(root, 'worker-1', status='FAILED', current_cycle='127',
                    entry_node_id='H-1', target_h_id='H-1', account=None,
                    claim_id='fixture-claim', epoch=1, pid=None)
        (root/'.theory-master'/'cycle_claims.json').write_text(json.dumps({
            'schema':'theory-master/cycle-claims/v1','epoch':1,'history':[],
            'claims':{'fixture-claim':{'claim_id':'fixture-claim','fencing_epoch':1,
                                       'worker_id':'worker-1','cycle_id':'127',
                                       'hypothesis_id':'H-1','status':'claimed'}}}))
        return cycle, state

    def test_off_does_not_launch_anything(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            (root/'.theory-master'/'env').write_text('THEORY_MASTER_SUPERVISOR=0\n')
            with patch('theory_master.worker_supervisor_dispatch.subprocess.Popen') as popen:
                self.assertFalse(dispatch_pending(root))
                popen.assert_not_called()

    def test_dead_helper_retains_recovery_ownership_without_duplicate_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            (root/'.theory-master'/'env').write_text('THEORY_MASTER_SUPERVISOR=1\n')
            mock_child = MagicMock(pid=123456)
            with patch('theory_master.worker_supervisor_dispatch.subprocess.Popen',
                       return_value=mock_child) as popen, \
                 patch('theory_master.worker_supervisor_dispatch.process_start_ticks',
                       return_value=55), \
                 patch('theory_master.worker_supervisor_dispatch.process_identity_alive',
                       return_value=False):
                self.assertTrue(dispatch_pending(root))
                self.assertTrue(dispatch_pending(root))
            self.assertEqual(popen.call_count, 1)
            args = popen.call_args.args[0]
            self.assertEqual(args[args.index('--worker')+1], 'worker-1')
            self.assertEqual(args[args.index('--cycle')+1], '127')

    def test_live_peer_defers_without_llm_or_code_edit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            (root/'.theory-master'/'env').write_text('THEORY_MASTER_SUPERVISOR=1\n')
            workers = [dict(worker_id='worker-1', status='FAILED', process_alive=False,
                            current_cycle='127', entry_node_id='H-1'),
                       dict(worker_id='worker-2', status='RUNNING', process_alive=True)]
            with patch('theory_master.worker_supervisor_dispatch.list_workers',
                       return_value=workers), \
                 patch('theory_master.worker_supervisor_dispatch.subprocess.Popen') as popen:
                self.assertFalse(dispatch_pending(root))
                popen.assert_not_called()

    def test_provider_quota_hold_becomes_bounded_retry_wait(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, state = self.fixture(root)
            (root/'.theory-master'/'env').write_text('THEORY_MASTER_SUPERVISOR=1\n')
            receipt = worker_dir(root, 'worker-1')/'supervisor_dispatch.json'
            receipt.write_text(json.dumps({
                'cycle':'127', 'worker_id':'worker-1', 'fingerprint':_fingerprint(state),
                'status':'hold', 'quota_retry_attempts':0,
                'reason':'Runtime Supervisor review failed safely (ProviderQuotaExhausted: retry after 06:24).',
            }))
            with patch('theory_master.worker_supervisor_dispatch.time.time', return_value=1000), \
                 patch('theory_master.worker_supervisor_dispatch.subprocess.Popen') as popen:
                self.assertTrue(dispatch_pending(root))
                popen.assert_not_called()
            saved = json.loads(receipt.read_text())
            self.assertEqual(saved['status'], 'retry_wait')
            self.assertEqual(saved['quota_retry_attempts'], 1)
            self.assertEqual(saved['retry_at_epoch'], 1900)

    def test_native_goal_unauthorized_block_is_runtime_failure_route(self):
        classified = classify_incident(
            severity='error', kind='system_failure', code='run_failed',
            summary='Native Goal repeatedly returned unauthorized blocked without a provider error; operational recovery required')
        self.assertEqual(classified['owner'], 'supervisor_runtime')
        self.assertEqual(classified['strategy'], 'isolated_code_repair_or_review')

    def test_stale_fingerprint_does_not_review_or_patch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, state = self.fixture(root)
            (root/'.theory-master'/'env').write_text('THEORY_MASTER_SUPERVISOR=1\n')
            path = worker_dir(root,'worker-1')/'supervisor_dispatch.json'
            path.write_text(json.dumps({'cycle':'127', 'fingerprint':'stale'}))
            with patch('theory_master.runtime_supervisor_review.review_runtime_failure') as review:
                self.assertEqual(review_worker(root,'worker-1','127',_fingerprint(state))['status'],'hold')
                review.assert_not_called()


if __name__ == '__main__':
    unittest.main()

class ReviewDispatchTests(unittest.TestCase):
    def test_isolated_reviewer_authorizes_exact_worker_resume_no_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, state = WorkerDispatchTests().fixture(root)
            (root/'.theory-master'/'env').write_text('THEORY_MASTER_SUPERVISOR=1\n')
            receipt = worker_dir(root,'worker-1')/'supervisor_dispatch.json'
            receipt.write_text(json.dumps({'cycle':'127','fingerprint':_fingerprint(state)}))
            with patch('theory_master.supervisor._supervisor_backend', return_value=object()), \
                 patch('theory_master.runtime_supervisor_review.review_runtime_failure',
                       return_value={'action':'resume','session_id':'SUP-test'}) as review, \
                 patch('theory_master.worker_supervisor_dispatch.worker_action',
                       return_value={'pid':42424, 'status':'STARTING'}) as action, \
                 patch('theory_master.specification.TaskSpec.from_task_root', return_value=object()):
                result = review_worker(root,'worker-1','127',_fingerprint(state))
            self.assertEqual(result['status'],'resume_launched')
            self.assertEqual(review.call_args.kwargs['log_path'],
                             worker_dir(root,'worker-1')/'runner.log')
            self.assertEqual(action.call_args.args[1:], ('worker-1','resume'))
            self.assertEqual(json.loads(receipt.read_text())['session_id'], 'SUP-test')

    def test_deterministic_hold_keeps_failed_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle,state = WorkerDispatchTests().fixture(root)
            (root/'.theory-master'/'env').write_text('THEORY_MASTER_SUPERVISOR=1\n')
            receipt = worker_dir(root,'worker-1')/'supervisor_dispatch.json'
            receipt.write_text(json.dumps({'cycle':'127','fingerprint':_fingerprint(state)}))
            with patch('theory_master.supervisor._supervisor_backend', return_value=None), \
                 patch('theory_master.runtime_supervisor_review.review_runtime_failure',
                       return_value={'action':'hold','session_id':'SUP-test','reason':'No safe checkpoint'}), \
                 patch('theory_master.worker_supervisor_dispatch.worker_action') as action:
                result = review_worker(root,'worker-1','127',_fingerprint(state))
            self.assertEqual(result['status'],'hold')
            action.assert_not_called()
            self.assertEqual(json.loads((cycle/'research_state.json').read_text())['status'],'failed')


class SchedulerStallTests(unittest.TestCase):
    def test_no_progress_with_remaining_quota_is_explicitly_stalled(self):
        from types import SimpleNamespace
        from theory_master import worker_runtime, specification, worker_supervisor_dispatch
        from theory_master.worker_mode import configure_mode
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'research'
            root.mkdir()
            configure_mode(root, enabled=True, desired_agents=2)
            meta = root / '.theory-master' / 'worker_scheduler.json'
            meta.write_text(json.dumps({
                'quota_mode': 'per_worker', 'status': 'STALLED', 'max_cycles_per_worker': 2,
                'worker_ids': ['worker-1', 'worker-2'],
                'cycles_started_by_worker': {'worker-1': 2, 'worker-2': 1},
                'resume_batch': True,
            }))
            spec = SimpleNamespace(research_root=root, max_cycles=2)
            with patch.object(specification.TaskSpec, 'from_task_root', return_value=spec), \
                 patch.object(worker_runtime, '_master_complete', return_value=False), \
                 patch.object(worker_supervisor_dispatch, 'dispatch_pending', return_value=False), \
                 patch.object(worker_runtime, 'schedule_once', return_value={'started': [], 'reason': 'no eligible idle Worker'}), \
                 patch.object(worker_runtime, 'list_workers', return_value=[]), \
                 patch.object(worker_runtime, 'SCHEDULER_NO_PROGRESS_GRACE_SECONDS', 0), \
                 patch.object(worker_runtime.threading.Event, 'wait', return_value=False):
                self.assertEqual(worker_runtime.scheduler_daemon(spec, 2), 2)
            saved = json.loads(meta.read_text())
            self.assertEqual(saved['status'], 'STALLED')
            self.assertEqual(saved['stall_reason'], 'no eligible idle Worker')
            self.assertEqual(saved['remaining_cycles_by_worker'], {'worker-1': 0, 'worker-2': 1})
