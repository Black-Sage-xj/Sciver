from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master import codex_sdk_compat
from theory_master.codex_backend import CodexBackend
from theory_master.prequalification_holdout import holdout_context, holdout_files, thread_profile
from theory_master.prompt_stages import stage_context
from theory_master.research_inputs import verified_research_inputs


def fixture(tmp_path):
    root = tmp_path / "benchmark"
    path = root / "inputs" / "validation.json"
    path.parent.mkdir(parents=True)
    data = b'{"label": "held"}\n'
    path.write_bytes(data)
    reference = "benchmark/inputs/validation.json"
    digest = hashlib.sha256(data).hexdigest()
    manifest = {"schema": "sciver/private-scientific-fixture-manifest/v1",
                "files": [{"path": reference, "sha256": digest, "size": len(data)}]}
    (root / "fixture_manifest.json").write_text(json.dumps(manifest))
    config_path = tmp_path / "sample-config.json"
    config = {"schema": "theory-master/system/v1", "benchmark_root": str(root),
              "prequalification_holdout": {"files": [{"reference": reference, "sha256": digest}]}}
    config_path.write_text(json.dumps(config))
    return config_path, path, manifest["files"][0]


def test_holdout_preserves_public_metadata_but_withholds_source_bytes(tmp_path):
    config_path, path, row = fixture(tmp_path)
    checked = holdout_files(config_path)
    meta = holdout_context(checked)
    assert meta["files"] == [{"reference": row["path"], "sha256": row["sha256"],
                              "size": row["size"], "status": "verified_reserved"}]
    assert "Investigator after Hypothesis Lock" in meta["access"]
    spec = SimpleNamespace(benchmark_root=path.parents[1], task_home=tmp_path,
                           document=tmp_path / "plan.md", required_context=(
                               "- benchmark/fixture_manifest.json\n- " + row["path"]))
    alias = tmp_path / "public-copy.json"
    alias.write_bytes(path.read_bytes())
    (tmp_path / "input_manifest.json").write_text(json.dumps({
        "schema": "theorymaster/public-research-input-manifest/v1",
        "data_files": [{"path": alias.name, "sha256": row["sha256"], "bytes": row["size"]}],
    }))
    assert len(verified_research_inputs(spec, include_text=True)) == 2
    assert verified_research_inputs(spec, include_text=True,
                                    excluded_sha256=frozenset({checked[0].sha256})) == []
    for role, stage in [("research_lead", "frontier_proposal"), ("challenger", "candidate_review"),
                        ("challenger", "repair_verification")]:
        projected = stage_context(role, stage, {"prequalification_holdout": meta, "agent_graph": {}})
        assert projected["prequalification_holdout"] == meta
        assert '"label"' not in json.dumps(projected)


@pytest.mark.parametrize("kind", ["changed", "hardlink", "symlink", "pattern", "duplicate", "invalid_config"])
def test_invalid_or_drifted_holdout_fails_closed(tmp_path, kind):
    config_path, path, _ = fixture(tmp_path)
    config = json.loads(config_path.read_text())
    if kind == "changed":
        path.write_bytes(b'{"label": "seen"}\n')
    elif kind == "hardlink":
        (tmp_path / "alias.json").hardlink_to(path)
    elif kind == "symlink":
        target = tmp_path / "alias.json"
        path.rename(target)
        path.symlink_to(target)
    elif kind == "pattern":
        config["prequalification_holdout"]["files"][0]["reference"] = "benchmark/inputs/*.json"
    elif kind == "duplicate":
        config["prequalification_holdout"]["files"] *= 2
    else:
        config = []
    config_path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        thread_profile(config_path, "research_lead")


class RawClient:
    def __init__(self):
        self.calls = []
        self.override_profile = None
        self._client = self

    def request(self, method, params, *, response_model):
        self.calls.append((method, params))
        return response_model.model_validate({
            "thread": {"id": params.get("threadId", f"guarded-{len(self.calls)}")},
            "activePermissionProfile": {"id": self.override_profile or params["config"]["default_permissions"]},
        })

    def thread_start(self, **params):
        self.calls.append(("legacy_start", params))
        return SimpleNamespace(id="ordinary")


