"""No-model regressions for independent Reviewer prompt/tools and read-only UI."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup
from tm_observer.budget_review_trace import review_trace
from tm_observer.budget_review_ui import render_budget_review_template
from tm_observer.run_views import run_detail_body


def review():
    return {
        "schema":"theory-master/investigator-budget-review/v1",
        "at":"2026-10-12T18:09:01+08:00", "cycle_id":"70",
        "review_index":2,"decision":"approve","tranche_tokens":50_000_000,
        "prior_allowance":100_122_468,"observed_tokens":100_437_171,
        "new_allowance":150_122_468,"reason":"An independently verified positive result and a bounded followup test.",
        "next_bounded_test":"Run twelve blinded networks and check the frozen criterion.",
        "evidence_ids":[],"progress_refs":["conversation-sha256:foo"],
    }


def seeded(folder: Path, *, with_trace: bool=True):
    root=folder/"research"
    budget=root/"70"/"budget"
    budget.mkdir(parents=True)
    row=review()
    (budget/"investigator_reviews.jsonl").write_text(json.dumps(row)+"\n")
    receipt={
        "schema":"theory-master/internal-budget-review/v1",
        "review_index":2,"decision":"approve","reason":row["reason"],
        "reviewer_trace":{"status":"available","file":"reviewer_dialogue_0002_"+("a"*32)+".json"} if with_trace else None,
    }
    (budget/"internal_review_0002.json").write_text(json.dumps(receipt))
    run={"observer_id":"0123456789abcdef","research_root":str(root),
         "cycle_dir":str(root/"70"),"cycle":"70"}
    if with_trace:
        entries=[]
        kinds=["usermessage","agentmessage","commandexecution","functioncalloutput"]
        for n in range(18):
            entry={"turn_index":n//3+1,"item_index":n+1,"item_type":kinds[n%4],
                   "payload":{"type":kinds[n%4],"content":"message-"+str(n),
                              "password":"do-not-expose","tool_result":{"api_key":"hidden-secret","stdout":"good-output"}}}
            entries.append(json.dumps(entry))
        source="reviewer_visible_thread_0002_"+("b"*32)+".jsonl"
        (budget/source).write_text("\n".join(entries)+"\n")
        (budget/receipt["reviewer_trace"]["file"]).write_text(json.dumps({
            "schema":"theory-master/budget-reviewer-dialogue/v1",
            "review_index":2,
            "model":"gpt-6-luna","provider":"CodexHTTPS","thread_id":"reviewer-uuid",
            "prompt":"Reviewer launch Context: first prompt",
            "model_output":"{\"decision\":\"approve\"}",
            "visible_thread":{"status":"available","file":source,"visible_items":18,
                              "excluded_private_items":1,"archive_truncated":False},
        }))
    return run


class BudgetReviewerTraceTests(unittest.TestCase):
    def test_live_reviewer_distinct_from_investigator_thread(self):
        with tempfile.TemporaryDirectory() as temp:
            run=seeded(Path(temp))
            first=review_trace(run,cycle="70",review_index=2)
            self.assertTrue(first["available"])
            self.assertEqual(first["model"],"gpt-6-luna")
            self.assertEqual(first["prompt"],"Reviewer launch Context: first prompt")
            self.assertEqual(first["thread_id"],"reviewer-uuid")
            self.assertEqual(len(first["messages"]),15)
            self.assertEqual(first["next_cursor"],15)
            self.assertEqual(first["messages"][2]["type"],"commandexecution")
            self.assertIn("good-output",first["messages"][2]["content"])
            self.assertNotIn("do-not-expose",json.dumps(first))
            self.assertNotIn("hidden-secret",json.dumps(first))
            second=review_trace(run,cycle="70",review_index=2,cursor=15)
            self.assertEqual(len(second["messages"]),3)
            self.assertIsNone(second["next_cursor"])
            complete=review_trace(run,cycle="70",review_index=2,full_item=16)
            self.assertEqual(complete["messages"][0]["index"],16)

    def test_historical_cycle70_without_reviewer_archive_cannot_fabricate(self):
        with tempfile.TemporaryDirectory() as temp:
            run=seeded(Path(temp),with_trace=False)
            data=review_trace(run,cycle="70",review_index=2)
            self.assertFalse(data["available"])
            self.assertIn("Investigator",data["notice"])
            self.assertNotIn("prompt",data)

    def test_untrusted_path_and_cycle_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            run=seeded(Path(temp))
            budget=Path(run["cycle_dir"])/"budget"
            receipt=budget/"internal_review_0002.json"
            contents=json.loads(receipt.read_text())
            for value in ["../../private.json","/etc/passwd"]:
                contents["reviewer_trace"]["file"]=value
                receipt.write_text(json.dumps(contents))
                self.assertFalse(review_trace(run,cycle="70",review_index=2)["available"])
            contents["reviewer_trace"]["file"]="reviewer_dialogue_0002_"+("a"*32)+".json"
            receipt.write_text(json.dumps(contents))
            saved=budget/contents["reviewer_trace"]["file"]
            saved.unlink()
            saved.symlink_to(Path(temp)/"private.json")
            self.assertFalse(review_trace(run,cycle="70",review_index=2)["available"])
            for index,cycle in [(99,"70"),(2,"../70"),(2,"71")]:
                with self.assertRaises((ValueError,FileNotFoundError,OSError)):
                    review_trace(run,cycle=cycle,review_index=index)
            with self.assertRaises(ValueError):
                review_trace(run,cycle="70",review_index=2,cursor=-1)
            with self.assertRaises(ValueError):
                review_trace(run,cycle="70",review_index=2,full_item=100_000)

    def test_budget_dialog_has_lazy_trace_selector_and_escapes_untrusted_ledger(self):
        row=review()
        row["reason"]="<img src=x onerror=alert(1)>"
        html=render_budget_review_template([row],"70")
        dom=BeautifulSoup(html,"html.parser")
        trace=dom.select_one('[data-budget-trace-load]')
        self.assertEqual(trace["data-budget-trace-review"],"2")
        self.assertEqual(trace["data-budget-trace-cycle"],"70")
        self.assertNotIn("<img",html)
        self.assertIn("&lt;img",html)
        with tempfile.TemporaryDirectory() as temp:
            run=seeded(Path(temp))
            markup=run_detail_body(dict(run,task_name="T",state="LIVE",run_id=1))
            self.assertIn("/budget-review-trace",markup)
            self.assertIn("加载下一批消息及工具结果",markup)
            self.assertIn("查看此条完整归档",markup)


if __name__=="__main__":
    unittest.main()
