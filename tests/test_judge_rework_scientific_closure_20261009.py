from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from theory_master.workflow import Workflow
from theory_master.specification import list_cycle_dirs
from theory_master.stage_registry import stage_card_text
from theory_master.observability.incident_registry import LOG_NAME


class JudgeReworkScientificClosureTest(unittest.TestCase):
    def test_frozen_test_and_objection_semantics(self):
        judge = stage_card_text("judge", "terminal_assessment")
        investigator = stage_card_text("investigator", "investigate")
        rework = stage_card_text("investigator", "judge_rework")
        self.assertIn("several linked prerequisites", judge)
        self.assertIn("terminal open", judge)
        self.assertIn("has actually been executed", judge)
        self.assertIn("whole frozen test", investigator)
        self.assertIn("support, falsification or a genuinely established non-identifiability or inconclusive result", investigator)
        self.assertIn("actual requested_resolution", rework)
        self.assertIn("exact Judge request", rework)
        self.assertNotIn("findings[].limitations", rework)

    def test_resume_same_cycle_does_not_replay_exhausted_rework(self):
        from test_core import TheoryMasterTest  # unittest discovers tests/ as an import root
        with tempfile.TemporaryDirectory() as temp:
            spec, backend = TheoryMasterTest().make(Path(temp), reworks=9)
            spec = replace(spec, max_judge_reworks_per_cycle=3)
            first = Workflow(spec, backend).run()
            directory = Path(first["cycle_dir"])
            self.assertEqual(first["status"], "needs_attention")
            self.assertEqual(first["attention"]["code"], "judge_rework_budget_exhausted")
            self.assertIsNone(first["scientific_verdict"])
            original_threads = dict(first["threads"])
            original_attempt = first["investigation_attempt"]
            self.assertTrue((directory / LOG_NAME).exists())
            records = [json.loads(row) for row in (directory / LOG_NAME).read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(row.get("code") == "judge_rework_budget_exhausted" for row in records))
            canvas_before = spec.branch_canvas.read_bytes()
            second = Workflow(spec, backend).resume(directory.name)
            self.assertEqual(second["status"], "needs_attention")
            self.assertEqual(second["cycle_dir"], first["cycle_dir"])
            self.assertEqual(second["threads"], original_threads)
            self.assertEqual(second["investigation_attempt"], original_attempt)
            self.assertEqual(second["judge_rework_count"], 3)
            self.assertEqual(len(list_cycle_dirs(spec.research_root)), 1)
            self.assertEqual(spec.branch_canvas.read_bytes(), canvas_before)
            self.assertIsNone(second["scientific_verdict"])


if __name__ == "__main__":
    unittest.main()
