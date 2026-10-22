"""Run progress deep links navigate within the current tab by default."""
import unittest
from bs4 import BeautifulSoup

from tm_observer.run_views import _recent_timeline


class RecentProgressSameTabTests(unittest.TestCase):
    def test_link_reuses_current_tab_and_preserves_exact_event_identity(self):
        stamp = "2026-10-11T21:53:18+08:00"
        row = "21:53:18  Investigator · skill loaded · theory-investigation"
        source = [{"at": stamp, "kind": "SKILL_LOADED",
                   "role": "investigator",
                   "hint": "Investigator · skill loaded · theory-investigation"}]
        page = BeautifulSoup(_recent_timeline(row, source), "html.parser")
        link = page.select_one("a.tm-recent-select")
        self.assertIsNotNone(link)
        self.assertNotIn("target", link.attrs)
        self.assertNotIn("rel", link.attrs)
        self.assertIn("当前页面", link.get("title", ""))
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(link["href"]).query)
        self.assertEqual(query["tab"], ["events"])
        self.assertEqual(query["at"], [stamp])
        self.assertEqual(query["kind"], ["SKILL_LOADED"])
        self.assertEqual(query["role"], ["investigator"])
        self.assertEqual(query["hint"], [source[0]["hint"]])
