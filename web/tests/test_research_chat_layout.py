from __future__ import annotations

import unittest

from tm_observer.dashboard_ui import dashboard_shell
from tm_observer.project_setup_ui import setup_panel
from tm_observer.research_chat_ui import page_body


class ResearchChatLayoutTests(unittest.TestCase):
    def test_new_chat_home_has_direct_composer_and_recommendation_starters(self) -> None:
        snapshot = {
            "tasks": [{"id": "task-1", "label": "矩阵腔体 Green 闭合"}],
            "sessions": [], "session": None,
            "available_models": ["gpt-6-sol", "gpt-6-luna", "gpt-6.1-sol"],
            "available_reasoning_efforts": ["low", "medium", "high", "xhigh"],
        }
        html = page_body(snapshot)
        self.assertIn('data-research-direct', html)
        self.assertIn('id="tm-research-first-message"', html)
        self.assertIn('placeholder="随心输入"', html)
        self.assertRegex(html, r'<button[^>]*data-research-send[^>]*disabled')
        self.assertIn('data-new-chat-model', html)
        self.assertIn('data-new-chat-effort', html)
        self.assertIn('data-research-prompt-count', html)
        self.assertIn('data-research-mode="project"', html)
        self.assertIn('data-research-mode="frontier"', html)
        self.assertNotIn('tm-research-chat-selected-task-v1', html)
        self.assertNotIn('taskSelect.selectedIndex = 1', html)
        self.assertIn('新想法 · 不关联已有项目', html)
        self.assertIn('tm-research-chat-draft-v1:', html)
        self.assertIn('tm-research-chat-selected-mode-v1', html)
        self.assertIn('tm-research-chat-new-model-v1', html)
        self.assertIn('tm-research-chat-new-effort-v1', html)
        self.assertIn('data-research-clear-draft', html)
        self.assertIn("event.key === 'Enter' && !event.shiftKey", html)
        self.assertIn("/api/research-chat/", html)
        self.assertIn('迁移已有研究', html)
        toolbar = html.split('<header class="tm-research-toolbar">', 1)[1].split('</header>', 1)[0]
        self.assertNotIn('data-research-task', toolbar)
        self.assertIn('aria-label="项目上下文"', html)
        self.assertEqual(html.count('data-research-task aria-label="选择项目任务"'), 1)
        self.assertLess(html.index('aria-label="选择项目任务"'), html.index('data-research-direct'))
        self.assertIn('aria-label="模型与推理强度"', html)
        self.assertIn('data-entry-choice="existing" aria-pressed="true"', html)
        self.assertIn('data-entry-choice="new" aria-pressed="false"', html)
        self.assertNotIn('按研究合同建立项目', html)
        self.assertNotIn('直接开始聊天</label>', html)
        self.assertNotIn('<h3>和 Sciver 讨论科研</h3>', html)
        for skill in ('research-progress', 'research-discussion', 'research-next-steps'):
            self.assertIn(f'data-research-skill="{skill}"', html)
        for skill in ('research-intervention', 'research-evidence'):
            self.assertNotIn(f'data-research-skill="{skill}"', html)
        self.assertEqual(html.count('<span class="tm-research-start-description">'), 6)

    def test_legacy_setup_welcome_defaults_to_automatic_and_can_be_suppressed(self) -> None:
        self.assertIn('const autoWelcome=true;', setup_panel('/workspace'))
        self.assertIn('const autoWelcome=false;', setup_panel('/workspace', auto_welcome=False))

    def test_saved_session_exposes_escaped_manual_title_editor(self) -> None:
        html = page_body({
            "tasks": [], "sessions": [], "available_models": ["gpt-6-sol"],
            "available_reasoning_efforts": ["low", "medium"],
            "session": {"id": "0123456789abcdef0123456789abcdef", "mode": "project",
                        "title": '<img src=x onerror="alert(1)">', "messages": []},
        })
        self.assertIn('data-research-rename', html)
        self.assertIn('data-research-title-form', html)
        self.assertIn('data-research-title-input maxlength="80"', html)
        self.assertIn('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;', html)
        self.assertNotIn('<img src=x onerror="alert(1)">', html)
        self.assertIn('data-session-mode="project"', html)
        self.assertIn("'/title'", html)

    def test_active_session_uses_independent_scroll_regions_and_collapsible_history(self) -> None:
        snapshot = {
            "tasks": [],
            "sessions": [
                {"id": "session-current", "title": "了解研究前沿", "updated_at": "2026-10-18T21:00:00"},
                {"id": "session-old", "title": "旧研究讨论", "updated_at": "2026-10-17T21:00:00"},
            ],
            "session": {
                "id": "session-current",
                "title": "了解研究前沿",
                "model": "gpt-6.1-sol",
                "messages": [],
            },
        }
        html = dashboard_shell("Research Chat", page_body(snapshot), active="research_chat")

        self.assertIn('class="tm-research_chat-view"', html)
        self.assertIn(".tm-research_chat-view .tm-page-head{display:none}", html)
        self.assertIn("#tm-research-chat-app .tm-aui-viewport{min-height:0}", html)
        self.assertIn(".tm-research_chat-view .tm-main{height:100vh;min-height:0;display:flex;flex-direction:column;overflow:hidden}", html)
        self.assertIn("#tm-research-chat-app .tm-aui-composer-wrap{position:relative;bottom:auto;flex:0 0 auto}", html)
        # The browser matrix checks computed layout and overflow. Here verify
        # the server contract: a left history region and an independent app.
        self.assertIn('data-chat-shell="codex"', html)
        self.assertIn('data-chat-state="active"', html)
        self.assertLess(html.index('<aside class="tm-research-history"'), html.index('<main class="tm-research-main"'))
        self.assertIn('data-research-history-toggle', html)
        self.assertIn('aria-controls="tm-research-history-list"', html)
        self.assertIn('id="tm-research-history-list"', html)
        self.assertIn("localStorage.getItem(historyKey)", html)
        self.assertIn("localStorage.setItem(historyKey", html)
        self.assertIn('href="/research-chat/?new=1"', html)
        self.assertIn("了解研究前沿", html)
        self.assertIn("@media(max-width:760px)", html)


if __name__ == "__main__":
    unittest.main()
