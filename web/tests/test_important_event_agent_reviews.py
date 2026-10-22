"""Real-ledger-to-reviewer association without a live LLM or filesystem writes outside fixtures."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from tm_observer.important_events import read_important_events
from tm_observer.attention_review_trace import attention_review_trace
from tm_observer.run_views import _incident_timeline, run_detail_body


class EventReviewerAssociationTests(unittest.TestCase):
    def test_attention_handoff_links_only_exact_incident_and_displays_saved_exchange(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = root / "70", root / "71"
            first.mkdir()
            second.mkdir()
            event = {
                "at": "2026-10-12T16:56:19+08:00",
                "incident_id": "INC-a4f8637d4775e688",
                "lifecycle": "repair_handoff", "severity": "attention",
                "kind": "needs_attention", "code": "investigator_native_goal_blocked",
                "repair": {"target": "sdk:internal_attention", "status": "blocked"},
            }
            (first / "cycle_anomalies.jsonl").write_text(json.dumps(event) + "\n")
            (first / "attention").mkdir()
            receipt = {
                "schema": "theory-master/internal-attention-review/v1",
                "at": event["at"], "incident_id": event["incident_id"],
                "attention_code": event["code"], "model": "gpt-6-sol",
                "action": "hold", "reason": "Original decision from SDK",
                "guidance": "", "exchange": {
                    "prompt": "Genuine reviewer prompt", "model_output": '{"action":"hold"}'
                }
            }
            (first / "attention" / "sdk_internal_review_01.json").write_text(json.dumps(receipt))
            run = {"observer_id": "a"*16, "research_root": str(root),
                   "cycle_dir": str(second), "cycle": "71", "state": "LIVE"}
            events = read_important_events(run)
            self.assertEqual(events[0]["_cycle_name"], "70")
            rendered = BeautifulSoup(_incident_timeline(events, run=run), "html.parser")
            item = rendered.select_one("li.tm-important-item")
            self.assertEqual(item.select_one(".tm-important-meta").find_all(recursive=False)[0].get_text(),
                             "Cycle 70")
            self.assertNotIn("执行需要关注", item.get_text())
            button = item.select_one("[data-important-review-kind='attention']")
            self.assertEqual(button["data-important-review-cycle"], "70")
            self.assertEqual(button["data-important-review-incident"], event["incident_id"])
            result = attention_review_trace(run, cycle="70", incident=event["incident_id"])
            self.assertTrue(result["exchange_recorded"])
            self.assertEqual(result["prompt"], "Genuine reviewer prompt")
            self.assertEqual(result["model_output"], '{"action":"hold"}')
            with self.assertRaises(ValueError):
                attention_review_trace(run, cycle="71", incident=event["incident_id"])
            with self.assertRaises(ValueError):
                attention_review_trace(run, cycle="../70", incident=event["incident_id"])
            with self.assertRaises(ValueError):
                attention_review_trace(run, cycle="70", incident="INC-"+"b"*16)
            self.assertIn("tm-important-review-dialog", run_detail_body(run))

    def test_legacy_receipt_is_not_misreported_as_full_dialogue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle = root / "5"
            cycle.mkdir()
            incident = "INC-" + "c"*16
            row = {"at": "2026-10-12T11:00:00+08:00",
                   "incident_id": incident, "lifecycle":"repair_handoff",
                   "kind":"needs_attention", "severity":"attention",
                   "repair":{"target":"sdk:internal_attention", "status":"blocked"}}
            (cycle / "important_events.jsonl").write_text(json.dumps(row))
            (cycle / "attention").mkdir()
            (cycle / "attention" / "sdk_internal_review_01.json").write_text(json.dumps({
                "schema":"theory-master/internal-attention-review/v1",
                "incident_id":incident, "action":"hold", "reason":"Previous saved decision"
            }))
            run={"research_root":str(root),"cycle_dir":str(cycle)}
            trace=attention_review_trace(run, cycle="5", incident=incident)
            self.assertTrue(trace["available"])
            self.assertFalse(trace["exchange_recorded"])
            self.assertIn("未归档", trace["notice"])
            self.assertEqual(trace["reason"], "Previous saved decision")

    def test_budget_links_are_exact_timestamp_and_cycle_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            previous,current=root/"70",root/"71"
            previous.mkdir()
            current.mkdir()
            when="2026-10-12T18:09:01+08:00"
            event={"at":when,"incident_id":"INC-"+ "d"*16,"kind":"needs_attention",
                   "severity":"attention","code":"investigator_budget_review_required",
                   "lifecycle":"resume_attempt"}
            (previous/"important_events.jsonl").write_text(json.dumps(event))
            (previous/"budget").mkdir()
            approval={"schema":"theory-master/investigator-budget-review/v1",
                      "at":when, "review_index":2, "decision":"approve",
                      "reason":"Saved actual reviewer decision",
                      "prior_allowance":100, "observed_tokens":101,
                      "new_allowance":150,"tranche_tokens":50}
            (previous/"budget"/"investigator_reviews.jsonl").write_text(json.dumps(approval))
            run={"research_root":str(root),"cycle_dir":str(current),"cycle":"71"}
            rendered=BeautifulSoup(_incident_timeline(read_important_events(run),run=run),"html.parser")
            self.assertEqual(rendered.select_one('[data-important-review-kind="budget"]')["data-important-review-index"],"2")
            self.assertEqual(rendered.select_one('[data-important-review-kind="budget"]')["data-important-review-cycle"],"70")
            approval["at"]="2026-10-12T18:09:02+08:00"
            (previous/"budget"/"investigator_reviews.jsonl").write_text(json.dumps(approval))
            rendered=BeautifulSoup(_incident_timeline(read_important_events(run),run=run),"html.parser")
            self.assertIsNone(rendered.select_one('[data-important-review-kind="budget"]'))

    def test_review_refs_prevent_same_incident_cross_talk_and_page_native_turns(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle = root / "6"
            cycle.mkdir()
            archive = cycle / "attention"
            archive.mkdir()
            incident = "INC-" + "e"*16
            refs = [
                "sdk_internal_review_01_" + "a"*32 + ".json",
                "sdk_internal_review_01_" + "b"*32 + ".json",
            ]
            rows = []
            for i, ref in enumerate(refs):
                rows.append({"at": f"2026-10-12T12:00:0{i}+08:00",
                             "incident_id": incident, "kind": "needs_attention",
                             "lifecycle": "repair_handoff", "severity": "attention",
                             "repair": {"target": "sdk:internal_attention",
                                        "review_ref": ref}})
                thread = "reviewer_visible_thread_" + ("a" if i == 0 else "b")*32 + ".jsonl"
                (archive / ref).write_text(json.dumps({
                    "schema": "theory-master/internal-attention-review/v1",
                    "incident_id": incident, "model": "review-model",
                    "action": "hold", "reason": f"Real review {i}",
                    "exchange": {"prompt": f"Prompt {i}", "model_output": f"Output {i}",
                                 "visible_thread": {"status": "available", "file": thread}}
                }))
                messages = [{"turn_index": j+1, "item_index": 1,
                             "item_type": "agentmessage", "payload": f"Turn {i}-{j}"}
                            for j in range(20)]
                (archive / thread).write_text(
                    "\n".join(json.dumps(msg) for msg in messages) + "\n"
                )
            (cycle / "important_events.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n"
            )
            run = {"observer_id": "a"*16, "research_root": str(root),
                   "cycle_dir": str(cycle), "cycle": "6"}
            dom = BeautifulSoup(_incident_timeline(read_important_events(run), run=run), "html.parser")
            buttons = dom.select('[data-important-review-kind="attention"]')
            self.assertEqual([x["data-important-review-ref"] for x in buttons], refs)
            first = attention_review_trace(run, cycle="6", incident=incident, review_ref=refs[0])
            self.assertEqual(first["model_output"], "Output 0")
            self.assertEqual(len(first["messages"]), 15)
            self.assertEqual(first["next_cursor"], 15)
            self.assertIn("多轮", first["native_notice"])
            last = attention_review_trace(run, cycle="6", incident=incident,
                                          review_ref=refs[0], cursor=15)
            self.assertEqual(len(last["messages"]), 5)
            self.assertEqual(last["messages"][0]["content"], "Turn 0-15")
            second = attention_review_trace(run, cycle="6", incident=incident, review_ref=refs[1])
            self.assertEqual(second["model_output"], "Output 1")
            self.assertEqual(second["messages"][0]["content"], "Turn 1-0")
            ambiguous = attention_review_trace(run, cycle="6", incident=incident)
            self.assertFalse(ambiguous["available"])
            with self.assertRaises(ValueError):
                attention_review_trace(run, cycle="6", incident=incident,
                                       review_ref="sdk_internal_review_01_"+"c"*32+".json")
            with self.assertRaises(ValueError):
                attention_review_trace(run, cycle="6", incident=incident,
                                       review_ref="../../secrets.json")


if __name__ == "__main__":
    unittest.main()
