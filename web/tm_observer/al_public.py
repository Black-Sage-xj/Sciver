from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Public hostname/VPS details are deployment settings, never source defaults.
AL_PUBLIC_HOSTNAME = os.environ.get("TM_OBSERVER_PUBLIC_HOSTNAME", "localhost")
AL_PUBLIC_URL = (f"https://{AL_PUBLIC_HOSTNAME}/" if AL_PUBLIC_HOSTNAME != "localhost"
                 else "http://127.0.0.1:8787/")
AL_REMOTE_HOST = os.environ.get("TM_OBSERVER_AL_HOST", "")
AL_REMOTE_USER = os.environ.get("TM_OBSERVER_AL_USER", "tmobserver")
AL_REMOTE_PORT = int(os.environ.get("TM_OBSERVER_AL_PORT", "8453"))
LOCAL_OBSERVER_PORT = 8787
TUNNEL_UNIT_NAME = "tm-observer-al-tunnel.service"


@dataclass(frozen=True)
class TunnelInstallResult:
    unit_path: Path
    enabled: bool
    started: bool


def default_tunnel_key() -> Path:
    return Path("~/.ssh/tm_observer_al_ed25519").expanduser()


def _quote(value: str | Path) -> str:
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_tunnel_service(
    *,
    ssh_executable: Path = Path("/usr/bin/ssh"),
    key_path: Path | None = None,
    known_hosts: Path | None = None,
    remote_host: str = AL_REMOTE_HOST,
    remote_user: str = AL_REMOTE_USER,
    remote_port: int = AL_REMOTE_PORT,
    local_port: int = LOCAL_OBSERVER_PORT,
) -> str:
    key_path = (key_path or default_tunnel_key()).expanduser().resolve()
    known_hosts = (known_hosts or Path("~/.ssh/known_hosts")).expanduser().resolve()
    if not (1 <= int(remote_port) <= 65535 and 1 <= int(local_port) <= 65535):
        raise ValueError("invalid tunnel port")
    remote = f"{remote_user}@{remote_host}"
    forward = f"127.0.0.1:{int(remote_port)}:127.0.0.1:{int(local_port)}"
    args = [
        ssh_executable,
        "-NT",
        "-i",
        key_path,
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ExitOnForwardFailure=yes",
        "-R",
        forward,
        remote,
    ]
    exec_start = " ".join(_quote(arg) for arg in args)
    return f"""[Unit]
Description=TM Observatory reverse tunnel to AL VPS
After=network-online.target tm-observer.service
Wants=network-online.target
Requires=tm-observer.service

[Service]
Type=simple
ExecStart={exec_start}
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=default.target
"""


def tunnel_unit_path() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(configured).expanduser() if configured else Path("~/.config").expanduser()
    return config_home / "systemd" / "user" / TUNNEL_UNIT_NAME


def install_tunnel_service(*, key_path: Path | None = None, start: bool = True) -> TunnelInstallResult:
    key_path = (key_path or default_tunnel_key()).expanduser().resolve()
    if not key_path.is_file():
        raise ValueError(f"tunnel private key not found: {key_path}")
    path = tunnel_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(render_tunnel_service(key_path=key_path), encoding="utf-8")
    os.replace(tmp, path)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    if start:
        try:
            subprocess.run(["systemctl", "--user", "enable", TUNNEL_UNIT_NAME], check=True)
            subprocess.run(["systemctl", "--user", "restart", TUNNEL_UNIT_NAME], check=True)
        except subprocess.CalledProcessError:
            subprocess.run(["systemctl", "--user", "disable", "--now", TUNNEL_UNIT_NAME], check=False)
            raise
    return TunnelInstallResult(path, start, start)


def uninstall_tunnel_service() -> Path:
    path = tunnel_unit_path()
    subprocess.run(["systemctl", "--user", "disable", "--now", TUNNEL_UNIT_NAME], check=False)
    if path.exists():
        path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    return path


def caddy_site() -> str:
    return f"""{AL_PUBLIC_HOSTNAME} {{
    encode gzip
    reverse_proxy 127.0.0.1:{AL_REMOTE_PORT}
}}
"""
