"""Offline Stage Card / contract regression anchored to archived Cycles 180–185.

These are design oracles, not LLM verdict predictors. No live model/Cycle is run.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from theory_master.contract_stages import role_contract
from theory_master.prompt_stages import build_initial_prompt, stage_context
from theory_master.stage_registry import stage_card_text, stage_definition_text


class ScientificQualification20261022(unittest.TestCase):
    def setUp(self):
        self.lead = stage_definition_text("research_lead", "frontier_proposal")
        self.challenger = stage_definition_text("challenger", "candidate_review")

    def test_lead_compares_scientific_value_not_gate_bookkeeping(self):
        # Stage owns scientific criteria; Contract owns the two named output fields.
        for phrase in (
            "consequential unmet Goal", "Final Gate", "linked supported Outcomes",
            "credible alternatives", "inconclusive results",
            "Scientific Model", "Indispensable premises need a credible in-scope route",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.lead)
        projection = role_contract("research_lead", phase="frontier_proposal").prompt_projection()
        self.assertIn("goal_advance", projection)
        self.assertIn("why_now", projection)

    def test_cycle_180_positive_local_mechanism_is_still_eligible(self):
        # H-0180: local graph-current prediction; terminal Judge supported.
        self.assertIn("claim can advance the Goal without completing it", self.lead)
        self.assertIn("a local result bears on a broader claim only within its actual logical implication", self.challenger)
        self.assertIn("necessary prerequisite before selection", self.challenger)
        self.assertIn("bounded mechanism, negative result or high-risk", self.lead)
        self.assertIn("necessary local prediction", self.lead)
        self.assertIn("eventual negative/open result is not itself a blocker", self.challenger)

    def test_cycle_182_finite_window_cannot_refute_infinite_bound(self):
        # Cycle 182 Challenger revised a second candidate's unjustified uniform-C test.
        # Generic proof precision and route feasibility replace H8's literal infinity example.
        self.assertIn("Match uncertainty and proof precision to the scientific distinction claimed", self.lead)
        self.assertIn("Indispensable premises need a credible in-scope route", self.lead)
        self.assertIn("Matching, contrary and inconclusive results", self.challenger)

    def test_cycle_183_negative_prediction_still_informs_goal(self):
        # H-0183: contrast of signed covariance law and marginal model.
        self.assertIn("Matching, contrary and inconclusive results each need an honest interpretation", self.lead)
        self.assertIn("negative", self.challenger)
        self.assertNotIn("all hypotheses must pass", self.challenger)

    def test_cycle_184_flags_compound_claims_not_expensive_research(self):
        # H-0184 combined full signed-curve prediction, channel allocation and far-field limit.
        self.assertIn("one coherent, independently adjudicable scientific claim", self.lead)
        self.assertIn("all visible independent blockers", self.challenger)
        self.assertIn("arbitrary sample sizes/cutoffs", self.challenger)

    def test_cycle_185_independent_limit_precondition_cannot_be_cosmetic_revise(self):
        # Candidate 185 was originally revised, then qualified after one test repair.
        # This is an oracle for the NEW policy, not a claim of a new model verdict.
        # Generalized: a crucial independent proof without a credible route is a design flaw.
        self.assertIn("Indispensable premises need a credible in-scope route", self.lead)
        self.assertIn("essential route is demonstrably unavailable", self.challenger)
        self.assertIn("unchanged hypothesis/scope/estimand", self.challenger)
        self.assertIn("Reject the current design, not its general topic", self.challenger)

    def test_contract_and_prompt_assembly_unchanged(self):
        lead_contract = role_contract("research_lead", phase="frontier_proposal")
        challenger_contract = role_contract("challenger", phase="candidate_review")
        self.assertEqual(challenger_contract.version, 5)
        self.assertEqual(
            set(lead_contract.prompt_schema["candidates"][0]) & {
                "hypothesis", "scope", "why_now", "goal_advance", "decisive_test"
            },
            {"hypothesis", "scope", "why_now", "goal_advance", "decisive_test"},
        )
        for role, stage, contract in (
            ("research_lead", "frontier_proposal", lead_contract),
            ("challenger", "candidate_review", challenger_contract),
        ):
            candidate = {"id": "cand-example", "hypothesis": "H", "scope": "S",
                         "why_now": "Unresolved", "goal_advance": "Discriminate",
                         "decisive_test": "Test"}
            ctx = stage_context(role, stage, {
                "goal": "Predict signed release", "research_contract": "Known inputs only",
                "final_gate": "Out-of-network prediction",
                "agent_graph": {"schema": "theorymaster/test-agent-graph/v1",
                                "hypotheses": [], "current_frontiers": [],
                                "lineage": [], "external_sources": []},
                "candidates": [candidate], "candidate": [candidate],
            })
            prompt = build_initial_prompt(
                prefix="test", role=role, stage=stage, contract_ref=contract.ref,
                contract_schema=contract.prompt_projection({}), context=ctx, write_rule="read only",
            )
            self.assertIn("Current task: " + stage, prompt)
            self.assertIn("Predict signed release", prompt)
            self.assertIn("credible", prompt)
            self.assertIn("Output contract:", prompt)
            if role == "research_lead":
                self.assertIn("Response-fitted lookup or interpolation alone is a baseline", prompt)
            else:
                self.assertIn("descriptive reproduction alone is insufficient", prompt)
                self.assertIn("closest prior claim and relevant simpler explanations or existing methods", prompt)
        self.assertIn("Do not reopen qualification", stage_card_text("research_lead", "candidate_selection"))
        self.assertIn("all original blockers are closed", stage_card_text("challenger", "repair_verification"))
        self.assertLess(len(self.lead), 6500)
        self.assertLess(len(self.challenger), 5000)

    def test_local_archived_cycles_read_only_when_available(self):
        root = Path(__file__).resolve().parents[3] / (
            "gs_study/.theory-master-tasks/"
            "1-matrix-cavity-green-g-closure-h8-dd60a2f2780a/research"
        )
        if not root.exists():
            self.skipTest("Local archived real Cycles are absent from public checkout")
        for cycle, fragment in (
            (180, "单位 root 偶极电流"),
            (182, "线性项补偿"),
            (183, "有符号逐边协方差"),
            (184, "两通道壳层再分配核"),
            (185, "无限网络体相远场"),
        ):
            with self.subTest(cycle=cycle):
                checkpoints = sorted((root / str(cycle) / "checkpoints").glob("*.json"))
                candidates = []
                for path in checkpoints:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if data.get("candidates"):
                        candidates = data["candidates"]
                self.assertTrue(candidates, f"Cycle {cycle} has a frozen candidate")
                self.assertIn(fragment, candidates[0]["hypothesis"])
        # These archive assertions establish only historical inputs, not new model behavior.


if __name__ == "__main__":
    unittest.main()
