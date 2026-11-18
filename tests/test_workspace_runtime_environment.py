"""Workspace environment boundaries, without systemd launches or model calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

from theory_master import provider_extensions, runtime_manager, worker_runtime


def _environment(root: Path) -> dict[str, str]:
    return {
        "SCIVER_ISOLATED_WORKSPACE": "1",
        "HOME": str(root / "home"),
        "PATH": os.defpath,
        "THEORY_MASTER_DATA_ROOT": str(root / "tasks"),
        "THEORY_MASTER_SEARCH_ROOT": str(root / "workspace"),
        "THEORYMASTER_CODEX_HOME": str(root / "codex"),
        "THEORYMASTER_ENV_FILE": str(root / "env"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "SCIVER_DOCUMENT_CONVERSION_CONFIG": str(root / "conversion.json"),
        "SCIVER_WORKSPACE": str(root / "workspace"),
        "PYTHONPATH": "/legacy/private/research",
        "OPENAI_API_KEY": "legacy-host-secret",
        "THEORYMASTER_DEEPSEEK_API_KEY": "legacy-provider-secret",
    }


def test_isolated_runtime_keeps_workspace_paths_and_drops_ambient_secrets(tmp_path):
    with patch.dict(os.environ, _environment(tmp_path), clear=True):
        env = runtime_manager._runtime_env()
    assert env["HOME"] == str(tmp_path / "home")
    assert env["THEORY_MASTER_DATA_ROOT"] == str(tmp_path / "tasks")
    assert env["XDG_CONFIG_HOME"] == str(tmp_path / "config")
    assert env["SCIVER_DOCUMENT_CONVERSION_CONFIG"] == str(tmp_path / "conversion.json")
    assert "OPENAI_API_KEY" not in env
    assert "THEORYMASTER_DEEPSEEK_API_KEY" not in env
    assert env["PYTHONPATH"] == str(runtime_manager._package_root())


def test_isolated_service_does_not_inherit_user_manager_defaults(tmp_path):
    env = _environment(tmp_path)
    command = runtime_manager._service_command([
        sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))",
    ], env)
    inherited = {**os.environ, "HOME": "/legacy/home", "LEGACY_MANAGER_SECRET": "secret"}
    result = subprocess.run(command, env=inherited, text=True, capture_output=True, check=True)
    child = json.loads(result.stdout)
    assert child["HOME"] == env["HOME"]
    assert child["THEORY_MASTER_DATA_ROOT"] == env["THEORY_MASTER_DATA_ROOT"]
    assert "LEGACY_MANAGER_SECRET" not in child
    assert "OPENAI_API_KEY" not in child


def test_supervisor_and_worker_forward_same_workspace_environment(tmp_path):
    supplied = _environment(tmp_path)
    spec = SimpleNamespace(research_root=tmp_path / "task", master_id=0, task_mode=True)
    completed = SimpleNamespace(returncode=0, stdout="started", stderr="")
    with (
        patch.dict(os.environ, supplied, clear=True),
        patch.object(runtime_manager, "_supervisor_command", return_value=["python", "supervisor"]),
        patch.object(runtime_manager.shutil, "which", return_value="/usr/bin/systemd-run"),
        patch.object(runtime_manager.subprocess, "run", return_value=completed) as run,
    ):
        runtime_manager.launch_managed_runtime(spec, operation="run", identifier="0")
        supervisor = run.call_args.args[0]
    with patch.object(worker_runtime.subprocess, "run", return_value=completed) as run:
        worker_runtime._launch_detached_service(["python", "worker"], root=tmp_path,
                                                env=supplied, output=None, kind="worker")
        worker = run.call_args.args[0]
    for key in ("HOME", "THEORY_MASTER_DATA_ROOT", "THEORY_MASTER_SEARCH_ROOT",
                "THEORYMASTER_CODEX_HOME", "THEORYMASTER_ENV_FILE", "XDG_CONFIG_HOME",
                "SCIVER_DOCUMENT_CONVERSION_CONFIG", "SCIVER_ISOLATED_WORKSPACE"):
        assert f"--setenv={key}={supplied[key]}" in supervisor
        assert f"--setenv={key}={supplied[key]}" in worker
    assert "/usr/bin/env" in supervisor and "-i" in supervisor
    assert "/usr/bin/env" in worker and "-i" in worker


def test_workspace_extra_environment_is_opt_in():
    supplied = {"XDG_CONFIG_HOME": "/unrelated/config", "SCIVER_WORKSPACE": "/unrelated/workspace"}
    assert runtime_manager._service_environment(supplied) == {}
    assert runtime_manager._service_command(["python"], supplied) == ["python"]


def test_isolated_provider_registry_never_falls_back_to_repository(tmp_path):
    with patch.dict(os.environ, {"SCIVER_ISOLATED_WORKSPACE": "1",
                                 "XDG_CONFIG_HOME": str(tmp_path / "config")}, clear=True):
        path = provider_extensions.local_provider_config()
        assert path == tmp_path / "config/theory-master/provider_connections.toml"
        assert provider_extensions.configured_optional_provider_ids() == frozenset()


def test_isolated_workspace_disables_host_quota_plugin(tmp_path):
    provider_extensions.optional_account_quota_plugin.cache_clear()
    try:
        with patch.dict(os.environ, {"SCIVER_ISOLATED_WORKSPACE": "1"}, clear=True), (
                patch.object(Path, "is_file", side_effect=AssertionError("host plugin was inspected"))):
            assert provider_extensions.optional_account_quota_plugin() is None
    finally:
        provider_extensions.optional_account_quota_plugin.cache_clear()
