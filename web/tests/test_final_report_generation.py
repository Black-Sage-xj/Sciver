"""Manual report requests use registered roots and never run scientific Cycles."""
import json
import os
import threading
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from tm_observer.final_report_entry_ui import render_report_entry
from tm_observer.http_task_routes import post_task

IDENT = "a" * 16


class Handler:
    def __init__(self, **headers):
        self.headers = {"Origin": "http://localhost", "Host": "localhost",
                        "X-TM-Operator-Intent": "1", **headers}
        self.rfile = BytesIO()

    def _authorized(self):
        return True

    def _send(self, data, content_type, status):
        self.result = json.loads(data), status


def test_generation_route_requires_authentication():
    handler = Handler()
    handler._authorized = lambda: False
    handler._send_unauthorized = lambda: setattr(handler, "result", ({"error": "unauthorized"}, 401))
    with patch("theory_master.final_report.launch_final_report_job") as launch:
        assert post_task(handler, f"/api/run/{IDENT}/final-report/generate", None, None)
        assert handler.result[1] == 401
        launch.assert_not_called()


@pytest.mark.parametrize("case,expected", [("completed", 202), ("active", 409),
    ("unsealed", 409), ("unknown", 404), ("origin", 403), ("intent", 403), ("binding", 409)])
def test_generation_route_validates_task_and_operator(tmp_path, case, expected):
    run = {"observer_id": IDENT, "state": "RUNNING" if case == "active" else "COMPLETED",
           "research_root": str(tmp_path)}
    ctx = SimpleNamespace(cache=SimpleNamespace(get=lambda: {"runs": [run]}))
    handler = Handler(**({"Origin": "http://elsewhere"} if case == "origin" else
                         {"X-TM-Operator-Intent": "0"} if case == "intent" else {}))
    target = "b" * 16 if case == "unknown" else IDENT
    spec = SimpleNamespace(research_root=tmp_path / "other" if case == "binding" else tmp_path)
    with patch("theory_master.cli_discovery.spec_from_task", return_value=spec) as resolve, \
            patch("theory_master.final_report.launch_final_report_job",
                  return_value=None if case == "unsealed" else {"state": "queued"}) as launch:
        assert post_task(handler, f"/api/run/{target}/final-report/generate", ctx, None)
        assert handler.result[1] == expected
        if case in {"completed", "unsealed"}:
            resolve.assert_called_once_with(tmp_path)
            launch.assert_called_once_with(spec, retry_failed=True)
        else:
            launch.assert_not_called()


@pytest.mark.parametrize("state,disabled", [("missing", False), ("queued", False),
    ("failed", False), ("running", True), ("reviewing", True), ("ready", None)])
def test_console_generation_button_follows_report_state(tmp_path, state, disabled):
    run = {"observer_id": IDENT, "research_root": str(tmp_path), "state": "COMPLETE"}
    metadata = {"state": state, "available": state == "ready"}
    with patch("tm_observer.final_report_ui.report_metadata", return_value=metadata):
        page = BeautifulSoup(render_report_entry(run, placement="console"), "html.parser")
        button = page.select_one("[data-final-report-generate]")
        if disabled is None:
            assert button is None
        else:
            assert button.has_attr("disabled") == disabled
        assert not BeautifulSoup(render_report_entry(run), "html.parser").select("button")


@pytest.mark.skipif(os.environ.get("TM_BROWSER_E2E") != "1", reason="browser test is opt-in")
@pytest.mark.parametrize("width", [390, 1440])
def test_generate_button_error_retry_and_refresh(tmp_path, width):
    from playwright.sync_api import sync_playwright, expect
    from tm_observer.web import ThreadingHTTPServer, make_handler

    cycle = tmp_path / "1"
    cycle.mkdir()
    (cycle / "run_state.json").write_text(json.dumps({"status": "COMPLETED", "cycle_id": "1", "master_id": 1}))
    run = {"observer_id": IDENT, "research_root": str(tmp_path), "cycle_dir": str(cycle),
           "state": "COMPLETED", "run_id": 1, "cycle": "1", "task_name": "报告按钮测试"}
    cache = SimpleNamespace(search_root=tmp_path, get=lambda: {"runs": [run]})
    metadata = {"state": "missing", "available": False}
    quota = {"accounts": [], "active_account": None}
    with patch("tm_observer.final_report_ui.report_metadata", side_effect=lambda root: dict(metadata)), \
            patch("tm_observer.run_tui_bridge.read_tui", return_value={"status": "COMPLETED", "cycle_id": "1", "parts": {}}), \
            patch("tm_observer.http_context.RequestContext.quota_snapshot", return_value=quota):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache, field_notes_state_dir=tmp_path / "state"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": width, "height": 950})
                errors, requests = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/codex-usage*.json", lambda route: route.fulfill(json=quota))
                def generate(route):
                    requests.append(route.request)
                    assert route.request.headers["x-tm-operator-intent"] == "1"
                    if len(requests) == 1:
                        route.fulfill(status=503, json={"error": "启动失败，请重试"})
                    else:
                        metadata.update(state="running")
                        route.fulfill(status=202, json={"ok": True, "state": "queued"})
                page.route("**/final-report/generate", generate)
                page.goto(f"http://127.0.0.1:{server.server_port}/run/{IDENT}/", wait_until="domcontentloaded")
                button = page.get_by_role("button", name="生成总报告", exact=True)
                page.locator('[data-final-report-entry="console"]').screenshot(path=str(tmp_path / f"report-button-{width}.png"))
                button.focus()
                button.press("Enter")
                expect(page.locator("[data-final-report-error]")).to_have_text("启动失败，请重试")
                expect(button).to_be_enabled()
                button.click()
                expect(page.locator("[data-final-report-generate]")).to_be_disabled()
                assert len(requests) == 2
                metadata.update(state="ready", available=True, title="测试总报告")
                expect(page.locator('[data-final-report-entry="console"] a')).to_be_visible(timeout=15000)
                assert page.locator("[data-final-report-generate]").count() == 0
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert not errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
