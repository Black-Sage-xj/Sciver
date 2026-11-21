"""Offline assembly checks for the single-Cycle qualification policy.

Synthetic candidates exercise qualification boundaries without any research
trajectory data. These checks validate prompt assembly and frozen-objective
transport; they do not assert a model verdict.
"""
from __future__ import annotations

import unittest

from theory_master.contract_stages import role_contract
from theory_master.prompt_stages import build_continuation_prompt, build_initial_prompt, stage_context
from theory_master.stage_registry import stage_definition_text


# Handwritten counterexamples: preparation alone, an unbounded program, and a
# missing prerequisite. Their truth is deliberately left for scientific review.
SYNTHETIC_CANDIDATES = (
    {
        "hypothesis": "A newly documented sensor protocol is reproducible.",
        "scope": "One synthetic sensor and one stated operating condition.",
        "decisive_test": "Write an acquisition plan without collecting any measurements.",
    },
    {
        "hypothesis": "A single estimator is optimal for every possible signal family.",
        "scope": "All finite and infinite signal families without a resource bound.",
        "decisive_test": "Derive every required representation and prove global optimality.",
    },
    {
        "hypothesis": "The proposed filter reduces error against the declared control.",
        "scope": "A fixed synthetic dataset with paired observations.",
        "decisive_test": "Compare paired errors after obtaining an unspecified unavailable calibration.",
    },
)
GRAPH = {
    "schema": "theorymaster/agent-science-graph/v1",
    "hypotheses": [], "current_frontiers": [], "lineage": [], "focus_details": [],
    "external_sources": [], "external_references": [], "source_relations": [],
}


def assembled(role: str, stage: str, *, candidate: dict | None = None) -> tuple[str, str, dict]:
    raw = {
        "goal": "Explain a physical response using admissible information.",
        "research_contract": "Use stated inputs and preserve valid controls.",
        "final_gate": "Deliver a tested explanation across its claimed domain.",
        "agent_graph": GRAPH,
    }
    if candidate is not None:
        raw["candidate"] = [candidate]
    context = stage_context(role, stage, raw)
    contract = role_contract(role, phase=stage)
    args = dict(role=role, stage=stage, contract_ref=contract.ref,
                contract_schema=contract.prompt_projection(context), context=context)
    return (
        build_initial_prompt(prefix="offline test", write_rule="read only", **args),
        build_continuation_prompt(**args),
        context,
    )


class SingleCycleQualificationPromptTests(unittest.TestCase):
    def test_lead_receives_complete_single_cycle_boundary_in_both_prompt_paths(self):
        initial, continuation, context = assembled("research_lead", "frontier_proposal")
        for prompt in (initial, continuation):
            for field in ("goal", "research_contract", "final_gate"):
                self.assertIn(context[field], prompt)
            self.assertIn("one Cycle-decidable proposition", prompt)
            self.assertIn("no_new_science", prompt)

    def test_synthetic_counterexamples_reach_challenger_with_scope_gate(self):
        self.assertEqual(len(SYNTHETIC_CANDIDATES), 3)
        for candidate in SYNTHETIC_CANDIDATES:
            with self.subTest(hypothesis=candidate["hypothesis"]):
                initial, continuation, context = assembled(
                    "challenger", "candidate_review", candidate=candidate,
                )
                self.assertEqual(context["candidate"][0]["frozen_objective"], {
                    key: candidate[key] for key in ("hypothesis", "scope", "decisive_test")
                })
                for prompt in (initial, continuation):
                    self.assertIn(candidate["hypothesis"], prompt)
                    self.assertIn(candidate["decisive_test"], prompt)
                    self.assertIn("Missing means to run the test are operational", prompt)

    def test_generic_theory_negative_and_local_work_remain_eligible(self):
        lead = stage_definition_text("research_lead", "frontier_proposal")
        challenger = stage_definition_text("challenger", "candidate_review")
        for text in (lead, challenger):
            for task_specific in ("Q_g", "P(k)", "bondingprob", "mother network"):
                self.assertNotIn(task_specific, text)
        self.assertIn("Do not demand the research answer at qualification", challenger)
        self.assertIn("unknown derivations and the decisive experiment remain Investigator work", lead)
        self.assertEqual(role_contract("research_lead", phase="frontier_proposal").version, 18)
        self.assertEqual(role_contract("challenger", phase="candidate_review").version, 6)


if __name__ == "__main__":
    unittest.main()
