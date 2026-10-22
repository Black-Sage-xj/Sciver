from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from theory_master.task import bootstrap
from theory_master.specification import TaskSpec
from theory_master.workflow import Workflow
from theory_master.backends import FakeBackend
from theory_master.workflow_support import RecoveryStop


class InvestigatorSubmissionRetirementTest(unittest.TestCase):
    def setup_workflow(self, root: Path):
        plan = root / "research_plan.md"
        plan.write_text(
            "# Goal\nVerify one local hypothesis.\n\n"
            "# Research Contract\nUse only the local synthetic inputs.\n\n"
            "# Final Gate\nIndependent judge reaches an outcome.\n\n"
            "# Required Context\nNone.\n", encoding="utf-8")
        binding = bootstrap(plan)
        workflow = Workflow(TaskSpec.from_config(binding.config_path), FakeBackend(reworks=0))
        workflow._ensure_cycle()
        state = workflow._new_state()
        state["hypothesis_lock"] = {
            "id": "H-0001", "status": "locked", "hypothesis": "H",
            "cycle_criteria": [{"id": "C-1", "criterion": "check the hypothesis"}],
            "decisive_test": "Check H",
        }
        state["hypothesis_locked"] = True
        workflow._start_investigation_attempt(state, "investigator")
        workflow.stop_before = "judge"
        return workflow, state

    def acceptance_evidence(self):
        return [{"evidence_id": "E-5-new", "summary": "bounded result",
                 "criterion_ids": ["C-1"], "relation": "supports"}]

    def test_new_result_goes_to_judge_after_one_invocation(self):
        with tempfile.TemporaryDirectory() as temp:
            wf, state = self.setup_workflow(Path(temp))
            wf._adjudicable_judge_records = lambda _: self.acceptance_evidence()
            calls = []

            def submit(_state, role, context, **kw):
                calls.append((role, dict(context)))
                state["last_invocation_id"] = "inv-single"
                return {"evidence": [{"summary": "bounded result", "criterion_ids": ["C-1"],
                                      "relation": "supports", "evidence_kind": "numerical"}]}

            wf._call = submit
            with self.assertRaises(RecoveryStop):
                wf._investigate_and_judge(state, {}, {"next_executor": "investigator"})
            self.assertEqual([role for role, _ in calls], ["investigator"])
            self.assertEqual(state["next_executor"], "judge")
            self.assertEqual(state["investigation_round_count"], 1)
            self.assertNotIn("internal_turn", calls[0][1])
            self.assertNotIn("recent_gap_history", calls[0][1])

    def test_legacy_turn_limit_uses_existing_evidence_without_second_invocation(self):
        with tempfile.TemporaryDirectory() as temp:
            wf, state = self.setup_workflow(Path(temp))
            wf._adjudicable_judge_records = lambda _: self.acceptance_evidence()
            record = state["investigation_attempts"]["attempt-0001"]
            record["outputs"] = [{"executor_role": "investigator", "round": 1,
                                  "internal_turn": 12, "round_completed": True,
                                  "invocation_id": "inv-old", "output": {"evidence": self.acceptance_evidence()}}]
            state["next_executor"] = "investigator"
            state["attention"] = {"code": "investigator_internal_turn_limit"}
            wf._call = MagicMock(side_effect=AssertionError("unexpected paid Investigator call"))
            with self.assertRaises(RecoveryStop):
                wf._investigate_and_judge(state, {}, {"next_executor": "investigator"})
            wf._call.assert_not_called()
            self.assertEqual(state["next_executor"], "judge")
            self.assertEqual(record["readiness"]["reason"], "legacy_investigator_result_reused")

    def test_single_investigator_budget_stops_new_call_without_adjudicable_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            wf, state = self.setup_workflow(Path(temp))
            wf._adjudicable_judge_records = lambda _: []
            (wf.cycle_dir / "run_state.json").write_text(json.dumps(
                {"agents": {"investigator": {"tokens_total": 52_000_000}}}))
            wf._call = MagicMock(side_effect=AssertionError("budget should suppress call"))
            with self.assertRaises(RecoveryStop):
                wf._investigate_and_judge(state, {}, {"next_executor": "investigator"})
            wf._call.assert_not_called()
            self.assertEqual(state["attention"]["code"], "investigator_budget_review_failed")
            self.assertEqual(state["status"], "needs_attention")

    def test_sdk_budget_review_approves_inline_then_calls_investigator(self):
        with tempfile.TemporaryDirectory() as temp:
            wf, state = self.setup_workflow(Path(temp))
            state["agents"] = {"investigator": {"tokens_total": 52_000_000}}
            record = state["investigation_attempts"]["attempt-0001"]
            record["outputs"] = [{
                "executor_role": "investigator", "invocation_id": "inv-01",
                "output": {"findings": [{"statement": "Reproducible bound on the toy system"}]},
            }]
            wf._persist(state)
            wf._adjudicable_judge_records = lambda _: []
            answer = json.dumps({
                "decision": "approve",
                "reason": "The latest reproducible bound is relevant to the fixed scientific objective.",
                "next_test": "Validate the new bound against an independent finite graph.",
                "evidence_ids": [], "progress_refs": ["inv-01"],
            })
            wf.backend.invoke = MagicMock(return_value=("budget-review-thread", answer))
            wf._call = MagicMock(side_effect=RecoveryStop("next invocation reached"))
            with self.assertRaisesRegex(RecoveryStop, "next invocation reached"):
                wf._investigate_and_judge(state, {}, {"next_executor": "investigator"})
            wf._call.assert_called_once()
            self.assertEqual(wf.backend.invoke.call_args.args[0], "budget_reviewer")
            self.assertEqual(state["status"], "active")
            self.assertEqual(state["attention"], {})
            self.assertFalse(state["investigator_budget"]["exhausted"])
            self.assertEqual(state["investigator_budget"]["allowance_tokens"], 100_000_000)

    def test_sdk_budget_review_rejects_without_scientific_call(self):
        with tempfile.TemporaryDirectory() as temp:
            wf, state = self.setup_workflow(Path(temp))
            state["agents"] = {"investigator": {"tokens_total": 52_000_000}}
            state["investigation_attempts"]["attempt-0001"]["outputs"] = [{
                "executor_role": "investigator", "invocation_id": "inv-reject-01",
                "output": {"findings": [{"statement": "A bounded reproducible result was recorded."}]},
            }]
            wf._persist(state)
            wf.backend.invoke = MagicMock(return_value=("budget-review-thread", json.dumps({
                "decision": "reject",
                "reason": "Persistent repeated identical operations without new scientific information.",
                "next_test": "Reassess the scientific method before another paid invocation.",
                "evidence_ids": [], "progress_refs": [],
            })))
            wf._call = MagicMock(side_effect=AssertionError("must not run"))
            with self.assertRaisesRegex(RecoveryStop, "rejected"):
                wf._investigate_and_judge(state, {}, {"next_executor": "investigator"})
            wf._call.assert_not_called()
            self.assertEqual(state["status"], "needs_attention")
            self.assertTrue((wf.cycle_dir / "budget" / "investigator_reviews.jsonl").exists())

    def test_sdk_budget_review_reuses_committed_grant_after_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            wf, state = self.setup_workflow(Path(temp))
            state["agents"] = {"investigator": {"tokens_total": 52_000_000}}
            wf._persist(state)
            from theory_master.investigator_budget import apply_review
            state["status"] = "needs_attention"
            state["resumable"] = True
            state["attention"] = {"code": "investigator_budget_review_required"}
            wf._persist(state)
            evidence_dir = wf.cycle_dir / "evidence"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            (evidence_dir / "registry.jsonl").write_text(
                json.dumps({"evidence_id":"E-r", "summary":"new evidence"}) + "\n"
            )
            apply_review(
                wf.cycle_dir, decision="approve",
                reason="Persistent reproducible evidence justifies another bounded check.",
                next_test="Check the accepted evidence against an independent sample.",
                evidence_ids=["E-r"],
            )
            wf.backend.invoke = MagicMock(side_effect=AssertionError("review must not replay"))
            wf._call = MagicMock(side_effect=RecoveryStop("scientific continuation"))
            with self.assertRaisesRegex(RecoveryStop, "scientific continuation"):
                wf._investigate_and_judge(state, {}, {"next_executor":"investigator"})
            wf.backend.invoke.assert_not_called()
            wf._call.assert_called_once()



if __name__ == "__main__":
    unittest.main()
