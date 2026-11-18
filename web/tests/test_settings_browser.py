"""Opt-in Chromium regression through real DOM, HTTP guards and task persistence.

TM_BROWSER_E2E=1 PYTHONPATH=web:. pytest -q web/tests/test_settings_browser.py
Requires playwright and its Chromium browser. Only the SDK subprocess boundary
is replaced: canonical operator_preferences.write writes an isolated temp task.
No model, account, scientific Cycle or external service is invoked.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theory_master.operator_preferences import read, write
from tm_observer.run_control import OperatorError
from tm_observer.web import ThreadingHTTPServer, make_handler


@unittest.skipUnless(os.environ.get('TM_BROWSER_E2E') == '1', 'set TM_BROWSER_E2E=1; install Playwright Chromium')
class SettingsBrowserTests(unittest.TestCase):
    def test_click_save_reload_and_rejected_save_rolls_back(self):
        from playwright.sync_api import sync_playwright, expect

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.theory-master').mkdir()
            cycle = root / '1'
            cycle.mkdir()
            (cycle / 'research_state.json').write_text(json.dumps({'status': 'STOPPED', 'cycle_id': '1'}))
            run = {'observer_id': 'a' * 16, 'task_name': 'Browser fixture', 'run_id': 1,
                   'research_root': str(root), 'task_root': str(root),
                   'cycle_dir': str(cycle), 'cycle': '1', 'state': 'STOPPED'}
            state = {'runs': [run], 'git': {}}
            cache = SimpleNamespace(search_root=root, get=lambda: state)
            calls = []
            rejected = []

            def apply_sdk(target, action, value):
                self.assertEqual(target['research_root'], str(root))
                self.assertEqual(action, 'set_operator_preference')
                calls.append(value)
                if rejected:
                    raise OperatorError('fixture save rejected', 409)
                return {'saved': write(root, **value), 'effective_at': 'next_run_or_resume'}

            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(cache))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch('tm_observer.run_control._sdk_apply', side_effect=apply_sdk), sync_playwright() as browser_api:
                    browser = browser_api.chromium.launch(headless=True)
                    page = browser.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_address[1]}/settings/?observer_id=' + run['observer_id'])
                    toggle = page.locator('[data-setting-key="cycle_audit"]')
                    model = page.locator('[data-setting-model="cycle_audit"]')
                    save = page.locator('[data-setting-save-model="cycle_audit"]')
                    feedback = page.locator('.tm-setting-feedback')
                    # Appearance is the first panel: listeners must bind the task editor.
                    expect(page.locator('.tm-task-settings-panel').first).to_have_class('tm-task-settings-panel tm-appearance-panel')
                    with page.expect_response('**/api/run/*/action') as posted:
                        toggle.check()
                    self.assertEqual(posted.value.status, 200)
                    expect(feedback).to_contain_text('已保存')
                    self.assertEqual(read(root)['flags'], {'cycle_audit': True})
                    model.select_option('gpt-6-sol')
                    with page.expect_response('**/api/run/*/action') as posted:
                        save.click()
                    self.assertEqual(posted.value.status, 200)
                    expect(feedback).to_contain_text('已保存')
                    self.assertEqual(read(root)['models'], {'cycle_audit': 'gpt-6-sol'})
                    self.assertEqual(len(calls), 2)
                    learning = page.locator('[data-setting-key="skill_learning"]')
                    expect(learning).not_to_be_checked()
                    with page.expect_response('**/api/run/*/action') as posted:
                        learning.check()
                    self.assertEqual(posted.value.status, 200)
                    expect(feedback).to_contain_text('不启动额外模型')
                    self.assertTrue(read(root)['flags']['skill_learning'])
                    page.reload()
                    expect(toggle).to_be_checked()
                    expect(learning).to_be_checked()
                    expect(model).to_have_value('gpt-6-sol')
                    rejected.append(True)
                    with page.expect_response('**/api/run/*/action') as posted:
                        learning.uncheck()
                    self.assertEqual(posted.value.status, 409)
                    expect(learning).to_be_checked()
                    expect(learning).to_be_enabled()
                    with page.expect_response('**/api/run/*/action') as posted:
                        toggle.uncheck()
                    self.assertEqual(posted.value.status, 409)
                    expect(feedback).to_contain_text('fixture save rejected')
                    expect(toggle).to_be_checked()
                    expect(toggle).to_be_enabled()
                    model.select_option('gpt-6-luna')
                    with page.expect_response('**/api/run/*/action') as posted:
                        save.click()
                    self.assertEqual(posted.value.status, 409)
                    expect(feedback).to_contain_text('保存失败')
                    expect(model).to_have_value('gpt-6-sol')
                    expect(model).to_be_enabled()
                    expect(save).to_be_enabled()
                    self.assertEqual(len(calls), 6)
                    page.reload()
                    expect(toggle).to_be_checked()
                    expect(model).to_have_value('gpt-6-sol')
                    self.assertEqual(errors, [])
                    # Cold unauthenticated users get per-account login steps,
                    # with no manufactured balance or usable reset controls.
                    page.route('**/services-selection.json', lambda route: route.fulfill(json={
                        'provider': 'codex', 'tier': '1', 'active_account': 'account-1'}))
                    page.route('**/codex-usage-cached.json', lambda route: route.fulfill(json={
                        'accounts': [{'account_home': 'account-1', 'ok': False, 'error': 'not logged in'},
                                     {'account_home': 'account-2', 'ok': False, 'error': 'not logged in'}]}))
                    page.goto(f'http://127.0.0.1:{server.server_address[1]}/services/')
                    expect(page.locator('#usage')).to_contain_text('sciver codex-account login-1')
                    expect(page.locator('#usage')).to_contain_text('sciver codex-account login-2')
                    expect(page.locator('#usage .reset-credit-button')).to_have_count(0)
                    expect(page.locator('#usage .tm-quota-window')).to_have_count(0)
                    self.assertEqual(errors, [])
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