def backend_for(tmp_path, monkeypatch):
    from theory_master import codex_backend

    monkeypatch.setattr(codex_backend, "_codex_home", lambda: tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(codex_sdk_compat, "require_holdout_runtime", lambda client: None)
    config_path, path, _ = fixture(tmp_path)
    backend = CodexBackend(tmp_path, task_config_path=config_path)
    client = RawClient()
    monkeypatch.setattr(backend, "_ensure_client", lambda: client)
    monkeypatch.setattr(backend, "_compute_python", lambda task_config_path=None: Path("/usr/bin/python3"))
    return backend, client, path


def test_guard_freshens_history_checks_resume_and_keeps_investigator_access(tmp_path, monkeypatch):
    backend, client, path = backend_for(tmp_path, monkeypatch)
    assert backend.qualification_thread("research_lead", "old-contaminated") is None
    assert backend.ensure_thread("research_lead", "old-contaminated", "m", "p") == "guarded-1"
    method, params = client.calls[-1]
    assert method == "thread/start" and "threadId" not in params and "sandbox" not in params
    assert params["approvalPolicy"] == "never"
    config = params["config"]
    profile = config["permissions"][config["default_permissions"]]
    assert profile == {"filesystem": {":root": "read", str(path): "deny"}, "network": {"enabled": False}}
    assert config["web_search"] == "disabled"
    assert backend.qualification_thread("research_lead", "guarded-1") == "guarded-1"
    assert backend.ensure_thread("research_lead", "guarded-1", "m", "p") == "guarded-1"
    assert len(client.calls) == 1
    backend.threads.clear()
    assert backend.ensure_thread("research_lead", "guarded-1", "m", "p") == "guarded-1"
    method, params = client.calls[-1]
    assert method == "thread/resume" and "sandbox" not in params
    assert params["config"]["default_permissions"] == config["default_permissions"]
    assert backend.qualification_thread("challenger", "guarded-1") is None
    assert backend.ensure_thread("investigator", None, "m", "p") == "ordinary"
    assert client.calls[-1][0] == "legacy_start"
    assert "permissions" not in client.calls[-1][1]["config"]


def test_writable_role_cannot_guard_a_file_placed_under_its_writable_workspace(tmp_path):
    config_path, path, _ = fixture(tmp_path)
    with pytest.raises(ValueError, match="outside.*writable workspace"):
        thread_profile(config_path, "incident_supervisor", workspace=tmp_path)
    name, config = thread_profile(config_path, "incident_supervisor", workspace=tmp_path / "cycle")
    assert config["permissions"][name]["filesystem"][str(path)] == "deny"


def test_unconfirmed_profile_or_disappearing_policy_prevents_turn(tmp_path, monkeypatch):
    from theory_master.codex_turn_transport import CodexTurnTransport

    backend, client, _ = backend_for(tmp_path, monkeypatch)
    client.override_profile = "dangerFullAccess"
    attempted = []
    monkeypatch.setattr(CodexTurnTransport, "_attempt", lambda *a, **k: attempted.append(True))
    with pytest.raises(RuntimeError, match="did not confirm"):
        backend.invoke("attention_reviewer", "{}", None, "m", "p")
    assert len(client.calls) == 1 and not backend.threads and not attempted
    config = json.loads(backend.task_config_path.read_text())
    config.pop("prequalification_holdout")
    backend.task_config_path.write_text(json.dumps(config))
    with pytest.raises(RuntimeError, match="holdout disappeared"):
        backend.invoke("challenger", "{}", None, "m", "p")
    backend.task_config_path.unlink()
    with pytest.raises(RuntimeError, match="configuration required"):
        backend.ensure_thread("challenger", None, "m", "p")
    assert len(client.calls) == 1 and not attempted


def test_linux_guard_requires_the_matching_sandbox_executable(tmp_path):
    from openai_codex import CodexConfig

    binary = tmp_path / "codex"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    helper = tmp_path / "codex-linux-sandbox"
    client = SimpleNamespace(_client=SimpleNamespace(config=CodexConfig(
        codex_bin=str(binary), env={"PATH": str(tmp_path)},
    )))
    with pytest.raises(RuntimeError, match="same bundled Codex runtime"):
        codex_sdk_compat.require_holdout_runtime(client)
    helper.symlink_to(binary)
    codex_sdk_compat.require_holdout_runtime(client)
