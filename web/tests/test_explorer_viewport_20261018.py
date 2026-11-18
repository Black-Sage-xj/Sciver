"""Viewport-height and obsolete-introduction regression for Research Explorer."""
from __future__ import annotations

import unittest

from tm_observer.dashboard_ui import dashboard_shell
from tm_observer.research_explorer_g6 import graph_content


class ExplorerViewportTest(unittest.TestCase):
    def test_graph_and_reader_share_remaining_viewport_height(self):
        page = graph_content()
        self.assertIn("height:calc(100dvh - 44px)", page)
        self.assertIn(".ex-workspace{display:flex;flex-direction:column;flex:1;min-width:0;min-height:0}", page)
        self.assertIn(".tm-explorer-view .ex-layout{display:grid", page)
        self.assertIn("gap:0;flex:1;min-height:0;height:auto", page)
        self.assertIn(".ex-workspace:fullscreen .ex-layout{flex:1;min-height:0;height:auto}", page)
        self.assertIn("@media(max-width:900px)", page)
        self.assertIn(".tm-explorer-view .ex-layout{min-height:0;height:auto}", page)

    def test_explorer_intro_and_floating_hint_are_gone(self):
        body = graph_content()
        page = dashboard_shell("Explore map", body, active="explorer")
        self.assertNotIn("ex-hint", body)
        self.assertNotIn("只读科研图谱 ·", body)
        self.assertNotIn("Canvas 科研图谱、Markdown 与双向引用（只读）", page)
        self.assertIn("<h1>图谱</h1>", page)
        introduction = page.split('aria-label="Page introduction"', 1)[1].split('</section>', 1)[0]
        self.assertNotIn('<p>', introduction)
        # The shared shell retains subtitles for other pages.
        settings = dashboard_shell("Settings", "<p>Body</p>", active="settings")
        settings_introduction = settings.split('aria-label="Page introduction"', 1)[1].split('</section>', 1)[0]
        self.assertIn('<p>Workspace 外观、PDF 转换与任务设置</p>', settings_introduction)
        self.assertIn('id="ex-g6"', page)
        self.assertIn('id="ex-reader"', page)


if __name__ == "__main__":
    unittest.main()
