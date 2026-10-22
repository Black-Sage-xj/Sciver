"""Show authoritative approved Investigator budget review count."""
import unittest
from bs4 import BeautifulSoup
from tm_observer.run_views import _investigation_card


class BudgetReviewBadgeTests(unittest.TestCase):
    def test_budget_approval_count_without_guessing_from_amount(self):
        raw="Attempt          1\nInv tokens       59,917.7k / 100,122.5k\nCurrent executor Investigator"
        once=BeautifulSoup(_investigation_card(raw,{"reviews":1}),"html.parser")
        self.assertEqual(once.select_one(".tm-budget-review-count").text,"已追加一次")
        self.assertIn("100,122.5k",once.get_text())
        twice=BeautifulSoup(_investigation_card(raw,{"reviews":2}),"html.parser")
        self.assertEqual(twice.select_one(".tm-budget-review-count").text,"已追加二次")
        for budget in (None,{"reviews":0},{"reviews":"1"}):
            self.assertIsNone(BeautifulSoup(_investigation_card(raw,budget),"html.parser").select_one(".tm-budget-review-count"))
