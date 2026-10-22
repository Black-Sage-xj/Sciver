"""Offline contract checks for single-tranche Investigator review and Attention."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from theory_master.investigator_budget import (
    BUDGET_ATTENTION_CODE, LEDGER_REL, apply_review, budget_status, format_k_tokens,
)
from theory_master.observability.attention_relay import spawn_attention_takeover_agent
from theory_master.internal_budget_review import review_budget_in_sdk
from theory_master.workflow_investigation import InvestigationMixin


class InvestigatorBudgetReviewTests(unittest.TestCase):
    def setup_cycle(self, root: Path, used: int = 52_000_000):
        research = root / ".theory-master" / "research"
        cycle = research / "1"
        cycle.mkdir(parents=True)
        state = {
            "run_dir": str(cycle), "research_root": str(research),
            "run_id": "1", "goal_id": "test", "phase": "investigator:budget_review",
            "status": "needs_attention", "resumable": True,
            "attention": {"code": BUDGET_ATTENTION_CODE, "reason": "review needed"},
            "agents": {"investigator": {"tokens_total": used}},
            "threads": {"investigator": "fixed-scientific-thread"},
            "last_safe_checkpoint": str(cycle / "checkpoints" / "000010.json"),
        }
        (cycle / "run_state.json").write_text(json.dumps(state))
        (cycle / "research_state.json").write_text(json.dumps(state))
        (cycle / "evidence").mkdir()
        (cycle / "evidence" / "registry.jsonl").write_text(
            json.dumps({"evidence_id": "E-1", "summary": "new concrete result"}) + "\n"
        )
        return research, cycle, state


    def test_3plus3_authoritative_policy_not_missing_from_run_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            research, cycle, state = self.setup_cycle(Path(temp))
            scientific = dict(state, investigator_budget_policy="three_plus_three_v1")
            (cycle / "research_state.json").write_text(json.dumps(scientific))
            approved = apply_review(
                cycle, decision="approve",
                reason="A new verified physical finding supports independent bounded investigation.",
                next_test="Check the registered result against another frozen network.",
                evidence_ids=["E-1"],
            )
            self.assertEqual(approved["policy_version"], "three_plus_three_v1")
            self.assertEqual(budget_status(cycle, research, scientific)["granted_tranches"], 2)

    def test_stale_projection_legacy_approval_requires_exact_no_grant_bridge(self):
        from theory_master.investigator_budget import bridge_verified_first_review
        with tempfile.TemporaryDirectory() as temp:
            research, cycle, state = self.setup_cycle(Path(temp))
            approved = apply_review(
                cycle, decision="approve",
                reason="A new verified physical finding supports independent bounded investigation.",
                next_test="Check the registered result against another frozen network.",
                evidence_ids=["E-1"],
            )
            self.assertNotIn("policy_version", approved)
            ledger = cycle / LEDGER_REL
            original_bytes = ledger.read_bytes()
            (cycle / "budget" / "internal_review_0001.json").write_text(
                json.dumps({
                    "review_index": approved["review_index"], "decision": approved["decision"],
                    "reason": approved["reason"], "new_allowance": approved["new_allowance"],
                }),
            )
            scientific = dict(state, investigator_budget_policy="three_plus_three_v1")
            (cycle / "research_state.json").write_text(json.dumps(scientific))
            with self.assertRaisesRegex(ValueError, "legacy budget ledger"):
                budget_status(cycle, research, scientific)
            bridge = bridge_verified_first_review(cycle)
            self.assertFalse(bridge["token_grant"])
            self.assertEqual(ledger.read_bytes(), original_bytes)
            current = budget_status(cycle, research, scientific)
            self.assertEqual((current["granted_tranches"], current["allowance_tokens"]), (2, 100_000_000))
            self.assertFalse(current["exhausted"])
            # Receipt tampering makes the bridge unusable; old ledger unchanged.
            (cycle / "budget" / "internal_review_0001.json").write_text('{"decision":"reject"}')
            with self.assertRaisesRegex(ValueError, "legacy budget ledger"):
                budget_status(cycle, research, scientific)

    def test_one_grant_handles_overshoot_and_preserves_cumulative_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            research, cycle, state = self.setup_cycle(Path(temp))
            status = budget_status(cycle, research, state)
            self.assertTrue(status["exhausted"])
            self.assertEqual(status["observed_tokens"], 52_000_000)
            self.assertEqual(status["allowance_tokens"], 50_000_000)
            grant = apply_review(
                cycle, decision="approve",
                reason="The new verified physical finding has a bounded follow-up and advances the frozen goal.",
                next_test="Verify this derivation against two independently prepared inputs.",
                evidence_ids=["E-1"],
            )
            self.assertEqual(grant["new_allowance"], 100_000_000)
            self.assertEqual(grant["investigator_thread_id"], "fixed-scientific-thread")
            self.assertFalse(budget_status(cycle, research, state)["exhausted"])
            with self.assertRaisesRegex(ValueError, "already resolved"):
                apply_review(
                    cycle, decision="approve",
                    reason="This second identical request has no independently added scientific evidence.",
                    next_test="Repeat the same scientific operation with the same original data.",
                    evidence_ids=["E-1"],
                )
            self.assertEqual(len((cycle / LEDGER_REL).read_text().splitlines()), 1)

    def test_later_review_needs_new_evidence_and_fails_closed_on_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            research, cycle, state = self.setup_cycle(Path(temp))
            reason = "A reproducible new derivation narrows the frozen hypothesis and has testable consequences."
            next_test = "Independently check the same derivation on a bounded comparison."
            apply_review(cycle, decision="approve", reason=reason,
                         next_test=next_test, evidence_ids=["E-1"])
            state["agents"]["investigator"]["tokens_total"] = 125_000_000
            (cycle / "run_state.json").write_text(json.dumps(state))
            (cycle / "research_state.json").write_text(json.dumps(state))
            self.assertEqual(budget_status(cycle, research, state)["review_index"], 2)
            with self.assertRaisesRegex(ValueError, "new registered Evidence or Investigator Findings"):
                apply_review(cycle, decision="approve", reason=reason,
                             next_test=next_test, evidence_ids=["E-1"])
            with (cycle / "evidence" / "registry.jsonl").open("a") as f:
                f.write(json.dumps({"evidence_id": "E-2", "summary": "new discovery"}) + "\n")
            reject = apply_review(cycle, decision="reject", reason=reason,
                                  next_test="Return the evidence to Research Lead for scientific redirection.",
                                  evidence_ids=["E-2"])
            self.assertEqual(reject["new_allowance"], 100_000_000)
            status = budget_status(cycle, research, state)
            self.assertTrue(status["rejected"])
            with self.assertRaisesRegex(ValueError, "already resolved"):
                apply_review(cycle, decision="approve", reason=reason,
                             next_test=next_test, evidence_ids=["E-2"])

    def test_ledger_decision_is_reflected_in_durable_snapshot_on_approve_or_reject(self):
        for decision in ("approve", "reject"):
            with self.subTest(decision=decision), tempfile.TemporaryDirectory() as temp:
                research, cycle, state = self.setup_cycle(Path(temp))
                state["investigator_budget"] = {
                    "allowance_tokens": 50_000_000, "observed_tokens": 52_000_000,
                    "rejected": False, "reviews": 0,
                }
                (cycle / "research_state.json").write_text(json.dumps(state))
                apply_review(
                    cycle, decision=decision,
                    reason="A concrete recorded result is available for a bounded decision.",
                    next_test="Verify the registered result using independent network data.",
                    evidence_ids=["E-1"],
                )
                workflow = InvestigationMixin()
                workflow.cycle_dir = cycle
                workflow.spec = SimpleNamespace(research_root=research)
                workflow._persist = lambda snapshot: (cycle / "research_state.json").write_text(
                    json.dumps(snapshot)
                )
                workflow._persist_investigator_budget_snapshot(state)
                persisted = json.loads((cycle / "research_state.json").read_text())
                self.assertEqual(persisted["investigator_budget"]["reviews"], 1)
                self.assertEqual(persisted["investigator_budget"]["rejected"], decision == "reject")
                self.assertEqual(
                    persisted["investigator_budget"]["allowance_tokens"],
                    50_000_000 if decision == "reject" else 100_000_000,
                )
                self.assertEqual(persisted["phase"], "investigator:budget_review")
                # Reconstruct after simulated process crash from persisted state.
                workflow._persist_investigator_budget_snapshot(persisted)
                replayed = json.loads((cycle / "research_state.json").read_text())
                self.assertEqual(replayed["investigator_budget"], persisted["investigator_budget"])
                self.assertEqual(len((cycle / LEDGER_REL).read_text().splitlines()), 1)

    def test_budget_attention_compatibility_never_spawns_tmux(self):
        with tempfile.TemporaryDirectory() as temp:
            research, cycle, state = self.setup_cycle(Path(temp))
            outcome = spawn_attention_takeover_agent(state)
            self.assertTrue(outcome.endswith(":sdk_internal"))
            self.assertFalse((cycle / "attention" / "budget_review_0001.md").exists())

    def test_sdk_inline_approval_is_a_single_model_call_and_durable(self):
        with tempfile.TemporaryDirectory() as temp:
            research, cycle, state = self.setup_cycle(Path(temp))
            model_answer = json.dumps({
                "decision": "approve",
                "reason": "The registered finite-graph calculation makes progress on the frozen hypothesis.",
                "next_test": "Check the result on a second independently sampled graph.",
                "evidence_ids": ["E-1"], "progress_refs": [],
            })
            backend = SimpleNamespace(
                invoke=MagicMock(return_value=("review-thread", model_answer)),
                drop_thread=MagicMock(),
            )
            grant = review_budget_in_sdk(
                backend=backend, cycle_dir=cycle, state=state, goal="Goal",
                research_contract="Contract", final_gate="Final Gate",
            )
            self.assertEqual(grant["new_allowance"], 100_000_000)
            backend.invoke.assert_called_once()
            self.assertEqual(backend.invoke.call_args.args[0], "budget_reviewer")
            self.assertEqual(backend.invoke.call_args.args[3:5], ("gpt-6-luna", "CodexHTTPS"))
            backend.drop_thread.assert_called_once_with("review-thread")
            self.assertTrue((cycle / "budget" / "internal_review_0001.json").is_file())
            self.assertFalse(budget_status(cycle, research, state)["exhausted"])

    def test_sdk_invalid_decision_fails_closed_without_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            research, cycle, state = self.setup_cycle(Path(temp))
            backend = SimpleNamespace(
                invoke=MagicMock(return_value=("review-thread", '{"decision":"approve","reason":"short"}')),
                drop_thread=MagicMock(),
            )
            with self.assertRaisesRegex(ValueError, "typed decision contract"):
                review_budget_in_sdk(
                    backend=backend, cycle_dir=cycle, state=state, goal="Goal",
                    research_contract="Contract", final_gate="Final Gate",
                )
            self.assertFalse((cycle / LEDGER_REL).exists())
            self.assertEqual(backend.invoke.call_count, 3)
            self.assertEqual(backend.drop_thread.call_count, 3)

    def test_new_investigator_findings_can_authorize_light_review(self):
        with tempfile.TemporaryDirectory() as temp:
            research, cycle, state = self.setup_cycle(Path(temp))
            state["investigation_attempts"] = {
                "attempt-0001": {"outputs": [{
                    "executor_role": "investigator", "invocation_id": "inv-1",
                    "output": {"findings": [{"statement": "Reproducible new bound for the frozen model"}]},
                }]}
            }
            (cycle / "run_state.json").write_text(json.dumps(state))
            (cycle / "research_state.json").write_text(json.dumps(state))
            (cycle / "evidence" / "registry.jsonl").write_text("")
            reason = "The persisted calculation adds a concrete, independently reviewable bound to the frozen model."
            next_test = "Check the derivation on a different finite graph to assess the bound."
            grant = apply_review(cycle, decision="approve", reason=reason,
                                 next_test=next_test, evidence_ids=[], progress_refs=["inv-1"])
            self.assertEqual(grant["progress_refs"], ["inv-1"])
            self.assertEqual(grant["new_allowance"], 100_000_000)
            state["agents"]["investigator"]["tokens_total"] = 125_000_000
            (cycle / "run_state.json").write_text(json.dumps(state))
            (cycle / "research_state.json").write_text(json.dumps(state))
            with self.assertRaisesRegex(ValueError, "new registered Evidence or Investigator Findings"):
                apply_review(cycle, decision="approve", reason=reason, next_test=next_test,
                             evidence_ids=[], progress_refs=["inv-1"])

    def test_old_budget_ledger_remains_readable_after_default_increase(self):
        with tempfile.TemporaryDirectory() as temp:
            research, cycle, state = self.setup_cycle(Path(temp), used=12_000_000)
            with patch.dict(os.environ, {"THEORY_MASTER_INVESTIGATOR_BUDGET_TOKENS": "10000000"}):
                apply_review(cycle, decision="approve",
                             reason="Prior productive scientific reasoning merits a bounded check of its physical prediction.",
                             next_test="Run the independent comparison and report its falsifying cases.",
                             evidence_ids=["E-1"])
            status = budget_status(cycle, research, state)
            self.assertEqual(status["tranche_tokens"], 10_000_000)
            self.assertEqual(status["allowance_tokens"], 20_000_000)
            self.assertEqual(format_k_tokens(23_418_859), "23,418.9k")

    def test_do_not_restart_after_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            research, cycle, state = self.setup_cycle(Path(temp))
            apply_review(
                cycle, decision="reject",
                reason="No meaningful science advanced and the recorded evidence is insufficient to justify renewed spend.",
                next_test="Re-plan the hypothesis in a new qualified research cycle.",
                evidence_ids=[],
            )
            outcome = spawn_attention_takeover_agent(state)
            self.assertTrue(outcome.endswith(":sdk_internal"))


    def test_budget_review_fails_closed_on_stale_run_state_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            _research, cycle, state = self.setup_cycle(Path(temp))
            stale = dict(state)
            stale["status"] = "active"
            (cycle / "research_state.json").write_text(json.dumps(stale))
            with self.assertRaisesRegex(ValueError, "research_state is not awaiting an Investigator budget review"):
                apply_review(
                    cycle, decision="approve",
                    reason="A reproducible finding supports a bounded independent check of the frozen hypothesis.",
                    next_test="Verify the registered evidence using an independently prepared input.",
                    evidence_ids=["E-1"],
                )
            self.assertFalse((cycle / LEDGER_REL).exists())

    def test_budget_review_requires_authoritative_research_state(self):
        with tempfile.TemporaryDirectory() as temp:
            _research, cycle, _state = self.setup_cycle(Path(temp))
            (cycle / "research_state.json").unlink()
            with self.assertRaisesRegex(ValueError, "authoritative research_state.json is missing"):
                apply_review(
                    cycle, decision="reject",
                    reason="No verified progress justifies another tranche for the frozen hypothesis.",
                    next_test="Requalify the research direction in a separately authorized Cycle.",
                    evidence_ids=[],
                )
            self.assertFalse((cycle / LEDGER_REL).exists())


if __name__ == "__main__":
    unittest.main()
