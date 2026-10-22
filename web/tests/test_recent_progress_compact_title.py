"""Recent Progress heading contains no redundant scroll-instruction subtitle."""
import unittest

from bs4 import BeautifulSoup

from tm_observer.run_views import run_detail_body


class RecentProgressCompactTitleTests(unittest.TestCase):
    def test_heading_only_and_existing_scrollable_region_still_present(self):
        run = {"observer_id": "aaaaaaaaaaaaaaaa", "task_name": "Fixture",
               "cycle": "1", "state": "FAILED"}
        soup = BeautifulSoup(run_detail_body(run), "html.parser")
        panel = soup.select_one(".tm-tui-history-panel")
        self.assertIsNotNone(panel)
        title = panel.select_one(".tm-tui-section-title")
        self.assertIsNotNone(title)
        # The heading is a compact switch for all supported history views;
        # the third exception-history entry is intentional, not redundant subtitle text.
        self.assertEqual([tab.get_text(" ",strip=True)
                          for tab in title.select('[data-history-view]')],
                         ["最近进展", "重要事件", "异常记录"])
        self.assertEqual(title.select("span"), [])
        self.assertEqual(title["role"], "tablist")
        from tm_observer.run_views import _recent_timeline
        history = BeautifulSoup(_recent_timeline("21:53:18  Investigator done 09:19"), "html.parser")
        self.assertIsNotNone(history.select_one(".tm-run-events-scroll"))
        self.assertNotIn("全部记录 · 区域内滚动", str(panel))
