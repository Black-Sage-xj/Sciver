from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tm_observer.access_settings import AccessSettings, AccessSettingsError, reset_local_login, settings_path
from tm_observer.auth import BasicAuthCredentials, SESSION_COOKIE, issue_session_token, match_account, verify_session_token
from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler


class AccessSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.state = self.root / 'state'

    def test_disabled_by_default_and_hashed_persistent_credentials(self):
        settings = AccessSettings(self.state, self.workspace)
        self.assertFalse(settings.public()['enabled'])
        settings.save({'enabled': True, 'username': 'researcher', 'password': 'strong-password'})
        path = settings_path(self.state, self.workspace)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('strong-password', path.read_text())
        self.assertTrue(AccessSettings(self.state, self.workspace).public()['enabled'])
        first = settings.current
        self.assertIsNotNone(match_account(first, 'researcher', 'strong-password'))
        token = issue_session_token(first)
        settings.save({'enabled': True, 'username': 'researcher', 'current_password': 'strong-password',
                       'password': 'new-strong-password'})
        self.assertFalse(verify_session_token(token, settings.current))
        self.assertIsNone(match_account(settings.current, 'researcher', 'strong-password'))
        with self.assertRaises(AccessSettingsError):
            settings.save({'enabled': False, 'current_password': 'wrong'})
        self.assertTrue(settings.public()['enabled'])
        settings.save({'enabled': False, 'current_password': 'new-strong-password'})
        self.assertFalse(AccessSettings(self.state, self.workspace).public()['enabled'])
        reset_local_login(self.state, self.workspace)
        self.assertFalse(path.exists())

    def test_workspace_isolation_and_external_auth_unchanged(self):
        AccessSettings(self.state, self.workspace).save({'enabled': True, 'username': 'a', 'password': '12345678'})
        second = self.root / 'other-workspace'
        second.mkdir()
        self.assertFalse(AccessSettings(self.state, second).public()['enabled'])
        external = AccessSettings(self.state, self.workspace, BasicAuthCredentials('old', 'file-secret'))
        self.assertTrue(external.public()['externally_managed'])
        with self.assertRaises(AccessSettingsError):
            external.save({'enabled': False, 'current_password': 'file-secret'})

    def test_http_enable_login_cookie_and_disable(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(StateCache(self.workspace), field_notes_state_dir=self.state))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(lambda: worker.join(timeout=3))
        base = f'http://127.0.0.1:{server.server_address[1]}'

        def post(payload, *, cookie=None, origin=None):
            headers = {'Content-Type': 'application/json', 'X-TM-Operator-Intent': '1',
                       'Origin': origin or base}
            if cookie:
                headers['Cookie'] = cookie
            request = Request(base + '/api/settings/access', data=json.dumps(payload).encode(), headers=headers)
            return urlopen(request, timeout=5)

        with urlopen(base + '/settings/', timeout=5) as response:
            self.assertIn('网页登录', response.read().decode())
        with post({'enabled': True, 'username': '科研者', 'password': 'safe-password'}) as response:
            self.assertNotIn('safe-password', response.read().decode())
        from urllib.request import build_opener, HTTPRedirectHandler
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        with self.assertRaises(HTTPError) as unauth:
            build_opener(NoRedirect).open(base + '/settings/', timeout=5)
        self.assertEqual(unauth.exception.code, 303)
        with self.assertRaises(HTTPError) as wrong_origin:
            post({'enabled': False, 'current_password': 'safe-password'}, origin='http://attacker.invalid')
        self.assertEqual(wrong_origin.exception.code, 401)
        login = Request(base + '/login', data=urlencode({'username': '科研者', 'password': 'safe-password'}).encode(),
                        headers={'Content-Type': 'application/x-www-form-urlencoded'})
        with self.assertRaises(HTTPError) as logged_in:
            build_opener(NoRedirect).open(login, timeout=5)
        cookie = logged_in.exception.headers['Set-Cookie']
        self.assertIn(SESSION_COOKIE, cookie)
        self.assertNotIn('Secure;', cookie)
        with urlopen(Request(base + '/settings/', headers={'Cookie': cookie}), timeout=5) as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(HTTPError) as forbidden:
            post({'enabled': False, 'current_password': 'safe-password'}, cookie=cookie,
                 origin='http://attacker.invalid')
        self.assertEqual(forbidden.exception.code, 403)
        with post({'enabled': False, 'current_password': 'safe-password'}, cookie=cookie) as response:
            self.assertFalse(json.loads(response.read())['enabled'])
        with urlopen(base + '/settings/', timeout=5) as response:
            self.assertEqual(response.status, 200)


if __name__ == '__main__':
    unittest.main()
