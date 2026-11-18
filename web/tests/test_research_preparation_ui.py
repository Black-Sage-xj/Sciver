"""Preparation is a first-class entry before a scientific task exists."""
from __future__ import annotations

import threading
import os
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from tm_observer.research_chat_ui import page_body


def test_both_new_project_routes_are_visible_with_and_without_existing_tasks():
    for tasks in ([], [{"id": "a" * 16, "label": "Existing research"}]):
        document = BeautifulSoup(page_body({"tasks": tasks, "sessions": [], "session": None}), "html.parser")
        assert document.select_one('[data-preparation-entry="clear"]')
        assert document.select_one('[data-preparation-entry="exploratory"]')
        assert not document.select_one('[data-prep-draft]')
        assert not document.select_one('[data-prep-create]')
        if tasks:
            assert document.select_one('[data-research-task]')
            assert document.select_one('form[data-research-direct]')


def test_preparation_session_uses_normal_chat_and_an_explicit_plan_review():
    session = {"id": "c" * 32, "mode": "preparation", "task_id": None, "title": "Project preparation",
               "preparation": {"entry": "exploratory", "title": '<unsafe "title">',
                               "intent": "A phenomenon", "draft": "", "revision": "r1"}}
    page = page_body({"tasks": [], "sessions": [], "session": session})
    document = BeautifulSoup(page, "html.parser")
    assert document.select_one('#tm-research-chat-app[data-mode="research"]')
    assert document.select_one('[data-session-mode="preparation"]')
    assert document.select_one('[data-prep-inline] [data-preparation-review]').text == "审阅计划并创建项目"
    assert document.select_one('[data-prep-inline]').has_attr("hidden")
    assert document.select_one('[data-prep-secondary]').has_attr("hidden")
    assert document.select_one('[data-prep-transport]')["data-prep-ready"] == "false"
    assert "项目筹备 · 尚未立项" not in document.select_one('.tm-prep-banner').text
    assert document.select_one('[data-prep-title]')["value"] == '<unsafe "title">'
    assert document.select_one('[data-prep-draft]').text == ""
    assert not document.select_one('[data-research-task]')
    assert '<unsafe "title">' not in page


def test_existing_project_and_supervisor_sessions_keep_their_current_flow():
    for mode in ("project", "frontier"):
        document = BeautifulSoup(page_body({"tasks": [], "sessions": [],
            "session": {"id": "a" * 32, "mode": mode, "messages": []}}), "html.parser")
        assert document.select_one('#tm-research-chat-app')
        assert not document.select_one('[data-preparation-review]')
    supervisor = BeautifulSoup(page_body({"tasks": [], "sessions": []}, mode="supervisor"), "html.parser")
    assert not supervisor.select_one('[data-preparation-entry]')


