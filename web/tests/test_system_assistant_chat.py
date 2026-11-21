"""System/research isolation and live Codex skill sharing; no paid model calls."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from theory_master.codex_backend import CodexBackend
from theory_master.codex_shared_skills import shared_skills
from tm_observer import research_chat as chat
from tm_observer.research_chat_management import list_sessions
from tm_observer.web import ThreadingHTTPServer, make_handler


def skill(root, name, description="Inspect system logs."):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\nRead the latest log.\n")
    return directory


class OfflineBackend:
    def __init__(self):
        self.calls = []

    def ensure_thread(self, role, thread, model, provider):
        return thread or role + "-thread"

    def invoke(self, role, prompt, thread, model, provider):
        self.calls.append((role, prompt, thread))
        return thread, "已根据日志定位连接问题。"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    skill(root, "system-debug")
    monkeypatch.setenv("THEORYMASTER_CODEX_SKILL_ROOTS", str(root))
    state = {"runs": [{"observer_id": "a" * 16, "task_name": "科研项目",
                       "task_root": str(tmp_path / "project"), "research_root": str(tmp_path / "runtime")} ]}
    return tmp_path, tmp_path / "observer", state, root


def test_system_context_and_threads_are_independent_and_skills_refresh(workspace, monkeypatch):
    root, notes, state, skills = workspace
    scientific = chat.create(notes, state, {"mode": "project", "task_id": "a" * 16})["session_id"]
    before = chat._file(chat._store(notes), scientific).read_bytes()
    system = chat.create(notes, state, {"mode": "system", "task_id": "a" * 16})["session_id"]
    def forbidden(*args, **kwargs):
        raise AssertionError("System Chat must not load scientific or Literature context")
    monkeypatch.setattr(chat, "_chat_task_binding", forbidden)
    monkeypatch.setattr("tm_observer.research_chat_literature_flow.prepare_literature", forbidden)
    backend = OfflineBackend()
    chat.send(notes, root, state, system, {"content": "启动失败，检查一下日志"}, backend=backend)
    skill(skills, "system-debug", "Updated diagnostics.")
    chat.send(notes, root, state, system, {"content": "再检查连接配置"}, backend=backend)
    assert len(backend.calls) == 2
    for role, prompt, thread in backend.calls:
        assert role == "system_assistant" and thread == "system_assistant-thread"
        assert "Live shared Codex skill catalog" in prompt
        assert str(skills / "system-debug" / "SKILL.md") in prompt
        assert "Loaded research chat skill" not in prompt
        assert "Research Plan:\n" not in prompt and "Canonical SQLite" not in prompt
    assert "Updated diagnostics." in backend.calls[1][1]
    assert "Latest operator message (continue the existing thread)" in backend.calls[1][1]
    row = chat.snapshot(notes, state, system)["session"]
    assert row["skill"] is None and row["title"] == "启动失败，检查一下日志"
    assert chat._file(chat._store(notes), scientific).read_bytes() == before
    assert [row["id"] for row in list_sessions(notes, channel="system")] == [system]
    assert [row["id"] for row in list_sessions(notes, channel="research")] == [scientific]
    assert not (root / "runtime").exists()


def test_system_without_task_and_invalid_cross_channel_skill(workspace):
    root, notes, state, _ = workspace
    ident = chat.create(notes, {"runs": []}, {"mode": "system"})["session_id"]
    chat.send(notes, root, state, ident, {"content": "服务连接失败"}, backend=OfflineBackend())
    for payload in ({"mode": "system", "task_id": "missing"}, {"mode": "system", "skill": "research-progress"}):
        with pytest.raises(chat.ResearchChatError):
            chat.create(notes, state, payload)
    with pytest.raises(chat.ResearchChatError, match="科研讨论"):
        chat.send(notes, root, state, ident, {"content": "/direction 修改方向"}, backend=OfflineBackend())


def test_skill_discovery_shares_symlinks_and_prefers_project_sources(tmp_path, monkeypatch):
    monkeypatch.delenv("THEORYMASTER_CODEX_SKILL_ROOTS", raising=False)
    home, project = tmp_path / "home", tmp_path / "project"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    original = skill(tmp_path / "original", "debug", "Project diagnostics.")
    shared = project / ".codex" / "skills"
    shared.mkdir(parents=True)
    (shared / "debug").symlink_to(original, target_is_directory=True)
    skill(home / ".codex" / "skills", "debug", "Global diagnostics.")
    skill(home / ".agents" / "skills", "browser")
    refs = {ref.name: ref for ref in shared_skills(project)}
    assert refs["debug"].path == original.resolve()
    assert refs["debug"].description == "Project diagnostics."
    assert "browser" in refs


def test_sdk_system_identity_and_workspace_write_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORYMASTER_COMPUTE_PYTHON", __import__("sys").executable)
    sdk = SimpleNamespace(Sandbox=SimpleNamespace(workspace_write="write", read_only="read"))
    client = Mock()
    client.thread_start.return_value = SimpleNamespace(id="system-thread")
    backend = CodexBackend(tmp_path, routes=chat._chat_routes("gpt-6-sol", "medium", "system_assistant"))
    with patch.dict("sys.modules", {"openai_codex": sdk}), patch.object(backend, "_ensure_client", return_value=client):
        backend.ensure_thread("system_assistant", None, "gpt-6-sol", "openai")
    args = client.thread_start.call_args.kwargs
    assert args["sandbox"] == "write"
    assert "Sciver System Assistant" in args["developer_instructions"]
    assert "Research Chat runtime AGENTS.md" not in args["developer_instructions"]


def test_system_async_send_edit_retry_and_fork_keep_their_role(workspace, monkeypatch):
    root, notes, state, _ = workspace
    backend = OfflineBackend()
    monkeypatch.setattr("theory_master.codex_backend.CodexBackend", lambda *args, **kwargs: backend)
    ident = chat.create(notes, state, {"mode": "system"})["session_id"]
    def completed():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = chat.snapshot(notes, state, ident)["session"]
            if not row.get("streaming") and (row.get("active_run") or {}).get("status") == "complete":
                return row
            time.sleep(0.01)
        raise AssertionError("Offline system reply did not complete")
    chat.begin_send(notes, root, state, ident, {"content": "诊断连接"})
    row = completed()
    message = next(m for m in row["messages"] if m["role"] == "user")
    chat.begin_send(notes, root, state, ident, {"content": "诊断启动", "edit_message_id": message["id"]})
    row = completed()
    fork = chat.fork_branch(notes, ident, {"message_id": row["messages"][-1]["id"]})["session_id"]
    assert chat.snapshot(notes, state, fork)["session"]["mode"] == "system"
    assert chat.snapshot(notes, state, fork)["session"]["codex_thread_id"] is None
    row["active_run"]["status"] = "interrupted"
    chat._write(chat._store(notes), row)
    chat._runtime_update(chat._store(notes), ident, lambda data: data.update({"run": {}}))
    chat.begin_retry(notes, root, state, ident)
    completed()
    assert len(backend.calls) == 3
    assert all(call[0] == "system_assistant" for call in backend.calls)


@pytest.mark.parametrize("attach", [False, True])
def test_browser_channel_switch_history_drafts_and_offline_send(workspace, monkeypatch, attach):
    from playwright.sync_api import expect, sync_playwright
    root, notes, state, _ = workspace
    science = chat.create(notes, state, {"mode": "project", "task_id": "a" * 16})["session_id"]
    chat.rename_session(notes, science, "科研历史独立保留")
    backend = OfflineBackend()
    monkeypatch.setattr(chat, "begin_send", lambda n, r, s, i, p: chat.send(n, r, s, i, p, backend=backend))
    class Cache:
        search_root = root
        def get(self):
            return state
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Cache(), field_notes_state_dir=notes))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/research-chat/models*", lambda route: route.fulfill(json={"available_models": ["gpt-6-sol"], "available_reasoning_efforts": ["medium"]}))
            page.goto(origin + "/research-chat/?new=1")
            page.get_by_role("textbox", name="科研问题", exact=True).fill("保留科研草稿")
            page.get_by_role("link", name="系统助手", exact=True).click()
            composer = page.get_by_role("textbox", name="系统问题", exact=True)
            expect(composer).to_have_value("")
            expect(page.locator("#tm-research-history-list")).not_to_contain_text("科研历史独立保留")
            composer.fill("保留系统草稿")
            page.get_by_role("link", name="科研讨论", exact=True).click()
            page.get_by_role("link", name="新对话", exact=True).click()
            expect(page.get_by_role("textbox", name="科研问题", exact=True)).to_have_value("保留科研草稿")
            page.get_by_role("link", name="系统助手", exact=True).click()
            expect(composer).to_have_value("保留系统草稿")
            page.wait_for_load_state("load")
            assert not errors, errors
            if attach:
                page.locator('[data-chat-first-files]').set_input_files({"name": "error-log.txt", "mimeType": "text/plain", "buffer": b"connection refused"})
            else:
                page.get_by_role("button", name="发送", exact=True).click()
            page.wait_for_url("**/research-chat/?session=*")
            ident = parse_qs(urlsplit(page.url).query)["session"][0]
            assert chat.snapshot(notes, state, ident)["session"]["mode"] == "system"
            if attach:
                assert backend.calls == []
                expect(page.get_by_role('textbox', name='发送给 Sciver 的消息', exact=True)).to_have_value('保留系统草稿')
            else:
                expect(page.get_by_text("已根据日志定位连接问题。", exact=True)).to_be_visible()
                assert backend.calls[0][0] == "system_assistant"
            expect(page.get_by_role("link", name="系统助手", exact=True)).to_have_attribute("aria-current", "page")
            expect(page.locator("#tm-research-history-list")).not_to_contain_text("科研历史独立保留")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
