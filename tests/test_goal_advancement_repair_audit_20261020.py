"""Offline regression for Goal advancement qualification and single frozen repair.

The historical Cycle 135/136 examples motivated the regression. No live
research Cycle or model provider is required.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from theory_master.core import FakeBackend, TaskSpec, Workflow
from theory_master.contract_stages import role_contract
from theory_master.contract_closure import close_agent_output
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.stage_registry import stage_card_text


class QualificationRepairAuditTest(unittest.TestCase):
    def _context(self):
        return {
            "candidates": [
                {
                    "id": "cand-old",
                    "hypothesis": "An independent topology statistic improves the energy law",
                    "scope": "Existing test networks only",
                    "goal_advance": "Distinguish whether a new structure input adds information",
                    "decisive_test": "Check signed absolute error against a budget",
                }
            ],
            "candidate_reviews": [
                {
                    "candidate_id": "cand-old",
                    "verdict": "revise",
                    "reason": "Error budget cannot be adjudicated until specified",
                    "blocking_issues": [
                        {
                            "id": "cand-old:block-1",
                            "problem": "Budget is undefined",
                            "required_change": "Specify a justified bound in the decisive test",
                        }
                    ],
                }
            ],
            "qualification_error": {"verdict": "revise"},
        }

    def test_explanation_alone_never_qualifies_repair(self):
        context = self._context()
        contract = role_contract("research_lead", phase="selection_repair")
        incomplete = {
            "outcome": "resolved",
            "repair": {
                "blocker_closures": [
                    {"blocker_id": "cand-old:block-1",
                     "resolution": "Use a 95% network-level confidence interval"}
                ],
                "estimand_or_operational_definition": "Use a 95% network interval",
            },
        }
        with self.assertRaisesRegex(ValueError, "repaired_decisive_test"):
            contract.validate(incomplete, context)
        with self.assertRaisesRegex(ValueError, "revised frozen decisive_test"):
            close_agent_output("research_lead", "selection_repair", incomplete, context, {})

        cosmetic = json.loads(json.dumps(incomplete))
        cosmetic["repair"]["repaired_decisive_test"] = "  Check signed absolute error against a budget  "
        with self.assertRaisesRegex(ValueError, "materially update"):
            contract.validate(cosmetic, context)

    def test_complete_repair_preserves_identity_and_scientific_payload(self):
        context = self._context()
        contract = role_contract("research_lead", phase="selection_repair")
        valid = {
            "outcome": "resolved",
            "repair": {
                "blocker_closures": [
                    {"blocker_id": "cand-old:block-1",
                     "resolution": "Use an a priori 2-unit bound and propagated 95% intervals"}
                ],
                "repaired_decisive_test": (
                    "Check signed absolute errors on independent networks against "
                    "a previously justified 2-unit bound; classify using simultaneous "
                    "95% confidence bounds, with crossing intervals inconclusive."
                ),
            },
        }
        contract.validate(valid, context)
        changed = close_agent_output("research_lead", "selection_repair", valid, context, {})
        candidate = changed["replacement_candidates"][0]
        self.assertEqual(candidate["hypothesis"], context["candidates"][0]["hypothesis"])
        self.assertEqual(candidate["scope"], context["candidates"][0]["scope"])
        self.assertEqual(candidate["goal_advance"], context["candidates"][0]["goal_advance"])
        self.assertIn("95%", candidate["decisive_test"])
        self.assertEqual(candidate["addressed_issue_ids"], ["cand-old:block-1"])
        self.assertEqual(candidate["blocker_closures"][0]["blocker_id"], "cand-old:block-1")

    def test_goal_advancement_is_scientific_review_not_success_guarantee(self):
        lead = stage_card_text("research_lead", "frontier_proposal")
        challenger = stage_card_text("challenger", "candidate_review")
        self.assertIn("Matching, contrary and inconclusive results each need an honest interpretation", lead)
        self.assertIn("Matching, contrary and inconclusive results must retain scientific meaning", challenger)
        self.assertIn("eventual negative/open result is not itself a blocker", challenger)
        self.assertIn("counterexample, bounded mechanism, negative result or high-risk claim can advance the Goal without completing it", lead)

    def test_one_repair_freezes_the_corrected_test_and_audit(self):
        class RevisingBackend(FakeBackend):
            challenger_calls = 0
            repair_calls = 0

            def invoke(self, role, prompt, thread_id, model, provider):
                ctx = json.JSONDecoder().raw_decode(
                    prompt.split("Context:\n", 1)[1]
                )[0] if "Context:\n" in prompt else {}
                if role == "challenger":
                    self.challenger_calls += 1
                    thread = self.ensure_thread(role, thread_id, model, provider)
                    if ctx.get("repaired_candidate"):
                        return thread, json.dumps({
                            "candidate_id": ctx["repaired_candidate"]["id"],
                            "verdict": "confirmed",
                            "reason": "original operational blocker is now resolved in the binding test",
                        })
                    candidate_id = ctx["candidate"][0]["id"]
                    return thread, json.dumps({"reviews": [{
                        "candidate_id": candidate_id,
                        "verdict": "revise",
                        "reason": "Valid scientific goal increment, but test lacks an operational criterion",
                        "blocking_issues": [{
                            "problem": "Observable acceptance criterion is missing",
                            "required_change": "Specify a concrete discriminating check in the frozen test",
                        }],
                    }]})
                if role == "research_lead" and ctx.get("repair_options"):
                    self.repair_calls += 1
                    thread = self.ensure_thread(role, thread_id, model, provider)
                    parent = ctx["repair_options"][0]
                    return thread, json.dumps({
                        "outcome": "resolved",
                        "selected_parent_candidate_id": parent["candidate_id"],
                        "repair": {
                            "blocker_closures": [{
                                "blocker_id": parent["blockers"][0]["id"],
                                "resolution": "State the new operational acceptance criterion",
                            }],
                            "repaired_decisive_test": (
                                "register deterministic smoke evidence and assess "
                                "a concrete operational acceptance criterion"
                            ),
                        },
                    })
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = root / "research_plan.md"
            plan.write_text(
                "# Goal\n\nEstablish a reproducible result.\n\n"
                "# Research Contract\n\nBounded scientific inquiry.\n\n"
                "# Final Gate\n\nEvidence is checked.\n\n"
                "# Required Context\n\n- local\n",
                encoding="utf-8",
            )
            spec = TaskSpec.load(plan)
            config = json.loads(spec.config_path.read_text(encoding="utf-8"))
            config["max_cycles"] = 1
            spec.config_path.write_text(json.dumps(config), encoding="utf-8")
            spec = TaskSpec.from_config(spec.config_path)
            with ScienceGraphStore(spec.branch_canvas.parent / "science_graph.sqlite3"):
                pass
            backend = RevisingBackend(reworks=0)
            state = Workflow(spec, backend).run()
            self.assertEqual(state["qualification_status"], "qualified")
            self.assertEqual(backend.challenger_calls, 2)
            self.assertEqual(backend.repair_calls, 1)
            lock = state["hypothesis_lock"]
            self.assertIn("operational acceptance criterion", lock["decisive_test"])
            self.assertEqual(
                lock["qualification"]["review"]["qualification_basis"], "repair_verified"
            )
            provenance = lock["candidate_provenance"]
            self.assertEqual(len(provenance["addressed_issue_ids"]), 1)
            self.assertEqual(len(provenance["blocker_closures"]), 1)
            self.assertIn("operational acceptance criterion", provenance["decisive_test"])


if __name__ == "__main__":
    unittest.main()
