"""Model-directory delivery/cache tests; never contact a real app-server/model."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import urlopen

import pytest

from theory_master import codex_model_catalog as probe
from tm_observer import research_chat as chat
from tm_observer import research_chat_models as models
from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler


CATALOG = {"status": "catalog_read", "models": ["gpt-new-terra", "gpt-new-luna"],
           "efforts": {"gpt-new-terra": ["low", "medium", "high", "xhigh", "max", "ultra"],
                       "gpt-new-luna": ["low", "medium", "high", "max"]},
           "default_efforts": {"gpt-new-terra": "low", "gpt-new-luna": "medium"}}


@pytest.fixture
def catalog_env(monkeypatch, tmp_path):
    home = tmp_path / "chat-codex"
    home.mkdir()
    auth = home / "auth.json"
    auth.write_text('{"token":"account-a"}', encoding="utf-8")
    monkeypatch.setenv("THEORYMASTER_CODEX_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "unrelated-codex"))
    monkeypatch.setattr(models, "_ENTRIES", {})
    clock = [100.0]
    monkeypatch.setattr(models, "_monotonic", lambda: clock[0])
    calls = []

    def fake_probe(root, worker_id, **kwargs):
        calls.append((root, worker_id, kwargs))
        return CATALOG

    monkeypatch.setattr(probe, "model_catalog", fake_probe)
    return auth, clock, calls


def test_snapshots_and_settings_are_nonblocking_cache_reads(catalog_env, tmp_path):
    _, _, calls = catalog_env
    ident = chat.create(tmp_path, {"runs": []}, {"mode": "migration"})["session_id"]
    initial = chat.snapshot(tmp_path, {"runs": []}, ident)
    assert initial["available_models"] == list(models.FALLBACK_MODELS)
    assert initial["model_catalog"]["status"] == "unqueried"
    assert calls == []
    models.refresh_catalog()
    updated = chat.snapshot(tmp_path, {"runs": []}, ident)
    assert "gpt-new-terra" in updated["available_models"]
    assert "gpt-6-luna" not in updated["available_models"]
    assert "ultra" in updated["available_reasoning_efforts"]
    assert "ultra" not in updated["model_reasoning_efforts"]["gpt-new-luna"]
    assert len(calls) == 1


def test_exact_runtime_auth_ttl_throttle_and_same_account_stale_fallback(catalog_env, monkeypatch):
    auth, clock, calls = catalog_env
    first = models.refresh_catalog()
    assert calls[0][2]["auth_path"] == auth
    assert calls[0][1] is None
    assert first["model_catalog"] == {"status": "catalog_read", "stale": False,
                                      "updated_at": first["model_catalog"]["updated_at"],
                                      "scope": first["model_catalog"]["scope"],
                                      "reason": "已读取当前聊天账号的模型目录"}
    models.refresh_catalog(force=True)
    assert len(calls) == 1
    clock[0] += 6
    models.refresh_catalog()
    assert len(calls) == 1
    monkeypatch.setattr(probe, "model_catalog", lambda *args, **kwargs: {"status": "unknown", "reason": "offline"})
    failed = models.refresh_catalog(force=True)
    assert failed["model_catalog"]["status"] == "unavailable"
    assert failed["model_catalog"]["stale"] is True
    assert failed["model_catalog"]["updated_at"] == first["model_catalog"]["updated_at"]
    assert "gpt-new-terra" in failed["available_models"]
    assert failed["model_catalog"]["reason"] == "offline"
    auth.write_text('{"token":"account-b"}', encoding="utf-8")
    switched = models.cached_catalog()
    assert switched["model_catalog"]["status"] == "unqueried"
    assert switched["model_catalog"]["scope"] != first["model_catalog"]["scope"]
    assert "gpt-new-terra" not in switched["available_models"]
    unavailable = models.refresh_catalog(current_model="saved-legacy-model")
    assert "gpt-new-terra" not in unavailable["available_models"]
    assert "saved-legacy-model" in unavailable["available_models"]


def test_concurrent_requests_share_one_probe_and_auth_switch_discards_old_result(catalog_env, monkeypatch):
    auth, _, _ = catalog_env
    entered, release = threading.Event(), threading.Event()
    count = []

    def blocked_probe(*args, **kwargs):
        count.append(1)
        entered.set()
        assert release.wait(3)
        return CATALOG

    monkeypatch.setattr(probe, "model_catalog", blocked_probe)
    outputs = []
    workers = [threading.Thread(target=lambda: outputs.append(models.refresh_catalog(force=True))) for _ in range(4)]
    for worker in workers:
        worker.start()
    assert entered.wait(2)
    assert models.cached_catalog()["model_catalog"]["status"] == "refreshing"
    release.set()
    for worker in workers:
        worker.join(3)
        assert not worker.is_alive()
    assert len(count) == 1
    assert len(outputs) == 4
    entered.clear()
    release.clear()
    models._ENTRIES.clear()
    thread = threading.Thread(target=lambda: outputs.append(models.refresh_catalog(force=True)))
    thread.start()
    assert entered.wait(2)
    auth.write_text('{"token":"account-b"}', encoding="utf-8")
    release.set()
    thread.join(3)
    assert "gpt-new-terra" not in outputs[-1]["available_models"]


def test_model_validation_refreshes_once_and_explicit_switch_adapts_effort(catalog_env, tmp_path):
    _, clock, calls = catalog_env
    ident = chat.create(tmp_path, {"runs": []}, {"mode": "migration"})["session_id"]
    first = chat.set_model(tmp_path, ident, "gpt-new-terra")
    assert first["model"] == "gpt-new-terra"
    assert len(calls) == 1
    chat.set_reasoning_effort(tmp_path, ident, "ultra")
    # Passive metadata does not change the saved selection.
    models.refresh_catalog(force=True)
    assert chat.snapshot(tmp_path, {"runs": []}, ident)["session"]["reasoning_effort"] == "ultra"
    result = chat.set_model(tmp_path, ident, "gpt-new-luna")
    assert result["reasoning_effort"] == "medium"
    chat.set_reasoning_effort(tmp_path, ident, "max")
    with pytest.raises(chat.ResearchChatError):
        chat.set_reasoning_effort(tmp_path, ident, "ultra")
    for invalid in ("invented-model", "x; shell", [], None):
        with pytest.raises(chat.ResearchChatError):
            chat.set_model(tmp_path, ident, invalid)
    assert len(calls) == 1
    assert chat.snapshot(tmp_path, {"runs": []}, ident)["session"]["model"] == "gpt-new-luna"
    route = chat._chat_routes("gpt-new-terra", "ultra")["research_chat"]
    from theory_master.codex_backend import CodexBackend
    backend = CodexBackend.__new__(CodexBackend)
    backend.routes = {"research_chat": route}
    backend.cwd = tmp_path
    assert backend._thread_config("research_chat", route.provider)["model_reasoning_effort"] == "ultra"
    chat.set_model(tmp_path, ident, "gpt-new-terra")
    chat.set_reasoning_effort(tmp_path, ident, "ultra")
    clock[0] += models.TTL_SECONDS + 1
    assert models.cached_catalog()["model_catalog"]["stale"] is True
    assert chat.set_model(tmp_path, ident, "gpt-new-luna")["reasoning_effort"] == "medium"


def test_saved_current_model_and_effort_remain_usable_on_catalog_failure(catalog_env, tmp_path, monkeypatch):
    ident = chat.create(tmp_path, {"runs": []}, {"mode": "migration"})["session_id"]
    row = chat._read(chat._store(tmp_path), ident)
    row.update(model="previous-approved", reasoning_effort="ultra")
    chat._write(chat._store(tmp_path), row)
    monkeypatch.setattr(probe, "model_catalog", lambda *args, **kwargs: {"status": "unknown"})
    models.refresh_catalog(force=True)
    assert chat.set_model(tmp_path, ident, "previous-approved")["model"] == "previous-approved"
    assert chat.set_reasoning_effort(tmp_path, ident, "ultra")["reasoning_effort"] == "ultra"
    assert "previous-approved" in chat.snapshot(tmp_path, {"runs": []}, ident)["available_models"]


def test_identity_cache_cannot_expand_while_eight_probes_are_in_flight(catalog_env):
    _, _, calls = catalog_env
    models._ENTRIES.update({f"busy-{i}": models._Entry(event=threading.Event()) for i in range(8)})
    result = models.refresh_catalog(force=True)
    assert len(models._ENTRIES) == 8
    assert not calls
    assert result["model_catalog"]["status"] == "unavailable"


def test_http_catalog_refresh_and_settings_forward_metadata(catalog_env, tmp_path):
    _, _, calls = catalog_env
    ident = chat.create(tmp_path / "observer", {"runs": []}, {"mode": "migration"})["session_id"]
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(StateCache(tmp_path), field_notes_state_dir=tmp_path / "observer"))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(base + f"/api/research-chat/{ident}/settings", timeout=4) as response:
            initial = json.loads(response.read())
        assert initial["model_catalog"]["status"] == "unqueried"
        assert not calls
        with urlopen(base + "/api/research-chat/models?refresh=1", timeout=4) as response:
            catalog = json.loads(response.read())
        assert "gpt-new-terra" in catalog["available_models"]
        with urlopen(base + f"/api/research-chat/{ident}/settings", timeout=4) as response:
            saved = json.loads(response.read())
        assert saved["model"] == "gpt-6-sol"
        assert saved["model_reasoning_efforts"] == catalog["model_reasoning_efforts"]
        assert saved["model_default_reasoning_efforts"]["gpt-new-terra"] == "low"
        assert len(calls) == 1
    finally:
        server.shutdown()
        server.server_close()
