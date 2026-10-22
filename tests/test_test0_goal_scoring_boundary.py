"""Offline regression for Test0's internal/external verdict boundary (no LLM)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from theory_master.regression_cases import materialize_case, resolve_case


class Test0BoundaryTest(unittest.TestCase):
    def setUp(self):
        self.case = resolve_case("Test0")
        self.script = self.case.root / "evaluator" / "score_trajectory.py"

    def _run(self, scientific: dict, verdict: str = "passed"):
        with tempfile.TemporaryDirectory() as td:
            result = Path(td) / "investigation_result.json"
            gate = Path(td) / "goal_completion_out.json"
            result.write_text(json.dumps(scientific), encoding="utf-8")
            gate.write_text(json.dumps({"verdict": verdict, "rationale": "offline fixture"}), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(self.script), "--scientific-result", str(result),
                 "--goal-completion", str(gate)],
                capture_output=True, text=True, check=False,
            )
            return proc.returncode, json.loads(proc.stdout)

    @staticmethod
    def _finding(estimate: float) -> dict:
        return {"findings": [{
            "method": "increment-covariance model fitted only on train",
            "statement": "Independent validation on held-out trajectories is consistent.",
            "result": {"train_D": estimate, "validation_D": 0.36},
        }]}

    def test_materialized_gate_is_internal_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = materialize_case("test0", Path(td) / "new_task")
            plan = (root / "research_plan.md").read_text(encoding="utf-8")
            self.assertIn("Goal Completion Judge", plan)
            self.assertIn("150 train trajectories", plan)
            self.assertIn("50 held-out validation trajectories", plan)
            self.assertIn("external post-run numerical score", plan)
            self.assertNotIn("The submitted D_estimate is successful if the relative error", plan)
            self.assertFalse((root / "evaluator").exists())
            self.assertFalse((root / "expected.json").exists())
            self.assertFalse((root / "PROTOCOL.md").exists())

    def test_both_pass_is_reported_independently(self):
        code, data = self._run(self._finding(0.36832477647871803))
        self.assertEqual(code, 0, data)
        self.assertEqual(data["classification"], "both_pass")
        self.assertTrue(data["external_oracle"]["passed"])
        self.assertEqual(data["internal_goal_completion"]["verdict"], "passed")
        self.assertNotIn("diffusion_coefficient", json.dumps(data))
        self.assertNotIn("hidden_truth", json.dumps(data))

    def test_pre_fix_goal_failure_does_not_overwrite_oracle_pass(self):
        code, data = self._run(self._finding(0.36832477647871803), verdict="not_passed")
        self.assertEqual(code, 1)
        self.assertEqual(data["classification"], "oracle_pass_goal_not_passed")
        self.assertTrue(data["external_oracle"]["passed"])
        self.assertEqual(data["internal_goal_completion"]["verdict"], "not_passed")

    def test_bad_science_is_not_excused_by_goal_pass(self):
        code, data = self._run(self._finding(1.5))
        self.assertEqual(code, 1)
        self.assertEqual(data["classification"], "goal_pass_oracle_fail")
        self.assertFalse(data["external_oracle"]["passed"])

    def test_missing_or_ambiguous_estimate_fails_closed(self):
        for findings in ([], [self._finding(.36)["findings"][0]] * 2):
            with self.subTest(findings=len(findings)):
                code, data = self._run({"findings": findings})
                self.assertEqual(code, 2)
                self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
