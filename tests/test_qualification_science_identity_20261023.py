"""Offline transport/integrity checks, not predictions of model judgments.

All writes use temporary private tasks. Scientific fixtures span measurement,
theory and population comparisons; none embed a production research answer.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theory_master.prompt_stages import stage_context
from theory_master.scientific_objective import (
    frozen_science_view, frozen_science_digest, locked_science_view, review_binding,
)
from theory_master.workflow import Workflow, RecoveryStop
from theory_master.workflow_invocation import _bind_review_output
from theory_master.invocations import InvocationLedger, stable_hash
from tests.test_repair_verification_state_machine_20261022 import RepairFixtureBackend, make_spec


GRAPH = {"schema": "theorymaster/test-agent-graph/v1", "hypotheses": [],
         "current_frontiers": [], "lineage": [], "external_sources": []}


def candidate():
    return {"id": "cand-offline", "hypothesis": "A bounded measurement transfers to a new population.",
            "scope": "Fixed population and units.", "decisive_test": "Compare the fixed statistic; uncertainty may be inconclusive.",
            "why_now": "BACKGROUND_ONLY: historical population did not settle this uncertainty.",
            "goal_advance": "Establish a reproducible bounded result."}


class QualificationScienceIdentity(unittest.TestCase):
    def test_authority_background_split_preserves_exact_text_in_both_prompt_modes(self):
        for claim in ("A stability theorem under a new boundary condition.",
                      "A measurement model transfers across populations.",
                      "A new mechanism distinguishes previously identical predictions."):
            c = {**candidate(), "hypothesis": claim, "decisive_test": "Exact α≤δ\n with  two spaces."}
            for continuation in (False, True):
                ctx = stage_context("challenger", "candidate_review", {"candidate": [c], "agent_graph": GRAPH},
                                    continuation=continuation)
                seen = ctx["candidate"][0]
                self.assertEqual(seen["frozen_objective"], frozen_science_view(c))
                self.assertEqual(seen["proposal_context"]["why_now"], c["why_now"])
                self.assertNotIn("hypothesis", seen["proposal_context"])
                self.assertNotIn("decisive_test", seen)
                for role, stage, key in (("investigator", "investigate", "frozen_objective"),
                                         ("judge", "terminal_assessment", "hypothesis_lock"),
                                         ("research_lead", "hypothesis_display", "frozen_hypothesis")):
                    actual = stage_context(role, stage, {"hypothesis_lock": c, "frozen_hypothesis": c})[key]
                    self.assertEqual(actual, seen["frozen_objective"])

    def test_background_cannot_complete_an_underspecified_objective(self):
        c = {**candidate(), "why_now": "Matching threshold is exactly 0.125.",
             "decisive_test": "Use the matching threshold stated above."}
        reviewed = stage_context("challenger", "candidate_review", {"candidate": [c], "agent_graph": GRAPH})
        obj = reviewed["candidate"][0]["frozen_objective"]
        self.assertNotIn("0.125", json.dumps(obj))
        # Hash equality proves transport even in this defective fixture; semantic
        # self-containment must be judged independently, not by a string heuristic.
        self.assertEqual(obj, locked_science_view(c))

    def test_repair_review_keeps_both_estimands_and_binding_definitions(self):
        original = {**candidate(), "estimand": {"quantity": "conditional variance"}}
        repaired = {**original, "id": "repaired", "decisive_test": "Compare with a fully specified control."}
        ctx = stage_context("challenger", "repair_verification", {
            "original_candidate": original, "repaired_candidate": repaired, "agent_graph": GRAPH})
        for key in ("original_candidate", "repaired_candidate"):
            self.assertEqual(ctx[key]["estimand"], original["estimand"])
            self.assertNotIn("why_now", ctx[key]["proposal_context"])
            self.assertNotIn("title", ctx[key]["proposal_context"])
        self.assertEqual(ctx["repaired_candidate"]["frozen_objective"], frozen_science_view(repaired))

    def test_exact_digest_preserves_scientific_whitespace_and_ignores_background(self):
        c = candidate(); obj = frozen_science_view(c)
        self.assertEqual(frozen_science_digest(obj), frozen_science_digest({**obj, "why_now": "different"}))
        for field in obj:
            self.assertNotEqual(frozen_science_digest(obj), frozen_science_digest({**obj, field: obj[field] + " "}))

    def test_new_review_closure_binds_actual_projection_and_reuse_never_rebinds(self):
        c = candidate(); context = {"candidate": [c], "agent_graph": GRAPH}
        delivered = stage_context("challenger", "candidate_review", context)
        output = {"reviews": [{"candidate_id": c["id"], "verdict": "qualified", "reason": "offline"}]}
        bound = _bind_review_output(output, delivered, "candidate_review")
        self.assertNotIn("reviewed_science", output["reviews"][0])
        self.assertEqual(_bind_review_output(bound, context, "candidate_review", verify=True), bound)
        for field in ("hypothesis", "scope", "decisive_test", "id"):
            changed = {"candidate": [{**c, field: c[field] + " changed"}]}
            with self.assertRaises(RecoveryStop):
                _bind_review_output(bound, changed, "candidate_review", verify=True)
        with self.assertRaisesRegex(RecoveryStop, "binding missing or changed"):
            _bind_review_output(output, context, "candidate_review", verify=True)

    def test_legacy_lock_readable_but_present_invalid_binding_never_downgrades(self):
        c = candidate(); legacy = {**c, "status": "locked", "candidate_id": c["id"]}
        before = copy.deepcopy(legacy)
        self.assertEqual(locked_science_view(legacy), frozen_science_view(c))
        self.assertEqual(legacy, before)
        valid = {**legacy, "qualification": {"review": {"reviewed_science": review_binding(c["id"], frozen_science_view(c))}}}
        self.assertEqual(locked_science_view(valid), frozen_science_view(c))
        for binding in (None, {}, {"protocol_version": 999}):
            invalid = {**valid, "qualification": {"review": {"reviewed_science": binding}}}
            with self.assertRaises(ValueError):
                locked_science_view(invalid)
        with self.assertRaises(ValueError):
            locked_science_view({**valid, "scope": "a different population"})

    def test_prelock_restore_checks_actual_finalized_body_before_any_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = RepairFixtureBackend(direct=True)
            w = Workflow(make_spec(Path(tmp)), backend, stop_before="hypothesis_lock")
            state = w.initialize()
            with self.assertRaisesRegex(RecoveryStop, "checkpoint reached"):
                w._qualify_and_lock(state, "1", {})
            self.assertEqual(backend.initial_reviews, 1)
            for side, field in (("selected_candidate", "scope"), ("finalized", "hypothesis"),
                                ("finalized", "decisive_test")):
                changed = copy.deepcopy(state)
                changed["checkpoint"][side][field] += " changed"
                before = copy.deepcopy(changed)
                with self.assertRaises(RecoveryStop):
                    w._lock_prequalified_checkpoint(changed)
                self.assertEqual(changed, before)
            self.assertEqual(backend.initial_reviews, 1)
            self.assertTrue(w._lock_prequalified_checkpoint(state))
            changed = copy.deepcopy(state)
            changed["hypothesis_lock"]["scope"] += " broadened"
            before = copy.deepcopy(changed)
            with patch.object(w, "_author_hypothesis_display") as display:
                with self.assertRaises(RecoveryStop):
                    w._restore_locked_cycle(changed, "1")
                display.assert_not_called()
            self.assertEqual(changed, before)

    def test_legacy_working_review_stops_before_spending_or_changing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = RepairFixtureBackend(direct=True)
            w = Workflow(make_spec(Path(tmp)), backend)
            state = w.initialize(); c = candidate()
            state["qualification_working_set"] = {"candidates": [c], "reviews": [
                {"candidate_id": c["id"], "verdict": "qualified", "reason": "old review"}]}
            before = copy.deepcopy(state)
            with self.assertRaises(RecoveryStop):
                w._qualify_and_lock(state, "1", {})
            self.assertEqual(state, before)
            self.assertEqual(backend.initial_reviews, 0)

    def test_invoke_reuses_exact_review_but_refuses_same_id_changed_science(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = RepairFixtureBackend(direct=True)
            w = Workflow(make_spec(Path(tmp)), backend)
            state = w.initialize(); c = candidate()
            first = w._call(state, "challenger", {"candidates": [c], "candidate": [c]})
            second = w._call(state, "challenger", {"candidates": [c], "candidate": [c]})
            self.assertEqual(first, second)
            self.assertEqual(backend.initial_reviews, 1)
            changed = {**c, "decisive_test": c["decisive_test"] + " Changed definition."}
            with self.assertRaises(RecoveryStop):
                w._call(state, "challenger", {"candidates": [changed], "candidate": [changed]})
            self.assertEqual(backend.initial_reviews, 1)

    def test_valid_legacy_closure_without_binding_is_not_recertified_or_recalled(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = RepairFixtureBackend(direct=True)
            w = Workflow(make_spec(Path(tmp)), backend)
            state = w.initialize(); c = candidate()
            w._call(state, "challenger", {"candidates": [c], "candidate": [c]})
            inv = state["last_invocation_id"]
            file = w.cycle_dir / "semantic_closure" / f"{inv}.json"
            closure = json.loads(file.read_text())
            closure["closed_output"]["reviews"][0].pop("reviewed_science")
            file.write_text(json.dumps(closure))
            snapshot = {"invocation_id": inv, "closure_hash": stable_hash(closure)}
            before = copy.deepcopy(state)
            with patch.object(InvocationLedger, "find_reusable", return_value=snapshot):
                with self.assertRaisesRegex(RecoveryStop, "binding missing or changed"):
                    w._call(state, "challenger", {"candidates": [c], "candidate": [c]})
            self.assertEqual(state, before)
            self.assertEqual(backend.initial_reviews, 1)


if __name__ == "__main__":
    unittest.main()
