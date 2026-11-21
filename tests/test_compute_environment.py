"""Offline scientific-interpreter configuration and preflight acceptance."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from theory_master.backend_config import RoleRoute
from theory_master.codex_backend import CodexBackend
from theory_master.compute_environment import (
    ComputeEnvironment, probe_compute_environment, record_compute_preflight,
    resolve_compute_environment, task_compute_python,
)
from theory_master.specification import TaskSpec
from theory_master.task import SYSTEM_SCHEMA


class ComputeEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.python = Path(sys.executable).absolute()

    def config(self, value=None, **extra):
        data = extra
        if value is not None:
            data["compute_python"] = str(value)
        path = self.root / "theory-master.config.json"
        path.write_text(json.dumps(data))
        return path

    def backend(self, config=None):
        with patch("theory_master.codex_backend._codex_home", return_value=self.root):
            return CodexBackend(self.root, {"investigator": RoleRoute("fixture", "Fixture")},
                                task_config_path=config)

    def test_task_setting_wins_over_environment_and_preserves_venv_symlink(self):
        local = self.root / "task venv" / "bin" / "python"
        local.parent.mkdir(parents=True)
        local.symlink_to(self.python)
        config = self.config(local)
        with patch.dict(os.environ, {"THEORYMASTER_COMPUTE_PYTHON": "/missing/global/python"}), \
             patch.object(CodexBackend, "_runtime_config", return_value=((), {})):
            selected = resolve_compute_environment(config)
            environment = CodexBackend._sdk_environment(task_config_path=config)
        self.assertEqual(selected.python, local)
        self.assertNotEqual(selected.python, local.resolve())
        self.assertEqual(selected.source, "task_config")
        self.assertEqual(selected.task_config_path, config)
        self.assertEqual(environment["PYTHON"], str(local))
        self.assertEqual(environment["THEORYMASTER_COMPUTE_PYTHON"], str(local))
        self.assertTrue(environment["PATH"].startswith(str(local.parent) + ":"))

    def test_unconfigured_task_keeps_environment_and_class_call_compatibility(self):
        config = self.config()
        with patch.dict(os.environ, {"THEORYMASTER_COMPUTE_PYTHON": str(self.python)}), \
             patch.object(CodexBackend, "_runtime_config", return_value=((), {})):
            selected = resolve_compute_environment(config)
            self.assertEqual(CodexBackend._compute_python(), self.python)
            self.assertEqual(CodexBackend._sdk_environment()["PYTHON"], str(self.python))
        self.assertEqual(selected.python, self.python)
        self.assertEqual(selected.source, "environment")

    def test_absent_setting_retains_established_default(self):
        local = self.root / "miniconda3" / "bin" / "python"
        local.parent.mkdir(parents=True)
        local.symlink_to(self.python)
        with patch.dict(os.environ, {"THEORYMASTER_COMPUTE_PYTHON": ""}), \
             patch("theory_master.compute_environment.Path.home", return_value=self.root):
            selected = resolve_compute_environment()
        self.assertEqual(selected.python, local)
        self.assertEqual(selected.source, "default")

    def test_invalid_task_configuration_never_falls_back(self):
        for value in (None, "", 1, [], "relative/bin/python"):
            with self.subTest(value=value), \
                 patch.dict(os.environ, {"THEORYMASTER_COMPUTE_PYTHON": str(self.python)}):
                path = self.config()
                path.write_text(json.dumps({"compute_python": value}))
                with self.assertRaisesRegex(ValueError, "compute_python"):
                    resolve_compute_environment(path)
        path = self.config(self.root / "missing")
        with patch.dict(os.environ, {"THEORYMASTER_COMPUTE_PYTHON": str(self.python)}):
            with self.assertRaisesRegex(RuntimeError, "task_config"):
                resolve_compute_environment(path)
        non_executable = self.root / "plain-file"
        non_executable.write_text("fixture")
        non_executable.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            task_compute_python({"compute_python": str(non_executable)})

    def test_task_spec_rejects_invalid_explicit_setting_before_loading_science(self):
        config = self.config("relative/python", schema=SYSTEM_SCHEMA)
        with self.assertRaisesRegex(ValueError, "absolute path"):
            TaskSpec.from_config(config)

    def test_plan_prose_is_not_a_configuration_source(self):
        plan = self.root / "research_plan.md"
        plan.write_text("Scientific Python: /missing/prose/python")
        config = self.config(plan_path=str(plan))
        with patch.dict(os.environ, {"THEORYMASTER_COMPUTE_PYTHON": str(self.python)}):
            self.assertEqual(resolve_compute_environment(config).python, self.python)

    def test_real_stdlib_probe_preserves_actual_venv_prefix_and_records_source(self):
        environment = self.root / "actual venv"
        venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
        config = self.config(environment / "bin" / "python")
        selected = resolve_compute_environment(config)
        result = probe_compute_environment(selected)
        self.assertEqual(result["selected_python"], str(environment / "bin" / "python"))
        self.assertEqual(result["interpreter"]["prefix"], str(environment))
        self.assertNotEqual(result["interpreter"]["prefix"], result["interpreter"]["base_prefix"])
        self.assertEqual(result["source"], "task_config")
        first = record_compute_preflight(self.root, result)
        second = record_compute_preflight(self.root, result)
        self.assertNotEqual(first, second)
        self.assertEqual(json.loads(first.read_text()), result)
        self.assertEqual(first.stat().st_mode & 0o777, 0o600)
        self.assertEqual(first.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(first.parent.parent.stat().st_mode & 0o777, 0o700)

    def test_probe_failure_or_invalid_output_cannot_produce_passed_receipt(self):
        selection = ComputeEnvironment(self.python, "environment")
        for failure in (OSError("fixture"), subprocess.TimeoutExpired("fixture", 10),
                        subprocess.CalledProcessError(1, "fixture")):
            with self.subTest(failure=type(failure).__name__), \
                 patch("theory_master.compute_environment.subprocess.run", side_effect=failure):
                with self.assertRaisesRegex(RuntimeError, "preflight failed"):
                    probe_compute_environment(selection)
        for output in ("not JSON", "[]", "{}"):
            with self.subTest(output=output), patch(
                    "theory_master.compute_environment.subprocess.run",
                    return_value=SimpleNamespace(stdout=output)):
                with self.assertRaises(RuntimeError):
                    probe_compute_environment(selection)
        self.assertFalse((self.root / "runtime_preflight").exists())

    def test_backend_preflight_is_local_probe_and_mock_route_checks_without_model_turn(self):
        config = self.config(self.python)
        backend = self.backend(config)
        client = Mock()
        backend.invoke = Mock(side_effect=AssertionError("model turn forbidden"))
        def route(*args):
            backend.client = client
            return "fixture-thread"
        with patch.object(backend, "_verify_provider_runtime"), \
             patch.object(backend, "ensure_thread", side_effect=route) as ensure:
            backend.preflight_runtime()
        self.assertEqual(ensure.call_count, 1)
        backend.invoke.assert_not_called()
        client.close.assert_called_once()
        self.assertIsNone(backend.client)
        self.assertEqual(backend.cwd, self.root)
        receipt = backend.compute_environment_preflight
        self.assertEqual(receipt["source"], "task_config")
        self.assertEqual(receipt["selected_python"], str(self.python))
        self.assertTrue(Path(receipt["receipt_path"]).is_file())

    def test_bad_task_interpreter_fails_before_provider_or_thread_creation(self):
        backend = self.backend(self.config(self.root / "missing"))
        with patch.object(backend, "_verify_provider_runtime") as verify, \
             patch.object(backend, "ensure_thread") as ensure:
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                backend.preflight_runtime()
        verify.assert_not_called()
        ensure.assert_not_called()

    def test_client_environment_and_fresh_identity_use_the_same_pinned_choice(self):
        chosen = self.root / "chosen-python"
        chosen.symlink_to(self.python)
        config = self.config(chosen)
        backend = self.backend(config)
        backend._thread_config = lambda *_: {}
        backend._investigator_navigation_rule = lambda: ""
        backend.invoke = Mock(side_effect=AssertionError("model turn forbidden"))
        client = Mock()
        client.thread_start.return_value = SimpleNamespace(id="fixture-thread")
        captured = {}
        def configure(**kwargs):
            captured.update(kwargs)
            # A concurrent config edit must not split the app-server env and
            # fresh thread's fixed interpreter instruction.
            config.write_text(json.dumps({"compute_python": "/missing/later-python"}))
            return SimpleNamespace(**kwargs)
        sdk = SimpleNamespace(Codex=Mock(return_value=client), CodexConfig=configure,
                              Sandbox=SimpleNamespace(workspace_write="write", read_only="read"))
        with patch.dict(sys.modules, {"openai_codex": sdk}), \
             patch.object(backend, "_verify_provider_runtime"), \
             patch.object(backend, "_run_network_guard"), \
             patch.object(CodexBackend, "_runtime_config", return_value=((), {})), \
             patch("theory_master.codex_sdk_compat.install_approval_handler"):
            backend.ensure_thread("investigator", None, "fixture", "Fixture")
        self.assertEqual(captured["env"]["PYTHON"], str(chosen))
        self.assertEqual(captured["env"]["THEORYMASTER_COMPUTE_PYTHON"], str(chosen))
        self.assertIn(str(chosen), client.thread_start.call_args.kwargs["developer_instructions"])
        self.assertNotIn("later-python", client.thread_start.call_args.kwargs["developer_instructions"])
        backend.invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
