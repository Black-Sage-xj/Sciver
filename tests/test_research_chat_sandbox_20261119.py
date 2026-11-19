"""Research Chat persistence permissions, without any model or scientific turn."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from theory_master.backend_config import RoleRoute
from theory_master.codex_backend import CodexBackend


def chat_backend(tmp_path, monkeypatch, *, roots=()):
    workspace = tmp_path / "account-1" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    monkeypatch.setattr("theory_master.codex_backend._codex_home", lambda: runtime)
    backend = CodexBackend(
        workspace, routes={"research_chat": RoleRoute("fixture", "FixtureProvider")},
        writable_roots=roots,
    )
    return backend


def test_chat_write_roots_are_explicit_normalized_and_scoped(tmp_path, monkeypatch):
    state = tmp_path / "account-1" / "state"
    state.mkdir(parents=True)
    alias = tmp_path / "state-alias"
    alias.symlink_to(state, target_is_directory=True)
    backend = chat_backend(tmp_path, monkeypatch, roots=(state, alias, state))

    from openai_codex import Sandbox
    config = backend._thread_config("research_chat", "FixtureProvider")
    assert backend._thread_sandbox("research_chat") == Sandbox.workspace_write
    assert config["sandbox_workspace_write"] == {
        "writable_roots": [str(backend.cwd.resolve()), str(state.resolve())],
        "network_access": True,
        "exclude_tmpdir_env_var": True,
        "exclude_slash_tmp": True,
    }
    for role in ("research_lead", "judge", "challenger", "reporter", "budget_reviewer"):
        assert backend._thread_sandbox(role) == Sandbox.read_only
        assert "sandbox_workspace_write" not in backend._thread_config(role, "FixtureProvider")
    # A scope supplied for chat must not expand a scientific role's Cycle sandbox.
    assert "sandbox_workspace_write" not in backend._thread_config("investigator", "FixtureProvider")


def test_chat_config_is_compatible_with_partial_backend_facades(tmp_path):
    backend = object.__new__(CodexBackend)
    backend.cwd = tmp_path
    backend.routes = {}
    assert backend._thread_config("research_chat", "FixtureProvider")["sandbox_workspace_write"]["writable_roots"] == [str(tmp_path)]


def test_start_and_resume_preserve_scoped_write_config_in_official_sdk_shape(tmp_path, monkeypatch):
    backend = chat_backend(tmp_path, monkeypatch, roots=(tmp_path / "account-1" / "state",))
    client = Mock()
    client.thread_start.return_value = client.thread_resume.return_value = SimpleNamespace(id="chat-thread")
    monkeypatch.setattr(backend, "_ensure_client", lambda: client)
    monkeypatch.setattr(backend, "_compute_python", lambda: Path(sys.executable))
    monkeypatch.setattr("theory_master.codex_backend.developer_instructions", lambda *args, **kwargs: "fixture")
    from openai_codex import ApprovalMode, Sandbox
    from openai_codex.generated.v2_all import SandboxMode, SandboxWorkspaceWrite, ThreadStartParams, ThreadResumeParams

    identifier = backend.ensure_thread("research_chat", None, "fixture", "FixtureProvider")
    started = client.thread_start.call_args.kwargs
    assert started["sandbox"] == Sandbox.workspace_write
    assert started["approval_mode"] == ApprovalMode.deny_all
    wire = ThreadStartParams(config=started["config"], sandbox=SandboxMode.workspace_write).model_dump(by_alias=True)
    assert SandboxWorkspaceWrite.model_validate(wire["config"]["sandbox_workspace_write"]).network_access is True
    backend.drop_thread(identifier)
    backend.ensure_thread("research_chat", identifier, "fixture", "FixtureProvider")
    resumed = client.thread_resume.call_args.kwargs
    assert resumed["config"] == started["config"]
    assert ThreadResumeParams(thread_id=identifier, config=resumed["config"]).model_dump(by_alias=True)["config"] == wire["config"]
    assert "developer_instructions" not in resumed


@pytest.mark.parametrize("roles, nested, scoped", [
    ({"research_chat"}, False, True), ({"investigator"}, False, False),
    ({"research_chat", "investigator"}, False, False), ({"literature"}, True, True),
])
def test_only_chat_and_nested_clients_receive_private_workspace_tmpdir(tmp_path, monkeypatch, roles, nested, scoped):
    backend = chat_backend(tmp_path, monkeypatch)
    backend.routes = {role: RoleRoute("fixture", "FixtureProvider") for role in roles}
    monkeypatch.setenv("TMPDIR", "/ambient-tmp-fixture")
    if nested:
        monkeypatch.setenv("THEORYMASTER_CHAT_SANDBOX", "1")
    else:
        monkeypatch.delenv("THEORYMASTER_CHAT_SANDBOX", raising=False)
    monkeypatch.delenv("THEORYMASTER_WORKER_PINNED_ACCOUNT", raising=False)
    monkeypatch.setattr(backend, "_verify_provider_runtime", lambda: None)
    monkeypatch.setattr(backend, "_run_network_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(backend, "_runtime_config", lambda *args: ((), {}))
    monkeypatch.setattr(backend, "_sdk_environment", lambda *args: {"EXISTING": "preserved"})
    monkeypatch.setattr("theory_master.codex_sdk_compat.install_approval_handler", lambda *args: None)
    constructor = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr("openai_codex.Codex", constructor)
    backend._ensure_client()
    env = constructor.call_args.args[0].env
    assert env["EXISTING"] == "preserved"
    assert os.environ["TMPDIR"] == "/ambient-tmp-fixture"
    temp_root = backend.cwd / ".theory-master-tmp"
    if scoped:
        assert env["TMPDIR"] == str(temp_root)
        assert env["THEORYMASTER_CHAT_SANDBOX"] == "1"
        assert temp_root.is_dir() and temp_root.stat().st_mode & 0o777 == 0o700
    else:
        assert "TMPDIR" not in env and not temp_root.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Codex Linux sandbox mount regression")
def test_actual_sdk_sandbox_allows_only_scoped_writes_and_nested_sdk_init(tmp_path, monkeypatch):
    """command/exec uses the real pinned sandbox; no thread/start or turn/start."""
    from openai_codex import Codex, CodexConfig

    state = tmp_path / "account-1" / "state"
    runtime = tmp_path / "account-1" / "codex-runtime"
    other = tmp_path / "account-2"
    source = tmp_path / "installation-source"
    outside_home = tmp_path / "home"
    for directory in (state, runtime, other, source, outside_home):
        directory.mkdir(parents=True, exist_ok=True)
    backend = chat_backend(tmp_path, monkeypatch, roots=(state, runtime))
    scoped_tmp = backend.cwd / ".theory-master-tmp"
    scoped_tmp.mkdir(mode=0o700)
    targets = {name: str(directory / "probe") for name, directory in {
        "workspace": backend.cwd, "state": state, "runtime": runtime,
        "other_account": other, "source": source, "home": outside_home,
        "global_tmp": tmp_path,
    }.items()}
    settings = backend._thread_config("research_chat", "FixtureProvider")["sandbox_workspace_write"]
    overrides = ('sandbox_mode="workspace-write"',) + tuple(
        f"sandbox_workspace_write.{key}={json.dumps(value)}" for key, value in settings.items()
    )
    probe = """
