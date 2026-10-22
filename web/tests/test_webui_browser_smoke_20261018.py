"""Opt-in real Firefox smoke test; no Selenium/Playwright dependency is required.

Run with TM_BROWSER_E2E=1 pytest -q web/tests/test_webui_browser_smoke_20261018.py.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

from tm_observer.supervisor_view import _refresh_script


@unittest.skipUnless(os.environ.get('TM_BROWSER_E2E') == '1'
                     and shutil.which('firefox') and shutil.which('geckodriver'),
                     'set TM_BROWSER_E2E=1 and install Firefox + geckodriver')
class RealBrowserSmokeTests(unittest.TestCase):
    def test_firefox_keeps_shadow_dom_draft_until_safe_to_refresh(self):
        state = {'requests': 0, 'lock': threading.Lock()}
        refresh = _refresh_script(active=True, waiting=True).replace('const delay=3000;', 'const delay=100;')
        page = ("<!doctype html><html><head><meta charset='utf-8'></head><body>"
                "<details open id='details'><summary>details</summary></details>"
                "<script>customElements.define('loquix-chat-composer',class extends HTMLElement {"
                "constructor(){super();const root=this.attachShadow({mode:'open'});"
                "const prompt=document.createElement('loquix-prompt-input');prompt.value='';root.append(prompt);}});"
                "</script><loquix-chat-composer></loquix-chat-composer>"
                + refresh + "</body></html>").encode('utf-8')

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/favicon.ico':
                    self.send_response(204); self.end_headers(); return
                with state['lock']:
                    state['requests'] += 1
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(page)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(page)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        def stop_server():
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=3)
        self.addCleanup(stop_server)

        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            driver_port = sock.getsockname()[1]
        driver = subprocess.Popen(
            ['geckodriver', '--host', '127.0.0.1', '--port', str(driver_port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        session_id = None
        base = f'http://127.0.0.1:{driver_port}'

        def command(method: str, path: str, body=None):
            data = json.dumps(body).encode('utf-8') if body is not None else None
            request = Request(base + path, data=data, method=method,
                              headers={'Content-Type': 'application/json'})
            with urlopen(request, timeout=4) as response:
                return json.loads(response.read()) if response.status != 204 else {}

        try:
            deadline = time.time() + 10
            while True:
                if driver.poll() is not None:
                    self.fail('geckodriver exited before accepting a session')
                try:
                    command('GET', '/status')
                    break
                except Exception:
                    if time.time() >= deadline:
                        self.fail('geckodriver did not become ready')
                    time.sleep(0.1)
            created = command('POST', '/session', {
                'capabilities': {'alwaysMatch': {
                    'browserName': 'firefox',
                    'moz:firefoxOptions': {'args': ['-headless']},
                }}
            })['value']
            session_id = created.get('sessionId') or created.get('session_id')
            self.assertTrue(session_id, 'WebDriver must return a session id')

            def execute(script: str):
                return command('POST', f'/session/{session_id}/execute/sync',
                               {'script': script, 'args': []})['value']

            command('POST', f'/session/{session_id}/url',
                    {'url': f'http://127.0.0.1:{server.server_address[1]}/'})
            for _ in range(50):
                if execute('return document.readyState') == 'complete':
                    break
                time.sleep(0.05)
            time.sleep(0.25)
            with state['lock']:
                self.assertEqual(state['requests'], 1, 'open details must defer reload')

            execute("document.getElementById('details').open=false;"
                    "document.querySelector('loquix-chat-composer').shadowRoot"
                    ".querySelector('loquix-prompt-input').value='unsent human draft';")
            time.sleep(0.25)
            with state['lock']:
                self.assertEqual(state['requests'], 1, 'a draft in the real shadow DOM must defer reload')

            execute("document.querySelector('loquix-chat-composer').shadowRoot"
                    ".querySelector('loquix-prompt-input').value='';")
            deadline = time.time() + 3
            while time.time() < deadline:
                with state['lock']:
                    if state['requests'] >= 2:
                        break
                time.sleep(0.05)
            with state['lock']:
                self.assertGreaterEqual(state['requests'], 2, 'polling resumes when it is safe to refresh')
        finally:
            if session_id:
                try:
                    command('DELETE', f'/session/{session_id}')
                except Exception:
                    pass
            driver.terminate()
            try:
                driver.wait(timeout=5)
            except subprocess.TimeoutExpired:
                driver.kill(); driver.wait(timeout=3)


if __name__ == '__main__':
    unittest.main()
