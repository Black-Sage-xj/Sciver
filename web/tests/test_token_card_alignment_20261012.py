"""Static Agent table UI regression: aligned controls and honest empty usage."""
import unittest

from bs4 import BeautifulSoup

from tm_observer.dashboard_ui import STYLE
from tm_observer.run_views import _tui_workflow_table, _usage_cell


class AgentTableAlignmentTests(unittest.TestCase):
    def test_unmeasured_literature_has_noninteractive_token_card(self):
        box = BeautifulSoup(_usage_cell({"project": {"tokens_total": None},
                                         "cycle": {"tokens_total": None}}), "html.parser")
        card = box.select_one("div.tm-usage-hit.tm-usage-hit-empty")
        self.assertIsNotNone(card)
        self.assertEqual(card.select_one(".tm-usage-value").get_text(), "未记录")
        self.assertEqual(card.select_one(".tm-usage-cycle").get_text(), "本轮未记录")
        self.assertIsNone(card.get("data-token-detail"))
        self.assertIsNone(box.select_one("button"))
        self.assertIsNone(card.get("tabindex"))
        self.assertEqual(card["aria-label"], "Token 用量未记录")

    def test_measured_tokens_still_use_real_clickable_details(self):
        box = BeautifulSoup(_usage_cell({"project": {"tokens_total": 2710, "tokens_in": 2600},
                                         "cycle": {"tokens_total": 310}}), "html.parser")
        button = box.select_one("button.tm-usage-hit[data-token-detail]")
        self.assertIsNotNone(button)
        self.assertNotIn("tm-usage-hit-empty", button["class"])
        self.assertEqual(button.select_one(".tm-usage-value").get_text(), "2,710")
        self.assertEqual(button.select_one(".tm-usage-cycle").get_text(), "本轮 310")

    def test_unmeasured_literature_in_six_column_table(self):
        names = ("Research Lead", "Challenger", "Investigator", "Literature", "Judge", "Reporter")
        raw = 'AGENT/STAGE    MODEL                         COST      TIME     STATE\n'
        raw += "\n".join(
            f'{name:<13}  {"GPT-6 Luna · low":<24}  {"—":>8}  {"00:00":>8}  '
            f'{("idle" if name == "Literature" else "done"):>8}'
            for name in names
        )
        page = BeautifulSoup(_tui_workflow_table(raw, settings={"literature": {
            "model": "gpt-6-luna", "provider": "CodexHTTPS", "reasoning_effort": "low"}},
            catalog=[{"model": "gpt-6-luna", "provider": "CodexHTTPS", "label": "GPT-6 Luna"}]),
            "html.parser")
        row = page.select_one("tr[data-agent-role=literature]")
        self.assertIsNotNone(row)
        self.assertEqual(len(row.select("td")), len(page.select("thead th")))
        self.assertEqual(len(row.select("td")), 6)
        self.assertEqual(row.select_one(".tm-tui-agent-state").get_text(), "idle")
        self.assertEqual(row.select_one("[data-route-model] option[selected]")["value"],
                         "gpt-6-luna@CodexHTTPS")
        self.assertIsNotNone(row.select_one(".tm-usage-cell .tm-usage-hit-empty"))

    def test_presentation_css_caps_selector_and_aligns_numeric_cells(self):
        self.assertIn(".tm-col-model { width:155px; }", STYLE)
        self.assertIn("max-width:155px; width:100%;", STYLE)
        self.assertIn("[data-route-effort] { display:block; margin-left:auto;", STYLE)
        self.assertIn("max-width:160px; margin-left:auto;", STYLE)
        self.assertIn(".tm-usage-hit-empty { cursor:default; }", STYLE)


if __name__ == "__main__":
    unittest.main()
