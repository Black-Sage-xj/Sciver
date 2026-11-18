"""A first question needs no project selection; only explicit context binds one."""
from __future__ import annotations

import hashlib
import threading
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from tm_observer import research_chat as chat
from tm_observer.web import ThreadingHTTPServer, make_handler


PROMPT = "我对于高分子的拓扑和力学感兴趣,搜搜文献看看."


@pytest.mark.parametrize("entry", ("empty", "generic", "context", "selected", "attachment"))
def test_first_prompt_and_attachment_preserve_explicit_project_context(tmp_path, monkeypatch, entry):
    project = tmp_path / "existing-project"
    project.mkdir()
    (project / "exploration.canvas").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
    task = hashlib.sha256(str(project).encode()).hexdigest()[:16]
    state_dir = tmp_path / "observer"
    state = {"runs": [] if entry == "empty" else [{"observer_id": task, "task_name": "已有项目",
             "task_root": str(project), "research_root": str(project)}]}
    calls = []

    class OfflineBackend:
        def invoke(self, role, prompt, thread, model, provider):
            calls.append(prompt)
            return "first-use-fixture", "先查阅相关文献，再比较值得研究的问题。"

    def offline_send(notes, search_root, current, ident, payload):
        return chat.send(notes, search_root, current, ident, payload, backend=OfflineBackend())

    def forbidden_backend(*args, **kwargs):
        raise AssertionError("First-use acceptance must never initialize a real model")

    monkeypatch.setattr("tm_observer.research_chat_literature_flow.prepare_literature",
        lambda *args, **kwargs: {"job_id": "a" * 32, "status": "completed", "report_markdown": "# Offline orientation"})
    monkeypatch.setattr(chat, "begin_send", offline_send)
    monkeypatch.setattr("theory_master.codex_backend.CodexBackend", forbidden_backend)

    class Cache:
        search_root = tmp_path
        def get(self):
            return state

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Cache(), field_notes_state_dir=state_dir))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    catalog = {"available_models": ["gpt-6-sol"], "available_reasoning_efforts": ["medium"],
               "model_reasoning_efforts": {"gpt-6-sol": ["medium"]}}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.add_init_script("""localStorage.setItem('tm-research-chat-selected-task-v1', %s);
                localStorage.setItem('tm-research-chat-selected-mode-v1', 'frontier');
                localStorage.setItem('tm-research-chat-selected-skill-v1', 'research-progress');""" % repr(task))

            def route(request):
                if "/api/research-chat/models" in request.request.url:
                    request.fulfill(json=catalog)
                elif request.request.url.startswith(origin):
                    request.continue_()
                else:
                    request.abort()

            page.route("**/*", route)
            page.goto(origin + "/research-chat/?new=1" + ("&task_id=" + task if entry == "context" else ""))
            composer = page.get_by_role("textbox", name="科研问题", exact=True)
            expect(composer).to_be_visible()
            expect(page.get_by_label("选择项目任务", exact=True)).to_have_value(task if entry == "context" else "")
            if entry == "selected":
                page.get_by_label("选择项目任务", exact=True).select_option(task)
            composer.fill(PROMPT)
            if entry == "attachment":
                page.locator("[data-chat-first-files]").set_input_files({"name": "研究兴趣.txt",
                    "mimeType": "text/plain", "buffer": "高分子拓扑与力学".encode()})
            else:
                page.get_by_role("button", name="发送", exact=True).click()
            page.wait_for_url("**/research-chat/?session=*")
            ident = parse_qs(urlsplit(page.url).query)["session"][0]
            row = chat._read(chat._store(state_dir), ident)
            contextual = entry in {"context", "selected"}
            assert row["mode"] == ("frontier" if contextual else "preparation")
            assert row["task_id"] == (task if contextual else None)
            if entry == "attachment":
                expect(page.get_by_role("textbox", name="发送给 Sciver 的消息", exact=True)).to_have_value(PROMPT)
                expect(page.get_by_label("待发送附件", exact=True)).to_contain_text("研究兴趣.txt")
                assert calls == []
                assert not any(message["role"] == "user" for message in row["messages"])
            else:
                expect(page.get_by_text("先查阅相关文献，再比较值得研究的问题。", exact=True)).to_be_visible()
                assert len(calls) == 1
                assert next(message for message in row["messages"] if message["role"] == "user")["content"] == PROMPT
                assert ("Loaded research chat skill: research-preparation" in calls[0]) is not contextual
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
