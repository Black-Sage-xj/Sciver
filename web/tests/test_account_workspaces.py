"""Real independent backend processes; no model calls or scientific Cycles."""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tm_observer.account_gateway import make_gateway_handler
from tm_observer.account_workspaces import (
    AccountWorkspace, BackendProcesses, backend_environment, prepare_workspace, workspace_layout,
)
from tm_observer.auth import BasicAuthCredentials, ObservatoryAuth


def test_layout_preserves_primary_and_gives_new_accounts_stable_distinct_roots(tmp_path):
    auth = ObservatoryAuth((BasicAuthCredentials("old", "old-password"),
                            BasicAuthCredentials("sciver1", "first-password"),
                            BasicAuthCredentials("sciver2", "second-password")))
    layouts = workspace_layout(auth, tmp_path / "old-workspace", tmp_path / "state")
    assert layouts[0] == AccountWorkspace("old", tmp_path / "old-workspace", tmp_path / "state")
    assert all(row.isolated for row in layouts[1:])
    assert len({row.search_root for row in layouts}) == 3
    assert len({row.state_dir for row in layouts}) == 3
    assert len({row.home for row in layouts[1:]}) == 2
    assert workspace_layout(auth, tmp_path / "old-workspace", tmp_path / "state") == layouts
    with pytest.raises(ValueError, match="outside"):
        workspace_layout(auth, tmp_path, tmp_path / "state")


def test_seed_copies_only_provider_identity_and_preserves_private_account_state(tmp_path):
    source = tmp_path / "operator"
    source.mkdir()
    (source / "auth.json").write_text('{"tokens":{"account_id":"fixture"}}')
    (source / "history.jsonl").write_text('private conversations')
    layout = AccountWorkspace("a", tmp_path / "accounts/a/workspace", tmp_path / "accounts/a/state",
                              tmp_path / "accounts/a/home")
    prepare_workspace(layout, provider_auth=source / "auth.json")
    assert (layout.home / ".codex/auth.json").read_bytes() == (source / "auth.json").read_bytes()
    assert not (layout.home / ".codex/auth.json").is_symlink()
    assert not (layout.home / ".codex/history.jsonl").exists()
    assert (layout.home / ".codex/auth.json").stat().st_mode & 0o777 == 0o600
    (layout.home / ".codex/auth.json").write_text("refreshed independently")
    prepare_workspace(layout, provider_auth=source / "auth.json")
    assert (layout.home / ".codex/auth.json").read_text() == "refreshed independently"
    environment = backend_environment(layout, {"HOME": str(source), "PATH": "/usr/bin",
        "THEORYMASTER_PROFILE_FILE": "/operator/profile", "CODEX_HOME": "/operator/codex",
        "THEORY_MASTER_DATA_ROOT": "/operator/tasks", "HTTPS_PROXY": "http://127.0.0.1:7890",
        "OPENAI_API_KEY": "operator-key", "MINERU_API_KEY": "operator-pdf-key"})
    assert environment["HOME"] == str(layout.home)
    assert environment["THEORY_MASTER_DATA_ROOT"] == str(layout.search_root / ".theory-master-tasks")
    assert environment["THEORYMASTER_CODEX_HOME"] == str(layout.home / ".codex-theory-master")
    assert "THEORYMASTER_PROFILE_FILE" not in environment and "CODEX_HOME" not in environment
    assert "OPENAI_API_KEY" not in environment and "MINERU_API_KEY" not in environment
    assert environment["HTTPS_PROXY"] == "http://127.0.0.1:7890"


def test_backend_processes_keep_chats_tasks_and_sdk_data_independent(tmp_path):
    accounts = (BasicAuthCredentials("alice", "alice-password"), BasicAuthCredentials("bob", "bob-password"))
    layouts = [AccountWorkspace(account.username, tmp_path / account.username / "workspace",
                               tmp_path / account.username / "state", tmp_path / account.username / "home")
               for account in accounts]
    processes = BackendProcesses()
    server = None
    try:
        targets = {}
        for account, layout in zip(accounts, layouts):
            prepare_workspace(layout)
            targets[account.username] = processes.start(layout, account, cache_ttl=0.2,
                archify_root=tmp_path / "unused-renderer", static_refresh=3600)
        assert processes.processes[0].pid != processes.processes[1].pid
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_gateway_handler(ObservatoryAuth(accounts), targets))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def request(account, path, payload=None, *, workspace_scope=None):
            token = base64.b64encode(f"{account.username}:{account.password}".encode()).decode()
            headers = {"Authorization": "Basic " + token, "Origin": base, "X-TM-Operator-Intent": "1"}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            if workspace_scope is not None:
                headers["X-Sciver-Workspace"] = workspace_scope
            with urlopen(Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers=headers), timeout=15) as response:
                return json.loads(response.read())

        for account in accounts:
            assert request(account, "/observer_state.json")["runs"] == []
        scope_a = hashlib.sha256(str(layouts[0].search_root.resolve()).encode()).hexdigest()[:16]
        with pytest.raises(HTTPError) as stale_read:
            request(accounts[1], "/observer_state.json", workspace_scope=scope_a)
        assert stale_read.value.code == 409
        with pytest.raises(HTTPError) as stale_tab:
            request(accounts[1], "/api/research-chat/session",
                    {"mode": "preparation", "entry": "clear"}, workspace_scope=scope_a)
        assert stale_tab.value.code == 409
        session = request(accounts[0], "/api/research-chat/session",
                          {"mode": "preparation", "entry": "clear", "title": "Alice-only task"},
                          workspace_scope=scope_a)["session_id"]
        initial = request(accounts[0], f"/api/research-chat/{session}/preparation")
        plan = ("# Goal\n\nIdentify the measured scaling.\n\n# Research Contract\n\nCompare the specified mechanisms.\n\n"
                "# Final Gate\n\nResolve the signed prediction.\n\n# Required Context\n\nUse the supplied definitions.\n")
        draft = request(accounts[0], f"/api/research-chat/{session}/preparation",
                        {"expected_revision": initial["revision"], "draft": plan})
        created = request(accounts[0], f"/api/research-chat/{session}/create-project",
                          {"confirm": True, "expected_revision": draft["revision"]})
        assert created["graph_empty"] and created["scientific_model_empty"] and not created["execution_started"]
        assert Path(created["research_root"]).is_relative_to(layouts[0].search_root / ".theory-master-tasks")
        assert Path(created["task_root"]).is_relative_to(layouts[0].search_root)
        state = request(accounts[0], "/observer_state.json")
        assert created["observer_id"] in {run["observer_id"] for run in state["runs"]}
        # After the optimistic creation cache expires, persisted discovery must
        # retain the task even with its SDK data root beneath the search root.
        time.sleep(0.3)
        request(accounts[0], "/observer_state.json")
        time.sleep(0.3)
        assert created["observer_id"] in {run["observer_id"] for run in request(accounts[0], "/observer_state.json")["runs"]}
        assert request(accounts[1], "/observer_state.json")["runs"] == []
        for path in (f"/api/research-chat/{session}/preparation", f"/api/run/{created['observer_id']}/monitor"):
            with pytest.raises(HTTPError) as error:
                request(accounts[1], path)
            assert error.value.code == 404
        assert (layouts[0].state_dir / "research-chat" / f"{session}.json").is_file()
        assert not (layouts[1].state_dir / "research-chat" / f"{session}.json").exists()
        assert not list(layouts[1].search_root.rglob("task.json"))
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        processes.close()
