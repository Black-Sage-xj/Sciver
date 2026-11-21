"""SDK command dispatcher mocks: never launch a live research task."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theory_master import operator_control
from theory_master.operator_control import ControlRejected, dispatch
from theory_master.operator_host_binding import HostBindingRejected, protected_launch_binding


class OperatorSDKTests(unittest.TestCase):
    def test_managed_resume_enforces_existing_supervisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config=root/'theory-master.config.json'
            config.write_text('{}')
            spec=SimpleNamespace(master_id=1,research_root=root)
            run={'task_root':str(root),'research_root':str(root),'run_id':1,'action':'resume','value':{'cycle_audit':True}}
            with patch('theory_master.operator_control.TaskBinding.load',return_value=SimpleNamespace(config_path=config)):
                with patch('theory_master.operator_control.TaskSpec.from_config',return_value=spec):
                    with patch('theory_master.runtime_manager.runtime_health',return_value={'active':False}):
                        with patch('theory_master.runtime_manager.launch_managed_runtime',return_value={'status':'started','launch_id':'fixture'}) as launch:
                            result=dispatch(run)
                            self.assertEqual(result['launch_status'],'started')
                            self.assertEqual(launch.call_args.kwargs['operation'],'resume')
                            self.assertTrue(launch.call_args.kwargs['cycle_audit'])
                    with patch('theory_master.runtime_manager.runtime_health',return_value={'active':True}):
                        with self.assertRaises(ControlRejected):dispatch(run)

    def test_add_worker_records_one_intent_and_starts_scheduler_without_moving_worker(self):
        from theory_master.worker_mode import read_mode
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'theory-master.config.json'
            config.write_text('{}')
            spec = SimpleNamespace(master_id=1, research_root=root)
            operation = {'task_root': str(root), 'research_root': str(root),
                         'run_id': 1, 'action': 'add_worker', 'value': None}
            binding = patch('theory_master.operator_control.TaskBinding.load',
                            return_value=SimpleNamespace(config_path=config))
            task_spec = patch('theory_master.operator_control.TaskSpec.from_config',
                              return_value=spec)
            with binding, task_spec:
                with patch('theory_master.worker_runtime.runtime_health', return_value={'active': True}), \
                     patch('theory_master.worker_runtime.start_scheduler') as launcher:
                    with self.assertRaises(ControlRejected):
                        dispatch(operation)
                    launcher.assert_not_called()
                    self.assertFalse(read_mode(root)['enabled'])
                with patch('theory_master.worker_runtime.runtime_health', return_value={'active': False}), \
                     patch('theory_master.worker_runtime.list_workers', return_value=[{'worker_id': 'worker-1'}]), \
                     patch('theory_master.worker_runtime.start_scheduler', return_value={'status': 'already_running'}) as launcher:
                    first = dispatch(operation)
                    self.assertEqual(first['requested_agents'], 2)
                    self.assertEqual(first['scheduler'], 'already_running')
                    launcher.assert_called_once()
                    second = dispatch(operation)
                    self.assertEqual(second['requested_agents'], 3)
                    self.assertTrue(read_mode(root)['enabled'])
                    self.assertEqual(read_mode(root)['desired_agents'], 3)

    def test_unknown_action_refused(self):
        with self.assertRaises(ControlRejected):dispatch({'action':'arbitrary_shell'})
        with self.assertRaises(ControlRejected):dispatch({'action':'interrupt'})


class SupervisorHostBindingTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        root = Path(workspace.name).resolve()
        self.config = root / "config" / "theory-master" / "supervisor-control.toml"
        self.config.parent.mkdir(parents=True)
        self.environment = {"XDG_CONFIG_HOME": str(self.config.parents[1])}
        self.source = Path(operator_control.__file__).resolve().parents[1]
        self.target = root / "reviewed sdk"

    def bind(self, target: Path, *, mode: int = 0o600, helper: Path | None = None) -> None:
        content = "sdk_root = " + json.dumps(str(target)) + "\n"
        if helper is not None:
            content += "sandbox_helper_dir = " + json.dumps(str(helper)) + "\n"
        self.config.write_text(content, encoding="utf-8")
        self.config.chmod(mode)

    def task_config(self, *, protected=True):
        root = self.config.parents[2] / "Task Home"
        root.mkdir(exist_ok=True)
        config = root / "sample-config.json"
        config.write_text(json.dumps({"prequalification_holdout": {"files": []}} if protected else {}))
        pointer = root / ".theory-master" / "task.json"
        pointer.parent.mkdir(exist_ok=True)
        pointer.write_text(json.dumps({"schema": "theory-master/task/v2", "config_path": str(config)}))
        return root, config

    def helper(self):
        directory = self.config.parents[2] / "trusted sandbox helper"
        directory.mkdir(mode=0o700)
        executable = directory / "codex-linux-sandbox"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o700)
        return directory

    def test_switch_reexecutes_the_fixed_module_in_the_reviewed_checkout(self):
        package = self.target / "theory_master"
        package.mkdir(parents=True)
        (package / "__init__.py").touch()
        (package / "operator_control.py").write_text(
            "import json, os, sys\n"
            "print(json.dumps({'command': json.load(sys.stdin), 'cwd': os.getcwd(), "
            "'pythonpath': os.environ['PYTHONPATH'], 'parent': os.getppid()}))\n",
            encoding="utf-8",
        )
        self.bind(self.target)
        env = {**os.environ, **self.environment, "PYTHONPATH": str(self.source)}
        for action, value in (("supervisor", True), ("attention_takeover", False)):
            with self.subTest(action=action, value=value):
                request = {"action": action, "value": value, "run_id": 22,
                           "task_root": "/fixture/任务", "research_root": "/fixture/研究",
                           "sdk_root": "/untrusted-input"}
                result = subprocess.run(
                    [sys.executable, "-m", "theory_master.operator_control"],
                    cwd=self.source, env=env, input=json.dumps(request, ensure_ascii=False),
                    text=True, encoding="utf-8", capture_output=True, timeout=30, check=True,
                )
                returned = json.loads(result.stdout)
                self.assertEqual(returned["command"], request)
                self.assertEqual(returned["cwd"], str(self.target))
                self.assertEqual(returned["pythonpath"], str(self.target))
                self.assertEqual(returned["parent"], os.getpid())

    def test_non_switch_and_invalid_values_keep_local_dispatch(self):
        ordinary, _ = self.task_config(protected=False)
        self.bind(self.target, mode=0o644)
        for request in ({"action": "pause", "value": None},
                        {"action": "run", "task_root": str(ordinary), "value": {"attention_takeover": True}},
                        {"action": "quota_auto_adjust", "value": True},
                        {"action": "supervisor", "value": 1}):
            with self.subTest(request=request), patch.dict(os.environ, self.environment), \
                 patch("sys.stdin", io.StringIO(json.dumps(request))), patch("sys.stdout", io.StringIO()), \
                 patch.object(operator_control, "dispatch", return_value={}) as local, \
                 patch.object(operator_control, "_exec_bound_supervisor_sdk") as target:
                self.assertEqual(operator_control.main(), 0)
                local.assert_called_once_with(request)
                target.assert_not_called()

    def test_no_binding_and_matching_checkout_dispatch_locally(self):
        request = {"action": "supervisor", "value": True}
        with patch.dict(os.environ, self.environment):
            self.assertIsNone(operator_control._bound_supervisor_sdk(request))
            self.bind(self.source)
            self.assertIsNone(operator_control._bound_supervisor_sdk(request))

    def test_invalid_bindings_reject_the_switch_before_dispatch(self):
        request = {"action": "supervisor", "value": True}
        for root, mode in ((Path("relative"), 0o600), (self.target, 0o644),
                           (self.target, 0o600)):
            self.bind(root, mode=mode)
            with self.subTest(root=root, mode=mode):
                self.assert_binding_rejected(request)
        self.config.unlink()
        self.config.symlink_to(self.source / "pyproject.toml")
        self.assert_binding_rejected(request)

    def assert_binding_rejected(self, request):
        with patch.dict(os.environ, self.environment), patch("sys.stdin", io.StringIO(json.dumps(request))), \
             patch("sys.stdout", io.StringIO()) as output, \
             patch.object(operator_control, "dispatch") as local, \
             patch.object(operator_control, "_exec_bound_supervisor_sdk") as target:
            self.assertEqual(operator_control.main(), 1)
            self.assertFalse(json.loads(output.getvalue())["ok"])
            local.assert_not_called()
            target.assert_not_called()

    def test_protected_launches_reexec_with_only_host_configured_path(self):
        root, _ = self.task_config()
        helper = self.helper()
        package = self.target / "theory_master"
        package.mkdir(parents=True)
        for module in ("__init__.py", "operator_host_binding.py", "prequalification_holdout.py"):
            (package / module).touch()
        (package / "operator_control.py").write_text(
            "import json, os, sys\n"
            "print(json.dumps({'command':json.load(sys.stdin), 'safe_path':sys.flags.safe_path, "
            "'path':os.environ['PATH'], 'cwd':os.getcwd()}))\n",
        )
        self.bind(self.target, helper=helper)
        env = {**os.environ, **self.environment, "PYTHONPATH": str(self.source)}
        for action, value in (("run", {}), ("resume", {}), ("add_worker", None),
                              ("start_worker_scheduler", None),
                              ("worker_control", {"operation": "retry_direction"}),
                              ("worker_control", {"operation": "resume"}),
                              ("worker_control", {"operation": "preserve_release"})):
            with self.subTest(action=action, value=value):
                request = {"action": action, "task_root": str(root), "value": value,
                           "sdk_root": "/untrusted/sdk", "sandbox_helper_dir": "/untrusted/helper"}
                result = subprocess.run(
                    [sys.executable, "-m", "theory_master.operator_control"],
                    cwd=self.source, env=env, input=json.dumps(request),
                    text=True, capture_output=True, timeout=30, check=True,
                )
                output = json.loads(result.stdout)
                self.assertEqual(output["cwd"], str(self.target))
                self.assertEqual(output["path"].split(os.pathsep)[0], str(helper))
                self.assertEqual(output["command"], request)
                self.assertTrue(output["safe_path"])

    def test_protected_legacy_checkout_cannot_launch_without_a_capable_host_binding(self):
        root, config = self.task_config()
        request = {"action": "run", "task_root": str(root), "value": {}}
        with patch.dict(os.environ, self.environment):
            self.assertEqual(protected_launch_binding(config).sdk_root, self.source)
            with patch("theory_master.operator_host_binding.SDK_ROOT", self.target):
                self.assert_binding_rejected(request)
            package = self.target / "theory_master"
            package.mkdir(parents=True)
            for name in ("__init__.py", "operator_control.py"):
                (package / name).touch()
            self.bind(self.target)
            self.assert_binding_rejected(request)

    def test_protected_local_dispatch_scopes_path_and_rejects_a_resident_legacy_scheduler(self):
        from theory_master.runtime_manager import _service_environment, runtime_source_provenance

        root, config = self.task_config()
        helper = self.helper()
        self.bind(self.source, helper=helper)
        request = {"action": "worker_control", "task_root": str(root),
                   "value": {"worker_id": "worker-1", "operation": "retry_direction"}}
        registry = root / ".theory-master" / "worker_scheduler.json"
        def capture_path(*_args):
            launch_env = _service_environment(dict(os.environ))
            return {"path": launch_env["PATH"], "safe_path": launch_env["PYTHONSAFEPATH"]}

        def scheduler(provenance):
            registry.write_text(json.dumps({"pid": 17, "start_ticks": 25,
                                            "runtime_source_provenance": provenance}))

        with patch.dict(os.environ, {**self.environment, "PATH": "/usr/bin", "PYTHONSAFEPATH": ""}), \
             patch.object(operator_control.TaskBinding, "load", return_value=SimpleNamespace(
                 config_path=config, research_root=root)), \
             patch("theory_master.runtime_manager.process_identity_alive", return_value=True), \
             patch.object(operator_control, "_dispatch_task", side_effect=capture_path) as apply:
            scheduler({"source_root": str(self.target), "source_coherent": True})
            with self.assertRaises(HostBindingRejected):
                dispatch(request)
            apply.assert_not_called()
            scheduler(runtime_source_provenance())
            self.assertEqual(dispatch(request), {"path": f"{helper}:/usr/bin", "safe_path": "1"})
            self.assertEqual(os.environ["PATH"], "/usr/bin")
            self.assertEqual(os.environ["PYTHONSAFEPATH"], "")


class SupervisorBootServiceTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.config = self.root / "boot-allowlist.json"
        self.python = Path(sys.executable)

    def write_config(self, *rows):
        from theory_master.supervisor_boot_service import SCHEMA

        self.config.write_text(json.dumps({"schema": SCHEMA, "tasks": list(rows)}))
        self.config.chmod(0o600)

    def row(self, master, profile="standard"):
        return {"master_id": master, "research_root": str(self.root / str(master)),
                "runtime_python": str(self.python), "profile": profile}

    def test_allowlist_rejects_exposed_files_duplicate_tasks_and_symlinks(self):
        from theory_master.supervisor_boot_service import _load_tasks

        self.write_config(self.row(22))
        self.assertEqual(_load_tasks(self.config)[0].master_id, 22)
        self.config.chmod(0o644)
        with self.assertRaises(ValueError):
            _load_tasks(self.config)
        self.write_config(self.row(22), self.row(22, "b23"))
        with self.assertRaises(ValueError):
            _load_tasks(self.config)
        self.write_config(self.row(22))
        alias = self.root / "alias.json"
        alias.symlink_to(self.config)
        with self.assertRaises(ValueError):
            _load_tasks(alias)

    def test_coordinator_uses_exact_interpreter_and_bound_sdk_for_eligible_tasks(self):
        from theory_master import supervisor_boot_service as boot
        from theory_master.operator_host_binding import HostSdkBinding

        self.write_config(self.row(22), self.row(23), self.row(47, "b23"))
        binding = HostSdkBinding(boot.SDK_ROOT)
        selected = SimpleNamespace(eligible_boot_wait=lambda root: root.name == "22",
                                   eligible_boot_resume=lambda root: root.name == "47")

        def child(argv, **kwargs):
            master = int(argv[-1])
            self.assertEqual(argv, [str(self.python), "-B", "-P", "-m", "theory_master.supervisor_boot_service",
                                    "--config", str(self.config), "--master-id", str(master)])
            self.assertEqual(kwargs["cwd"], binding.sdk_root)
            self.assertEqual(kwargs["env"]["PYTHONPATH"], str(binding.sdk_root))
            self.assertEqual(kwargs["env"]["THEORYMASTER_RUNTIME_PYTHON"], str(self.python))
            self.assertEqual(kwargs["env"].get("THEORY_MASTER_B23_RUNTIME_SOURCE"),
                             str(binding.sdk_root) if master == 47 else None)
            self.assertLessEqual(kwargs["timeout"], 30)
            if master == 22:
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            return subprocess.CompletedProcess(argv, 0, json.dumps({"master_id": master, "status": "started"}), "")

        with patch.dict(sys.modules, {"theory_master.supervisor_boot_recovery": selected}), \
             patch.dict(os.environ, {"THEORY_MASTER_B23_RUNTIME_SOURCE": "/previous/sdk"}), \
             patch.object(boot, "_registered_config", return_value=self.config), \
             patch.object(boot.subprocess, "run", side_effect=child) as invoke:
            outcomes, failed = boot._coordinate(self.config, boot._load_tasks(self.config), binding)
        self.assertTrue(failed)
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual([(row["master_id"], row["status"]) for row in outcomes],
                         [(22, "error"), (23, "not_eligible"), (47, "started")])

    def test_protected_child_validates_binding_and_applies_host_sandbox_environment(self):
        from theory_master import supervisor_boot_service as boot
        from theory_master.operator_host_binding import HostSdkBinding

        self.write_config(self.row(47, "b23"))
        task = boot._load_tasks(self.config)[0]
        task.research_root.mkdir()
        binding = HostSdkBinding(boot.SDK_ROOT, self.root / "sandbox-bin")
        spec = SimpleNamespace(master_id=47, research_root=task.research_root)

        def recover(actual):
            self.assertIs(actual, spec)
            self.assertEqual(os.environ["PATH"].split(os.pathsep)[0], str(binding.sandbox_helper_dir))
            self.assertEqual(os.environ["THEORY_MASTER_B23_RUNTIME_SOURCE"], str(binding.sdk_root))
            self.assertEqual(os.environ["PYTHONSAFEPATH"], "1")
            return {"status": "started"}

        selected = SimpleNamespace(recover_task_wait=lambda spec: {"status": "skipped"},
                                   recover_task_active=recover)
        with patch.dict(sys.modules, {"theory_master.supervisor_boot_recovery": selected}), \
             patch.object(boot, "notebook_config", return_value=(self.config, {"task_id": 47})), \
             patch.object(boot, "effective_path_model", return_value=(self.root, task.research_root, None)), \
             patch.object(boot, "protected_launch_binding", return_value=binding), \
             patch("theory_master.runtime_manager.runtime_python", return_value=str(self.python)), \
             patch("theory_master.specification.TaskSpec.from_task_root", return_value=spec), \
             patch.dict(os.environ, {"PATH": "/usr/bin"}):
            self.assertEqual(boot._recover_selected(task, binding), {"master_id": 47, "status": "started"})
            self.assertEqual(os.environ["PATH"], "/usr/bin")
            spec.master_id = 49
            with self.assertRaises(ValueError):
                boot._recover_selected(task, binding)

    def test_coordinator_reexecs_into_new_host_bound_sdk(self):
        from theory_master import supervisor_boot_service as boot
        from theory_master.operator_host_binding import HostSdkBinding

        self.write_config(self.row(22))
        binding = HostSdkBinding(self.root / "current-sdk")
        package = binding.sdk_root / "theory_master"
        package.mkdir(parents=True)
        (package / "supervisor_boot_service.py").touch()
        with patch.object(boot, "load_host_sdk_binding", return_value=binding), \
             patch.object(boot.os, "chdir") as chdir, \
             patch.object(boot.os, "execve", side_effect=SystemExit(0)) as reexec:
            with self.assertRaises(SystemExit):
                boot.main(["--config", str(self.config)])
        chdir.assert_called_once_with(binding.sdk_root)
        executable, argv, env = reexec.call_args.args
        self.assertEqual(executable, sys.executable)
        self.assertEqual(argv[1:5], ["-B", "-P", "-m", "theory_master.supervisor_boot_service"])
        self.assertEqual(env["PYTHONPATH"], str(binding.sdk_root))


if __name__=='__main__':
    unittest.main()
