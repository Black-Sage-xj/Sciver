"""Opt-in console UX regression using isolated fixtures and no model calls.

TM_BROWSER_E2E=1 PYTHONPATH=.:web pytest -q web/tests/test_task_console_ux_browser.py
"""
import json
import os
import threading
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("TM_BROWSER_E2E") != "1", reason="browser test is opt-in")


@pytest.mark.parametrize("status,action", [("RUNNING", "pause"), ("PAUSED", "resume")])
@pytest.mark.parametrize("width,height", [(320, 800), (1440, 1000)])
def test_console_navigation_and_safe_controls(tmp_path, status, action, width, height):
    playwright = pytest.importorskip("playwright.sync_api")
    from tm_observer.web import ThreadingHTTPServer, make_handler

    cycle = tmp_path / "1"
    cycle.mkdir()
    (cycle / "run_state.json").write_text(json.dumps({
        "status": status, "cycle_id": "1", "master_id": 1,
        "resumable": status == "PAUSED",
    }), encoding="utf-8")
    task = {"observer_id": "a" * 16, "research_root": str(tmp_path),
            "cycle_dir": str(cycle), "task_name": "任务界面检查",
            "run_id": 1, "cycle": "1", "state": status}
    header = (f"Process {'RUNNING' if status == 'RUNNING' else 'STOPPED'}   State {status}\n"
              "Cycle 1 / 3     Phase investigator  Elapsed 36:50\n"
              "Started 11-18T16:26:08  Cycle start 11-18T16:26:08  Cost —")
    projection = {"status": status, "cycle_id": "1", "parts": {"header": header},
                  "recovery_info": {"resumable": status == "PAUSED", "execution_active": False}}

    class Cache:
        search_root = tmp_path

        def get(self):
            return {"runs": [task]}

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Cache(), field_notes_state_dir=tmp_path / "state"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    requests = []
    try:
        with patch("tm_observer.run_tui_bridge.read_tui", return_value=projection), playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": width, "height": height})
                def abort_action(route):
                    requests.append(route.request.method)
                    route.abort()
                page.route("**/api/run/**/action", abort_action)
                page.goto(f"http://127.0.0.1:{server.server_port}/run/{'a' * 16}/", wait_until="domcontentloaded")
                bar = page.locator(".tm-topbar").bounding_box()
                crumb = page.locator(".tm-breadcrumb").bounding_box()
                assert crumb["y"] >= bar["y"] - 1
                assert crumb["y"] + crumb["height"] <= bar["y"] + bar["height"] + 1
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                trigger = page.locator("[data-tui-process-actions]").first
                trigger.focus()
                trigger.press("Enter")
                quick = page.locator("#tm-operator-quick-dialog")
                playwright.expect(quick).to_be_visible()
                quick.locator(f'[data-tui-quick-action="{action}"]').click()
                confirm = page.locator("#tm-operator-confirm-dialog")
                playwright.expect(confirm).to_be_visible()
                confirm.locator("#tm-operator-confirm-cancel").click()
                playwright.expect(confirm).to_be_hidden()
                assert not requests
                if width <= 540:
                    history = page.locator('[data-history-panel="recent"] .tm-run-events-scroll').first
                    assert history.bounding_box()["height"] < 200
                    history.evaluate("e => { const x=document.createElement('div'); x.style.height='1200px'; e.appendChild(x); }")
                    assert history.evaluate("e => e.scrollHeight > e.clientHeight")
                    history.evaluate("e => e.scrollTop=100")
                    assert history.evaluate("e => e.scrollTop > 0")
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