import json, pathlib, sys, tempfile
from openai_codex import Codex, CodexConfig
results = {}
for name, filename in json.loads(sys.argv[1]).items():
    try:
        pathlib.Path(filename).write_text('probe')
        results[name] = 'writable'
    except OSError:
        results[name] = 'denied'
with tempfile.TemporaryDirectory() as temporary:
    results['temporary_directory'] = str(pathlib.Path(temporary).parent)
nested_cwd = pathlib.Path.cwd() / 'nested-literature'
nested_cwd.mkdir()
nested_tmp = nested_cwd / '.theory-master-tmp'
nested_tmp.mkdir()
client = Codex(CodexConfig(cwd=str(nested_cwd), config_overrides=(
    'sandbox_mode="workspace-write"',
    'sandbox_workspace_write.exclude_slash_tmp=true',
    'sandbox_workspace_write.exclude_tmpdir_env_var=true',
), env={'TMPDIR': str(nested_tmp)}))
try:
    results['nested_sdk'] = 'initialized'
    inner = client._client._request_raw('command/exec', {
        'command': [sys.executable, '-c', "from pathlib import Path; Path('nested-tool.txt').write_text('ready'); print('nested-tool-ready')"],
        'cwd': str(nested_cwd), 'timeoutMs': 10000,
    })
    results['nested_tool'] = 'writable' if inner['exitCode'] == 0 and (nested_cwd / 'nested-tool.txt').read_text() == 'ready' else inner
finally:
    client.close()
print(json.dumps(results))
"""
    client = Codex(CodexConfig(
        config_overrides=overrides, cwd=str(backend.cwd),
        env={"CODEX_HOME": str(runtime), "TMPDIR": str(scoped_tmp)},
    ))
    try:
        result = client._client._request_raw("command/exec", {
            "command": [sys.executable, "-c", probe, json.dumps(targets)],
            "cwd": str(backend.cwd), "timeoutMs": 25000,
        })
    finally:
        client.close()
    assert result["exitCode"] == 0, result
    observed = json.loads(result["stdout"])
    print("SANDBOX_PROBE " + json.dumps(observed))
    assert observed == {
        "workspace": "writable", "state": "writable", "runtime": "writable",
        "other_account": "denied", "source": "denied", "home": "denied", "global_tmp": "denied",
        "temporary_directory": str(scoped_tmp), "nested_sdk": "initialized", "nested_tool": "writable",
    }, json.dumps(observed)
