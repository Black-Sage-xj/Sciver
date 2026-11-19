"""Chat SDK construction grants only its bound account's operational roots."""
from __future__ import annotations

from pathlib import Path

from tm_observer import research_chat as chat


def test_real_chat_backend_receives_account_roots_and_restarts_old_read_only_thread(tmp_path, monkeypatch):
    from theory_master import codex_accounts

    state_dir, search = tmp_path / "account/state", tmp_path / "account/workspace"
    search.mkdir(parents=True)
    runtime, accounts = tmp_path / "account/home/.codex-theory-master", tmp_path / "account/home/.codex-accounts"
    monkeypatch.setattr(codex_accounts, "runtime_home", lambda: runtime)
    monkeypatch.setattr(codex_accounts, "account_root", lambda: accounts)
    selected = []
    class Backend:
        def __init__(self, cwd, routes, *, writable_roots):
            selected.append((Path(cwd), writable_roots))
        def ensure_thread(self, role, thread, model, provider):
            assert thread is None
            return "writable-native-thread"
        def invoke(self, role, prompt, thread, model, provider, **kwargs):
            assert "先计划、立项，再执行科研" in prompt
            return thread, "先梳理问题，再保存计划供审阅。"
        def close(self):
            pass
    monkeypatch.setattr("theory_master.codex_backend.CodexBackend", Backend)
    state = {"runs": []}
    ident = chat.create(state_dir, state, {"mode": "preparation", "entry": "exploratory"})["session_id"]
    store = chat._store(state_dir)
    row = chat._read(store, ident)
    row.update(agent_policy_version=9, codex_thread_id="previous-read-only-thread")
    chat._write(store, row)

    chat.send(state_dir, search, state, ident, {"content": "请帮我整理研究计划"})

    cwd, roots = selected[0]
    assert cwd == state_dir / "research-preparations" / ident
    assert set(roots) == {state_dir.resolve(), search.resolve(), runtime.resolve(), accounts.resolve()}
    assert tmp_path / "other-account" not in roots
    assert Path(__file__).resolve().parents[2] not in roots
    row = chat._read(store, ident)
    assert row["agent_policy_version"] == chat._AGENT_POLICY_VERSION
    assert row["codex_thread_id"] == "writable-native-thread"
