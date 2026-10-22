"""Deterministic, no-model regression for Lead/Challenger qualification and prompts."""
import json
import unittest

from theory_master.contracts import role_contract
from theory_master.contract_closure import close_agent_output
from theory_master.prompt_stages import build_initial_prompt, stage_context as _stage_context
from theory_master.stage_registry import stage_card_text, procedure_card_text


_TEST_AGENT_GRAPH = {
    "schema": "theorymaster/test-agent-graph/v1",
    "hypotheses": [], "current_frontiers": [], "lineage": [], "external_sources": [],
}
_GRAPH_REQUIRED_STAGES = {
    ("research_lead", "frontier_proposal"), ("research_lead", "frontier_maintenance"),
    ("challenger", "candidate_review"), ("judge", "goal_completion"),
}

def stage_context(role, stage, context, *, continuation=False):
    values = dict(context)
    if ((role, stage) in _GRAPH_REQUIRED_STAGES
            and "agent_graph" not in values and "agent_graph_reuse" not in values):
        values["agent_graph"] = dict(_TEST_AGENT_GRAPH)
    return _stage_context(role, stage, values, continuation=continuation)


class QualificationIntegrity(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            {"id": "cand-A", "hypothesis": "H-A", "scope": "A", "goal_advance": "Gate A",
             "why_now": "A", "decisive_test": "A test"},
            {"id": "cand-B", "hypothesis": "H-B", "scope": "B", "goal_advance": "Gate B",
             "why_now": "B", "decisive_test": "B test"},
        ]
        self.context = {"candidates": self.candidates}
        self.contract = role_contract("challenger", phase="candidate_review")

    def test_candidate_id_authoritative_even_if_review_array_reordered(self):
        self.assertEqual(self.contract.version, 5)
        reviews = {"reviews": [
            {"candidate_id": "cand-B", "verdict": "reject", "reason": "B fails"},
            {"candidate_id": "cand-A", "verdict": "qualified", "reason": "A works"},
        ]}
        self.contract.validate(reviews, self.context)
        closed = close_agent_output("challenger", "candidate_review", reviews, self.context, {})
        self.assertEqual([(r["candidate_id"],r["reason"]) for r in closed["reviews"]],
                         [("cand-A", "A works"), ("cand-B", "B fails")])

    def test_missing_duplicate_unknown_ids_rejected(self):
        valid = {"candidate_id": "cand-A", "verdict": "qualified", "reason": "reason A"}
        other = {"candidate_id": "cand-B", "verdict": "reject", "reason": "reason B"}
        for rows in ([dict(valid, candidate_id=""), other],
                     [dict(valid), dict(valid)],
                     [dict(valid), dict(other, candidate_id="cand-X")],
                     [dict(valid, candidate_id=None), other],
                     [{k:v for k,v in valid.items() if k!="candidate_id"}, other]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.contract.validate({"reviews": rows}, self.context)
        with self.assertRaises(ValueError):
            close_agent_output("challenger","candidate_review",
                               {"reviews":[valid,dict(valid)]},self.context,{})

    def test_stage_context_preserves_exact_supplied_ids(self):
        procedure = procedure_card_text("challenger", "candidate_review")
        self.assertIn("SQLite-derived source_hypotheses and typed lineage", procedure)
        self.assertIn("never open or parse exploration.canvas", procedure)
        self.assertNotIn("opening relevant Canvas lineage", procedure)

        projected = stage_context("challenger", "candidate_review", {
            "candidate": self.candidates,
        })
        self.assertEqual([r["id"] for r in projected["candidate"]], ["cand-A","cand-B"])
        self.assertNotIn("title", projected["candidate"][0])
        self.assertNotIn("status", projected["candidate"][0])


    def test_actual_stage_turn_contains_current_contract_and_compact_stage(self):
        ctx = stage_context("challenger","candidate_review",{"candidate":self.candidates})
        prompt = build_initial_prompt(prefix="test", role="challenger",stage="candidate_review",
                    contract_ref=self.contract.ref,
                    contract_schema=self.contract.prompt_projection(self.context),
                    context=ctx,write_rule="read only")
        self.assertIn("candidate_id",prompt)
        self.assertIn("hypothesis",prompt)
        self.assertIn("Goal alignment",prompt)
        self.assertIn("Output contract: theory-master/role/challenger/candidate-review/v5",prompt)
        self.assertLess(len(stage_card_text("challenger","candidate_review")
                            +procedure_card_text("challenger","candidate_review")),4500)
        self.assertLess(len(stage_card_text("research_lead","frontier_proposal")
                            +procedure_card_text("research_lead","frontier_proposal")),6000)

    def test_cannot_repair_prompt_never_qualifies_parent(self):
        card = stage_card_text("research_lead","selection_repair")
        self.assertIn("cannot_repair",card)
        self.assertIn("does not lock H",card)
        self.assertIn("does not enter Investigator",card)
        self.assertIn("Investigation work",stage_card_text("challenger","candidate_review"))


if __name__=="__main__":
    unittest.main()
