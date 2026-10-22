"""Deterministic regression of bounded Challenger repair verification and recovery.

No external model, new scientific Cycle, or production Worker is invoked.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from theory_master.backends import FakeBackend
from theory_master.contracts import role_contract
from theory_master.contract_closure import close_agent_output
from theory_master.prompt_stages import stage_context
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.specification import TaskSpec
from theory_master.stage_registry import stage_card_text
from theory_master.workflow import RecoveryStop, Workflow
from theory_master.workflow_qualification import QualificationMixin


class RepairFixtureBackend(FakeBackend):
    def __init__(self, *, verification="confirmed", direct=False, repair_test=None):
        super().__init__(reworks=0)
        self.verification = verification
        self.direct = direct
        self.repair_test = repair_test
        self.initial_reviews = 0
        self.repair_calls = 0
        self.verification_calls = 0

    def invoke(self, role, prompt, thread_id, model, provider):
        context = json.JSONDecoder().raw_decode(
            prompt.split("Context:\n", 1)[1]
        )[0] if "Context:\n" in prompt else {}
        if role == "challenger" and "repaired_candidate" in context:
            self.verification_calls += 1
            thread = self.ensure_thread(role, thread_id, model, provider)
            if self.verification == "invalid_json":
                return thread, "{malformed-json"
            if self.verification == "wrong_id":
                return thread, json.dumps({"candidate_id": "not-this-repair",
                                           "verdict": "confirmed", "reason": "wrong id"})
            if self.verification == "missing":
                return thread, json.dumps({"candidate_id": context["repaired_candidate"]["id"]})
            if self.verification == "c189_zero_pairs":
                verdict = "unresolved"
                reason = ("Fatal design flaw: all 52 networks have distinct exact P(d) frequency vectors, "
                          "so the required exact-equality cross-network pairing set is empty (zero eligible pairs).")
            elif self.verification == "c186_weak_effect_open":
                verdict = "confirmed"
                reason = ("Original blocker is closed; the revised comparison is executable and discriminating. "
                          "A weak effect or eventual open result is an empirical outcome, not a design flaw.")
            else:
                verdict = self.verification
                reason = ("Original blocker is closed and the revised test remains feasible and discriminating"
                          if self.verification == "confirmed"
                          else "Original blocker remains unresolved")
            return thread, json.dumps({
                "candidate_id": context["repaired_candidate"]["id"],
                "verdict": verdict,
                "reason": reason,
            })
        if role == "challenger" and not self.direct and "candidate" not in context:
            # A malformed model answer enters SDK JSON/contract repair prompts,
            # not another scientific candidate_review. Keep failing deterministically.
            thread = self.ensure_thread(role, thread_id, model, provider)
            return thread, "{malformed-json"
        if role == "challenger" and not self.direct:
            self.initial_reviews += 1
            thread = self.ensure_thread(role, thread_id, model, provider)
            return thread, json.dumps({"reviews": [{
                "candidate_id": context["candidate"][0]["id"],
                "verdict": "revise",
                "reason": "Scientific discriminating criterion is undefined",
                "blocking_issues": [{
                    "problem": "No operational criterion for the frozen scientific target",
                    "required_change": "Include a response-blind check in decisive_test",
                }],
            }]})
        if role == "challenger" and self.direct:
            self.initial_reviews += 1
        if role == "research_lead" and "repair_options" in context:
            self.repair_calls += 1
            parent = context["repair_options"][0]
            thread = self.ensure_thread(role, thread_id, model, provider)
            return thread, json.dumps({
                "outcome": "resolved",
                "selected_parent_candidate_id": parent["candidate_id"],
                "repair": {
                    "repaired_decisive_test": self.repair_test or (
                        "Test original frozen scientific question using a response-blind "
                        "operational criterion and explicit contrary outcome"
                    ),
                    "blocker_closures": [{
                        "blocker_id": parent["blockers"][0]["id"],
                        "resolution": "The operational check enters the binding final test",
                    }],
                },
            })
        return super().invoke(role, prompt, thread_id, model, provider)


def make_spec(root: Path) -> TaskSpec:
    plan = root / "research_plan.md"
    plan.write_text(
        "# Goal\n\nEstablish a reproducible bounded result.\n\n"
        "# Research Contract\n\nCompare test predictions scientifically.\n\n"
        "# Final Gate\n\nAn independently adjudicated result.\n\n"
        "# Required Context\n\n- offline fixture\n", encoding="utf-8",
    )
    spec = TaskSpec.load(plan)
    config = json.loads(spec.config_path.read_text(encoding="utf-8"))
    config["max_cycles"] = 1
    spec.config_path.write_text(json.dumps(config), encoding="utf-8")
    spec = TaskSpec.from_config(spec.config_path)
    with ScienceGraphStore(spec.branch_canvas.parent / "science_graph.sqlite3"):
        pass
    return spec


class BoundedRepairVerificationTest(unittest.TestCase):
    def test_binding_definition_survives_verification_lock_and_science_views(self):
        definition = "Code(x) = floor(x/0.25); only exactly equal codes are compared."

        class DefinitionBackend(RepairFixtureBackend):
            verified_test = None

            def invoke(self, role, prompt, thread_id, model, provider):
                context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
                if role == "challenger" and "repaired_candidate" in context:
                    self.verified_test = context["repaired_candidate"]["decisive_test"]
                thread, text = super().invoke(role, prompt, thread_id, model, provider)
                if role == "research_lead" and context.get("repair_options"):
                    result = json.loads(text)
                    result["repair"]["estimand_or_operational_definition"] = definition
                    return thread, json.dumps(result)
                return thread, text

        with tempfile.TemporaryDirectory() as temp:
            backend = DefinitionBackend()
            workflow = Workflow(make_spec(Path(temp)), backend)
            state = workflow.initialize()
            workflow._qualify_and_lock(state, "1", {})
            self.assertIn(definition, backend.verified_test)
            self.assertEqual(backend.verification_calls, 1)
            lock = state["hypothesis_lock"]
            self.assertEqual(lock["decisive_test"], backend.verified_test)
            for role, stage, key in (
                ("investigator", "investigate", "frozen_objective"),
                ("judge", "terminal_assessment", "hypothesis_lock"),
                ("research_lead", "hypothesis_display", "frozen_hypothesis"),
            ):
                projected = stage_context(role, stage, {"hypothesis_lock": lock, "frozen_hypothesis": lock})
                self.assertEqual(projected[key]["decisive_test"], backend.verified_test)
            self.assertTrue(any(definition in p.read_text() for p in Path(temp).rglob("*.md")))

    def test_direct_qualified_prelock_checkpoint_qualification_restore_path(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = RepairFixtureBackend(direct=True)
            workflow = Workflow(make_spec(Path(temp)), backend, stop_before="hypothesis_lock")
            state = workflow.initialize()
            with self.assertRaisesRegex(RecoveryStop, "pre_hypothesis_lock checkpoint reached"):
                workflow._qualify_and_lock(state, "1", {})
            self.assertTrue(workflow._lock_prequalified_checkpoint(state))
            self.assertEqual(state["hypothesis_lock"]["status"], "locked")
            self.assertEqual(backend.initial_reviews, 1)
            self.assertEqual(backend.repair_calls, 0)
            self.assertEqual(backend.verification_calls, 0)

    def test_direct_qualification_has_one_challenger_review(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = RepairFixtureBackend(direct=True)
            state = Workflow(make_spec(Path(temp)), backend).run()
            self.assertEqual(state["qualification_status"], "qualified")
            self.assertEqual(backend.initial_reviews, 1)
            self.assertEqual(backend.repair_calls, 0)
            self.assertEqual(backend.verification_calls, 0)

    def test_c189_exact_p_d_pair_set_empty_prevents_repaired_hypothesis_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = RepairFixtureBackend(
                verification="c189_zero_pairs",
                repair_test=("Across the 52 available networks compute exact degree-frequency vectors P(d); "
                             "an eligible cross-network pair requires exact equality of the full vectors, "
                             "then compare the frozen outcome under the same scope."),
            )
            state = Workflow(make_spec(Path(temp)), backend).run()
            self.assertEqual(state["qualification_status"], "no_qualified_candidate")
            self.assertFalse(state.get("hypothesis_locked"))
            self.assertNotEqual((state.get("hypothesis_lock") or {}).get("status"), "locked")
            self.assertEqual(backend.repair_calls, backend.verification_calls)
            self.assertEqual(backend.verification_calls, 3)  # Existing bounded proposal gate; one review per repaired Candidate.
            reasons = []
            for history in state.get("qualification_resume_history") or []:
                reasons.extend(str(review.get("reason") or "") for review in history.get("reviews") or [])
            working = state.get("qualification_working_set") or {}
            result = (working.get("repair_verification") or {}).get("result") or {}
            reasons.append(str(result.get("reason") or ""))
            self.assertTrue(any("zero eligible pairs" in reason for reason in reasons))

    def test_c186_weak_effect_open_is_not_rejected_during_repair_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = RepairFixtureBackend(
                verification="c186_weak_effect_open",
                repair_test=("Run the feasible fixed-scope comparison with matching, contrary and inconclusive "
                             "interpretations; the empirical effect may be weak."),
            )
            state = Workflow(make_spec(Path(temp)), backend).run()
            self.assertEqual(state["hypothesis_lock"]["status"], "locked")
            self.assertEqual(backend.repair_calls, 1)
            self.assertEqual(backend.verification_calls, 1)
            review = state["hypothesis_lock"]["qualification"]["review"]
            self.assertEqual(review["repair_review_protocol_version"], 2)
            self.assertIn("weak effect", review["reason"])

    def test_legacy_repaired_prelock_checkpoint_runs_current_review_once(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = RepairFixtureBackend()
            workflow = Workflow(make_spec(Path(temp)), backend)
            state = workflow.initialize()
            parent = {
                "id": "cand-parent-1", "hypothesis": "Same frozen hypothesis",
                "why_now": "The fixed goal still needs this comparison.",
                "goal_advance": "Establish a reproducible bounded result.",
                "decisive_test": "Old test without an operational comparison key",
                "scope": "fixed 52-network scope", "status": "provisional",
            }
            blocker = {"id": "cand-parent-1:block-1", "problem": "No operational comparison key",
                       "required_change": "Specify the comparison key in the decisive test"}
            repaired = {
                **parent, "id": "cand-repaired-1",
                "decisive_test": "Use a specified operational comparison key and record contrary outcomes",
                "parent_candidate_id": parent["id"], "addressed_issue_ids": [blocker["id"]],
                "blocker_closures": [{"blocker_id": blocker["id"], "resolution": "Bound key is stated in the test"}],
            }
            original_review = {"candidate_id": parent["id"], "verdict": "revise",
                               "reason": "The comparison key is undefined", "blocking_issues": [blocker]}
            legacy_review = {
                "candidate_id": repaired["id"], "verdict": "qualified", "reason": "Legacy blocker-only confirmation",
                "qualification_basis": "repair_verified", "parent_candidate_id": parent["id"],
                "original_blocking_issues": [blocker],
                "repair_verification": {"candidate_id": repaired["id"], "verdict": "confirmed", "reason": "Old policy"},
            }
            state["role_outputs"]["research_lead_frontier"] = {"candidates": [parent]}
            state["role_outputs"]["candidate_review"] = {"reviews": [original_review]}
            state["checkpoint"] = {
                "name": "pre_hypothesis_lock", "selected_candidate_id": repaired["id"],
                "selected_candidate": repaired, "selected_review": legacy_review,
                "finalized": {"id": "H-legacy", "title": "Same frozen hypothesis",
                              "hypothesis": parent["hypothesis"], "scope": parent["scope"],
                              "goal_advance": parent["goal_advance"], "decisive_test": repaired["decisive_test"]},
            }
            state["qualification_status"] = "qualified"
            state["hypothesis_lock"] = {"status": "unlocked"}
            state["hypothesis_locked"] = False
            workflow._persist(state)
            self.assertFalse(workflow._lock_prequalified_checkpoint(state))
            workflow._qualify_and_lock(state, "1", {})
            self.assertEqual(state["hypothesis_lock"]["status"], "locked")
            self.assertEqual(backend.initial_reviews, 0)
            self.assertEqual(backend.repair_calls, 0)
            self.assertEqual(backend.verification_calls, 1)
            self.assertEqual(state["hypothesis_lock"]["qualification"]["review"]["repair_review_protocol_version"], 2)

    def test_repaired_checkpoint_preserves_frozen_estimand(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = RepairFixtureBackend()
            workflow = Workflow(make_spec(Path(temp)), backend, stop_before="hypothesis_lock")
            state = workflow.initialize()
            estimand = {"quantity": "P(d)", "identity": "exact vector"}
            original = {
                "id": "cand-parent-2", "hypothesis": "Same frozen hypothesis",
                "why_now": "The fixed goal still needs this comparison.",
                "goal_advance": "Establish a reproducible bounded result.",
                "decisive_test": "Old test without an operational comparison key",
                "scope": "fixed 52-network scope", "estimand": estimand, "status": "provisional",
            }
            state["role_outputs"]["research_lead_frontier"] = {"candidates": [original]}
            workflow._persist(state)
            with self.assertRaisesRegex(RecoveryStop, "pre_hypothesis_lock checkpoint reached"):
                workflow._qualify_and_lock(state, "1", {})
            selected = state["checkpoint"]["selected_candidate"]
            self.assertEqual(selected["estimand"], estimand)
            self.assertEqual(state["checkpoint"]["selected_review"]["repair_review_protocol_version"], 2)

    def test_revise_confirmed_enters_lock_only_after_second_call(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = RepairFixtureBackend()
            state = Workflow(make_spec(Path(temp)), backend).run()
            self.assertEqual(state["hypothesis_lock"]["status"], "locked")
            self.assertEqual(backend.initial_reviews, 1)
            self.assertEqual(backend.verification_calls, 1)
            self.assertEqual(backend.repair_calls, 1)
            review = state["hypothesis_lock"]["qualification"]["review"]
            self.assertEqual(review["qualification_basis"], "repair_verified")
            self.assertEqual(review["repair_verification"]["verdict"], "confirmed")
            self.assertEqual(review["repair_review_protocol_version"], 2)

    def test_unresolved_exits_bounded_proposal_without_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = RepairFixtureBackend(verification="unresolved")
            state = Workflow(make_spec(Path(temp)), backend).run()
            self.assertEqual(state["qualification_status"], "no_qualified_candidate")
            self.assertFalse(state["hypothesis_locked"])
            self.assertEqual(backend.repair_calls, 3)
            self.assertEqual(backend.verification_calls, 3)
            self.assertEqual(backend.initial_reviews, 3)

    def test_verification_schema_and_identity_are_fail_closed(self):
        original = {"id": "old", "hypothesis": "H", "scope": "S",
                    "goal_advance": "G", "decisive_test": "old", "estimand": "P(d)"}
        blocker = {"id": "old:block-1", "problem": "no comparator",
                   "required_change": "add a comparator"}
        repaired = {**original, "id": "new", "parent_candidate_id": "old",
                    "decisive_test": "new comparator test",
                    "addressed_issue_ids": ["old:block-1"]}
        context = {"original_candidate": original, "repaired_candidate": repaired,
                   "original_review": {"candidate_id": "old", "verdict": "revise",
                                       "blocking_issues": [blocker]},
                   "goal": "Goal", "research_contract": "Contract", "final_gate": "Final Gate",
                   "agent_graph": {"schema": "fixture/sqlite-graph"},
                   "science_graph_tools": {"command_prefix": "read-only fixture"},
                   "task_home": "/task", "research_root": "/research", "cycle_workspace": "/cycle"}
        contract = role_contract("challenger", phase="repair_verification")
        valid = {"candidate_id": "new", "verdict": "confirmed", "reason": "Original blocker resolved"}
        contract.validate(valid, context)
        self.assertEqual(close_agent_output("challenger", "repair_verification", valid, context, {}),
                         valid)
        for output in (
            {"candidate_id": "wrong", "verdict": "confirmed", "reason": "yes"},
            {"candidate_id": "new", "verdict": "qualified", "reason": "yes"},
            {"candidate_id": "new", "verdict": "confirmed"},
            {**valid, "new_blocker": "unapproved condition"},
        ):
            with self.subTest(output=output), self.assertRaises(ValueError):
                contract.validate(output, context)
        for field, changed in (
            ("hypothesis", "H2"), ("scope", "S2"),
            ("goal_advance", "G2"), ("estimand", "new estimand"),
            ("decisive_test", "old"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                contract.validate(valid, {**context, "repaired_candidate": {**repaired, field: changed}})
        projected = stage_context("challenger", "repair_verification", context)
        self.assertEqual(set(projected), {
            "goal", "research_contract", "final_gate", "original_candidate", "repaired_candidate",
            "original_blockers", "agent_graph", "science_graph_tools", "workspace_access",
        })
        self.assertEqual(projected["original_blockers"], [blocker])
        self.assertEqual(projected["workspace_access"]["research_root"], "/research")
        self.assertEqual(projected["repaired_candidate"]["estimand"], "P(d)")
        card = stage_card_text("challenger", "repair_verification")
        for required in ("non-empty", "matching/contrary/inconclusive", "weak/uncertain effect", "open result", "runs once"):
            self.assertIn(required, card)

    def test_fault_after_repair_persist_resumes_one_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = RepairFixtureBackend()
            workflow = Workflow(make_spec(Path(temp)), backend)
            state = workflow.initialize()
            real_persist = workflow._persist
            tripped = False

            def interrupt(saved, **kwargs):
                nonlocal tripped
                real_persist(saved, **kwargs)
                check = (saved.get("qualification_working_set") or {}).get("repair_verification") or {}
                if not tripped and check.get("status") == "repair_verification_pending":
                    tripped = True
                    raise RuntimeError("simulated crash after repairing and persisting")

            workflow._persist = interrupt
            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                workflow._qualify_and_lock(state, "1", {})
            workflow._persist = real_persist
            restored = json.loads(workflow._state_path().read_text(encoding="utf-8"))
            self.assertEqual(restored["qualification_working_set"]["repair_verification"]["status"],
                             "repair_verification_pending")
            self.assertEqual(restored["qualification_working_set"]["repair_verification"]["review_protocol_version"], 2)
            self.assertEqual(backend.verification_calls, 0)
            workflow._qualify_and_lock(restored, "1", {})
            self.assertEqual(backend.verification_calls, 1)
            self.assertEqual(restored["hypothesis_lock"]["status"], "locked")

    def test_fault_after_confirmed_invocation_reuses_committed_closure(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = RepairFixtureBackend()
            workflow = Workflow(make_spec(Path(temp)), backend)
            state = workflow.initialize()
            real_call = workflow._call
            tripped = False

            def interrupt(saved, role, context, *args, **kwargs):
                nonlocal tripped
                result = real_call(saved, role, context, *args, **kwargs)
                if (not tripped and role == "challenger"
                        and context.get("mode") == "repair_verification"):
                    tripped = True
                    raise RuntimeError("crash after committed Challenger closure")
                return result

            workflow._call = interrupt
            with self.assertRaisesRegex(RuntimeError, "committed Challenger closure"):
                workflow._qualify_and_lock(state, "1", {})
            workflow._call = real_call
            restored = json.loads(workflow._state_path().read_text(encoding="utf-8"))
            self.assertEqual(backend.verification_calls, 1)
            workflow._qualify_and_lock(restored, "1", {})
            self.assertEqual(backend.verification_calls, 1)
            self.assertEqual(restored["hypothesis_lock"]["status"], "locked")
            self.assertEqual(restored["hypothesis_lock"]["qualification"]["review"]["repair_review_protocol_version"], 2)
            self.assertGreaterEqual(int(restored.get("invocation_reuse_count") or 0), 1)

    def test_malformed_or_wrong_id_verification_never_qualifies(self):
        for failure in ("invalid_json", "wrong_id", "missing"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                backend = RepairFixtureBackend(verification=failure)
                workflow = Workflow(make_spec(Path(temp)), backend)
                state = workflow.run()
                self.assertNotEqual(state.get("qualification_status"), "qualified")
                self.assertNotEqual(state.get("hypothesis_lock", {}).get("status"), "locked")

    def test_provider_quota_at_recheck_keeps_scientific_verdict_pending(self):
        from theory_master.provider_errors import ProviderQuotaExhausted

        class QuotaAtVerification(RepairFixtureBackend):
            def invoke(self, role, prompt, thread_id, model, provider):
                if role == "challenger" and '"repaired_candidate"' in prompt:
                    self.verification_calls += 1
                    raise ProviderQuotaExhausted("You've hit your usage limit",
                                                 provider="CodexHTTPS")
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            backend = QuotaAtVerification()
            state = Workflow(make_spec(Path(temp)), backend).run()
            self.assertEqual(state["status"], "paused")
            self.assertEqual(state["qualification_status"], "pending")
            self.assertFalse(state["hypothesis_locked"])
            checkpoint = (state.get("qualification_working_set") or {}).get("repair_verification") or {}
            self.assertEqual(checkpoint.get("status"), "repair_verification_pending")
            self.assertEqual(backend.initial_reviews, 1)
            self.assertEqual(backend.repair_calls, 1)
            self.assertEqual(backend.verification_calls, 1)
            self.assertNotIn("repair_verified", str(state.get("frontier_review") or {}))
            self.assertEqual((state.get("recovery") or {}).get("code"), "provider_quota_exhausted")

    def test_legacy_prelock_synthetic_review_cannot_lock(self):
        workflow = Workflow.__new__(Workflow)
        state = {"checkpoint": {
            "name": "pre_hypothesis_lock", "selected_candidate_id": "r",
            "selected_candidate": {"id": "r", "parent_candidate_id": "old"},
            "selected_review": {"candidate_id": "r", "verdict": "qualified",
                                "qualification_basis": "frozen_blocker_repair"},
            "finalized": {"decisive_test": "x"},
        }}
        with self.assertRaisesRegex(Exception, "independent Challenger repair confirmation"):
            QualificationMixin._lock_prequalified_checkpoint(workflow, state)
        self.assertNotEqual(state.get("hypothesis_lock", {}).get("status"), "locked")

    def test_proposal_gate_is_released_after_failure(self):
        class ObservedGate:
            def __init__(self):
                self.holds = 0
                self.releases = 0

            @contextmanager
            def holding(self, **kwargs):
                self.holds += 1
                try:
                    yield "fixture-token"
                finally:
                    self.releases += 1

            def assert_holder(self, token):
                if token != "fixture-token":
                    raise AssertionError("Lost ProposalGate ownership")

        with tempfile.TemporaryDirectory() as temp:
            backend = RepairFixtureBackend(verification="unresolved")
            workflow = Workflow(make_spec(Path(temp)), backend)
            gate = ObservedGate()
            workflow.worker_proposal_gate = gate
            state = workflow.run()
            self.assertEqual(state["qualification_status"], "no_qualified_candidate")
            self.assertEqual(gate.holds, gate.releases)
            self.assertEqual(gate.holds, 1)


if __name__ == "__main__":
    unittest.main()
