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
            "run_id": 1, "cycle": "1",
            "state": "LIVE" if status == "RUNNING" else status,
            "raw_status": status, "pid_alive": status == "RUNNING"}
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
                agent = page.locator("button.tm-worker-tab.is-selected[data-tui-process-actions]")
                playwright.expect(agent.locator("small")).to_have_text(status)
                playwright.expect(agent.locator(".tm-worker-dot")).to_have_attribute("data-status", status)
                quick = page.locator("#tm-operator-quick-dialog")
                assert quick.count() == 1
                original_url = page.url
                agent.focus()
                agent.press("Enter")
                playwright.expect(quick).to_be_visible()
                assert not requests
                assert page.url == original_url
                quick.locator("[data-operator-quick-close]").click()
                playwright.expect(quick).to_be_hidden()
                trigger = page.locator(".tm-tui-header [data-tui-process-actions]")
                playwright.expect(trigger).to_have_text(f"{status}▾")
                trigger.focus()
                trigger.press("Enter")
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


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
def test_final_report_entries_navigate_and_follow_console_refresh(tmp_path, width, height):
    """Report-only changes must update the existing console without reloading it."""
    from urllib.parse import urlsplit

    playwright = pytest.importorskip("playwright.sync_api")
    from tm_observer.web import ThreadingHTTPServer, make_handler

    research = tmp_path / "research"
    cycle = research / "1"
    cycle.mkdir(parents=True)
    (cycle / "run_state.json").write_text(json.dumps({
        "status": "COMPLETED", "cycle_id": "1", "master_id": 1,
    }), encoding="utf-8")
    ident = "b" * 16
    task = {"observer_id": ident, "research_root": str(research),
            "cycle_dir": str(cycle), "task_name": "Public toy final-report task",
            "run_id": 1, "cycle": "1", "state": "COMPLETED",
            "raw_status": "COMPLETED", "pid_alive": False}
    projection = {"status": "COMPLETED", "cycle_id": "1", "parts": {
        "header": "Process STOPPED   State COMPLETED\n"
                  "Cycle 1 / 1     Phase complete  Elapsed 00:12\n"
                  "Started 11-20T10:00:00  Cycle start 11-20T10:00:00  Cost —",
        "identity": "Task ID 1 Cycle ID 1",
    }}
    report_title = "Toy final report: two recorded settings"
    report_state = {"available": True, "state": "ready", "title": report_title,
                    "version": "c" * 64, "versions": []}
    quota = {"accounts": [], "active_account": None, "refreshing": False}

    class Cache:
        search_root = tmp_path

        def get(self):
            return {"runs": [task]}

    writes, external_requests, provider_reads, view_requests, errors = [], [], [], [], []
    metadata_roots = []

    def report_metadata(root):
        metadata_roots.append(root)
        assert root == research
        return dict(report_state)

    # Both the server-rendered quota projection and its browser fetch stay local.
    # In particular, request_update=True must never start a provider quota query.
    with patch("tm_observer.run_tui_bridge.read_tui", return_value=projection), \
            patch("tm_observer.final_report_ui.report_metadata", side_effect=report_metadata), \
            patch("tm_observer.http_context.RequestContext.quota_snapshot", return_value=quota):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Cache(), field_notes_state_dir=tmp_path / "state"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        report_url = origin + f"/research-explorer/?observer_id={ident}&final_report=1"
        console_url = origin + f"/run/{ident}/"
        try:
            with playwright.sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width": width, "height": height})
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    def fixture_request(route):
                        request = route.request
                        parsed = urlsplit(request.url)
                        if request.method == "POST":
                            writes.append(request.url)
                            route.abort()
                        elif parsed.netloc != f"127.0.0.1:{server.server_port}":
                            external_requests.append(request.url)
                            route.abort()
                        elif parsed.path == "/codex-usage-cached.json":
                            route.fulfill(json=quota)
                        elif parsed.path == "/codex-usage.json":
                            provider_reads.append(request.url)
                            route.abort()
                        elif parsed.path == "/research-explorer/":
                            route.fulfill(content_type="text/html", body=
                                '<!doctype html><html><title>Toy report reader</title>'
                                '<main id="toy-report">Frozen toy final report</main></html>')
                        else:
                            if parsed.path == f"/api/run/{ident}/view":
                                view_requests.append(request.url)
                            route.continue_()

                    page.route("**/*", fixture_request)
                    # Capture exactly the callback installed by the console's
                    # eight-second timer. Invoking it drives the real fetch and
                    # reconciliation without allowing unrelated periodic work.
                    page.add_init_script("""(() => {
                      const nativeInterval = window.setInterval.bind(window);
                      window.__reportPollDelays = [];
                      window.setInterval = (callback, delay, ...args) => {
                        if (delay === 8000 && callback.name === 'refresh') {
                          window.__reportPollDelays.push(delay);
                          window.__runReportPoll = () => callback(...args);
                          return 0;
                        }
                        return nativeInterval(callback, delay, ...args);
                      };
                    })();""")

                    task["state"] = "LIVE"
                    page.goto(origin + "/run/", wait_until="networkidle")
                    assert page.locator('td[data-label="最终总报告"] .tm-final-report-entry').count() == 0
                    if width <= 600:
                        playwright.expect(page.locator('td[data-label="最终总报告"]')).to_be_hidden()
                    assert metadata_roots == []
                    page.goto(console_url, wait_until="networkidle")
                    playwright.expect(page.locator('[data-final-report-entry="console"]')).to_be_hidden()
                    assert page.locator('.tm-final-report-console').count() == 0
                    assert page.locator('.tm-tui-titlebar').evaluate("e => getComputedStyle(e).display") == "flex"
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    assert metadata_roots == []

                    task["state"] = "COMPLETED"
                    page.goto(origin + "/run/", wait_until="networkidle")
                    playwright.expect(page.locator(".tm-run-table th", has_text="最终总报告")).to_be_visible()
                    list_entry = page.locator('td[data-label="最终总报告"] a[data-report-available="true"]')
                    playwright.expect(list_entry).to_be_visible()
                    playwright.expect(list_entry).to_have_attribute("data-report-state", "ready")
                    assert "已生成" in list_entry.get_attribute("aria-label")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    list_entry.focus()
                    list_entry.press("Enter")
                    playwright.expect(page).to_have_url(report_url)
                    playwright.expect(page.locator("#toy-report")).to_have_text("Frozen toy final report")

                    report_state.update(available=False, state="queued")
                    page.goto(console_url, wait_until="networkidle")
                    report = page.locator('[data-final-report-entry="console"] .tm-final-report-entry')
                    playwright.expect(report).to_be_visible()
                    playwright.expect(report).to_have_attribute("data-report-state", "queued")
                    playwright.expect(report).to_have_attribute("data-report-available", "false")
                    assert "排队中" in report.inner_text()
                    assert "已生成" not in report.inner_text()
                    assert report.get_attribute("href") is None
                    assert report.get_attribute("role") == "status"
                    assert report.locator("svg").count() == 0
                    playwright.expect(report.locator("strong")).to_have_text("最终总报告")
                    assert page.evaluate("window.__reportPollDelays") == [8000]
                    page.evaluate("""() => {
                      window.__originalReportScreen = document.querySelector('.tm-tui-screen');
                      window.__originalReportHeader = document.querySelector('[data-tui-pane="header"]');
                      window.__reportPageIdentity = {};
                    }""")
                    old_screen = page.locator(".tm-tui-screen")
                    playwright.expect(old_screen).to_have_attribute("data-tui-status", "COMPLETED")
                    playwright.expect(old_screen).to_have_attribute("data-tui-cycle", "1")

                    report_state.update(available=True, state="ready")
                    page.evaluate("window.__runReportPoll()")
                    playwright.expect(report).to_have_attribute("data-report-state", "ready")
                    playwright.expect(report).to_have_attribute("data-report-available", "true")
                    assert report.get_attribute("href") == report_url.removeprefix(origin)
                    playwright.expect(report.locator(".tm-final-report-title")).to_have_text(report_title)
                    assert "已生成" in report.get_attribute("aria-label")
                    assert page.url == console_url
                    assert page.evaluate("document.querySelector('.tm-tui-screen') === window.__originalReportScreen")
                    assert page.evaluate("document.querySelector('[data-tui-pane=header]') === window.__originalReportHeader")
                    assert page.evaluate("typeof window.__reportPageIdentity === 'object'")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    box = report.bounding_box()
                    assert 0 <= box["x"] and box["x"] + box["width"] <= width + 1
                    if width >= 1180:
                        titlebar = page.locator(".tm-tui-titlebar").bounding_box()
                        assert abs(box["x"] + box["width"] / 2 - titlebar["x"] - titlebar["width"] / 2) <= 2

                    report_state.update(state="needs_revision")
                    page.evaluate("window.__runReportPoll()")
                    playwright.expect(report).to_have_attribute("data-report-state", "needs_revision")
                    assert "待修订" in report.get_attribute("aria-label")
                    assert len(view_requests) == 2
                    assert page.url == console_url
                    assert page.evaluate("document.querySelector('.tm-tui-screen') === window.__originalReportScreen")
                    assert page.evaluate("document.querySelector('[data-tui-pane=header]') === window.__originalReportHeader")
                    report.press("Enter")
                    playwright.expect(page).to_have_url(report_url)
                    page.goto(origin + "/run/", wait_until="networkidle")
                    draft_entry = page.locator('td[data-label="最终总报告"] a')
                    playwright.expect(draft_entry).to_have_attribute("data-report-state", "needs_revision")
                    assert "待修订" in draft_entry.get_attribute("aria-label")

                    assert metadata_roots
                    assert not writes and not external_requests and not provider_reads
                    assert not errors
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
