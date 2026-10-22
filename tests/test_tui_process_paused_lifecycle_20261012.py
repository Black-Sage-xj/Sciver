"""PAUSED Workflow must not look like an active scientific call when a
provider-quota watcher or managed Supervisor is still alive.
"""
import unittest
from theory_master.tui.render import render_header
from theory_master.observability.models import blank_run_state


class ProcessLifecycleStatusTests(unittest.TestCase):
    def header(self,status,active=True):
        d=blank_run_state()
        d.update({'status':status,'phase':'provider_quota_exhausted',
                  'execution_active':active,'resumable':True,
                  'cycle_index':1,'max_cycles':40})
        return render_header(d)

    def test_paused_with_runtime_heartbeat_shows_process_paused(self):
        s=self.header('PAUSED',True)
        self.assertIn('Process PAUSED',s)
        self.assertIn('State PAUSED',s)
        self.assertNotIn('Process RUNNING',s)

    def test_running_agent_remains_running(self):
        self.assertIn('Process RUNNING',self.header('RUNNING',True))
        self.assertIn('Process STOPPED',self.header('RUNNING',False))

    def test_interrupted_and_failed_stay_distinct(self):
        self.assertIn('Process INTERRUPTED',self.header('INTERRUPTED',True))
        self.assertIn('State INTERRUPTED',self.header('INTERRUPTED',True))
        self.assertIn('Process STOPPED',self.header('FAILED',True))
        self.assertIn('State FAILED',self.header('FAILED',True))

    def test_finished_or_attentional_supervisor_not_science_running(self):
        for s in ('NEEDS_ATTENTION','OVER','COMPLETE','CANCELLED','ORPHANED'):
            with self.subTest(s=s):
                self.assertIn('Process STOPPED',self.header(s,True))
