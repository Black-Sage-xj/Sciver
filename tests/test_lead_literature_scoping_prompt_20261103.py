"""Offline checks for Lead's conditional pre-proposal Literature route.

These checks verify prompt assembly and routing contracts, not model adherence.
"""
from __future__ import annotations

import unittest

from theory_master.contract_stages import role_contract
from theory_master.prompt_stages import build_continuation_prompt, build_initial_prompt, stage_context


GRAPH = {
    "schema": "theorymaster/agent-science-graph/v1",
    "hypotheses": [], "current_frontiers": [], "lineage": [], "focus_details": [],
    "external_sources": [], "external_references": [], "source_relations": [],
}


def prompts(role: str, stage: str) -> tuple[str, str]:
    raw = {"goal": "Investigate a scientific mechanism.",
           "research_contract": "Use admissible sources.",
           "final_gate": "Resolve the stated Goal.", "agent_graph": GRAPH,
           "literature_task": {"task_type": "landscape", "search_target": "method assumptions"},
           "sdk_scholarly_retrieval": {"output_dir": "/tmp/offline-literature-fixture"}}
    context = stage_context(role, stage, raw)
    contract = role_contract(role, phase=stage)
    args = dict(role=role, stage=stage, contract_ref=contract.ref,
                contract_schema=contract.prompt_projection(context), context=context)
    return (build_initial_prompt(prefix="offline test", write_rule="read only", **args),
            build_continuation_prompt(**args))


class PreproposalLiteraturePromptTests(unittest.TestCase):
    def test_lead_research_and_conditional_detour_reach_both_prompt_paths(self):
        for prompt in prompts("research_lead", "frontier_proposal"):
            for field in ("needs_literature", "landscape", "targeted_source", "novelty_check"):
                self.assertIn(field, prompt)
            self.assertIn("Local sources sufficient for this decision do not require an external search", prompt)

    def test_literature_and_repair_boundaries_reach_prompt(self):
        for prompt in prompts("literature", "landscape"):
            self.assertIn("source-backed distinctions and assumptions", prompt)
            self.assertIn("without proposing or selecting a Hypothesis", prompt)
        for prompt in prompts("challenger", "repair_verification"):
            self.assertIn("original_blockers", prompt)
            self.assertIn("inability to execute is not a scientific inconclusive result", prompt)

    def test_detour_remains_one_exclusive_request_or_local_candidate(self):
        contract = role_contract("research_lead", phase="frontier_proposal")
        request = {"needs_literature": True, "literature_request": {
            "task_type": "landscape", "search_target": "assumptions of mechanism X",
            "why_external_needed": "local reports do not establish assumption A",
            "expected_external_evidence": "primary source for A and counterexamples",
        }}
        contract.validate(request, {})
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            contract.validate({**request, "candidates": [{"hypothesis": "H"}]}, {})
        self.assertIn("candidates", contract.prompt_schema)

    def test_unresolved_literature_question_reaches_lead_digest(self):
        context = stage_context("research_lead", "frontier_proposal", {
            "agent_graph": GRAPH,
            "lead_literature_result": {
                "evidence_records": [{
                    "source_ref": "paper:1", "scientific_insight": "Assumption A has a limited domain",
                    "source_access": "abstract", "locator": {"section": "Abstract"},
                }],
                "remaining_scientific_question": "Does A hold for the target network family?",
            },
        })
        digest = context["lead_literature_result"]
        self.assertEqual(digest["remaining_scientific_question"],
                         "Does A hold for the target network family?")
        self.assertEqual(digest["evidence_records"][0]["source_access"], "abstract")


if __name__ == "__main__":
    unittest.main()
