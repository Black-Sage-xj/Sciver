"""Exercise metadata RPC using a deterministic local child, never a model."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from theory_master import codex_model_catalog as catalog


FAKE_SERVER = r'''
import json, os, sys
from pathlib import Path
scenario = os.environ.get("TM_CATALOG_TEST_SCENARIO", "normal")
log = Path(os.environ["TM_CATALOG_TEST_LOG"])
initialized = False
for line in sys.stdin:
    request = json.loads(line)
    with log.open("a") as output:
        output.write(json.dumps({"method": request["method"], "params": request.get("params"),
            "isolated_cwd": str(Path.cwd()) == os.environ["CODEX_HOME"]}) + "\n")
    if request["method"] == "initialize":
        if scenario == "init-error":
            print(json.dumps({"id": request["id"], "error": {"message": "unavailable"}}), flush=True)
            break
        print(json.dumps({"method": "notification", "params": {}}), flush=True)
        print(json.dumps({"id": request["id"], "result": {"userAgent": "fake-catalog"}}), flush=True)
    elif request["method"] == "initialized":
        initialized = True
        # Token refresh may mutate the disposable copy, never the source.
        (Path(os.environ["CODEX_HOME"]) / "auth.json").write_text("child-only-refresh")
    elif request["method"] == "model/list":
        assert initialized
        params = request["params"]
        assert params["includeHidden"] is False
        if scenario == "timeout":
            continue
        if params.get("cursor"):
            rows = [{"id":"opaque-luna-id", "model":"gpt-probe-luna", "displayName":"Luna Label",
                "supportedReasoningEfforts":[{"reasoningEffort":"low"},{"reasoningEffort":"max"}],
                "defaultReasoningEffort":"low"}]
            cursor = "again" if scenario == "repeated-cursor" else None
        else:
            rows = [{"id":"opaque-terra-id", "model":"gpt-probe-terra", "displayName":"Terra Label",
                "supportedReasoningEfforts":[{"reasoningEffort":"low"},{"reasoningEffort":"ultra"}],
                "defaultReasoningEffort":"ultra"},
                {"model":"hidden-model", "hidden":True}, {"model":"invalid model"}]
            cursor = "again"
        # Batched notifications + response exercise the unbuffered line parser.
        sys.stdout.write(json.dumps({"method":"status/event", "params":{}}) + "\n" +
            json.dumps({"id":request["id"], "result":{"data":rows,"nextCursor":cursor}}) + "\n")
        sys.stdout.flush()
'''


@pytest.fixture
def fake_server(tmp_path, monkeypatch):
    server = tmp_path / "fake-codex"
    server.write_text(f"#!{sys.executable}\n" + FAKE_SERVER, encoding="utf-8")
    server.chmod(0o700)
    auth = tmp_path / "runtime-auth.json"
    auth.write_text('{"token":"original-identity"}', encoding="utf-8")
    log = tmp_path / "rpc-log.jsonl"
    monkeypatch.setenv("TM_CATALOG_TEST_LOG", str(log))
    monkeypatch.setattr(catalog, "_catalog_executable", lambda: str(server))
    monkeypatch.setattr(catalog, "codex_auth_for_worker", lambda *args: pytest.fail("explicit auth must not inspect ambient account"))
    return auth, log


def test_explicit_auth_handshake_pagination_and_submission_model_slug(fake_server, tmp_path):
    auth, log = fake_server
    original = auth.read_bytes()
    result = catalog.model_catalog(tmp_path, None, auth_path=auth,
                                   expected_auth_fingerprint=hashlib.sha256(original).hexdigest())
    assert result["status"] == "catalog_read"
    assert result["models"] == ["gpt-probe-luna", "gpt-probe-terra"]
    assert result["efforts"]["gpt-probe-luna"] == ["low", "max"]
    assert result["default_efforts"]["gpt-probe-terra"] == "ultra"
    messages = [json.loads(line) for line in log.read_text().splitlines()]
    assert [row["method"] for row in messages] == ["initialize", "initialized", "model/list", "model/list"]
    assert messages[-1]["params"]["cursor"] == "again"
    assert all(row["isolated_cwd"] for row in messages)
    assert auth.read_bytes() == original
    assert "original-identity" not in json.dumps(result)
    assert hashlib.sha256(original).hexdigest() not in json.dumps(result)


@pytest.mark.parametrize("scenario", ["init-error", "repeated-cursor", "timeout"])
def test_incomplete_catalog_is_unknown_and_never_claims_missing_models(fake_server, tmp_path, monkeypatch, scenario):
    auth, log = fake_server
    monkeypatch.setenv("TM_CATALOG_TEST_SCENARIO", scenario)
    result = catalog.model_catalog(tmp_path, None, auth_path=auth, timeout=.5)
    assert result["status"] == "unknown"
    assert "models" not in result
    if scenario == "init-error":
        assert [json.loads(line)["method"] for line in log.read_text().splitlines()] == ["initialize"]


def test_auth_changed_before_freezing_is_rejected_without_starting_process(fake_server, tmp_path):
    auth, log = fake_server
    expected = hashlib.sha256(auth.read_bytes()).hexdigest()
    auth.write_text('{"token":"switched-identity"}', encoding="utf-8")
    result = catalog.model_catalog(tmp_path, None, auth_path=auth, expected_auth_fingerprint=expected)
    assert result["status"] == "unknown"
    assert "已变化" in result["reason"]
    assert not log.exists()
