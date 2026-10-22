"""Offline integration for Reviewer tool-thread snapshot before thread cleanup."""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from theory_master.internal_budget_review import review_budget_in_sdk
from tests.test_investigator_budget_review import InvestigatorBudgetReviewTests
from tm_observer.budget_review_trace import review_trace


class ReviewerTranscriptCaptureTests(unittest.TestCase):
    def test_prompt_keeps_recent_verified_reports_bounded(self):
        from unittest.mock import patch
        from theory_master.internal_budget_review import _pending_progress, budget_review_prompt
        with tempfile.TemporaryDirectory() as tmp:
            _research, cycle, state = InvestigatorBudgetReviewTests().setup_cycle(Path(tmp))
            # The real workspace reader returns newest first. Do not silently
            # expose the oldest 24 sources when a Native Goal is very long.
            rows = [
                {"invocation_id": f"workspace-sha256:r{i:02d}.md:{i:064x}",
                 "kind": "unfinished_workspace_scientific_report_not_finding",
                 "artifact": f"r{i:02d}.md", "sha256": f"{i:064x}",
                 "summary": "Verified independent workspace report"}
                for i in range(20)
            ]
            with patch("theory_master.internal_budget_review.workspace_review_progress", return_value=rows):
                scope = _pending_progress(cycle, state)
            selected = [r for r in scope["progress"] if r.get("artifact", "").startswith("r")]
            self.assertEqual(len(selected), 12)
            self.assertEqual(selected[0]["artifact"], "r00.md")
            self.assertEqual(selected[-1]["artifact"], "r11.md")
            self.assertIn("at most 10 targeted", budget_review_prompt({"new_unreviewed_progress_references_not_scientific_verdicts": scope["progress"]}))

    def test_sdk_capture_uses_actual_reviewer_thread_before_drop(self):
        with tempfile.TemporaryDirectory() as tmp:
            research,cycle,state=InvestigatorBudgetReviewTests().setup_cycle(Path(tmp))
            observed=[]
            def invoke(prompt,model,provider):
                observed.append(("invoke",model,provider,prompt))
                return "fresh-reviewer-thread",json.dumps({
                    "decision":"approve",
                    "reason":"Registered exact physical result provides meaningful independent progress.",
                    "next_test":"Verify the same result against a second independent frozen input.",
                    "evidence_ids":["E-1"],"progress_refs":[],
                })
            def capture(thread,destination):
                observed.append(("capture",thread))
                destination.write_text(json.dumps({
                    "turn_index":1,"item_index":1,"item_type":"commandexecution",
                    "payload":{"stdout":"checked evidence file","type":"commandexecution"},
                })+"\n")
                return {"status":"available","visible_items":1,
                        "excluded_private_items":2,"archive_truncated":False,
                        "sha256":"ab"*32}
            backend=SimpleNamespace(invoke_budget_review=invoke,
                                    snapshot_budget_review_thread=capture,
                                    drop_thread=lambda tid:observed.append(("drop",tid)),
                                    last_budget_review_reads=[])
            decision=review_budget_in_sdk(backend=backend,cycle_dir=cycle,state=state,
                                          goal="test goal",research_contract="known contract",
                                          final_gate="frozen criterion")
            self.assertEqual(decision["decision"],"approve")
            self.assertEqual([x[0] for x in observed],["capture","invoke","capture","drop"])
            self.assertEqual(observed[0][1],"fixed-scientific-thread")
            self.assertEqual(observed[2][1],"fresh-reviewer-thread")
            receipt=json.loads((cycle/"budget/internal_review_0001.json").read_text())
            self.assertEqual(receipt["reviewer_trace"]["status"],"available")
            path=cycle/"budget"/receipt["reviewer_trace"]["file"]
            raw=json.loads(path.read_text())
            self.assertEqual(raw["thread_id"],"fresh-reviewer-thread")
            self.assertEqual(raw["visible_thread"]["status"],"available")
            self.assertEqual(raw["model_output"][:1],"{")
            self.assertIn("Independent, read-only",raw["prompt"])
            view=review_trace({"research_root":str(research),"cycle_dir":str(cycle)},
                              cycle="1",review_index=1)
            self.assertTrue(view["available"])
            self.assertEqual(view["messages"][0]["type"],"commandexecution")
            self.assertIn("checked evidence file",view["messages"][0]["content"])

    def test_snapshot_failure_does_not_cancel_valid_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            research,cycle,state=InvestigatorBudgetReviewTests().setup_cycle(Path(tmp))
            def capture(*_args):
                raise RuntimeError("provider thread/read not available")
            backend=SimpleNamespace(
                invoke_budget_review=lambda *_: ("thread-a",json.dumps({
                    "decision":"approve","reason":"Real verified progress on the bounded hypothesis.",
                    "next_test":"Validate the registered result with a second controlled test.",
                    "evidence_ids":["E-1"],"progress_refs":[],
                })),
                snapshot_budget_review_thread=capture,
                drop_thread=lambda *_:None,
                last_budget_review_reads=[])
            record=review_budget_in_sdk(backend=backend,cycle_dir=cycle,state=state,
                                         goal="G",research_contract="R",final_gate="T")
            self.assertEqual(record["decision"],"approve")
            data=review_trace({"research_root":str(research),"cycle_dir":str(cycle)},
                              cycle="1",review_index=1)
            self.assertTrue(data["available"])
            self.assertEqual(data["native_status"],"unavailable")
            self.assertEqual(data["messages"],[])
            self.assertIn("Independent, read-only",data["prompt"])

    def test_technical_retries_never_mint_budget_without_valid_decision(self):
        from theory_master.investigator_budget import budget_status
        cases = (
            ("malformed", [("thread-bad", "not JSON")]),
            ("missing_final", [RuntimeError(
                "Codex backend delivery error: turn completed without assistant final response")]),
        )
        for label, failures in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                research, cycle, state = InvestigatorBudgetReviewTests().setup_cycle(Path(tmp))
                calls = []
                valid = json.dumps({
                    "decision": "approve",
                    "reason": "Real registered physical result supports one bounded additional independent test.",
                    "next_test": "Repeat and verify the reported result against an independent frozen network.",
                    "evidence_ids": ["E-1"], "progress_refs": [],
                })
                responses = list(failures) + [("valid-review", valid)]
                def invoke(*_):
                    calls.append("invoke")
                    response = responses.pop(0)
                    if isinstance(response, Exception):
                        raise response
                    return response
                backend = SimpleNamespace(
                    invoke_budget_review=invoke, drop_thread=lambda *_: None,
                    last_budget_review_reads=[],
                )
                result = review_budget_in_sdk(
                    backend=backend, cycle_dir=cycle, state=state,
                    goal="Goal", research_contract="Contract", final_gate="Gate",
                )
                self.assertEqual(result["decision"], "approve")
                self.assertEqual(len(calls), 2)
                self.assertEqual(len(list((cycle / "budget").glob("technical_retry_*.json"))), 1)
                self.assertFalse(budget_status(cycle, research, state)["exhausted"])
                self.assertEqual(len((cycle / "budget" / "investigator_reviews.jsonl").read_text().splitlines()), 1)

    def test_missing_final_switches_to_stronger_model_only_for_technical_retry(self):
        from theory_master.investigator_budget import budget_status
        with tempfile.TemporaryDirectory() as tmp:
            research, cycle, state = InvestigatorBudgetReviewTests().setup_cycle(Path(tmp))
            seen = []
            valid = json.dumps({
                "decision": "approve",
                "reason": "Independently verified new results justify a bounded continuation under the original frozen test.",
                "next_test": "Check the same scientific mechanism on the predefined independent network cohort.",
                "evidence_ids": ["E-1"], "progress_refs": [],
            })
            def invoke(_prompt, model, provider):
                seen.append((model, provider))
                if len(seen) == 1:
                    raise RuntimeError("Codex backend delivery error: turn completed without assistant final response")
                return "review-thread-b", valid
            backend = SimpleNamespace(
                invoke_budget_review=invoke, drop_thread=lambda *_: None,
                last_budget_review_reads=[],
            )
            result = review_budget_in_sdk(
                backend=backend, cycle_dir=cycle, state=state,
                goal="Goal", research_contract="Contract", final_gate="Gate",
            )
            self.assertEqual(result["decision"], "approve")
            self.assertEqual(seen, [("gpt-6-luna", "CodexHTTPS"),
                                    ("gpt-6-sol", "CodexHTTPS")])
            self.assertEqual(budget_status(cycle, research, state)["reviews"], 1)
            receipts = list((cycle / "budget").glob("reviewer_dialogue_*.json"))
            self.assertEqual(len(receipts), 1)
            self.assertEqual(json.loads(receipts[0].read_text())["model"], "gpt-6-sol")

    def test_malformed_output_does_not_switch_model_or_grant_budget(self):
        from theory_master.investigator_budget import budget_status
        with tempfile.TemporaryDirectory() as tmp:
            research, cycle, state = InvestigatorBudgetReviewTests().setup_cycle(Path(tmp))
            seen = []
            def invoke(_prompt, model, provider):
                seen.append(model)
                return "review-bad", "not valid json"
            backend = SimpleNamespace(
                invoke_budget_review=invoke, drop_thread=lambda *_: None,
                last_budget_review_reads=[],
            )
            with self.assertRaisesRegex(ValueError, "one valid JSON object"):
                review_budget_in_sdk(
                    backend=backend, cycle_dir=cycle, state=state,
                    goal="Goal", research_contract="Contract", final_gate="Gate",
                )
            self.assertEqual(seen, ["gpt-6-luna"] * 3)
            self.assertEqual(budget_status(cycle, research, state)["reviews"], 0)

    def test_all_three_invalid_reviewer_calls_fail_closed(self):
        from theory_master.investigator_budget import budget_status
        with tempfile.TemporaryDirectory() as tmp:
            research, cycle, state = InvestigatorBudgetReviewTests().setup_cycle(Path(tmp))
            calls = []
            def malformed(*_):
                calls.append(True)
                return "bad-review", "still not json"
            backend = SimpleNamespace(
                invoke_budget_review=malformed, drop_thread=lambda *_: None,
                last_budget_review_reads=[],
            )
            with self.assertRaisesRegex(ValueError, "one valid JSON object"):
                review_budget_in_sdk(
                    backend=backend, cycle_dir=cycle, state=state,
                    goal="Goal", research_contract="Contract", final_gate="Gate",
                )
            self.assertEqual(len(calls), 3)
            self.assertEqual(len(list((cycle / "budget").glob("technical_retry_*.json"))), 3)
            self.assertTrue(budget_status(cycle, research, state)["exhausted"])
            self.assertEqual(budget_status(cycle, research, state)["reviews"], 0)

if __name__=="__main__":
    unittest.main()
