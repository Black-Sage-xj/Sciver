"""Read-only Investigator budget-approval modal regression tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from tm_observer.budget_review_ui import (
    read_budget_reviews, render_budget_review_template,
)
from tm_observer.run_views import _investigation_card, run_detail_body


def _review(**kwargs):
    row = {
        "schema": "theory-master/investigator-budget-review/v1",
        "at": "2026-10-12T12:46:58+08:00",
        "cycle_id": "70",
        "review_index": 1,
        "decision": "approve",
        "tranche_tokens": 50_000_000,
        "prior_allowance": 50_000_000,
        "observed_tokens": 50_122_468,
        "new_allowance": 100_122_468,
        "reason": "An independent test was partially fulfilled but needs further work.",
        "next_bounded_test": "Run the frozen test on twelve qualifying networks.",
        "evidence_ids": ["E-70-1"],
        "progress_refs": ["conversation-sha256:1-377:abc"],
    }
    row.update(kwargs)
    return row


class BudgetReviewModalTests(unittest.TestCase):

    def test_missing_or_symlink_ledger_does_not_follow_external_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "research"
            cycle = root / "70"
            budget = cycle / "budget"
            budget.mkdir(parents=True)
            external = Path(folder) / "private.jsonl"
            external.write_text(json.dumps(_review()) + "\n")
            (budget / "investigator_reviews.jsonl").symlink_to(external)
            run = {"research_root": str(root), "cycle_dir": str(cycle)}
            self.assertEqual(read_budget_reviews(run), [])
            (budget / "investigator_reviews.jsonl").unlink()
            (budget / "investigator_reviews.jsonl").write_text("invalid JSON\n")
            self.assertEqual(read_budget_reviews(run), [])
            self.assertEqual(read_budget_reviews({
                "research_root": str(root), "cycle_dir": str(external.parent)}), [])

    def test_multiple_tranches_and_rejected_review_have_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cycle = root / "70"
            budget = cycle / "budget"
            budget.mkdir(parents=True)
            entries = [
                _review(),
                _review(review_index=2, decision="reject",
                        prior_allowance=100_122_468,
                        observed_tokens=100_122_468,
                        new_allowance=100_122_468,
                        reason="Another full grant has no sufficient new progress."),
            ]
            (budget / "investigator_reviews.jsonl").write_text(
                "\n".join(json.dumps(x) for x in entries))
            rows = read_budget_reviews({"research_root": str(root),
                                        "cycle_dir": str(cycle)})
            self.assertEqual(len(rows), 2)
            dom = BeautifulSoup(render_budget_review_template(rows, "70"),
                                "html.parser")
            self.assertEqual(len(dom.select("[data-budget-review-select]")), 2)
            self.assertEqual([b["aria-pressed"] for b in dom.select(
                "[data-budget-review-select]")], ["false", "true"])
            panels = dom.select("[data-budget-review-panel]")
            self.assertEqual(len(panels), 2)
            self.assertTrue(panels[0].has_attr("hidden"))
            self.assertFalse(panels[1].has_attr("hidden"))
            self.assertIn("审批未通过", str(panels[1]))
            self.assertIn("Evidence ID", str(panels[0]))
            self.assertIn("E-70-1", str(panels[0]))
            self.assertIn("conversation-sha256", str(panels[0]))
            self.assertIn("完整审批账本原文", str(panels[1]))

    def test_special_characters_escaped_and_secrets_scrubbed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cycle = root / "70"
            budget = cycle / "budget"
            budget.mkdir(parents=True)
            row = _review(reason='<img src=x onerror=alert(1)>',
                          next_bounded_test='test "against" threshold',
                          api_key="do-not-expose")
            (budget / "investigator_reviews.jsonl").write_text(json.dumps(row))
            rows = read_budget_reviews({"research_root": str(root),
                                        "cycle_dir": str(cycle)})
            html = render_budget_review_template(rows, "70")
            self.assertNotIn("<img", html)
            self.assertIn("&lt;img", html)
            self.assertNotIn("do-not-expose", html)
            self.assertIn("[redacted]", html)
            self.assertIn("&quot;against&quot;", html)

    def test_badge_is_button_and_readonly_modal_outside_live_host(self):
        budget = {"reviews": 1, "allowance_tokens": 100_122_468}
        raw = ("Attempt          2\nCurrent executor Investigator\n"
               "Inv tokens       77,178.2k / 100,122.5k")
        content = _investigation_card(raw, budget, [_review()], cycle="70")
        dom = BeautifulSoup(content, "html.parser")
        opener = dom.select_one("button[data-budget-review-open]")
        self.assertIsNotNone(opener)
        self.assertEqual(opener.get_text(), "已追加一次")
        self.assertEqual(opener["aria-haspopup"], "dialog")
        self.assertEqual(opener["aria-controls"], "tm-investigator-budget-dialog")
        self.assertIsNotNone(dom.select_one("template[data-budget-review-template]"))
        self.assertIn("调查轮次与原始记录", content)
        only_rejected = _investigation_card(raw, budget, [
            _review(decision="reject", new_allowance=50_000_000)], cycle="70")
        self.assertIn("已审查一次", only_rejected)
        unavailable = _investigation_card(raw, budget, [], cycle="70")
        self.assertIn("暂不可读取", unavailable)
        self.assertIn("已追加一次", unavailable)

    def test_run_page_modal_refresh_and_keyboard_handlers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cycle = root / "70"
            budget = cycle / "budget"
            budget.mkdir(parents=True)
            (budget / "investigator_reviews.jsonl").write_text(
                json.dumps(_review()) + "\n")
            (cycle / "run_state.json").write_text(json.dumps({
                "cycle_id": "70", "status": "RUNNING",
                "investigator_budget": {
                    "reviews": 1, "allowance_tokens": 100_122_468},
                "action_history": [],
            }))
            run = {"observer_id": "8dd38a9977eef7e5",
                   "research_root": str(root),
                   "cycle_dir": str(cycle), "cycle": "70",
                   "task_name": "Test", "state": "LIVE", "run_id": 1}
            html = run_detail_body(run)
            dom = BeautifulSoup(html, "html.parser")
            modal = dom.select_one("dialog#tm-investigator-budget-dialog")
            self.assertIsNotNone(modal)
            self.assertNotIn(modal, dom.select_one("#tm-run-live").descendants)
            self.assertIn("content.replaceChildren(source.content.cloneNode(true))",
                          html)
            self.assertIn("budgetDialog.showModal()", html)
            self.assertIn("budgetDialog.close()", html)
            self.assertIn("budgetOpener?.isConnected", html)
            self.assertIn("budgetDialog.querySelectorAll('[data-budget-review-panel]')",
                          html)
            self.assertIn("oldPanes[n].innerHTML=newPanes[n].innerHTML", html)


if __name__ == "__main__":
    unittest.main()
