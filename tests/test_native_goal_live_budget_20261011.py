"""Live Native Goal quota guard, cross-process high-water and review proofs."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from theory_master.native_goal import (
    NativeGoalBudgetExceeded, investigator_goal, run_native_goal,
)
from theory_master.investigator_budget import (
    budget_status, apply_review, workspace_review_progress,
)
from theory_master.internal_budget_review import _pending_progress
from theory_master.codex_backend import CodexBackend
from theory_master.observability.collector import Collector
from theory_master.specification import TaskSpec
from theory_master.task import bootstrap


def spec_fixture(root: Path) -> TaskSpec:
    plan = root / "research_plan.md"
    plan.write_text("# Goal\nQuantify frozen science.\n# Research Contract\nSynthetic.\n"
                    "# Final Gate\nJudge verifies test.\n# Required Context\nNone.\n", encoding="utf-8")
    return TaskSpec.from_config(bootstrap(plan).config_path)


class LiveBudgetTests(unittest.TestCase):
    def test_native_highwater_reconciles_unfinished_goal_with_cached_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec=spec_fixture(Path(tmp))
            c=spec.research_root/"49";c.mkdir(parents=True)
            state={"agents":{"investigator":{"tokens_total":12_000_000,
                "raw_usage":{"input_tokens":262_000_000,"output_tokens":1_000_000,
                             "cached_input_tokens":259_000_000}}}}
            (c/"run_state.json").write_text(json.dumps(state))
            d=budget_status(c,spec.research_root,state)
            self.assertEqual(d["observed_tokens"],263_000_000)
            self.assertTrue(d["exhausted"])
            self.assertEqual(d["remaining_tokens"],0)
            # Restarts lose in-memory Collector state; durable scientific high-water wins.
            (c/"research_state.json").write_text(json.dumps({
                "investigator_budget":{"observed_tokens":263_000_000}}))
            (c/"run_state.json").write_text(json.dumps({"agents":{"investigator":{"tokens_total":0}}}))
            self.assertEqual(budget_status(c,spec.research_root,{})["observed_tokens"],263_000_000)

    def test_persist_live_tokens_before_goal_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec=spec_fixture(Path(tmp))
            collector=Collector(spec)
            d=spec.research_root/"49";d.mkdir(parents=True)
            collector.run_dir=d
            collector.record_native_goal_usage("investigator",observed_tokens=263_000_000,
                                               allowance_tokens=50_000_000)
            state=json.loads((d/"run_state.json").read_text())
            self.assertEqual(state["agents"]["investigator"]["tokens_total_live"],263_000_000)
            self.assertEqual(state["tokens_total_live"],263_000_000)
            self.assertEqual(state["investigator_budget"]["observed_tokens"],263_000_000)
            self.assertTrue(state["investigator_budget"]["exhausted"])
            self.assertEqual(collector.agent_total_tokens("investigator"),263_000_000)

    def test_budget_hit_is_not_swallowed_by_best_effort_observability(self):
        backend=object.__new__(CodexBackend)
        backend.threads={"thread-1": SimpleNamespace(id="thread-1")}
        backend._new_thread_ids=set()
        backend.last_native_goal={}
        backend.collector=SimpleNamespace(
            on_codex=MagicMock(side_effect=RuntimeError("optional observer failed")),
            record_native_goal_usage=MagicMock(),
        )
        backend._logger=SimpleNamespace(debug=MagicMock())
        backend.ensure_thread=MagicMock(return_value="thread-1")
        def fake_goal(thread, objective, *, on_event):
            on_event(SimpleNamespace(method="thread/tokenUsage/updated"))
            raise AssertionError("Goal returned despite budget overrun")
        with patch("theory_master.native_goal.run_native_goal",side_effect=fake_goal), \
             patch("theory_master.observability.codex_events.map_notification",return_value={
                 "event":"TOKEN_USAGE",
                 "raw_usage":{"input_tokens":240,"output_tokens":10,"cached_input_tokens":190},
                 "last_request_usage":{"input_tokens":35,"output_tokens":5},
             }):
            with self.assertRaises(NativeGoalBudgetExceeded) as exc:
                backend.invoke_goal("investigator",goal_objective="frozen target",
                    context_prompt="",handoff_path="/tmp/unused-findings.json",thread_id="thread-1",
                    model="gpt-6-luna",provider="CodexHTTPS",
                    tokens_before_goal=80,token_allowance=100)
        self.assertEqual(exc.exception.observed_tokens,120)
        self.assertEqual(exc.exception.allowance_tokens,100)
        self.assertEqual(backend.last_native_goal["status"],"budgetLimited")
        backend.collector.record_native_goal_usage.assert_called_once()
        self.assertEqual(
            backend.collector.record_native_goal_usage.call_args.kwargs["observed_tokens"],120)

    def test_native_goal_cancels_logical_goal_on_budget_exception(self):
        from openai_codex.generated.v2_all import ThreadGoalStatus
        calls=[]
        state=SimpleNamespace(status=ThreadGoalStatus.paused)
        class MockStream:
            def __init__(self,**kwargs):pass
            def __iter__(self):return iter([SimpleNamespace(method="turn/started",payload=None)])
            def close(self):calls.append("closed")
        client=SimpleNamespace(
            start_goal_operation=lambda tid,obj:(state,"logical-one"),
            cancel_goal_operation=lambda st:calls.append("cancel"),
        )
        thread=SimpleNamespace(id="thread-1",_client=client)
        def on_event(event):
            raise NativeGoalBudgetExceeded("thread-1",101,100)
        with patch("openai_codex._goal._GoalNotificationStream",MockStream):
            with self.assertRaises(NativeGoalBudgetExceeded):
                run_native_goal(thread,"frozen science",on_event=on_event)
        self.assertEqual(calls,["cancel","closed"])

    def test_native_goal_adds_direction_but_not_changed_hypothesis(self):
        ctx={"frozen_objective":{
            "hypothesis":"H-0046 frozen science", "scope":"same frozen network family",
            "decisive_test":"must test Green signed release with independent network"
        }}
        plain=investigator_goal("investigate",ctx,layout="v2")
        ctx["operator_guidance"]={"text":"Prioritize 12 existing discovery bases and physics derivation."}
        directed=investigator_goal("investigate",ctx,layout="v2")
        self.assertIn("HUMAN EXECUTION DIRECTION",directed)
        self.assertNotIn("HUMAN EXECUTION DIRECTION",plain)
        self.assertIn(ctx["frozen_objective"]["decisive_test"],directed)
        self.assertLess(len(directed),4000)

    def test_unfinished_science_artifact_can_be_reviewed_once_by_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cycle=root/"49"
            dir=cycle/"workspace/mechanics_identity"
            dir.mkdir(parents=True)
            doc=dir/"prestress_assessment.md"
            doc.write_text("# Physically meaningful constraint\nExact energy identity from controlled test.\n")
            progress=workspace_review_progress(cycle)
            self.assertEqual(len(progress),1)
            token=progress[0]["invocation_id"]
            (cycle/"budget").mkdir()
            state={"status":"NEEDS_ATTENTION","resumable":True,
                   "attention":{"code":"investigator_budget_review_required"},
                   "research_root":str(root),
                   "investigator_budget":{"observed_tokens":75_000_000},
                   "agents":{"investigator":{"tokens_total_live":75_000_000}}}
            (cycle/"run_state.json").write_text(json.dumps(state))
            (cycle/"research_state.json").write_text(json.dumps(state))
            scope=_pending_progress(cycle,state)
            self.assertEqual(scope["progress"][0]["invocation_id"],token)
            self.assertEqual(scope["progress"][0]["kind"],"unfinished_workspace_scientific_report_not_finding")
            decision=apply_review(cycle,decision="approve",
                reason="The exact derived prestress identity is a substantive reproducible scientific advance.",
                next_test="Check structural identifiability using the already qualified independent networks.",
                evidence_ids=[],progress_refs=[token])
            self.assertEqual(decision["decision"],"approve")
            self.assertEqual(decision["new_allowance"],100_000_000)
            state["agents"]["investigator"]["tokens_total_live"]=130_000_000
            (cycle/"run_state.json").write_text(json.dumps(state))
            (cycle/"research_state.json").write_text(json.dumps(state))
            with self.assertRaisesRegex(ValueError,"previously reviewed progress"):
                apply_review(cycle,decision="approve",
                    reason="No new scientific progress exists after the first allowance.",
                    next_test="Continue bounded theoretical derivation without new data.",
                    evidence_ids=[],progress_refs=[token])

    def test_review_rejects_altered_report_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cycle=root/"49";doc=cycle/"workspace/discovery_response/failed_screen_assessment.md"
            doc.parent.mkdir(parents=True)
            doc.write_text("# Initial failed controlled model\n")
            prior=workspace_review_progress(cycle)[0]["invocation_id"]
            doc.write_text("# Modified claims\n")
            (cycle/"budget").mkdir()
            state={"status":"NEEDS_ATTENTION","resumable":True,
                   "attention":{"code":"investigator_budget_review_required"},
                   "research_root":str(root),
                   "agents":{"investigator":{"tokens_total":55_000_000}}}
            (cycle/"run_state.json").write_text(json.dumps(state))
            (cycle/"research_state.json").write_text(json.dumps(state))
            with self.assertRaisesRegex(ValueError,"unregistered"):
                apply_review(cycle,decision="approve",
                    reason="Reports require independent verification before they can justify resource use.",
                    next_test="Check the physical derivation against the frozen test.",
                    evidence_ids=[],progress_refs=[prior])


if __name__=="__main__":
    unittest.main()


class ReviewerProtocolCorrectionTests(unittest.TestCase):
    def test_false_evidence_requirement_can_be_audited_and_re_reviewed(self):
        from theory_master.investigator_budget import invalidate_protocol_flawed_review, _entries
        from theory_master.internal_budget_review import review_budget_in_sdk
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); cycle=root/"49"
            report=cycle/"workspace/mechanics_identity/prestress_channel_assessment.md"
            report.parent.mkdir(parents=True)
            report.write_text("# Certified physics\nExact prestress identity verified on independent networks.\n")
            progress=workspace_review_progress(cycle)[0]["invocation_id"]
            (cycle/"budget").mkdir(parents=True)
            state={
                "status":"NEEDS_ATTENTION","resumable":True,
                "attention":{"code":"investigator_budget_review_required"},
                "research_root":str(root),
                "agents":{"investigator":{"tokens_total_live":284_507_153}},
                "investigation_attempts":{},
                "operator_guidance":{"text":"Analyze existing inputs rather than producing new networks."},
            }
            (cycle/"run_state.json").write_text(json.dumps(state))
            (cycle/"research_state.json").write_text(json.dumps(state))
            reason=("The recorded progress shows substantial diagnostics but no newly registered Evidence ID, "
                    "so approval cannot rely on an eligible new progress reference under this review. "
                    "In addition, the latest execution failed before research because the network "
                    "route was unreachable; the scientific state did not change.")
            apply_review(cycle,decision="reject",reason=reason,
                         next_test="Verify actual scientific progress in the frozen goal.",
                         evidence_ids=[],progress_refs=[])
            self.assertTrue(budget_status(cycle,root,state)["rejected"])
            before=(cycle/"budget/investigator_reviews.jsonl").read_text()
            invalidated=invalidate_protocol_flawed_review(
                cycle,reason=("The prior reviewer incorrectly required official Evidence despite available "
                              "workspace-sha256 progress refs, and misidentified a resolved, older network "
                              "failure as the latest execution outcome."),
                evidence=("Input scope supplied an independently source-hashed physical prestress derivation, "
                          "and the fresh real science invocation ran for over six hours."),
            )
            self.assertEqual(invalidated["decision"],"invalidate")
            after=(cycle/"budget/investigator_reviews.jsonl").read_text()
            self.assertTrue(after.startswith(before))
            self.assertEqual(len(_entries(cycle)),2)
            status=budget_status(cycle,root,state)
            self.assertFalse(status["rejected"])
            self.assertEqual(status["allowance_tokens"],50_000_000)
            self.assertTrue(status["exhausted"])
            prior_review=cycle/"budget/internal_review_0001.json"
            prior_review.write_text('{"decision":"reject","reason":"old judgment retained"}')
            response=json.dumps({
                "decision":"approve",
                "reason":"Exact prestress decomposition adds reproducible scientific progress, with a concrete identifiability next test.",
                "next_test":"Derive whether all allowed descriptors can determine the dominant prestress contribution.",
                "evidence_ids":[], "progress_refs":[progress],
            })
            backend=SimpleNamespace(
                invoke_budget_review=lambda *args:("review-thread",response),
                drop_thread=MagicMock(),
            )
            grant=review_budget_in_sdk(backend=backend,cycle_dir=cycle,state=state,
                goal="Frozen science",research_contract="Immutable protocols",final_gate="Independent Judge")
            self.assertEqual(grant["decision"],"approve")
            self.assertEqual(grant["new_allowance"],100_000_000)
            self.assertEqual(prior_review.read_text(),'{"decision":"reject","reason":"old judgment retained"}')
            copies=list((cycle/"budget").glob("internal_review_0001_ledger*.json"))
            self.assertEqual(len(copies),1)
            self.assertEqual(budget_status(cycle,root,state)["allowance_tokens"],100_000_000)
            self.assertTrue(budget_status(cycle,root,state)["exhausted"])

    def test_cannot_invalidate_a_valid_scientific_rejection_or_repeat_invalidation(self):
        from theory_master.investigator_budget import invalidate_protocol_flawed_review
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cycle=root/"49"; (cycle/"budget").mkdir(parents=True)
            state={"status":"NEEDS_ATTENTION","resumable":True,
                   "attention":{"code":"investigator_budget_review_required"},
                   "research_root":str(root),
                   "agents":{"investigator":{"tokens_total_live":60_000_000}}}
            (cycle/"run_state.json").write_text(json.dumps(state))
            (cycle/"research_state.json").write_text(json.dumps(state))
            apply_review(cycle,decision="reject",reason=(
                "No new scientifically meaningful progress occurred and the repeatedly tested model "
                "classes consistently fail on held-out cases."),
                next_test="Re-evaluate the hypothesis scope in the next human-authorized Cycle.",
                evidence_ids=[],progress_refs=[])
            with self.assertRaisesRegex(ValueError,"no grounded reviewer-protocol contradiction"):
                invalidate_protocol_flawed_review(
                    cycle,reason="No error existed, but I want to override the valid negative opinion today.",
                    evidence="Reasonable test and more than thirty-five characters of provenance.")
