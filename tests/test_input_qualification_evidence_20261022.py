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
        rules = {
            ("research_lead", "frontier_proposal"): (
                "joint/conditional support", "catalog counts do not establish it",
                "cite sources and observed results", "bounded and input-only",
                "do not fit responses, select samples from outcomes",
            ),
            ("challenger", "candidate_review"): (
                "Joint/conditional support checks apply only", "Catalog counts or a check-later plan",
                "cite the source and observed result", "bounded and input-only",
                "no response fitting, outcome-based sample selection",
            ),
            ("challenger", "repair_verification"): (
                "joint/conditional support only when established", "comparison set only where required",
                "cite source/observed result", "bounded cheap input-side checks",
                "no response fitting, outcome-based sample selection",
            ),
        }
        for (role, stage), instructions in rules.items():
            for prompt in self.prompts(role, stage):
                with self.subTest(role=role, stage=stage):
                    for instruction in instructions:
                        self.assertIn(instruction, prompt)

    def test_missing_cheap_check_is_not_silently_deferred_at_any_review(self):
        expected = {
            "candidate_review": ("verify cheap necessary input/metadata conditions", "check-later plan do not establish necessary eligibility"),
            "repair_verification": ("omitted cheap necessary checks remain unresolved", "existing summaries or bounded cheap input-side checks"),
        }
        for stage, rules in expected.items():
            for prompt in self.prompts("challenger", stage):
                for rule in rules:
                    self.assertIn(rule, prompt)
                self.assertIn("Investigator", prompt)
        for prompt in self.prompts("research_lead", "frontier_proposal"):
            self.assertIn("expose missing indispensable checks", prompt)
            self.assertIn("unknown derivations and the decisive experiment remain Investigator work", prompt)

    def test_scientific_unknowns_remain_research_and_matching_is_conditional(self):
        # An informative high-risk test is not an advance-proof demand.
        for prompt in self.prompts("challenger", "candidate_review"):
            for rule in ("do not impose matching on a design that does not require it",
                         "credible in-scope route and meaningful test remain",
                         "Do not demand optional stronger claims", "uncertain effect",
                         "eventual negative/open result is not itself a blocker",
                         "purely theoretical or existence proofs need no data matching or statistical sample requirement"):
                self.assertIn(rule, prompt)
        for prompt in self.prompts("challenger", "repair_verification"):
            self.assertIn("unfinished science with a credible route is Investigator work", prompt)
            self.assertIn("Do not renew the full novelty or Goal-priority review", prompt)
            self.assertIn("no response fitting, outcome-based sample selection or full experiment", prompt)

    def test_history_distinctions_remain_in_actual_assembled_context(self):
        # These are design fixtures, not predictions of model judgments. All
        # cases reach the reviewer unchanged and retain relevant generic rules.
        cases = (
            ("paraphrased duplicate", "Repeat the adjudicated model under a renamed parameter",
             "Compare its new information with the closest prior claim"),
            ("new scope", "Test a local claim on a distinct admissible scope with new uncertainty",
             "local result bears on a broader claim only within its actual logical implication"),
            ("mechanism repair", "Old model failed; test a specified additional mechanism against that baseline",
             "relevant adverse results from primary reports"),
            ("new scientific work", "A credible in-scope derivation remains unknown and is the scientific test",
             "credible in-scope route and meaningful test remain"),
        )
        for label, claim, instruction in cases:
            candidate = {"id": "cand-offline", "hypothesis": claim, "scope": "bounded",
                         "decisive_test": "Use existing inputs; test the prespecified distinction",
                         "why_now": label, "goal_advance": "Resolve an unmet scoped question"}
            for prompt in self.prompts("challenger", "candidate_review", {"candidate": [candidate]}):
                with self.subTest(case=label):
                    self.assertIn(claim, prompt)
                    self.assertIn(instruction, prompt)

    def test_existing_contracts_and_stage_separation_remain(self):
        review = role_contract("challenger", phase="candidate_review")
        output = {"reviews": [{"candidate_id": "cand-offline", "verdict": "qualified",
                               "reason": "Observed input-side qualification from fixture; science remains open"}]}
        original = copy.deepcopy(output)
        review.validate(output, {"candidates": [{"id": "cand-offline"}]})
        self.assertEqual(output, original)
        self.assertEqual(review.version, 5)
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
