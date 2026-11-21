"""Offline input-design counterexamples and real qualification prompt assembly.

No model is asked to classify these cases. Arithmetic checks demonstrate why a
catalog cannot establish eligibility; assembly checks protect the instructions
and contract boundary. Semantic compliance requires later independent review.
"""
from __future__ import annotations

import copy
import unittest

from theory_master.contracts import role_contract
from theory_master.prompt_stages import build_initial_prompt, build_continuation_prompt, stage_context
from theory_master.stage_registry import stage_card_text, procedure_card_text


GRAPH = {"schema": "theorymaster/test-agent-graph/v1", "hypotheses": [],
         "current_frontiers": [], "lineage": [], "external_sources": []}


class InputQualificationEvidence(unittest.TestCase):
    def prompts(self, role, stage, values=None):
        values = dict(values or {})
        values.setdefault("agent_graph", GRAPH)
        contract = role_contract(role, phase=stage)
        for continuation in (False, True):
            context = stage_context(role, stage, values, continuation=continuation)
            kwargs = dict(role=role, stage=stage, contract_ref=contract.ref,
                          contract_schema=contract.prompt_projection(values), context=context)
            yield (build_continuation_prompt(**kwargs) if continuation else
                   build_initial_prompt(prefix="offline", write_rule="Read only", **kwargs))

    def test_catalog_size_does_not_establish_joint_coverage_or_conditional_contrast(self):
        # Equal-sized catalogs; only the latter admits the stipulated
        # within-control contrast with both required measurements present.
        invalid = [("a", 0, 10, {1, 2}), ("b", 0, 10, {1, 2}),
                   ("c", 1, 20, {1}), ("d", 1, 20, {1, 2})]
        valid = [("a", 0, 10, {1, 2}), ("b", 0, 11, {1, 2}),
                 ("c", 1, 20, {1, 2}), ("d", 1, 21, {1, 2})]
        self.assertEqual(len(invalid), len(valid))
        for records, expected in ((invalid, 0), (valid, 2)):
            contrasts = [(a[0], b[0]) for i, a in enumerate(records) for b in records[i+1:]
                         if a[1] == b[1] and a[2] != b[2] and {1, 2} <= a[3] & b[3]]
            self.assertEqual(len(contrasts), expected)
        # The comparison design is task-specific; preserve it in either prompt mode.
        rule = "Compare two distinct eligible records within the declared control."
        for role, stage in (("research_lead", "frontier_proposal"), ("challenger", "candidate_review"),
                            ("challenger", "repair_verification")):
            for prompt in self.prompts(role, stage, {"research_contract": rule}):
                self.assertIn(rule, prompt)
                self.assertNotIn("joint/conditional support", prompt)

    def test_missing_cheap_check_is_not_silently_deferred_at_any_review(self):
        blocker = {"id": "missing-input", "problem": "Necessary source file not located.",
                   "required_change": "Identify an accessible source of those inputs."}
        for prompt in self.prompts("challenger", "repair_verification", {"original_review": {"blocking_issues": [blocker]}}):
            self.assertIn(blocker["problem"], prompt)
            self.assertIn(blocker["required_change"], prompt)
            self.assertIn("unresolved", prompt)
        for prompt in self.prompts("challenger", "candidate_review"):
            self.assertIn("verify cheap necessary input/metadata conditions", prompt)

    def test_scientific_unknowns_remain_research_and_matching_is_conditional(self):
        # An informative high-risk test is not an advance-proof demand.
        claim = {"hypothesis": "Every admitted sequence terminates.", "scope": "Finite rewrite systems.",
                 "decisive_test": "Produce a derivation or a counterexample."}
        for prompt in self.prompts("challenger", "candidate_review", {"candidate": [claim]}):
            for value in claim.values():
                self.assertIn(value, prompt)
            self.assertNotIn("joint/conditional support", prompt)
            self.assertIn("Do not demand optional stronger claims", prompt)

    def test_history_distinctions_remain_in_actual_assembled_context(self):
        # These are design fixtures, not predictions of model judgments. All
        # cases reach the reviewer unchanged and retain relevant generic rules.
        cases = (
            ("paraphrased duplicate", "Repeat the adjudicated model under a renamed parameter"),
            ("new scope", "Test a local claim on a distinct admissible scope with new uncertainty"),
            ("mechanism repair", "Test a specified additional mechanism against the earlier baseline"),
            ("new scientific work", "Derive a previously unknown relation under the stated conditions"),
        )
        for label, claim in cases:
            candidate = {"id": "cand-offline", "hypothesis": claim, "scope": "bounded",
                         "decisive_test": "Use existing inputs; test the prespecified distinction",
                         "why_now": label, "goal_advance": "Resolve an unmet scoped question"}
            for prompt in self.prompts("challenger", "candidate_review", {"candidate": [candidate]}):
                with self.subTest(case=label):
                    self.assertIn(claim, prompt)
                    self.assertIn(candidate["decisive_test"], prompt)
                    self.assertIn(label, prompt)

    def test_existing_contracts_and_stage_separation_remain(self):
        review = role_contract("challenger", phase="candidate_review")
        output = {"reviews": [{"candidate_id": "cand-offline", "verdict": "qualified",
                               "reason": "Observed input-side qualification from fixture; science remains open"}]}
        original = copy.deepcopy(output)
        review.validate(output, {"candidates": [{"id": "cand-offline"}]})
        self.assertEqual(output, original)
        self.assertEqual(review.version, 6)
        for role, stage in (("research_lead", "frontier_proposal"),
                            ("challenger", "candidate_review")):
            text = stage_card_text(role, stage) + procedure_card_text(role, stage)
            # Stage definitions are delivered once; absolute character caps
            # do not establish scientific quality or correct role separation.
            for prompt in self.prompts(role, stage):
                self.assertEqual(prompt.count(text), 1)
            self.assertNotIn("candidate_id", text)
            self.assertNotIn("blocking_issues", text)
            for project_detail in ("C_dd", "phi2", "52 graphs", "H-0197", "rubric", "score"):
                self.assertNotIn(project_detail, text)


if __name__ == "__main__":
    unittest.main()
