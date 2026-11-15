"""Empty workspaces offer an offline-first route to a usable task."""
from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from tm_observer.onboarding_ui import first_use_panel
from tm_observer.research_chat_ui import page_body as chat_body
from tm_observer.research_plan_ui import page_body as plan_body
from tm_observer.run_views import run_index_body


class OnboardingTests(unittest.TestCase):
    def test_shared_guide_separates_preparation_from_real_run(self):
        body = first_use_panel()
        for expected in (
            'aria-label="首次使用引导"', 'Python 3.12', 'sciver smoke',
            'sciver test Test0 --root', '--prepare-only', 'Cycle 1',
            'THEORY_MASTER_SEARCH_ROOT', 'THEORY_MASTER_DATA_ROOT',
            'sciver codex-account login-1', 'sciver codex-account status',
            'sciver model-profile status', '消耗模型额度', 'sciver run 0',
            '实际 Task ID', 'href="/run/"', 'href="/services/"',
        ):
            self.assertIn(expected, body)
        self.assertLess(body.index('--prepare-only'), body.index('sciver run 0'))
        self.assertNotIn('<script', body)

    def test_empty_task_list_offers_guide_but_no_search_results_does_not(self):
        self.assertIn('首次使用引导', run_index_body({'runs': []}))
        row = {'observer_id': '0123456789abcdef', 'task_name': 'known'}
        body = run_index_body({'runs': [row]}, query='absent')
        self.assertIn('没有符合条件的任务', body)
        self.assertNotIn('首次使用引导', body)

    def test_empty_plan_offers_creation_instead_of_broken_editor(self):
        body = plan_body([])
        self.assertIn('sciver test Test0', body)
        self.assertNotIn('id="rp-refresh"', body)
        self.assertNotIn('正在读取 Research Plan', body)

    def test_empty_research_chat_offers_creation_instead_of_unusable_form(self):
        body = chat_body({'tasks': [], 'sessions': []})
        self.assertIn('科研对话需要一个项目任务', body)
        self.assertIn('sciver test Test0', body)
        document = BeautifulSoup(body, 'html.parser')
        self.assertIsNone(document.select_one('form[data-research-direct]'))
        self.assertIsNone(document.select_one('[data-research-mode="project"]'))
        self.assertIsNotNone(document.select_one('.tm-research-entry .tm-onboarding'))
        supervisor = chat_body({'tasks': [], 'sessions': []}, mode='supervisor')
        self.assertNotIn('首次使用引导', supervisor)
