"""Run boot recovery for the host's explicitly enrolled Supervisor waits."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .notebook import notebook_config
from .operator_host_binding import SDK_ROOT, HostSdkBinding, load_host_sdk_binding, protected_launch_binding
from .public_submission import strict_json
from .task import effective_master_id, effective_path_model


SCHEMA = "theory-master/supervisor-boot-allowlist/v1"
_MODULE = "theory_master.supervisor_boot_service"
_CONFIG_LIMIT = 32_768
_TASK_LIMIT = 64
_TASK_TIMEOUT = 30
_INTERPRETER = "THEORYMASTER_RUNTIME_PYTHON"
_B23_SOURCE = "THEORY_MASTER_B23_RUNTIME_SOURCE"


@dataclass(frozen=True)
class _Task:
    master_id: int
    research_root: Path
    runtime_python: Path
    profile: str


def _absolute_path(value: Any) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Allowlist paths must be absolute")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError("Allowlist paths must be absolute and normalized")
    return path


def _load_tasks(config: Path) -> list[_Task]:
    path = _absolute_path(str(config))
    if path.resolve(strict=True) != path:
        raise ValueError("Supervisor boot allowlist must not traverse symlinks")
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Supervisor boot allowlist must be a private regular file owned by the current user")
        data = stream.read(_CONFIG_LIMIT + 1)
    if len(data) > _CONFIG_LIMIT:
        raise ValueError("Supervisor boot allowlist is too large")
    payload = strict_json(data)
    if not isinstance(payload, dict) or set(payload) != {"schema", "tasks"} or payload["schema"] != SCHEMA:
        raise ValueError("Invalid Supervisor boot allowlist schema")
    rows = payload["tasks"]
    if not isinstance(rows, list) or len(rows) > _TASK_LIMIT:
        raise ValueError("Invalid Supervisor boot task list")
    tasks = []
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"master_id", "research_root", "runtime_python", "profile"}
                or type(row["master_id"]) is not int or row["master_id"] < 0
                or row["profile"] not in ("standard", "b23")):
            raise ValueError("Invalid Supervisor boot task entry")
        tasks.append(_Task(row["master_id"], _absolute_path(row["research_root"]),
                           _absolute_path(row["runtime_python"]), row["profile"]))
    if len({task.master_id for task in tasks}) != len(tasks) or len({task.research_root for task in tasks}) != len(tasks):
        raise ValueError("Supervisor boot allowlist contains duplicate task identities")
    return tasks


def _task_environment(binding: HostSdkBinding, task: _Task, *, protected: bool = False) -> dict[str, str]:
    env = binding.environment(protected=protected)
    env["PYTHONSAFEPATH"] = "1"
    env[_INTERPRETER] = str(task.runtime_python)
    env.pop(_B23_SOURCE, None)
    if task.profile == "b23":
        env[_B23_SOURCE] = str(binding.sdk_root)
    return env


def _require_interpreter(task: _Task) -> None:
    if not task.runtime_python.is_file() or not os.access(task.runtime_python, os.X_OK):
        raise ValueError("Allowlisted runtime interpreter is not executable")


def _registered_config(task: _Task) -> Path:
    root = task.research_root
    if not root.is_dir() or root.resolve(strict=True) != root:
        raise ValueError("Allowlisted research root is unavailable or unsafe")
    registered = notebook_config(root)
    if registered is None:
        raise ValueError("Allowlisted research root has no task binding")
    config_path, config = registered
    if effective_path_model(config)[1] != root or effective_master_id(config) != task.master_id:
        raise ValueError("Allowlisted task identity does not match its registered binding")
    return config_path


def _recover_selected(task: _Task, binding: HostSdkBinding) -> dict:
    from .runtime_manager import runtime_python
    from .specification import TaskSpec
    from .supervisor_boot_recovery import recover_task_active, recover_task_wait

    _require_interpreter(task)
    root = task.research_root
    config_path = _registered_config(task)
    protected = protected_launch_binding(config_path)
    if protected is not None and protected.sdk_root != binding.sdk_root:
        raise ValueError("Protected task requires its bound Supervisor SDK")
    env = _task_environment(protected or binding, task, protected=protected is not None)
    keys = ("PATH", "PYTHONSAFEPATH", "PYTHONPATH", _INTERPRETER, _B23_SOURCE)
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            if key in env:
                os.environ[key] = env[key]
            else:
                os.environ.pop(key, None)
        if runtime_python() != str(task.runtime_python):
            raise ValueError("Allowlisted runtime interpreter cannot run the Supervisor SDK")
        spec = TaskSpec.from_task_root(root)
        if spec.research_root != root or spec.master_id != task.master_id:
            raise ValueError("Allowlisted task binding changed before recovery")
        result = recover_task_wait(spec)
        if result["status"] == "skipped":
            result = recover_task_active(spec)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return {"master_id": task.master_id, **{key: result[key] for key in ("status", "cycle", "launch_id", "unit")
                                        if key in result}}


def _coordinate(config: Path, tasks: list[_Task], binding: HostSdkBinding) -> tuple[list[dict], bool]:
    from .supervisor_boot_recovery import eligible_boot_resume, eligible_boot_wait

    results = []
    failed = False
    for task in tasks:
        try:
            _registered_config(task)
            if not (eligible_boot_wait(task.research_root) or eligible_boot_resume(task.research_root)):
                results.append({"master_id": task.master_id, "status": "not_eligible"})
                continue
            _require_interpreter(task)
            command = [str(task.runtime_python), "-B", "-P", "-m", _MODULE, "--config", str(config),
                       "--master-id", str(task.master_id)]
            process = subprocess.run(command, cwd=binding.sdk_root, env=_task_environment(binding, task),
                                     stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                     timeout=_TASK_TIMEOUT, check=False)
            if process.returncode:
                raise RuntimeError("Supervisor boot child failed")
            result = strict_json(process.stdout)
            if (not isinstance(result, dict) or result.get("master_id") != task.master_id
                    or not isinstance(result.get("status"), str)):
                raise ValueError("Invalid Supervisor boot child response")
            if result["status"] == "systemd_launch_failed":
                failed = True
        except (OSError, RuntimeError, ValueError, AttributeError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
            failed = True
            result = {"master_id": task.master_id, "status": "error", "error": type(error).__name__}
        results.append(result)
    return results, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--master-id", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        tasks = _load_tasks(args.config)
        binding = load_host_sdk_binding() or HostSdkBinding(SDK_ROOT)
        if binding.sdk_root != SDK_ROOT:
            module = binding.sdk_root / "theory_master" / "supervisor_boot_service.py"
            if not module.is_file() or module.resolve() != module:
                raise ValueError("Bound Supervisor SDK does not support boot recovery")
            command = [sys.executable, "-B", "-P", "-m", _MODULE, "--config", str(args.config)]
            if args.master_id is not None:
                command.extend(["--master-id", str(args.master_id)])
            os.chdir(binding.sdk_root)
            os.execve(sys.executable, command, binding.environment(protected=False))
        if args.master_id is not None:
            task = next((task for task in tasks if task.master_id == args.master_id), None)
            if task is None:
                raise ValueError("Task is absent from the Supervisor boot allowlist")
            print(json.dumps(_recover_selected(task, binding), ensure_ascii=False))
            return 0
        results, failed = _coordinate(args.config, tasks, binding)
        print(json.dumps({"tasks": results}, ensure_ascii=False))
        return int(failed)
    except (OSError, RuntimeError, ValueError, AttributeError, KeyError, TypeError) as error:
        print(f"Supervisor boot recovery rejected: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