def test_browser_preparation_both_routes_save_conflict_and_create_real_task(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    from tm_observer.collector import collect_state, discover_tasks
    from tm_observer.research_chat import send
    from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler

    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "data"))
    calls = []
    class FakeBackend:
        def invoke(self, role, prompt, thread, model, provider):
            calls.append(prompt)
            return "offline-preparation", "我们可以先澄清问题与相关文献，再逐步形成研究计划。"
    def offline_send(notes, search_root, state, session, payload):
        return send(notes, search_root, state, session, payload, backend=FakeBackend())
    monkeypatch.setattr("tm_observer.research_chat.begin_send", offline_send)
    cache = StateCache(tmp_path)
    monkeypatch.setattr(cache, "get", lambda: collect_state(tmp_path, include_git_info=False))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache, field_notes_state_dir=tmp_path / "observer"))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = "http://127.0.0.1:" + str(server.server_port)
    catalog = {"available_models": ["gpt-6-sol"], "available_reasoning_efforts": ["medium"],
               "model_reasoning_efforts": {"gpt-6-sol": ["medium"]}}
    plan = "# Goal\n\nExplain the observed periodic change.\n\n# Research Contract\n\nCompare two explanations using independent observations.\n\n# Final Gate\n\nReport a discriminating test with uncertainty.\n\n# Required Context\n\nThe observation is preliminary and needs verification.\n"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            def route(request):
                if "/api/research-chat/models" in request.request.url:
                    request.fulfill(json=catalog)
                elif request.request.url.startswith(origin):
                    request.continue_()
                else:
                    request.abort()
            page.route("**/*", route)
            created_requests = []
            page.on("request", lambda request: created_requests.append(request.url) if request.url.endswith("/create-project") else None)
            for index, entry in enumerate(("clear", "exploratory")):
                page.goto(origin + "/research-chat/?new=1")
                if index == 0:
                    expect(page.locator('[data-entry-choice="existing"]')).to_be_disabled()
                    expect(page.locator('[data-entry-panel="new"]')).to_be_visible()
                else:
                    expect(page.locator('[data-entry-panel="existing"]')).to_be_visible()
                    expect(page.locator('[data-entry-panel="existing"] .tm-research-start')).to_have_count(3)
                    page.locator('[data-entry-choice="new"]').click()
                    expect(page.locator('[data-entry-panel="existing"]')).not_to_be_visible()
                    page.locator('[data-entry-choice="existing"]').click()
                    expect(page.locator('[data-entry-panel="existing"]')).to_be_visible()
                    page.locator('[data-entry-choice="new"]').click()
                expect(page.locator('[data-entry-panel="new"] .tm-research-start')).to_have_count(3)
                expect(page.locator('[data-research-mode="migration"]')).to_be_visible()
                expect(page.locator('[data-preparation-entry="clear"]')).to_be_visible()
                expect(page.locator('[data-preparation-entry="exploratory"]')).to_be_visible()
                if index == 0 and os.environ.get("SCIVER_PREPARATION_PREVIEW"):
                    page.evaluate("document.documentElement.setAttribute('data-sciver-theme','white')")
                    page.screenshot(path=os.environ["SCIVER_PREPARATION_PREVIEW"])
                page.locator('[data-preparation-entry="' + entry + '"]').click()
                page.wait_for_url("**/research-chat/?session=*")
                session = parse_qs(urlsplit(page.url).query)["session"][0]
                expect(page.locator('[data-prep-inline]')).not_to_be_visible()
                expect(page.locator('[data-prep-secondary]')).not_to_be_visible()
                expect(page.locator('[data-research-task]')).to_have_count(0)
                assert len(calls) == index
                composer = page.get_by_role("textbox", name="发送给 Sciver 的消息")
                composer.fill("我观察到周期性的变化，想先澄清研究问题。")
                composer.press("Enter")
                expect(page.get_by_text("我们可以先澄清问题与相关文献，再逐步形成研究计划。", exact=True)).to_be_visible()
                assert len(calls) == index + 1
                expect(page.locator('[data-prep-inline]')).not_to_be_visible()
                base = origin + "/api/research-chat/" + session
                current = page.request.get(base + "/preparation").json()
                response = page.request.post(base + "/preparation", data={"title": "Draft discussion", "intent": "An early interest", "draft": "A draft question, still incomplete.", "expected_revision": current["revision"]},
                    headers={"Origin": origin, "X-TM-Operator-Intent": "1"})
                assert response.ok and response.json()["plan_ready"] is False
                expect(page.locator('[data-prep-secondary]')).to_be_visible(timeout=7000)
                expect(page.locator('[data-prep-inline]')).not_to_be_visible()
                page.locator('[data-prep-secondary]').click()
                dialog = page.locator('[data-preparation-dialog]')
                expect(dialog).to_be_visible()
                page.locator('[data-prep-title]').fill("Periodic observation " + entry)
                page.locator('[data-prep-intent]').fill("Clarify the cause before committing to an explanation.")
                page.locator('[data-prep-draft]').fill("")
                page.locator('[data-prep-save]').click()
                expect(page.locator('[data-prep-status]')).to_contain_text("草稿已保存")
                page.locator('[data-prep-close]').click()
                expect(dialog).not_to_be_visible()
                expect(page.locator('[data-prep-secondary]')).not_to_be_visible()
                current = page.request.get(base + "/preparation").json()
                response = page.request.post(base + "/preparation", data={"draft": plan, "expected_revision": current["revision"]},
                    headers={"Origin": origin, "X-TM-Operator-Intent": "1"})
                assert response.ok and response.json()["plan_ready"] is True
                inline = page.locator('[data-prep-inline]')
                expect(inline).to_be_visible(timeout=7000)
                assert inline.evaluate("element => element.parentElement.hasAttribute('data-preparation-inline-slot')")
                assert page.locator('.tm-aui-message-list').evaluate("element => element.lastElementChild.hasAttribute('data-preparation-inline-slot')")
                page.locator('[data-preparation-inline-slot]').evaluate("element => element.dataset.streaming = 'true'")
                expect(inline).not_to_be_visible()
                page.locator('[data-preparation-inline-slot]').evaluate("element => element.dataset.streaming = 'false'")
                expect(inline).to_be_visible()
                page.locator('[data-prep-inline] [data-preparation-review]').click()
                expect(page.locator('[data-prep-title]')).to_have_value("Periodic observation " + entry)
                expect(page.locator('[data-prep-intent]')).to_have_value("Clarify the cause before committing to an explanation.")
                page.locator('[data-prep-draft]').fill(plan)
                current = page.request.get(base + "/preparation").json()
                response = page.request.post(base + "/preparation", data={"title": "Remote revision", "intent": "Changed elsewhere", "draft": "A competing draft revision", "expected_revision": current["revision"]},
                    headers={"Origin": origin, "X-TM-Operator-Intent": "1"})
                assert response.ok
                page.locator('[data-prep-save]').click()
                expect(page.locator('[data-prep-status]')).to_contain_text("当前输入已保留")
                expect(page.locator('[data-prep-draft]')).to_have_value(plan)
                page.once("dialog", lambda confirmation: confirmation.accept())
                page.locator('[data-prep-reload]').click()
                expect(page.locator('[data-prep-title]')).to_have_value("Remote revision")
                page.locator('[data-prep-title]').fill("Periodic observation " + entry)
                page.locator('[data-prep-intent]').fill("Clarify and compare the competing explanations.")
                page.locator('[data-prep-draft]').fill(plan)
                page.locator('[data-prep-create]').evaluate("button => {button.click();button.click();}")
                expect(page.locator('[data-prep-status]')).to_contain_text("项目已创建")
                assert len(created_requests) == index + 1
                assert len(calls) == index + 1
                assert len(discover_tasks(tmp_path)) == index + 1
                page.locator('[data-prep-close]').click()
                link = page.locator('.tm-prep-banner [data-prep-created] a')
                expect(link).to_be_visible()
                link.click()
                page.wait_for_url("**/run/*/")
            page.goto(origin + "/research-chat/?new=1")
            task_select = page.locator('[data-research-task]')
            task_select.select_option(index=1)
            page.locator('[data-entry-choice="new"]').click()
            page.locator('[data-research-mode="migration"]').click()
            page.wait_for_url("**/research-chat/?session=*")
            migration = parse_qs(urlsplit(page.url).query)["session"][0]
            from tm_observer import research_chat
            row = research_chat._read(research_chat._store(tmp_path / "observer"), migration)
            assert row["mode"] == "migration" and row["task_id"] is None
            assert len(calls) == 2
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
