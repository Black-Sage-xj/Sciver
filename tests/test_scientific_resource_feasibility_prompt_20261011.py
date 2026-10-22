"""Regression coverage of resource-aware scientific plan and native Goal prompts.

This checks the real production assembly and capabilities, not whether a later
scientific agent will reason well or whether the Cycle55 hypothesis is solved.
"""
from __future__ import annotations

import unittest

from theory_master.contracts import role_contract
from theory_master.native_goal import investigator_goal
from theory_master.prompt_stages import build_initial_prompt, build_continuation_prompt, stage_context
from theory_master.skills import role_skills, skills_root
from theory_master.stage_registry import stage_card_text


LOCK = {
    "hypothesis": "Fixed scientific hypothesis",
    "scope": "Fixed estimand, admissible sources, and independent scoring",
    "decisive_test": "Estimate the prespecified effect before evaluating claims",
}


class ResourceFeasibilityPromptTests(unittest.TestCase):
    def prompts(self, role, stage, context=None):
        common = dict(role=role, stage=stage, contract_ref="test-ref",
                      contract_schema='{"result":"text"}', context=context or {})
        return (
            build_initial_prompt(prefix="test", write_rule="No writes.", **common),
            build_continuation_prompt(**common),
        )

    def test_native_investigation_goal_abandons_method_not_frozen_science(self):
        for stage in ("investigate", "integrate_literature", "judge_rework"):
            with self.subTest(stage=stage):
                ctx = {"frozen_objective": LOCK, "judge_objections": [
                    {"requested_resolution": "Answer the exact Judge objection"}
                ]}
                goal = investigator_goal(stage, ctx, layout="v2")
                for phrase in ("Estimate feasibility and scientific value",
                               "abandon unaffordable METHODS",
                               "Worst-case sample bounds",
                               "bounded exploration",
                               "Never self-terminate or request native blocked",
                               "actual Investigator token budget boundary",
                               "no proof of mathematical impossibility"):
                    self.assertIn(phrase, goal)
                self.assertIn("FROZEN SCIENTIFIC OBJECTIVE (immutable)", goal)
                self.assertIn(LOCK["decisive_test"], goal)
                self.assertIn("silently weaken the frozen estimand", goal)
                self.assertIn("Native Goal completion does not", goal)
                self.assertNotIn("381", goal)
                self.assertLessEqual(len(goal), 4000)

    def test_native_goal_v1_compatibility_and_realistic_length(self):
        old = investigator_goal("investigate", {"frozen_objective": LOCK}, layout="v1")
        self.assertIn("Only rigorous impossibility", old)
        self.assertNotIn("Abandon a clearly unaffordable METHOD", old)
        objective = {"hypothesis": "H" * 300, "scope": "S" * 350,
                     "decisive_test": "D" * 1000}
        assembled = investigator_goal("investigate", {"frozen_objective": objective}, layout="v2")
        self.assertLessEqual(len(assembled), 4000)
        self.assertIn("D" * 1000, assembled)
        # Operator guidance is still a separate input and not new evidence.
        guided = investigator_goal("investigate", {
            "frozen_objective": LOCK, "operator_guidance": {"text": "Try a smaller route"},
        }, layout="v2")
        self.assertIn("HUMAN EXECUTION DIRECTION", guided)
        self.assertIn("Never self-terminate or request native blocked", guided)

    def test_lead_design_can_trade_unneeded_global_precision_for_local_value(self):
        for prompt in self.prompts("research_lead", "frontier_proposal",
                                   {"goal": "Discover shell response", "final_gate": "Predict shells"}):
            for phrase in ("A matching local result may advance the Cycle",
                           "high-risk or otherwise credible work remains eligible",
                           "Indispensable premises need a credible in-scope route"):
                self.assertIn(phrase, prompt)
            self.assertIn("add discriminating information beyond adjudicated outcomes", prompt)
            self.assertIn("Match uncertainty and proof precision to the claimed inference", prompt)
            self.assertNotIn("381", prompt)

    def test_challenger_distinguishes_scientific_design_failure_from_unknown_work(self):
        for prompt in self.prompts("challenger", "candidate_review",
                                   {"candidate": [{"hypothesis": "H", "scope": "S",
                                                   "decisive_test": "a bounded local test"}]}):
            for phrase in ("essential route is demonstrably unavailable",
                           "decisive outcome is unreachable", "credible in-scope route", "Investigation work",
                           "Do not demand optional stronger claims"):
                self.assertIn(phrase, prompt)
            self.assertIn("Qualify when the design", prompt)
            self.assertIn("Revise only when", prompt)
            self.assertIn("Reject only when", prompt)
            self.assertIn("eventual open/negative result as a design failure", prompt)

    def test_investigator_stage_preserves_no_premature_findings_and_native_blocked(self):
        card = stage_card_text("investigator", "investigate")
        for phrase in ("value and resources before costly work", "abandon infeasible METHODS",
                       "bounded in-scope alternatives", "frozen scientific estimand",
                       "Never request native blocked for scientific difficulty",
                       "without fabricating completion"):
            self.assertIn(phrase, card)
        self.assertIn("Return complete findings[] ONLY", card)
        self.assertIn("frozen decisive test", card)
        self.assertIn("Do not hand off after a partial theorem", card)
        self.assertIn("Do not scale a worst-case bound", card)
        self.assertIn("requested_resolution", stage_card_text("investigator", "judge_rework"))
        self.assertIn("abandon a disproportionate method", stage_card_text("investigator", "judge_rework"))
        self.assertIn("without requesting native blocked", stage_card_text("investigator", "judge_rework"))
        self.assertNotIn("native Goal blocked status", stage_card_text("judge", "terminal_assessment"))

    def test_skills_extend_existing_cost_gate_without_setting_global_limits(self):
        numeric = (skills_root() / "numerical-investigation" / "SKILL.md").read_text(encoding="utf-8")
        dispatch = (skills_root() / "compute-dispatch" / "SKILL.md").read_text(encoding="utf-8")
        for text in (numeric, dispatch):
            self.assertTrue("最坏情况" in text or "保守不等式" in text)
            self.assertIn("停止", text)
            self.assertTrue("不得主动" in text or "不得自行" in text)
            self.assertIn("预算", text)
            self.assertIn("冻结", text)
        self.assertIn("独立计算单元达到 10 个", numeric)
        self.assertIn("科学收益与计算可行性门槛", dispatch)
        self.assertIn("不设一刀切", dispatch)
        self.assertIn("并行策略", dispatch)
        refs = {skill.name for skill in role_skills("investigator")}
        self.assertIn("numerical-investigation", refs)
        self.assertIn("compute-dispatch", refs)

    def test_contract_does_not_gain_manual_workflow_or_budget_fields(self):
        c_lead = role_contract("research_lead", phase="frontier_proposal")
        c_challenger = role_contract("challenger", phase="candidate_review")
        c_investigator = role_contract("investigator", phase="investigate")
        self.assertIn("candidates", c_lead.prompt_schema)
        self.assertIn("reviews", c_challenger.prompt_schema)
        self.assertIn("findings", c_investigator.prompt_schema)
        for contract in (c_lead, c_challenger, c_investigator):
            self.assertNotIn("resource_approval", contract.prompt_schema)
            self.assertNotIn("need_attention", contract.prompt_schema)
            self.assertNotIn("scientific_abandonment", contract.prompt_schema)
        self.assertIn("inconclusive", stage_card_text("challenger", "candidate_review"))
        self.assertIn("cannot_repair", stage_card_text("research_lead", "selection_repair"))
        self.assertNotIn("Resource-proportional design",
                         stage_card_text("research_lead", "selection_repair"))
        candidate = {
            "hypothesis": "H", "scope": "S", "why_now": "Goal",
            "goal_advance": "Progress", "decisive_test": "Test",
        }
        self.assertEqual(
            stage_context("challenger", "candidate_review", {
                "candidate": [candidate],
                "agent_graph": {"schema": "theorymaster/agent-science-graph/v1", "hypotheses": [],
                                "current_frontiers": [], "lineage": [], "focus_details": [],
                                "external_sources": [], "external_references": [], "source_relations": []},
            })["candidate"][0],
            candidate,
        )


if __name__ == "__main__":
    unittest.main()
