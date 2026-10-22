"""Viewport-height and obsolete-introduction regression for Research Explorer."""
from __future__ import annotations

import unittest

from tm_observer.dashboard_ui import dashboard_shell
from tm_observer.research_explorer_g6 import graph_content


class ExplorerViewportTest(unittest.TestCase):
    def test_graph_and_reader_share_remaining_viewport_height(self):
        page = graph_content()
        self.assertIn("height:calc(100dvh - 62px)", page)
        self.assertIn(".ex-workspace{display:flex;flex-direction:column;flex:1;min-width:0;min-height:0}", page)
        self.assertIn(".tm-explorer-view .ex-layout{display:grid", page)
        self.assertIn("gap:0;flex:1;min-height:0;height:auto", page)
        self.assertIn(".ex-workspace:fullscreen .ex-layout{flex:1;min-height:0;height:auto}", page)
        self.assertIn("@media(max-width:900px)", page)
        self.assertIn(".tm-explorer-view .ex-layout{min-height:500px}", page)

    def test_explorer_intro_and_floating_hint_are_gone(self):
        body = graph_content()
        page = dashboard_shell("Explore map", body, active="explorer")
        self.assertNotIn("ex-hint", body)
        self.assertNotIn("只读科研图谱 ·", body)
        self.assertNotIn("Canvas 科研图谱、Markdown 与双向引用（只读）", page)
        self.assertIn("<h1>Explore map</h1>", page)
        # The shared shell retains subtitles for other pages.
        self.assertIn("Workspace 设置与任务级自动审计选项",
                      dashboard_shell("Settings", "<p>Body</p>", active="settings"))
        self.assertIn('id="ex-g6"', page)
        self.assertIn('id="ex-reader"', page)


if __name__ == "__main__":
    unittest.main()
