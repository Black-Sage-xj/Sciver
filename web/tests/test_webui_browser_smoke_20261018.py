"""Opt-in Firefox smoke test using the same Playwright runtime installed by CI.

Run with TM_BROWSER_E2E=1 pytest -q web/tests/test_webui_browser_smoke_20261018.py.
Requires playwright and its Firefox browser (python -m playwright install firefox).
"""
from __future__ import annotations

import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tm_observer.supervisor_view import _refresh_script


@unittest.skipUnless(os.environ.get('TM_BROWSER_E2E') == '1',
                     'set TM_BROWSER_E2E=1; install Playwright Firefox')
class RealBrowserSmokeTests(unittest.TestCase):
    def test_firefox_keeps_shadow_dom_draft_until_safe_to_refresh(self):
        from playwright.sync_api import sync_playwright

        state = {'requests': 0, 'lock': threading.Lock()}
        refresh = _refresh_script(active=True, waiting=True)
        page_html = ("<!doctype html><html><head><meta charset='utf-8'></head><body>"
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
                self.send_header('Content-Length', str(len(page_html)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(page_html)

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

        with sync_playwright() as browser_api:
            # Use Playwright's matching Firefox build, not a system browser/driver.
            browser = browser_api.firefox.launch(headless=True, timeout=60000)
            try:
                page = browser.new_page()
                page.set_default_timeout(5000)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.clock.install(time='2026-01-01T00:00:00Z')
                url = f'http://127.0.0.1:{server.server_address[1]}/'
                page.goto(url)
                page.clock.pause_at('2026-01-01T00:01:00Z')

                # Exercise multiple real polling callbacks without wall-clock sleeps.
                page.clock.run_for(6000)
                with state['lock']:
                    self.assertEqual(state['requests'], 1, 'open details must defer reload')

                page.evaluate("document.getElementById('details').open=false;"
                              "document.querySelector('loquix-chat-composer').shadowRoot"
                              ".querySelector('loquix-prompt-input').value='unsent human draft';")
                page.clock.run_for(6000)
                with state['lock']:
                    self.assertEqual(state['requests'], 1, 'a draft in the real shadow DOM must defer reload')

                page.evaluate("document.querySelector('loquix-chat-composer').shadowRoot"
                              ".querySelector('loquix-prompt-input').value='';")
                with page.expect_response(url) as reloaded:
                    page.clock.run_for(3000)
                self.assertEqual(reloaded.value.status, 200)
                with state['lock']:
                    self.assertGreaterEqual(state['requests'], 2, 'polling resumes when it is safe to refresh')
                self.assertEqual(errors, [])
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
