"""Safety/continuation tests for SDK-only Attention review."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from theory_master.attention_guidance import load_attention_guidance
from theory_master.backends import FakeBackend
from theory_master.internal_attention_review import review_attention_in_sdk
from theory_master.specification import TaskSpec
from theory_master.task import bootstrap
from theory_master.workflow import Workflow
from theory_master.workflow_support import RecoveryStop


class InternalAttentionReviewTests(unittest.TestCase):
    def make(self, root: Path) -> tuple[Workflow, dict]:
        plan = root / "research_plan.md"
        plan.write_text(
            "# Goal\nA meaningful scientific goal.\n\n"
            "# Research Contract\nOnly task-approved inputs may be used.\n\n"
            "# Final Gate\nIndependent bounded prediction.\n\n"
            "# Required Context\n- local context\n",
            encoding="utf-8",
        )
        binding = bootstrap(plan)
        wf = Workflow(TaskSpec.from_config(binding.config_path), FakeBackend(reworks=0))
        wf._ensure_cycle()
        return wf, wf._new_state()

    @staticmethod
    def set_unqualified(state):
        state.update({
            "status": "needs_attention", "phase": "qualification",
            "qualification_status": "unqualified",
            "resumable": True, "execution_active": False,
            "hypothesis_lock": {"status": "unlocked"},
            "attention": {"code": "no_qualified_candidate",
                          "reason": "Previous candidate test was not Goal-aligned."},
            "frontier_review": {
                "generated_candidates": [
                    {"id": "candidate-1", "hypothesis": "A rejected hypothesis"},
                ],
                "reviews": [{"candidate_id": "candidate-1",
                             "verdict": "reject", "reason": "Does not test the goal"}],
            },
        })

    def test_internal_review_one_fresh_model_and_no_external_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            wf, state = self.make(Path(temp))
            self.set_unqualified(state)
            answer = json.dumps({
                "action": "retry_qualification",
                "reason": "A new mainline candidate may repair the Goal-alignment gap.",
                "guidance": "Propose independently falsifiable Goal-aligned candidates under the original allowed inputs, with genuine new decisive tests.",
            })
            wf.backend.invoke = MagicMock(return_value=("internal-thread", answer))
            wf.backend.drop_thread = MagicMock()
            result = review_attention_in_sdk(
                backend=wf.backend, cycle_dir=wf.cycle_dir, state=state,
                goal=wf.spec.goal, research_contract=wf.spec.contract,
                final_gate=wf.spec.final_gate,
            )
            self.assertEqual(result["action"], "retry_qualification")
            wf.backend.invoke.assert_called_once()
            self.assertEqual(wf.backend.invoke.call_args.args[0], "attention_reviewer")
            wf.backend.drop_thread.assert_called_once_with("internal-thread")
            self.assertFalse((wf.cycle_dir / "theory_master_attention_relay.md").exists())
            receipts = list((wf.cycle_dir / "attention").glob("sdk_internal_review_01_*.json"))
            self.assertEqual(len(receipts), 1)
            recorded = json.loads(receipts[0].read_text())
            self.assertIn("ORIGINAL Research Contract", recorded["exchange"]["prompt"])
            self.assertEqual(recorded["exchange"]["model_output"], answer)
            self.assertEqual(recorded["exchange"]["thread_id"], "internal-thread")
            self.assertEqual(recorded["exchange"]["visible_thread"]["status"], "unavailable")
            handoff = [json.loads(line) for line in (wf.cycle_dir / "important_events.jsonl").read_text().splitlines()
                       if 'repair_handoff' in line][-1]
            self.assertEqual(handoff["repair"]["review_ref"], receipts[0].name)

    def test_hard_budget_or_runtime_guard_never_calls_a_model(self):
        with tempfile.TemporaryDirectory() as temp:
            wf, state = self.make(Path(temp))
            state.update({
                "phase": "investigator:budget_review",
                "attention": {"code": "investigator_budget_review_failed", "reason": "review invalid"},
            })
            wf.backend.invoke = MagicMock(side_effect=AssertionError("should not call"))
            result = review_attention_in_sdk(
                backend=wf.backend, cycle_dir=wf.cycle_dir, state=state,
                goal=wf.spec.goal, research_contract=wf.spec.contract,
                final_gate=wf.spec.final_gate,
            )
            self.assertEqual(result["action"], "hold")
            wf.backend.invoke.assert_not_called()
            receipts = list((wf.cycle_dir / "attention").glob("sdk_internal_review_01_*.json"))
            self.assertEqual(len(receipts), 1)
            recorded = json.loads(receipts[0].read_text())
            self.assertNotIn("exchange", recorded)

    def test_qualification_retries_exactly_once_in_same_workflow(self):
        with tempfile.TemporaryDirectory() as temp:
            wf, _ = self.make(Path(temp))
            calls = []
            def qualify(state, run_id, prior_cycle):
                calls.append(run_id)
                self.set_unqualified(state)
                raise RecoveryStop("none qualified")
            wf._qualify_and_lock = qualify
            wf.backend.invoke = MagicMock(return_value=("internal-thread", json.dumps({
                "action": "retry_qualification",
                "reason": "A genuinely new independent test is possible under the current contract.",
                "guidance": "Consider a new Goal-aligned competitor that tests the sufficiency of the existing input summary versus one additional allowed response-blind quantity.",
            })))
            state = wf._execute_cycle()
            self.assertEqual(len(calls), 2)
            self.assertEqual(wf.backend.invoke.call_count, 1)
            self.assertEqual(state["internal_attention_retry_count"], 1)
            self.assertEqual(state["status"], "needs_attention")
            guidance = load_attention_guidance(wf.cycle_dir)
            self.assertIsNotNone(guidance)
            self.assertEqual(guidance["source"], "sdk_internal")
            self.assertEqual(len(state["qualification_resume_history"]), 1)

    def test_internal_model_invalid_json_never_authorizes_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            wf, _ = self.make(Path(temp))
            calls = []
            def qualify(state, run_id, prior_cycle):
                calls.append(run_id)
                self.set_unqualified(state)
                raise RecoveryStop("none qualified")
            wf._qualify_and_lock = qualify
            wf.backend.invoke = MagicMock(return_value=("internal-thread", "not json"))
            state = wf._execute_cycle()
            self.assertEqual(len(calls), 1)
            self.assertEqual(state["status"], "needs_attention")
            self.assertEqual(state["internal_attention_review"]["action"], "hold")

    def test_nonqualification_attention_records_internal_hold(self):
        with tempfile.TemporaryDirectory() as temp:
            wf, state = self.make(Path(temp))
            state.update({
                "phase": "investigation", "status": "needs_attention",
                "attention": {"code": "needs_scientific_input", "reason": "no suitable input"},
            })
            wf.backend.invoke = MagicMock(return_value=("internal-thread", json.dumps({
                "action": "hold",
                "reason": "Additional task-local scientific inputs are required for a safe next step.",
                "guidance": "Review existing local research inputs before selecting an independent test.",
            })))
            result = review_attention_in_sdk(
                backend=wf.backend, cycle_dir=wf.cycle_dir, state=state,
                goal=wf.spec.goal, research_contract=wf.spec.contract,
                final_gate=wf.spec.final_gate,
            )
            self.assertEqual(result["action"], "hold")
            wf.backend.invoke.assert_called_once()


if __name__ == "__main__":
    unittest.main()
