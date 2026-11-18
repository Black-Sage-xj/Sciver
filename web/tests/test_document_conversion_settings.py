"""Host conversion settings: guarded API, safe secrets and an optional real UI.

TM_BROWSER_E2E=1 enables offline Playwright checks through make_handler. No
converter, remote PDF service, model or scientific Cycle is invoked.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from theory_master import document_conversion
from tm_observer.task_settings_ui import workspace_settings_body
from tm_observer.web import ThreadingHTTPServer, make_handler


@contextmanager
def settings_server():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        config = root / 'settings' / 'document-conversion.json'
        marker = root / 'fixture-marker'
        marker.write_text('#!/bin/sh\nexit 19\n', encoding='utf-8')
        marker.chmod(0o700)
        state = {'runs': [], 'git': {}}
        cache = SimpleNamespace(search_root=root, get=lambda: state)
        with patch.dict(os.environ, {'SCIVER_DOCUMENT_CONVERSION_CONFIG': str(config),
                                    'MINERU_API_KEY': ''}):
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(cache))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield f'http://127.0.0.1:{server.server_address[1]}', config, marker
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


def api(base, payload=None, *, origin=None, intent='1'):
    headers = {}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json', 'Origin': origin or base,
                   'X-TM-Operator-Intent': intent}
    request = Request(base + '/api/settings/document-conversion', data=data, headers=headers)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read()), response.headers
    except HTTPError as response:
        return response.code, json.loads(response.read()), response.headers


class DocumentConversionSettingsTests(unittest.TestCase):
    def test_workspace_and_selected_task_offer_global_panel_without_task_fields(self):
        plain = workspace_settings_body([], None)
        dom = BeautifulSoup(plain, 'html.parser')
        self.assertEqual(len(dom.select('[data-document-conversion]')), 1)
        self.assertIsNone(dom.select_one('[data-setting-key]'))
        self.assertEqual(dom.select_one('#tm-doc-conversion-token')['type'], 'password')
        self.assertNotIn('value', dom.select_one('#tm-doc-conversion-token').attrs)
        self.assertIn('默认关闭', plain)
        self.assertIn('PDF 将上传到所配置的服务', plain)
        self.assertNotIn('localStorage', plain)
        self.assertNotIn('sessionStorage', plain)
        run = {'observer_id': 'a' * 16, 'task_name': 'Task'}
        with (patch('tm_observer.task_settings_ui.run_monitor.data', return_value={'status': 'STOPPED'}),
              patch('tm_observer.task_settings_ui._launch_id', return_value='launch'),
              patch('tm_observer.task_settings_ui.settings_panel', return_value='<p>Task settings</p>')):
            selected = workspace_settings_body([run], run)
        self.assertEqual(selected.count('data-document-conversion\n'), 1)
        self.assertIn('data-settings-task="aaaaaaaaaaaaaaaa"', selected)
        self.assertIn('Task settings', selected)

    def test_guarded_api_default_off_local_save_and_cas(self):
        with settings_server() as (base, config, marker), \
                patch.object(document_conversion, '_marker') as convert_local, \
                patch.object(document_conversion, '_mineru') as convert_online:
            code, first, headers = api(base)
            self.assertEqual(code, 200)
            self.assertTrue(first['ok'])
            self.assertFalse(first['enabled'])
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertFalse(config.exists())
            payload = {'enabled': True, 'provider': 'marker', 'marker_executable': str(marker),
                       'expected_revision': first['revision']}
            for changes in ({'origin': 'https://foreign.invalid'}, {'intent': ''}):
                with self.subTest(changes=changes):
                    code, _, _ = api(base, payload, **changes)
                    self.assertEqual(code, 403)
                    self.assertFalse(config.exists())
            code, saved, _ = api(base, payload)
            self.assertEqual(code, 200)
            self.assertTrue(saved['enabled'])
            self.assertTrue(saved['available'])
            self.assertEqual(saved['marker_executable'], str(marker))
            self.assertNotEqual(saved['revision'], first['revision'])
            self.assertEqual(api(base, payload)[0], 409)
            code, disabled, _ = api(base, {'enabled': False, 'expected_revision': saved['revision']})
            self.assertEqual(code, 200)
            self.assertFalse(disabled['enabled'])
            self.assertEqual(api(base, {'enabled': 'yes', 'expected_revision': disabled['revision']})[0], 400)
            self.assertEqual(api(base, {'enabled': True})[0], 400)
            self.assertEqual(api(base)[1]['revision'], disabled['revision'])
            convert_local.assert_not_called()
            convert_online.assert_not_called()

    def test_api_secret_write_preserve_clear_without_echo_or_conversion(self):
        secret = 'fixture-private-token-never-echo'
        with settings_server() as (base, config, _), \
                patch.object(document_conversion, '_mineru') as convert_online:
            current = api(base)[1]
            code, saved, _ = api(base, {'provider': 'mineru', 'api_key': secret,
                                       'expected_revision': current['revision']})
            self.assertEqual(code, 200)
            self.assertTrue(saved['api_key_configured'])
            self.assertTrue(saved['available'])
            self.assertNotIn('api_key', saved)
            self.assertNotIn(secret, json.dumps(saved))
            self.assertEqual(json.loads(config.read_text())['api_key'], secret)
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            code, kept, _ = api(base, {'enabled': True, 'api_key': '',
                                      'expected_revision': saved['revision']})
            self.assertEqual(code, 200)
            self.assertTrue(kept['api_key_configured'])
            self.assertEqual(json.loads(config.read_text())['api_key'], secret)
            self.assertNotIn(secret, json.dumps(api(base)[1]))
            with urlopen(base + '/settings/', timeout=5) as response:
                self.assertNotIn(secret, response.read().decode('utf-8'))
            code, cleared, _ = api(base, {'clear_api_key': True, 'expected_revision': kept['revision']})
            self.assertEqual(code, 200)
            self.assertFalse(cleared['api_key_configured'])
            self.assertFalse(cleared['available'])
            self.assertNotIn('api_key', json.loads(config.read_text()))
            convert_online.assert_not_called()


@unittest.skipUnless(os.environ.get('TM_BROWSER_E2E') == '1', 'set TM_BROWSER_E2E=1; install Playwright Chromium')
class DocumentConversionBrowserTests(unittest.TestCase):
    def test_real_settings_local_toggle_online_token_and_conflict(self):
        from playwright.sync_api import expect, sync_playwright

        with settings_server() as (base, config, marker), sync_playwright() as browser_api:
            browser = browser_api.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1100, 'height': 950})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base + '/settings/')
            enabled = page.locator('#tm-doc-conversion-enabled')
            provider = page.locator('#tm-doc-conversion-provider')
            command = page.locator('#tm-doc-conversion-command')
            token = page.locator('#tm-doc-conversion-token')
            save = page.locator('[data-doc-conversion-save]')
            status = page.locator('[data-doc-conversion-status]')
            reload_settings = page.locator('[data-doc-conversion-reload]')
            expect(enabled).to_be_enabled()
            expect(enabled).not_to_be_checked()
            self.assertFalse(config.exists())
            command.fill(str(marker))
            enabled.check()
            with page.expect_response('**/api/settings/document-conversion') as posted:
                save.click()
            self.assertEqual(posted.value.status, 200)
            expect(status).to_contain_text('已保存')
            expect(page.locator('[data-doc-conversion-availability]')).to_contain_text('已找到')
            page.reload()
            expect(enabled).to_be_checked()
            expect(command).to_have_value(str(marker))
            enabled.uncheck()
            with page.expect_response('**/api/settings/document-conversion') as posted:
                save.click()
            self.assertEqual(posted.value.status, 200)
            expect(status).to_contain_text('已关闭')
            provider.select_option('mineru')
            expect(token).to_be_visible()
            expect(command).not_to_be_visible()
            secret = 'browser-private-token-never-echo'
            token.fill(secret)
            with page.expect_response('**/api/settings/document-conversion') as posted:
                save.click()
            self.assertEqual(posted.value.status, 200)
            expect(token).to_have_value('')
            expect(token).to_have_attribute('placeholder', '已配置；留空保留')
            self.assertNotIn(secret, page.content())
            self.assertNotIn(secret, page.url)
            self.assertNotIn(secret, page.evaluate('JSON.stringify([Object.entries(localStorage),Object.entries(sessionStorage)])'))
            page.reload()
            expect(token).to_have_attribute('placeholder', '已配置；留空保留')
            enabled.check()
            with page.expect_response('**/api/settings/document-conversion') as posted:
                save.click()
            self.assertEqual(posted.value.status, 200)
            self.assertEqual(json.loads(config.read_text())['api_key'], secret)
            current = document_conversion.load_settings()
            document_conversion.save_settings({'enabled': False, 'expected_revision': current['revision']})
            with page.expect_response('**/api/settings/document-conversion') as posted:
                save.click()
            self.assertEqual(posted.value.status, 409)
            expect(status).to_contain_text('其他页面修改')
            expect(enabled).to_be_checked()
            reload_settings.click()
            expect(enabled).not_to_be_checked()
            page.locator('#tm-doc-conversion-clear-token').check()
            with page.expect_response('**/api/settings/document-conversion') as posted:
                save.click()
            self.assertEqual(posted.value.status, 200)
            expect(token).to_have_attribute('placeholder', '尚未配置')
            self.assertNotIn('api_key', json.loads(config.read_text()))
            page.set_viewport_size({'width': 390, 'height': 844})
            for theme in ('white', 'black', 'blue'):
                page.locator('[data-sciver-theme-picker]').select_option(theme)
                expect(page.locator('html')).to_have_attribute('data-sciver-theme', theme)
                bounds = page.locator('[data-document-conversion]').bounding_box()
                self.assertLessEqual(bounds['x'] + bounds['width'], 391)
                self.assertGreaterEqual(bounds['x'], 0)
            page.screenshot(path='/tmp/sciver-document-conversion-settings.png', full_page=True)
            self.assertEqual(errors, [])
            browser.close()
