"""Scientific canary *reference* checks, not language-model behavior tests.

Do not describe these as a passed live Challenger/Lead/Judge test. Real agent
behavior requires fresh blinded trajectories plus independent scientific review.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path


CANARY_FILE = Path(__file__).resolve().parent / "fixtures" / "scientific_discrimination_canaries.json"


def reference_outcome(case: dict) -> str:
    kind = case["kind"]
    if kind == "exact_sign_test":
        n = case["n"]
        tails = case["tails"]
        alpha = case["alpha"]
        if not (isinstance(n, int) and not isinstance(n, bool) and n > 0):
            raise ValueError("positive integer n is required")
        if tails not in (1, 2) or not (0 < alpha < 1):
            raise ValueError("unsupported sign test or alpha")
        # The most extreme outcome is all successes or all failures.
        minimum_p = tails / (2 ** n)
        return "reachable" if minimum_p <= alpha else "unreachable"
    if kind == "bounded_numerical_error":
        estimate = float(case["estimate"])
        bound = float(case["error_bound"])
        if not (bound >= 0 and abs(estimate) < float("inf") and bound < float("inf")):
            raise ValueError("invalid finite error estimate/bound")
        return ("positive_sign_resolved" if estimate - bound > 0
                else "insufficient_precision")
    if kind == "analytic_counterexample":
        return "statistical_power_not_applicable"
    raise ValueError(f"unsupported canary kind: {kind}")


class ScientificDiscriminationCanaryReferenceTest(unittest.TestCase):
    def test_reference_cases_cover_attainable_unattainable_and_not_applicable(self):
        data = json.loads(CANARY_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "theory-master/scientific-discrimination-canary/v1")
        cases = data["cases"]
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        self.assertGreaterEqual(len(cases), 5)
        self.assertIn("MUST NOT be passed", data["scope"])
        outcomes = {case["expected"] for case in cases}
        self.assertEqual(outcomes, {
            "unreachable", "reachable", "insufficient_precision",
            "positive_sign_resolved", "statistical_power_not_applicable",
        })
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["scenario"])
                self.assertTrue(case["review_focus"])
                self.assertEqual(reference_outcome(case), case["expected"])
        self.assertIn("never proves real Agent science", data["live_audit_requirement"])

    def test_edge_cases_avoid_universal_scientific_thresholds(self):
        # Significance reachability is conditional on n/test/tails/alpha.
        self.assertEqual(reference_outcome({
            "kind": "exact_sign_test", "n": 3, "tails": 2, "alpha": 0.025
        }), "unreachable")
        self.assertEqual(reference_outcome({
            "kind": "exact_sign_test", "n": 8, "tails": 2, "alpha": 0.025
        }), "reachable")
        self.assertEqual(reference_outcome({
            "kind": "exact_sign_test", "n": 6, "tails": 1, "alpha": 0.025
        }), "reachable")
        # A method that is invalid for one task need not be demanded in another.
        self.assertEqual(reference_outcome({
            "kind": "analytic_counterexample"
        }), "statistical_power_not_applicable")

    def test_rejects_malformed_statistical_reference(self):
        for payload in (
            {"kind": "exact_sign_test", "n": 0, "tails": 2, "alpha": .025},
            {"kind": "exact_sign_test", "n": 3, "tails": 3, "alpha": .025},
            {"kind": "bounded_numerical_error", "estimate": 1, "error_bound": -1},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    reference_outcome(payload)


if __name__ == "__main__":
    unittest.main()
