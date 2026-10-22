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
        for role, stage in (("research_lead", "frontier_proposal"),
                            ("challenger", "candidate_review"),
                            ("challenger", "repair_verification")):
            for prompt in self.prompts(role, stage):
                with self.subTest(role=role, stage=stage):
                    self.assertIn("input", prompt)
                    self.assertIn("after", prompt)
                    self.assertIn("controls", prompt)
                    self.assertIn("catalog count", prompt)
                    self.assertIn("observed result", prompt)
                    self.assertIn("source", prompt)
                    self.assertIn("bounded", prompt)
                    self.assertIn("response fitting", prompt)
                    self.assertIn("outcome-based sample selection", prompt)

    def test_missing_cheap_check_is_not_silently_deferred_at_any_review(self):
        for stage in ("candidate_review", "repair_verification"):
            for prompt in self.prompts("challenger", stage):
                self.assertIn("missing cheap necessary checks", prompt.lower())
                self.assertIn("Investigator", prompt)
                if stage == "candidate_review":
                    self.assertIn("An unresolved scientific prerequisite", prompt)
                    self.assertIn("Omitted cheap input checks remain qualification gaps", prompt)
                else:
                    self.assertIn("existing summaries or cheap bounded input-only checks", prompt)
        for prompt in self.prompts("research_lead", "frontier_proposal"):
            self.assertIn("expose the qualification gap", prompt)
            self.assertIn("new derivations and the full decisive test remain Investigator work", prompt)

    def test_scientific_unknowns_remain_research_and_matching_is_conditional(self):
        # An informative high-risk test is not an advance-proof demand.
        for prompt in self.prompts("challenger", "candidate_review"):
            for rule in ("do not impose matching when the science does not require it",
                         "treat it as Investigation work rather than a revise blocker",
                         "Do not demand optional stronger claims", "uncertain effect",
                         "negative results can be valuable", "New derivations remain Investigator work"):
                self.assertIn(rule, prompt)
        for prompt in self.prompts("challenger", "repair_verification"):
            self.assertIn("Investigator-solvable prerequisites", prompt)
            self.assertIn("or the full decisive experiment", prompt)

    def test_history_distinctions_remain_in_actual_assembled_context(self):
        # These are design fixtures, not predictions of model judgments. All
        # cases reach the reviewer unchanged and retain relevant generic rules.
        cases = (
            ("paraphrased duplicate", "Repeat the adjudicated model under a renamed parameter",
             "information beyond already terminal-adjudicated tests"),
            ("new scope", "Test a local claim on a distinct admissible scope with new uncertainty",
             "a necessary local prediction is acceptable"),
            ("mechanism repair", "Old model failed; test a specified additional mechanism against that baseline",
             "Compare the proposed scientific difference with that result and adverse evidence"),
            ("new scientific work", "A credible in-scope derivation remains unknown and is the scientific test",
             "treat it as Investigation work rather than a revise blocker"),
        )
        for label, claim, instruction in cases:
            candidate = {"id": "cand-offline", "hypothesis": claim, "scope": "bounded",
                         "decisive_test": "Use existing inputs; test the prespecified distinction",
                         "why_now": label, "goal_advance": "Resolve an unmet scoped question"}
            for prompt in self.prompts("challenger", "candidate_review", {"candidate": [candidate]}):
                with self.subTest(case=label):
                    self.assertIn(claim, prompt)
                    self.assertIn(instruction, prompt)

    def test_existing_contracts_and_stage_budgets_remain(self):
        review = role_contract("challenger", phase="candidate_review")
        output = {"reviews": [{"candidate_id": "cand-offline", "verdict": "qualified",
                               "reason": "Observed input-side qualification from fixture; science remains open"}]}
        original = copy.deepcopy(output)
        review.validate(output, {"candidates": [{"id": "cand-offline"}]})
        self.assertEqual(output, original)
        self.assertEqual(review.version, 5)
        for role, stage, limit in (("research_lead", "frontier_proposal", 6000),
                                   ("challenger", "candidate_review", 4500)):
            text = stage_card_text(role, stage) + procedure_card_text(role, stage)
            self.assertLess(len(text), limit)
            for project_detail in ("C_dd", "phi2", "52 graphs", "H-0197", "rubric", "score"):
                self.assertNotIn(project_detail, text)


if __name__ == "__main__":
    unittest.main()
