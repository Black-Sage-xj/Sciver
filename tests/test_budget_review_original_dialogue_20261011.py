"""Budget Reviewer reads original native Goal dialogue and real files autonomously.

All tests are offline. No Codex model/Goal run, no real budget mutation.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from theory_master.budget_review_transcript import (
    _dialogue, conversation_review_progress, dialogue_source,
    verified_conversation_refs, write_sdk_thread_snapshot,
)
from theory_master.codex_backend import CodexBackend
from theory_master.internal_budget_review import _pending_progress, review_budget_in_sdk
from theory_master.investigator_budget import budget_status, apply_review, LEDGER_REL, workspace_review_progress


def recorded(cycle: Path, event: str, *, content: str = "", tool: str = "", detail: str = "", agent: str = "investigator") -> None:
    path = cycle / "runtime_events.jsonl"
    row = {"ts": "2026-10-11T04:00:00+08:00", "agent": agent, "event": event, "thread_id": "frozen-goal-51"}
    if content:
        row["content"] = content
    if tool:
        row["tool"] = tool
    if detail:
        row["detail"] = detail
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def fixture(root: Path) -> tuple[Path, dict]:
    research = root / "task/.theory-master/research"
    cycle = research / "51"
    (cycle / "workspace/response_blind_prestress").mkdir(parents=True)
    state = {
        "research_root": str(research),
        "status": "needs_attention", "resumable": True,
        "attention": {"code": "investigator_budget_review_required", "thread_id": "frozen-goal-51"},
        "agents": {"investigator": {"tokens_total": 50_160_851}},
        "threads": {"investigator": "frozen-goal-51"},
        "hypothesis_lock": {
            "id": "H-0051", "hypothesis": "Predict signed prestress without response",
            "decisive_test": "Freeze formula and evaluate on independent networks",
        },
    }
    (cycle / "run_state.json").write_text(json.dumps(state))
    (cycle / "research_state.json").write_text(json.dumps(state))
    recorded(cycle, "AGENT_INPUT", content="Freeze full hypothesis and objective")
    recorded(cycle, "LLM_MESSAGE", content="First derive exact Green identity.")
    recorded(cycle, "TOOL_ACTIVITY", tool="command", detail="python exact_derivation.py")
    recorded(cycle, "TOKEN_USAGE", content="50000000")
    recorded(cycle, "LLM_MESSAGE", content="First seven networks fail support: change strategy.")
    recorded(cycle, "TOOL_ACTIVITY", tool="command", detail="python derive_covariance.py")
    return cycle, state


class BudgetReviewOriginalDialogueTests(unittest.TestCase):
    def test_primary_original_dialogue_is_unabridged_and_excludes_billing(self):
        with tempfile.TemporaryDirectory() as root:
            cycle, _ = fixture(Path(root))
            rows = _dialogue(cycle)
            self.assertEqual([r["event"] for r in rows], [
                "LLM_MESSAGE", "TOOL_ACTIVITY", "LLM_MESSAGE", "TOOL_ACTIVITY",
            ])
            self.assertEqual(dialogue_source(cycle)["assistant_messages"], 2)
            self.assertEqual(dialogue_source(cycle)["tool_activities"], 2)
            marker = conversation_review_progress(cycle)[0]
            self.assertEqual(marker["first_event"], 1)
            self.assertEqual(marker["last_event"], 4)
            self.assertIn(marker["invocation_id"], verified_conversation_refs(cycle, [marker["invocation_id"]]))
            self.assertTrue(marker["invocation_id"].startswith("conversation-sha256:1-4:"))

    def test_reviewed_dialogue_cannot_approve_twice_without_new_events(self):
        with tempfile.TemporaryDirectory() as root:
            cycle, state = fixture(Path(root))
            progress = conversation_review_progress(cycle)[0]["invocation_id"]
            first = apply_review(cycle,
                decision="approve", reason="New physical derivation with a grounded and worthwhile next test.",
                next_test="Independently validate the derived model and inspect common-support failures.",
                evidence_ids=[], progress_refs=[progress])
            self.assertEqual(first["decision"], "approve")
            self.assertEqual(first["new_allowance"], 100_000_000)
            self.assertFalse(budget_status(cycle, cycle.parent, state)["exhausted"])
            self.assertEqual(conversation_review_progress(cycle, [progress]), [])
            state["agents"]["investigator"]["tokens_total"] = 105_000_000
            (cycle / "run_state.json").write_text(json.dumps(state))
            (cycle / "research_state.json").write_text(json.dumps(state))
            with self.assertRaisesRegex(ValueError, "unregistered Investigator progress|previously reviewed progress"):
                apply_review(cycle,
                    decision="approve", reason="Repeat prior report without genuinely new scientific work.",
                    next_test="Repeat the exact same simulation using the same old roots and seed.",
                    evidence_ids=[], progress_refs=[progress])
            recorded(cycle, "TOKEN_USAGE", content="105000000")
            self.assertEqual(conversation_review_progress(cycle, [progress]), [])
            recorded(cycle, "LLM_MESSAGE", content="New independent analytic bound after the reviewed segment.")
            new_ref = conversation_review_progress(cycle, [progress])[0]["invocation_id"]
            self.assertTrue(new_ref.startswith("conversation-sha256:5-5:"))
            renewed = apply_review(cycle,
                decision="approve", reason="One new reviewable analytic bound supplies an actionable physics test.",
                next_test="Check the new conditional stress bound against independently prepared data.",
                evidence_ids=[], progress_refs=[new_ref])
            self.assertGreater(renewed["new_allowance"], first["new_allowance"])

    def test_tampered_or_invented_conversation_ref_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            cycle, _ = fixture(Path(root))
            candidate = conversation_review_progress(cycle)[0]["invocation_id"]
            bad = candidate[:-1] + ("0" if candidate[-1] != "0" else "1")
            self.assertEqual(verified_conversation_refs(cycle, [bad]), set())
            with self.assertRaisesRegex(ValueError, "unregistered Investigator progress"):
                apply_review(cycle,
                    decision="approve", reason="The source should be verified before spending additional tokens.",
                    next_test="Derive an independent prediction with no response leakage.",
                    evidence_ids=[], progress_refs=[bad])
            self.assertFalse((cycle / LEDGER_REL).exists())
            path = cycle / "runtime_events.jsonl"
            path.write_text(path.read_text().replace("seven networks", "six networks"))
            self.assertEqual(verified_conversation_refs(cycle, [candidate]), set())

    def test_prior_reviewed_segment_tampering_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            cycle, _ = fixture(Path(root))
            old_ref = conversation_review_progress(cycle)[0]["invocation_id"]
            path = cycle / "runtime_events.jsonl"
            path.write_text(path.read_text().replace("seven networks", "six networks"))
            with self.assertRaisesRegex(ValueError, "previously reviewed Investigator conversation was modified"):
                conversation_review_progress(cycle, [old_ref])

    def test_corrupted_dialogue_does_not_silently_return_zero_progress(self):
        with tempfile.TemporaryDirectory() as root:
            cycle, _ = fixture(Path(root))
            with (cycle / "runtime_events.jsonl").open("a") as out:
                out.write("{broken json\n")
            with self.assertRaisesRegex(ValueError, "invalid Investigator dialogue"):
                conversation_review_progress(cycle)

    def test_arbitrary_scientific_workspace_directory_is_reviewable(self):
        with tempfile.TemporaryDirectory() as root:
            cycle, state = fixture(Path(root))
            scientific = cycle / "workspace/response_blind_prestress/new_original_report.md"
            scientific.write_text("Derived covariance closure with actual statistical tests\n")
            result = workspace_review_progress(cycle)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["artifact"], "response_blind_prestress/new_original_report.md")
            scope = _pending_progress(cycle, state)
            self.assertTrue(any(
                x["kind"] == "unfinished_native_goal_original_dialogue_not_finding"
                for x in scope["progress"]
            ))
            self.assertTrue(any(x.get("artifact") == "response_blind_prestress/new_original_report.md"
                                for x in scope["progress"]))

    def test_independent_tool_using_reviewer_can_approve_unfinished_goal(self):
        with tempfile.TemporaryDirectory() as root:
            cycle, state = fixture(Path(root))
            report = cycle / "workspace/response_blind_prestress/input_and_error_audit.md"
            report.write_text("Checked exact Green linear prestress on independent real networks.\n")
            observed = []
            def fake_review(prompt: str, model: str, provider: str):
                observed.append((prompt, model, provider))
                content = json.loads(prompt.split("\nContext:\n", 1)[1])
                dialogue = content["source_access"]["original_dialogue"]
                self.assertEqual(dialogue["assistant_messages"], 2)
                self.assertEqual(dialogue["tool_activities"], 2)
                self.assertEqual(content["source_access"]["workspace_root"], str((cycle / "workspace").resolve()))
                self.assertIn("read-only", content["policy"])
                refs = content["new_unreviewed_progress_references_not_scientific_verdicts"]
                marker = next(x["invocation_id"] for x in refs if x.get("kind")=="unfinished_native_goal_original_dialogue_not_finding")
                return ("independent-review-thread", json.dumps({
                    "decision": "approve",
                    "reason": "The original conversation shows a genuine new derivation with reproducible calculations.",
                    "next_test": "Verify the discovered prestress covariance on independent network data.",
                    "evidence_ids": [], "progress_refs": [marker],
                }))
            backend = SimpleNamespace(
                invoke_budget_review=fake_review, drop_thread=MagicMock(),
                snapshot_budget_review_thread=lambda tid, dest: {
                    "status": "unavailable", "reason": "historical native thread truncated",
                },
                last_budget_review_reads=[str(cycle/"runtime_events.jsonl"), str(report)],
            )
            grant = review_budget_in_sdk(backend=backend, cycle_dir=cycle, state=state,
                                         goal="Goal", research_contract="Contract", final_gate="Final Gate")
            self.assertEqual(grant["decision"], "approve")
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0][1:], ("gpt-6-luna", "CodexHTTPS"))
            backend.drop_thread.assert_called_once_with("independent-review-thread")
            receipt = json.loads((cycle / "budget/internal_review_0001.json").read_text())
            self.assertEqual(receipt["research_sources"]["original_dialogue"]["assistant_messages"], 2)
            self.assertIn(str(report), receipt["read_paths_reported_by_sdk"])

    def test_missing_all_sources_is_operational_failure_not_scientific_rejection(self):
        with tempfile.TemporaryDirectory() as root:
            cycle, state = fixture(Path(root))
            (cycle / "runtime_events.jsonl").unlink()
            backend = SimpleNamespace(
                invoke_budget_review=MagicMock(), drop_thread=MagicMock(),
            )
            with self.assertRaisesRegex(RuntimeError, "no readable native Goal dialogue"):
                review_budget_in_sdk(backend=backend, cycle_dir=cycle, state=state,
                                     goal="Goal", research_contract="Contract", final_gate="Final Gate")
            backend.invoke_budget_review.assert_not_called()
            self.assertFalse((cycle / LEDGER_REL).exists())

    def test_provider_snapshot_includes_visible_tool_results_not_private_reasoning(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root) / "snapshot.jsonl"
            response = {
                "thread": {"turns": [
                    {"id": "t1", "items": [
                        {"root": {"type": "userMessage", "content": "FROZEN objective"}},
                        {"root": {"type": "agentMessage", "text": "Derived Green"} },
                        {"root": {"type": "reasoning", "text": "PRIVATE internal thought"}},
                        {"root": {"type": "commandExecution", "command": ["python", "check.py"],
                                  "aggregated_output": "root excluded, exact sum confirmed"}},
                        {"root": {"type": "functionCallOutput", "output": "real result 5.2"}},
                    ]},
                ]},
            }
            meta = write_sdk_thread_snapshot(response, thread_id="goal-51", destination=out)
            raw = out.read_text()
            self.assertIn("root excluded, exact sum confirmed", raw)
            self.assertIn("real result 5.2", raw)
            self.assertNotIn("PRIVATE internal thought", raw)
            self.assertEqual(meta["excluded_private_items"], 1)
            self.assertEqual(meta["visible_items"], 4)
            self.assertEqual(meta["sha256"], hashlib.sha256(out.read_bytes()).hexdigest())
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)

    def test_provider_sdk_snapshot_never_starts_or_resumes_investigator(self):
        with tempfile.TemporaryDirectory() as root:
            backend = object.__new__(CodexBackend)
            mock_client = MagicMock()
            mock_client._client.thread_read.return_value = {"thread": {"turns": []}}
            backend._ensure_client = MagicMock(return_value=mock_client)
            output = backend.snapshot_budget_review_thread("same-native-goal-thread",
                                                            Path(root)/"thread.jsonl")
            mock_client._client.thread_read.assert_called_once_with("same-native-goal-thread", include_turns=True)
            mock_client.thread_start.assert_not_called()
            mock_client.thread_resume.assert_not_called()
            self.assertEqual(output["status"], "available")
            self.assertTrue((Path(root)/"thread.jsonl").exists())
            from openai_codex import Sandbox
            self.assertEqual(CodexBackend._thread_sandbox("budget_reviewer"), Sandbox.read_only)


if __name__ == "__main__":
    unittest.main()
