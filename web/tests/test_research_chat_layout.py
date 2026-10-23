from __future__ import annotations

import unittest

from tm_observer.dashboard_ui import dashboard_shell
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
        self.assertIn('placeholder="直接输入任何科研问题，不必选择上面的推荐内容…"', html)
        self.assertIn('data-research-send disabled', html)
        self.assertIn('data-new-chat-model', html)
        self.assertIn('data-new-chat-effort', html)
        self.assertIn('data-research-prompt-count', html)
        self.assertIn('data-research-mode="project"', html)
        self.assertIn('data-research-mode="frontier"', html)
        self.assertIn('localStorage.getItem(taskKey)', html)
        self.assertIn('tm-research-chat-draft-v1:', html)
        self.assertIn('tm-research-chat-selected-mode-v1', html)
        self.assertIn('tm-research-chat-new-model-v1', html)
        self.assertIn('tm-research-chat-new-effort-v1', html)
        self.assertIn('data-research-clear-draft', html)
        self.assertIn("event.key === 'Enter' && !event.shiftKey", html)
        self.assertIn("/api/research-chat/", html)
        self.assertIn('推荐的聊天内容', html)
        self.assertIn('建立科研项目', html)
        self.assertIn('旧版目录内初始化（兼容入口）', html)

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
        self.assertIn(".tm-research_chat-view .tm-content{display:flex;flex:1;flex-direction:column;min-height:0;height:auto;max-width:none;overflow:hidden;padding:8px 18px 8px}", html)
        self.assertIn("#tm-research-chat-app .tm-aui-viewport{min-height:0}", html)
        self.assertIn(".tm-research_chat-view .tm-main{height:100vh;min-height:0;display:flex;flex-direction:column;overflow:hidden}", html)
        self.assertIn("#tm-research-chat-app .tm-aui-composer-wrap{position:relative;bottom:auto;flex:0 0 auto}", html)
        self.assertIn(".tm-research-history{position:sticky;top:0;align-self:stretch;min-width:0;min-height:0;height:100%;max-height:100%;overflow:hidden;", html)
        self.assertIn(".tm-research-layout{display:grid;grid-template-columns:minmax(0,1fr) 258px;grid-template-rows:minmax(0,1fr);gap:16px;align-items:stretch;flex:1;min-height:0;overflow:hidden}", html)
        self.assertIn(".tm-research-chat.history-collapsed .tm-research-layout{grid-template-columns:minmax(0,1fr) 46px;gap:8px}", html)
        self.assertIn('data-research-history-toggle', html)
        self.assertIn('aria-controls="tm-research-history-list"', html)
        self.assertIn('id="tm-research-history-list"', html)
        self.assertIn("localStorage.getItem(historyKey)", html)
        self.assertIn("localStorage.setItem(historyKey", html)
        self.assertIn('href="/research-chat/?new=1"', html)
        self.assertIn("了解研究前沿", html)
        self.assertIn("@media(max-width:760px)", html)
        self.assertIn("grid-template-rows:minmax(0,1fr) auto", html)
        self.assertIn(".tm-research-history{grid-row:2;position:relative;height:auto;min-height:0;border-left:0;border-top:1px solid #303038;padding:8px 0 0;max-height:112px}", html)


if __name__ == "__main__":
    unittest.main()
