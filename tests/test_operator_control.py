"""SDK command dispatcher mocks: never launch a live research task."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theory_master.operator_control import ControlRejected, dispatch


class OperatorSDKTests(unittest.TestCase):
    def test_managed_resume_enforces_existing_supervisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config=root/'theory-master.config.json'
            config.write_text('{}')
            spec=SimpleNamespace(master_id=1,research_root=root)
            run={'task_root':str(root),'research_root':str(root),'run_id':1,'action':'resume','value':{'cycle_audit':True}}
            with patch('theory_master.operator_control.TaskBinding.load',return_value=SimpleNamespace(config_path=config)):
                with patch('theory_master.operator_control.TaskSpec.from_config',return_value=spec):
                    with patch('theory_master.runtime_manager.runtime_health',return_value={'active':False}):
                        with patch('theory_master.runtime_manager.launch_managed_runtime',return_value={'status':'started','launch_id':'fixture'}) as launch:
                            result=dispatch(run)
                            self.assertEqual(result['launch_status'],'started')
                            self.assertEqual(launch.call_args.kwargs['operation'],'resume')
                            self.assertTrue(launch.call_args.kwargs['cycle_audit'])
                    with patch('theory_master.runtime_manager.runtime_health',return_value={'active':True}):
                        with self.assertRaises(ControlRejected):dispatch(run)

    def test_add_worker_records_one_intent_and_starts_scheduler_without_moving_worker(self):
        from theory_master.worker_mode import read_mode
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'theory-master.config.json'
            config.write_text('{}')
            spec = SimpleNamespace(master_id=1, research_root=root)
            operation = {'task_root': str(root), 'research_root': str(root),
                         'run_id': 1, 'action': 'add_worker', 'value': None}
            binding = patch('theory_master.operator_control.TaskBinding.load',
                            return_value=SimpleNamespace(config_path=config))
            task_spec = patch('theory_master.operator_control.TaskSpec.from_config',
                              return_value=spec)
            with binding, task_spec:
                with patch('theory_master.worker_runtime.runtime_health', return_value={'active': True}), \
                     patch('theory_master.worker_runtime.start_scheduler') as launcher:
                    with self.assertRaises(ControlRejected):
                        dispatch(operation)
                    launcher.assert_not_called()
                    self.assertFalse(read_mode(root)['enabled'])
                with patch('theory_master.worker_runtime.runtime_health', return_value={'active': False}), \
                     patch('theory_master.worker_runtime.list_workers', return_value=[{'worker_id': 'worker-1'}]), \
                     patch('theory_master.worker_runtime.start_scheduler', return_value={'status': 'already_running'}) as launcher:
                    first = dispatch(operation)
                    self.assertEqual(first['requested_agents'], 2)
                    self.assertEqual(first['scheduler'], 'already_running')
                    launcher.assert_called_once()
                    second = dispatch(operation)
                    self.assertEqual(second['requested_agents'], 3)
                    self.assertTrue(read_mode(root)['enabled'])
                    self.assertEqual(read_mode(root)['desired_agents'], 3)

    def test_unknown_action_refused(self):
        with self.assertRaises(ControlRejected):dispatch({'action':'arbitrary_shell'})
        with self.assertRaises(ControlRejected):dispatch({'action':'interrupt'})


if __name__=='__main__':
    unittest.main()
