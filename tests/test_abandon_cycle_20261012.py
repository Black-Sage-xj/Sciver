"""Abandoning current Cycle is a terminal non-scientific operational action."""
import json
import tempfile
import unittest
from pathlib import Path

from theory_master.core import Workflow
from theory_master.workflow_support import RecoveryStop
import test_core


class AbandonCycleTests(unittest.TestCase):
    def test_abandon_requires_safe_checkpoint_preserves_canvas_and_disables_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture=test_core.TheoryMasterTest()
            spec,backend=fixture.make(Path(directory),reworks=0)
            workflow=Workflow(spec,backend)
            original=workflow.initialize()
            original["sdk_runtime_capabilities"]={"abandon_cycle_v1":True}
            workflow._persist(original)
            cycle=fixture.cycle(spec)
            canvas_before=spec.branch_canvas.read_bytes()
            requested=workflow.request_control('abandon_cycle',reason='human change of scientific direction')
            self.assertEqual(requested['status'],'interrupted')
            self.assertEqual(requested['phase'],'abandon_requested')
            self.assertFalse(requested['resumable'])
            pending=json.loads((cycle/'recovery_control.json').read_text())
            self.assertEqual(pending['action'],'abandon_cycle')
            saved=json.loads((cycle/'research_state.json').read_text())
            with self.assertRaises(RecoveryStop):
                workflow._check_recovery_control(saved)
            finalized=json.loads((cycle/'research_state.json').read_text())
            self.assertEqual(finalized['status'],'cancelled')
            self.assertEqual(finalized['phase'],'abandoned')
            self.assertEqual(finalized['invocation_stop_reason'],'human_abandoned_cycle')
            self.assertFalse(finalized['resumable'])
            self.assertFalse(finalized['execution_active'])
            self.assertIsNone(finalized.get('abandoned_cycle',{}).get('scientific_verdict'))
            self.assertTrue(finalized['abandoned_cycle']['requires_new_cycle'])
            self.assertFalse((cycle/'recovery_control.json').exists())
            self.assertEqual(spec.branch_canvas.read_bytes(),canvas_before)
            with self.assertRaisesRegex(ValueError,'not resumable'):
                workflow.resume(str(cycle.name))
            with self.assertRaisesRegex(ValueError,'explicit new Run'):
                workflow.resume(str(spec.master_id))
            self.assertFalse(workflow._can_continue_cycle_budget(finalized))

    def test_legacy_running_cycle_without_runtime_marker_fails_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture=test_core.TheoryMasterTest()
            spec,backend=fixture.make(Path(directory),reworks=0)
            workflow=Workflow(spec,backend)
            workflow.initialize()
            with self.assertRaisesRegex(ValueError,'not loaded'):
                workflow.request_control('abandon_cycle')
            self.assertFalse((fixture.cycle(spec)/'recovery_control.json').exists())

    def test_end_current_cycle_keeps_pause_interrupt_semantics_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture=test_core.TheoryMasterTest()
            spec,backend=fixture.make(Path(directory),reworks=0)
            workflow=Workflow(spec,backend)
            workflow.initialize()
            state=workflow.request_control('pause')
            self.assertEqual(state['status'],'paused')
            self.assertTrue(state['resumable'])
            self.assertEqual(json.loads((fixture.cycle(spec)/'recovery_control.json').read_text())['action'],'pause')
