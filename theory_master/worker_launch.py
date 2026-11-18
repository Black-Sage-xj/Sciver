"""Detached service launch and task-scoped executor environment."""
from __future__ import annotations
import subprocess
import uuid
from pathlib import Path
from typing import Any


def _launch_detached_service(args: list[str], *, root: Path, env: dict[str, str], output: Any, kind: str) -> None:
    """Launch each TM executor as its own systemd user service, not an Observer child.

    setsid() does not leave the Observer systemd cgroup. A scope launcher may
    wait for its payload, so use a transient service whose independently owned
    process self-registers the authoritative PID/start ticks.
    """
    from . import runtime_manager
    unit = f"tm-{kind}-{uuid.uuid4().hex[:12]}"
    values = runtime_manager._service_environment(env).items()
    cmd = ["systemd-run", "--user", "--collect", "--unit", unit,
           "--working-directory", str(root)]
    cmd.extend(f"--setenv={key}={value}" for key, value in values)
    if getattr(output, "name", None) and str(output.name).startswith("/"):
        cmd.append(f"--property=StandardOutput=append:{output.name}")
        cmd.append("--property=StandardError=inherit")
    proc = subprocess.run([*cmd, *runtime_manager._service_command(args, env)], cwd=root, env=env,
                          stdin=subprocess.DEVNULL, stdout=output,
                          stderr=subprocess.STDOUT, check=False, timeout=15)
    if proc.returncode:
        raise RuntimeError(f"independent TM service failed to start ({kind}, exit {proc.returncode})")


def _task_scoped_process_env(root: Path) -> dict[str, str]:
    """Honor task-scoped Supervisor/email OFF in detached Worker processes.

    Only recognized on/off controls are transferred; never import arbitrary
    task .env lines, provider credentials, or owner-local secrets.
    """
    from . import runtime_manager
    env = runtime_manager._runtime_env()
    path = Path(root).resolve() / ".theory-master" / "env"
    if path.is_file():
        keys = {"THEORY_MASTER_SUPERVISOR", "THEORY_MASTER_EMAIL_TERMINAL"}
        for line in path.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name in keys:
                control = value.strip().strip("\"'").lower()
                if control in {"0", "false", "off", "no"}:
                    env[name] = "0"
                elif control in {"1", "true", "on", "yes"}:
                    env[name] = "1"
    return env
