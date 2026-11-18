"""Account boundaries use real HTTP requests and never invoke a research model."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler
from tm_observer.workspace_scope import WorkspaceScopeError, scoped_state, workspace_path


def task(root: Path, name: str = "project") -> dict:
    home, research = root / "projects" / name, root / "data" / name
    (home / ".theory-master").mkdir(parents=True)
    research.mkdir(parents=True)
    config = home / "theory-master.config.json"
    config.write_text(json.dumps({
        "schema": "theory-master/system/v1", "task_home": str(home),
        "research_root": str(research), "plan_path": str(home / "research_plan.md"),
        "branch_canvas": str(home / "exploration.canvas"), "notes_dir": str(home / "notes"),
        "graph_database": str(research / "science_graph.sqlite3"),
    }))
    (home / ".theory-master" / "task.json").write_text(json.dumps({
        "schema": "theory-master/task/v2", "config_path": str(config)}))
    return {"observer_id": hashlib.sha256(str(research).encode()).hexdigest()[:16],
            "task_root": str(home), "research_root": str(research), "task_name": name}


@contextmanager
def http_workspace(root: Path, *, runs: list[dict] | None = None, isolated: bool = True,
                   manager=None, state_dir: Path | None = None):
    root.mkdir(parents=True, exist_ok=True)
    state_dir = state_dir or root / "web-state"
    cache = StateCache(root)
    cache._state = {"search_root": str(root), "runs": runs or [],
                    "system": {"task_count": len(runs or [])}}
    cache._at = cache._git_at = time.monotonic()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
        cache, manager, field_notes_state_dir=state_dir, isolated_workspace=isolated))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", cache, state_dir
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(base: str, path: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(base + path, data=data,
                  headers={"Content-Type": "application/json", "Origin": base,
                           "X-TM-Operator-Intent": "1"})
    try:
        with urlopen(req, timeout=5) as response:
            return response.status, response.read(), response.headers
    except HTTPError as response:
        return response.code, response.read(), response.headers


def test_path_scope_rejects_siblings_symlinks_and_relative_paths(tmp_path):
    root, other = tmp_path / "account-a", tmp_path / "account-b"
    root.mkdir()
    other.mkdir()
    (root / "shortcut").symlink_to(other, target_is_directory=True)
    assert workspace_path(str(root / "new-project"), root) == root / "new-project"
    for path in (str(other), str(root / "shortcut"), "../account-b", str(root / "..")):
        with pytest.raises(WorkspaceScopeError):
            workspace_path(path, root)


def test_state_filters_external_task_roots_and_config_references(tmp_path, monkeypatch):
    root, other = tmp_path / "account-a", tmp_path / "account-b"
    own, foreign = task(root), task(other)
    poisoned = task(root, "poisoned")
    config = Path(poisoned["task_root"]) / "theory-master.config.json"
    values = json.loads(config.read_text())
    values["graph_database"] = str(other / "private.sqlite3")
    config.write_text(json.dumps(values))
    monkeypatch.delenv("THEORY_MASTER_DATA_ROOT", raising=False)
    invalid_id = {**own, "observer_id": "0" * 16}
    unregistered = {**own, "task_root": str(root / "missing-project")}
    state = {"runs": [own, foreign, poisoned, invalid_id, unregistered], "system": {"task_count": 5}}
    assert scoped_state(state, root)["runs"] == [own]
    with http_workspace(root, runs=state["runs"]) as (base, cache, _state):
        status, body, _ = request(base, "/observer_state.json")
        assert status == 200
        view = json.loads(body)
        assert view["runs"] == [own]
        assert view["system"]["task_count"] == 1
        assert str(other).encode() not in body
        assert cache.isolated_workspace


def test_explicit_account_data_root_is_allowed_but_not_other_data(tmp_path, monkeypatch):
    root, data = tmp_path / "account", tmp_path / "runtime"
    own = task(root)
    research = data / "project"
    research.mkdir(parents=True)
    config = Path(own["task_root"]) / "theory-master.config.json"
    values = json.loads(config.read_text())
    values.update(research_root=str(research), graph_database=str(research / "science.sqlite3"))
    config.write_text(json.dumps(values))
    own.update(research_root=str(research),
               observer_id=hashlib.sha256(str(research).encode()).hexdigest()[:16])
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(data))
    assert scoped_state({"runs": [own]}, root)["runs"] == [own]
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(tmp_path / "different-runtime"))
    assert scoped_state({"runs": [own]}, root)["runs"] == []


def test_import_preview_and_direct_session_creation_enforce_same_scope(tmp_path):
    root, other = tmp_path / "account-a", tmp_path / "account-b"
    other.mkdir()
    (other / "notes.md").write_text("Private research")
    root.mkdir()
    source = root / "inputs"
    source.mkdir()
    (source / "notes.md").write_text("Own research")
    with http_workspace(root) as (base, _cache, state):
        for path, payload in (
            ("/api/research-chat/import-preview", {"source_directory": str(other)}),
            ("/api/research-chat/session", {"mode": "setup", "name": "Forbidden",
                "source_directory": str(other), "read_source": True, "source_files": ["notes.md"]}),
        ):
            assert request(base, path, payload)[0] == 403
        status, body, _ = request(base, "/api/research-chat/import-preview", {"source_directory": str(source)})
        assert status == 200
        assert json.loads(body)["files"] == [{"path": "notes.md", "size": 12}]
        status, body, _ = request(base, "/api/research-chat/session", {
            "mode": "setup", "name": "Own project", "source_directory": str(source),
            "read_source": True, "source_files": ["notes.md"]})
        assert status == 200
        assert (state / "research-chat" / (json.loads(body)["session_id"] + ".json")).is_file()


def test_vault_browse_and_move_cannot_leave_workspace(tmp_path, monkeypatch):
    root, other = tmp_path / "account-a", tmp_path / "account-b"
    own = task(root)
    other.mkdir()
    calls = []

    def browse(_path, query, *_args, **_kwargs):
        from urllib.parse import parse_qs
        target = parse_qs(query)["path"][0]
        calls.append(target)
        return json.dumps({"path": target, "parent": str(Path(target).parent), "directories": []}).encode(), "application/json", None

    monkeypatch.setattr("tm_observer.web.vault_get", browse)
    monkeypatch.setattr("tm_observer.web.vault_post", lambda *_args, **_kwargs: calls.append("moved") or {"ok": True})
    with http_workspace(root, runs=[own]) as (base, _cache, _state):
        path = f'/api/canvas-md/tasks/{own["observer_id"]}'
        assert request(base, path + "/vault-directories?" + urlencode({"path": str(other)}))[0] == 403
        assert calls == []
        assert request(base, path + "/vault-action", {"action": "move", "destination": str(other)})[0] == 403
        assert calls == []
        status, body, _ = request(base, path + "/vault-directories")
        assert status == 200 and json.loads(body)["parent"] is None
        assert calls == [str(root)]


@pytest.mark.parametrize("path,method", [
    ("/services/", "GET"), ("/services-selection.json", "GET"),
    ("/codex-usage-cached.json", "GET"), ("/codex-usage.json", "GET"),
    ("/api/services/providers", "GET"),
    ("/services-selection/switch", "POST"), ("/codex-account/switch", "POST"),
    ("/codex-account/reset-credit", "POST"), ("/api/settings/access", "POST"),
    ("/workflow-field-notes/update", "POST"),
    ("/change-requests/create", "POST"), ("/artifacts/changes.html", "GET"),
])
def test_shared_host_routes_are_denied(tmp_path, path, method):
    with http_workspace(tmp_path) as (base, _cache, _state):
        assert request(base, path, {} if method == "POST" else None)[0] == 403


def test_known_foreign_artifact_id_is_denied_and_own_html_has_workspace_marker(tmp_path):
    root = tmp_path / "account"
    own = task(root)
    manager = SimpleNamespace(state_dir=tmp_path / "static-state")
    for ident in (own["observer_id"], "f" * 16):
        artifact = manager.state_dir / "runs" / ident / "sequence.html"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("<html><head></head><body>Research</body></html>")
    with http_workspace(root, runs=[own], manager=manager) as (base, _cache, _state):
        assert request(base, "/run-artifacts/" + "f" * 16 + "/sequence.html")[0] == 404
        status, body, headers = request(base, f'/run-artifacts/{own["observer_id"]}/sequence.html')
        assert status == 200
        ident = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
        assert f'<meta name="sciver-workspace" content="{ident}">'.encode() in body
        assert int(headers["Content-Length"]) == len(body)


def test_chat_ids_are_unavailable_in_other_account_state(tmp_path):
    with http_workspace(tmp_path / "account-a") as (base_a, _cache_a, _state_a):
        status, body, _ = request(base_a, "/api/research-chat/session", {"mode": "migration"})
        assert status == 200
        session = json.loads(body)["session_id"]
    with http_workspace(tmp_path / "account-b") as (base_b, _cache_b, _state_b):
        assert request(base_b, f"/api/research-chat/{session}/state")[0] == 404
        status, body, _ = request(base_b, "/api/research-chat/sessions")
        assert status == 200 and session.encode() not in body


def test_primary_keeps_existing_host_service_access(tmp_path, monkeypatch):
    monkeypatch.setattr("theory_master.service_provider_switch.service_selection", lambda: {"account": "primary"})
    with http_workspace(tmp_path, isolated=False) as (base, _cache, _state):
        status, body, _ = request(base, "/services-selection.json")
        assert status == 200 and json.loads(body)["account"] == "primary"


def test_isolated_workflow_does_not_load_host_change_manifests(tmp_path, monkeypatch):
    monkeypatch.setattr("tm_observer.web.load_change_requests", lambda **_kwargs: pytest.fail("read shared manifests"))
    with http_workspace(tmp_path) as (base, _cache, _state):
        assert request(base, "/workflow-stage-preview/?request=private-request")[0] == 404
        assert request(base, "/workflow-stage-preview/")[0] == 200


def test_isolated_run_page_does_not_probe_shared_quota(tmp_path, monkeypatch):
    own = task(tmp_path)
    monkeypatch.setattr("tm_observer.web.QuotaHistory.snapshot", lambda *_args, **_kwargs: pytest.fail("read shared quota"))
    monkeypatch.setattr("tm_observer.web.run_detail_body", lambda *_args, **_kwargs: "<main>Own task</main>")
    with http_workspace(tmp_path, runs=[own]) as (base, _cache, _state):
        assert request(base, f'/run/{own["observer_id"]}/')[0] == 200


def test_pdf_and_model_settings_save_to_account_process_configuration(tmp_path, monkeypatch):
    from theory_master import document_conversion, user_model_presets
    from theory_master.backend_config import ROUTE_ROLE_ORDER

    monkeypatch.delenv("THEORYMASTER_MODEL_PRESETS_FILE", raising=False)
    primary_home = tmp_path / "primary-home"
    monkeypatch.setenv("HOME", str(primary_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(primary_home / ".config"))
    monkeypatch.setenv("SCIVER_DOCUMENT_CONVERSION_CONFIG", str(primary_home / ".config" / "sciver" / "document-conversion.json"))
    document_conversion.save_settings({"expected_revision": document_conversion.load_settings()["revision"],
                                       "enabled": False, "max_pages": 33})
    primary_roles = {role: {"model": "gpt-6.1-sol", "reasoning_effort": "low"}
                     for role in ROUTE_ROLE_ORDER}
    user_model_presets.save("test", primary_roles)
    primary_pdf, primary_presets = document_conversion.config_path(), user_model_presets.path()
    original_pdf, original_presets = primary_pdf.read_bytes(), primary_presets.read_bytes()
    monkeypatch.setattr("tm_observer.web.execute_run_action", lambda _run, data: {
        "ok": True, "configured": user_model_presets.save(data["value"]["profile"], data["value"]["roles"])})
    for account in ("account-a", "account-b"):
        root = tmp_path / account
        own = task(root)
        app_home = root / "app-home"
        config = root / "config"
        monkeypatch.setenv("HOME", str(app_home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
        monkeypatch.setenv("SCIVER_DOCUMENT_CONVERSION_CONFIG", str(config / "document-conversion.json"))
        with http_workspace(root, runs=[own]) as (base, _cache, _state):
            status, body, _ = request(base, "/api/settings/document-conversion")
            assert status == 200
            status, body, _ = request(base, "/api/settings/document-conversion", {
                "expected_revision": json.loads(body)["revision"], "enabled": False, "max_pages": 17})
            assert status == 200
            assert json.loads(body)["max_pages"] == 17
            assert document_conversion.config_path() == config / "document-conversion.json"
            roles = {role: {"model": "gpt-6-sol", "reasoning_effort": "medium"}
                     for role in ROUTE_ROLE_ORDER}
            status, _body, _ = request(base, f'/api/run/{own["observer_id"]}/action', {
                "action": "save_model_tier_template", "value": {"profile": "test", "roles": roles}})
            assert status == 200
            assert user_model_presets.path() == app_home / ".config" / "theory-master" / "codex_model_presets.json"
            assert user_model_presets.path().is_file()
    assert primary_pdf.read_bytes() == original_pdf
    assert primary_presets.read_bytes() == original_presets
    assert (tmp_path / "account-a" / "config" / "document-conversion.json").is_file()
    assert (tmp_path / "account-b" / "config" / "document-conversion.json").is_file()


def test_created_preparation_task_remains_visible_in_isolated_workspace(tmp_path, monkeypatch):
    root = tmp_path / "account"
    monkeypatch.setenv("THEORY_MASTER_DATA_ROOT", str(root / ".theory-master-tasks"))
    plan = ("# Goal\n\nCompare the two mechanisms.\n\n"
            "# Research Contract\n\nUse independent observations.\n\n"
            "# Final Gate\n\nReport a discriminating result.\n\n"
            "# Required Context\n\nUse supplied measurements.\n")
    with http_workspace(root) as (base, cache, _state):
        status, body, _ = request(base, "/api/research-chat/session", {"mode": "preparation", "entry": "clear"})
        assert status == 200
        ident = json.loads(body)["session_id"]
        status, body, _ = request(base, f"/api/research-chat/{ident}/preparation")
        assert status == 200
        revision = json.loads(body)["revision"]
        status, body, _ = request(base, f"/api/research-chat/{ident}/preparation", {
            "expected_revision": revision, "draft": plan})
        assert status == 200
        revision = json.loads(body)["revision"]
        status, body, _ = request(base, f"/api/research-chat/{ident}/create-project", {
            "confirm": True, "expected_revision": revision})
        assert status == 200, body
        created = json.loads(body)
        assert created["graph_empty"] and created["scientific_model_empty"]
        assert not created["execution_started"]
        status, body, _ = request(base, "/observer_state.json")
        assert status == 200
        assert [run["observer_id"] for run in json.loads(body)["runs"]] == [created["observer_id"]]
        cache.refresh_after_write()
        status, body, _ = request(base, "/observer_state.json")
        assert status == 200
        persisted = json.loads(body)["runs"]
        assert [run["observer_id"] for run in persisted] == [created["observer_id"]]
        assert persisted[0]["task_root"] == created["task_root"]
