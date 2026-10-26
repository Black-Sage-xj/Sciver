"""Offline binding-input-limit handoff through real SDK checkpoint/projections.

FakeBackend supplies the scientific text; these checks prove transport, not
whether a real Lead or Challenger will retain the correct scientific limits.
All workflow/database writes are in disposable private temporary directories.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import test_input_qualification_evidence_20261022 as input_fixtures
from tests import test_repair_verification_state_machine_20261022 as repair_fixtures
from theory_master.prompt_stages import stage_context
from theory_master.workflow import RecoveryStop, Workflow


LIMITS = (
    "Checked representation: two stated topology coordinates, not the full input vector. "
    "Units: distinct objects; overlapping pairs are not independent objects. "
    "Domain: a fixed finite subset; observed contrast is within evaluation only, "
    "not calibration-to-evaluation support. Pending: cross-side support in the full "
    "representation must be checked before claiming shared prediction. "
    "The finite-capacity question remains local; insufficient support has no population implication."
)
TEST = "Test the fixed local capacity claim. " + LIMITS
RATIONALE_ONLY = "RATIONALE_ONLY_INPUT_LIMIT_MUST_NOT_BECOME_A_NEW_SCIENTIFIC_RULE"
REASON_ONLY = "REVIEW_REASON_ONLY_MUST_NOT_SILENTLY_CHANGE_THE_TEST"


class InputLimitsHandoff(unittest.TestCase):
    def test_initial_continuation_and_repair_prompts_bind_material_limits(self):
        helper = input_fixtures.InputQualificationEvidence()
        for role, stage in (("research_lead", "frontier_proposal"),
                            ("challenger", "candidate_review"),
                            ("challenger", "repair_verification")):
            for prompt in helper.prompts(role, stage):
                self.assertIn("decisive_test", prompt)
                self.assertIn("independent units" if stage != "repair_verification" else "comparison set", prompt)
                self.assertIn("input", prompt.lower())
        for prompt in helper.prompts("research_lead", "selection_repair"):
            self.assertIn("complete revised decisive_test", prompt)
            self.assertIn("scope", prompt)
            self.assertIn("blocker", prompt)

    def test_graph_authority_guard_survives_procedure_compression(self):
        # Missing graph is already rejected by actual production assembly;
        # this is why its duplicate explanatory sentence can be removed.
        for role, stage in (("research_lead", "frontier_proposal"),
                            ("challenger", "candidate_review")):
            with self.assertRaisesRegex(RuntimeError, "SQLite Agent graph context is missing"):
                stage_context(role, stage, {"candidate": []})
        helper = input_fixtures.InputQualificationEvidence()
        for prompt in helper.prompts("challenger", "candidate_review"):
            self.assertIn("primary reports", prompt)
            self.assertIn("unavailable material history is a qualification gap", prompt)
            self.assertIn("Do not demand the research answer", prompt)

    def _checkpoint(self, root: Path, *, repair: bool):
        class LimitBackend(repair_fixtures.RepairFixtureBackend):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.seen_review_tests = []

            def invoke(self, role, prompt, thread_id, model, provider):
                if "Context:\n" not in prompt:
                    raise AssertionError(prompt)
                context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
                if role == "challenger":
                    candidates = context.get("candidate", [])
                    if context.get("repaired_candidate"):
                        candidates = [context["repaired_candidate"]]
                    self.seen_review_tests.extend(
                        c.get("frozen_objective", c)["decisive_test"] for c in candidates
                    )
                thread, raw = super().invoke(role, prompt, thread_id, model, provider)
                out = json.loads(raw)
                if role == "research_lead" and "candidates" in out:
                    out["candidates"][0]["hypothesis"] = "Local capacity differs on a fixed finite class."
                    out["candidates"][0]["scope"] = "Fixed local finite-object capacity only."
                    out["candidates"][0]["decisive_test"] = TEST
                    out["candidates"][0]["why_now"] = RATIONALE_ONLY
                if role == "challenger" and "reviews" in out:
                    for review in out["reviews"]:
                        review["reason"] += " " + REASON_ONLY
                return thread, json.dumps(out)

        backend = LimitBackend(direct=not repair, repair_test=TEST + " Binding measurement: compare the fixed local channels without changing the domain.")
        workflow = Workflow(repair_fixtures.make_spec(root), backend, stop_before="hypothesis_lock")
        state = workflow.initialize()
        with self.assertRaisesRegex(RecoveryStop, "pre_hypothesis_lock checkpoint reached"):
            workflow._qualify_and_lock(state, "1", {})
        self.assertEqual(state["hypothesis_lock"], {"status": "unlocked"})
        self.assertFalse(state["hypothesis_locked"])
        selected = state["checkpoint"]["selected_candidate"]
        self.assertIn(LIMITS, selected["decisive_test"])
        self.assertEqual(selected["hypothesis"], "Local capacity differs on a fixed finite class.")
        self.assertEqual(selected["scope"], "Fixed local finite-object capacity only.")
        branch = next(b for b in state["branches"] if b["id"] == state["active_branch_id"])
        self.assertEqual(branch["decisive_test"], selected["decisive_test"])
        self.assertIn(LIMITS, json.dumps(branch["cycle_criteria"]))
        self.assertTrue(all(LIMITS in text for text in backend.seen_review_tests))
        self.assertEqual(backend.verification_calls, int(repair))
        for role, stage, key in (("investigator", "investigate", "frozen_objective"),
                                 ("judge", "terminal_assessment", "hypothesis_lock"),
                                 ("research_lead", "hypothesis_display", "frozen_hypothesis")):
            # Exercise actual downstream context projection only. No downstream
            # agent or formal lock is run in this temporary qualification trial.
            projected = stage_context(role, stage, {"hypothesis_lock": selected, "frozen_hypothesis": selected})
            self.assertEqual(projected[key]["decisive_test"], selected["decisive_test"])
            self.assertNotIn(RATIONALE_ONLY, json.dumps(projected[key]))
            self.assertNotIn(REASON_ONLY, json.dumps(projected[key]))

    def test_direct_checkpoint_keeps_partial_limits_and_pending_domain(self):
        with tempfile.TemporaryDirectory() as root:
            self._checkpoint(Path(root), repair=False)

    def test_one_repair_keeps_scientific_scope_and_all_binding_limits(self):
        with tempfile.TemporaryDirectory() as root:
            self._checkpoint(Path(root), repair=True)

    def test_readable_rationale_is_not_an_authoritative_test(self):
        source = {"hypothesis": "Local bounded hypothesis", "scope": "finite objects",
                  "decisive_test": "Test without an input-screen qualification statement",
                  "why_now": LIMITS, "reason": LIMITS}
        # This negative handoff fixture documents why the fix belongs in the
        # binding test. The SDK must not infer/append new science from rationale.
        ctx = stage_context("investigator", "investigate", {"hypothesis_lock": source})
        self.assertNotIn(LIMITS, json.dumps(ctx["frozen_objective"]))
        self.assertEqual(ctx["frozen_objective"]["decisive_test"], source["decisive_test"])


if __name__ == "__main__":
    unittest.main()
