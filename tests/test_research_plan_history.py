"""Regression: SDK scientific-model writes are versioned exactly as Web saves."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

from theory_master.embedded_state import replace_summary
from theory_master.research_plan_history import capture_current, content, plan_lock, versions, project, record


class PlanHistorySDKTest(unittest.TestCase):
    def test_sdk_save_and_external_observation(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            (home / ".theory-master").mkdir()
            plan = home / "research_plan.md"
            original = (
                "# Goal\n\nG\n\n# Research Contract\n\nC\n\n"
                "# Final Gate\n\nF\n\n# Required Context\n\nR\n\n"
                "# Scientific Model\n\nold\n"
            )
            plan.write_text(original, encoding="utf-8")
            replace_summary(plan, "new", reason="Cycle 1 verdict reconciled")
            history = versions(plan)
            self.assertEqual(len(history), 1)
            self.assertEqual(content(plan, 1), project(original))
            self.assertEqual(versions(plan, "model")[-1]["reason"], "Cycle 1 verdict reconciled")
            self.assertIn("new", content(plan, 2, "model"))
            replace_summary(plan, "new", reason="No material change")
            self.assertEqual(len(versions(plan)), 1)
            self.assertEqual(len(versions(plan, "model")), 2)
            plan.write_text(plan.read_text().replace("# Goal\n\nG", "# Goal\n\nG2"))
            with plan_lock(plan):
                capture_current(plan)
            self.assertEqual(len(versions(plan)), 2)
            self.assertEqual(len(versions(plan, "model")), 2)
            self.assertEqual(versions(plan)[-1]["source"], "external-observed")


if __name__ == "__main__":
    unittest.main()
