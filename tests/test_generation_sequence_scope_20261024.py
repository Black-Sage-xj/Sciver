"""Offline prompt delivery and authority checks; no model verdict is simulated."""
import json
import unittest

from theory_master.contract_stages import role_contract
from theory_master.prompt_stages import (
    build_continuation_prompt, build_initial_prompt, stage_context,
)
from theory_master.stage_registry import stage_definition_text


STAGES = (
    ("research_lead", "frontier_proposal"),
    ("research_lead", "candidate_selection"),
    ("research_lead", "selection_repair"),
    ("challenger", "candidate_review"),
    ("challenger", "repair_verification"),
)
GRAPH = {"hypotheses": [], "current_frontiers": [], "lineage": []}


class GenerationSequenceScope(unittest.TestCase):
    def test_scope_obligations_follow_task_requirements_in_both_thread_modes(self):
        # These assert delivered instructions, not that a model correctly applies them.
        requirements = {
            ("research_lead", "frontier_proposal"): (
                "only when the task explicitly requires it", "terminology alone establishes neither restriction",
                "If decisive_test depends on comparison", "Do not impose matching"),
            ("challenger", "candidate_review"): (
                "only when those requirements establish them", "terminology alone does not",
                "only to actual empirical comparisons", "purely theoretical or existence proofs"),
            ("research_lead", "candidate_selection"): (
                "already-qualified set", "Do not reopen qualification", "select one unchanged"),
            ("research_lead", "selection_repair"): (
                "frozen Challenger blocker ledger", "COMPLETE revised decisive_test",
                "do not add sequence or matching restrictions inferred only from terminology"),
            ("challenger", "repair_verification"): (
                "fixed checklist", "Do not renew the full novelty or Goal-priority review",
                "only when established by the actual Goal", "terminology alone adds no obligation"),
        }
        goals = (
            "Derive the complete signed shell-energy law and its far-field limit.",
            "Prove a matrix inequality over the stated deterministic class; no empirical comparison.",
        )
        for role, stage in STAGES:
            contract = role_contract(role, phase=stage)
            for goal in goals:
                for continuation in (False, True):
                    with self.subTest(role=role, stage=stage, goal=goal, continuation=continuation):
                        candidate = {"id": "scope-case", "hypothesis": "A specific claim.",
                                     "scope": goal, "decisive_test": "Resolve exactly the stated scope."}
                        context = stage_context(role, stage, {
                            "goal": goal, "agent_graph": GRAPH, "candidates": [candidate],
                            "candidate_reviews": [{"candidate_id": "scope-case", "blocking_issues": [
                                {"id": "definition", "problem": "Define the observable", "required_change": "Clarify it."}
                            ]}],
                        }, continuation=continuation)
                        args = dict(role=role, stage=stage, contract_ref=contract.ref,
                                    contract_schema=contract.prompt_projection(context), context=context)
                        prompt = (build_continuation_prompt(**args) if continuation else
                                  build_initial_prompt(prefix="offline", write_rule="read only", **args))
                        if stage in ("candidate_selection", "selection_repair"):
                            # Same-thread handoff retains only IDs/blockers; a fresh
                            # thread receives the original frozen scientific fields.
                            field = "qualified_candidates" if stage == "candidate_selection" else "repair_options"
                            self.assertIn("scope-case", prompt)
                            if continuation:
                                self.assertNotIn("scope", context[field][0])
                            else:
                                self.assertEqual(context[field][0]["scope"], goal)
                                self.assertEqual(context[field][0]["decisive_test"], candidate["decisive_test"])
                            if stage == "selection_repair":
                                self.assertEqual(context[field][0]["blockers"][0]["id"], "definition")
                        else:
                            self.assertIn(goal, prompt)
                        for rule in requirements[(role, stage)]:
                            self.assertIn(rule, prompt)
                        self.assertNotIn("even as a local mechanism or prerequisite", prompt)
                        self.assertNotIn("Selected indices may serve only as internal diagnostics", prompt)
                        if stage == "candidate_selection":
                            self.assertNotIn("full-sequence", stage_definition_text(role, stage))
                            self.assertNotIn("joint/conditional support", stage_definition_text(role, stage))

    def test_positive_and_negative_science_are_delivered_exactly_for_independent_review(self):
        # These are review inputs, not deterministic classifications or mock LLM judgments.
        claims = (
            ("Predict only shells 6 and 8.", "A finite window is the entire deliverable."),
            ("Explain only the first-shell mechanism.", "Call this a prerequisite for the full curve."),
            ("Derive a common law for every shell.", "Use selected shells as diagnostics and prove the required limit."),
            ("Refute the proposed common law.", "Construct a counterexample to its all-shell validity."),
            ("Prove a matrix inequality.", "A bounded matrix family, with no generation-resolved Goal."),
        )
        for claim, test in claims:
            candidate = {"id": "offline", "hypothesis": claim, "scope": "Explicit scientific domain.",
                         "decisive_test": test, "why_now": "Historical scientific gap.",
                         "goal_advance": "Address the Goal."}
            for continuation in (False, True):
                ctx = stage_context("challenger", "candidate_review", {
                    "candidate": [candidate], "agent_graph": GRAPH,
                }, continuation=continuation)
                objective = ctx["candidate"][0]["frozen_objective"]
                self.assertEqual(objective, {key: candidate[key] for key in
                                           ("hypothesis", "scope", "decisive_test")})
                self.assertNotIn("generation_scope", json.dumps(ctx))


if __name__ == "__main__":
    unittest.main()
