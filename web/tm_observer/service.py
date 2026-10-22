from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .archify_views import default_archify_root, default_state_dir

UNIT_NAME = "tm-observer.service"


@dataclass(frozen=True)
class ServiceInstallResult:
    unit_path: Path
    enabled: bool
    started: bool


def _systemd_quote(value: str | Path) -> str:
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _systemd_path(value: str | Path) -> str:
    # WorkingDirectory= treats surrounding quotes as literal characters.
    # Use systemd-style escapes for whitespace instead.
    text = str(value)
    return (
        text.replace("\\", "\\x5c")
        .replace(" ", "\\x20")
        .replace("\t", "\\x09")
    )


def render_user_service(
    repo_root: Path,
    *,
    python_executable: Path | None = None,
    state_dir: Path | None = None,
    archify_root: Path | None = None,
    node_path: Path | None = None,
    auth_file: Path | None = None,
    port: int = 8787,
) -> str:
    repo_root = repo_root.expanduser().resolve()
    python_executable = (python_executable or Path(sys.executable)).expanduser().resolve()
    state_dir = (state_dir or default_state_dir()).expanduser().resolve()
    archify_root = (archify_root or default_archify_root()).expanduser().resolve()
    if node_path is None:
        discovered_node = shutil.which("node")
        if not discovered_node:
            raise ValueError("node executable not found; Archify requires Node.js >=18")
        node_path = Path(discovered_node)
    node_path = node_path.expanduser().resolve()
    package_dir = repo_root / "tool" / "tm_observer"
    if not package_dir.is_dir():
        raise ValueError(f"TM Observatory package directory not found: {package_dir}")
    if not (1 <= int(port) <= 65535):
        raise ValueError(f"invalid TCP port: {port}")

    args = [
        python_executable,
        "-m",
        "tm_observer",
        "serve",
        "--search-root",
        repo_root,
        "--host",
        "127.0.0.1",
        "--port",
        str(int(port)),
        "--state-dir",
        state_dir,
        "--archify-root",
        archify_root,
    ]
    if auth_file is not None:
        auth_file = auth_file.expanduser().resolve()
        if not auth_file.is_file():
            raise ValueError(f"TM Observatory auth file not found: {auth_file}")
        args += ["--basic-auth-file", auth_file]
    exec_start = " ".join(_systemd_quote(arg) for arg in args)
    return f"""[Unit]
Description=TheoryMaster Observatory (read-only)
After=network.target

[Service]
Type=simple
WorkingDirectory={_systemd_path(package_dir)}
ExecStart={exec_start}
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1
Environment={_systemd_quote("TM_OBSERVER_NODE=" + str(node_path))}
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=default.target
"""


def user_unit_path() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(configured).expanduser() if configured else Path("~/.config").expanduser()
    return config_home / "systemd" / "user" / UNIT_NAME


def install_user_service(
    repo_root: Path,
    *,
    python_executable: Path | None = None,
    state_dir: Path | None = None,
    archify_root: Path | None = None,
    node_path: Path | None = None,
    auth_file: Path | None = None,
    port: int = 8787,
    start: bool = True,
) -> ServiceInstallResult:
    state_dir = (state_dir or default_state_dir()).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    path = user_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_user_service(
        repo_root,
        python_executable=python_executable,
        state_dir=state_dir,
        archify_root=archify_root,
        node_path=node_path,
        auth_file=auth_file,
        port=port,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    enabled = False
    started = False
    if start:
        try:
            subprocess.run(["systemctl", "--user", "enable", UNIT_NAME], check=True)
            subprocess.run(["systemctl", "--user", "restart", UNIT_NAME], check=True)
        except subprocess.CalledProcessError:
            # Avoid leaving a half-installed enabled unit behind after a bad start.
            subprocess.run(["systemctl", "--user", "disable", "--now", UNIT_NAME], check=False)
            raise
        enabled = True
        started = True
    return ServiceInstallResult(path, enabled, started)


def uninstall_user_service() -> Path:
    path = user_unit_path()
    subprocess.run(["systemctl", "--user", "disable", "--now", UNIT_NAME], check=False)
    if path.exists():
        path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    return path
