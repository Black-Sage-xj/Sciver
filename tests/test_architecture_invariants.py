from __future__ import annotations

import unittest

from theory_master.abc_pipeline import annotate_specialist


class ArchitectureInvariantTest(unittest.TestCase):
    def test_specialist_maps_each_finding_to_success_and_failure_separately(self):
        class Decisions:
            name = "test"

            def decide(self, *, task, state, questions):
                answers = {}
                for question in questions:
                    key = question["id"]
                    answers[key] = (
                        "related" if key in {"finding:0/criterion:M-S1", "finding:1/criterion:M-F1"}
                        else "unrelated" if "/criterion:" in key
                        else "supports" if key.endswith("/relation")
                        else "none" if key == "remaining_gap"
                        else "addressed"
                    )
                return {"answers": answers}

        artifact = {"findings": [
            {"statement": "support", "scope": "", "limitations": "", "artifacts": []},
            {"statement": "counterexample", "scope": "", "limitations": "", "artifacts": []},
        ]}
        criteria = {"success": [{"id": "M-S1", "criterion": "support"}],
                    "failure": [{"id": "M-F1", "criterion": "counterexample"}]}
        closed, _ = annotate_specialist(artifact, {"cycle_progress_criteria": criteria}, Decisions())
        self.assertEqual([item["criterion_ids"] for item in closed["evidence"]], [["M-S1"], ["M-F1"]])
        self.assertTrue(all("verification" not in item for item in closed["evidence"]))


if __name__ == "__main__":
    unittest.main()
