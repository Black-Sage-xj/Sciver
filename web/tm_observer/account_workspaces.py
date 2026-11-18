"""One authenticated Web account per backend process and persisted workspace.

The first (legacy) account keeps the configured workspace. Additional accounts
start with empty task/chat stores and an independent application home. Provider
authentication can be seeded once; model conversations and research are never
copied. These are application workspaces, not OS/container security boundaries.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path

from .auth import AuthRealm, BasicAuthCredentials, auth_accounts


_BASE_ENVIRONMENT = frozenset({
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "USER", "LOGNAME",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "LD_LIBRARY_PATH",
    "SYSTEMROOT", "WINDIR", "PYTHONUTF8", "PYTHONIOENCODING",
})


@dataclass(frozen=True)
class AccountWorkspace:
    username: str
    search_root: Path
    state_dir: Path
    home: Path | None = None

    @property
    def isolated(self) -> bool:
        return self.home is not None


def workspace_layout(auth: AuthRealm, search_root: Path, state_dir: Path) -> tuple[AccountWorkspace, ...]:
    search_root, state_dir = search_root.expanduser().resolve(), state_dir.expanduser().resolve()
    accounts = auth_accounts(auth)
    layouts = [AccountWorkspace(accounts[0].username, search_root, state_dir)]
    base = state_dir / "accounts"
    if len(accounts) > 1 and base.is_relative_to(search_root):
        raise ValueError("Multi-account state-dir must be outside the legacy search-root")
    for account in accounts[1:]:
        label = re.sub(r"[^A-Za-z0-9_-]+", "-", account.username).strip("-")[:40] or "account"
        suffix = hashlib.sha256(account.username.encode()).hexdigest()[:16]
        root = base / f"{label}-{suffix}"
        layouts.append(AccountWorkspace(account.username, root / "workspace", root / "state", root / "home"))
    return tuple(layouts)


def _private_directory(path: Path) -> None:
    if path.absolute() != path.resolve():
        raise ValueError("Account workspace directory must not traverse symbolic links")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def prepare_workspace(layout: AccountWorkspace, *, provider_auth: Path | None = None,
                      codex_binary: Path | None = None) -> None:
    if not layout.isolated:
        return
    assert layout.home is not None
    for path in (layout.search_root.parent.parent, layout.search_root.parent,
                 layout.search_root, layout.state_dir, layout.home):
        _private_directory(path)
    # Only the active provider identity is copied, into a private regular file.
    # Existing account authentication and all persisted work survive restarts.
    if provider_auth is not None and provider_auth.is_file():
        daily = layout.home / ".codex"
        _private_directory(daily)
        destination = daily / "auth.json"
        if not destination.exists() and not destination.is_symlink():
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(provider_auth.read_bytes())
    if codex_binary is not None and codex_binary.is_file():
        binary_dir = layout.home / "bin"
        _private_directory(binary_dir)
        for name in ("codex", "codex.real"):
            target = binary_dir / name
            if not target.exists() and not target.is_symlink():
                target.symlink_to(codex_binary.resolve())


def backend_environment(layout: AccountWorkspace, inherited: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ if inherited is None else inherited)
    if not layout.isolated:
        return environment
    assert layout.home is not None
    runtime_python = environment.get("THEORYMASTER_RUNTIME_PYTHON") or sys.executable
    compute_python = (environment.get("THEORYMASTER_COMPUTE_PYTHON")
                      or str(Path(environment.get("HOME", str(Path.home()))) / "miniconda3" / "bin" / "python"))
    if not Path(compute_python).is_file():
        compute_python = sys.executable
    scholarly_python = environment.get("THEORYMASTER_SCHOLARLY_PYTHON") or sys.executable
    # Start from explicit infrastructure settings. Ambient provider/API keys,
    # SDK roots, pinned identities and development overrides are not inherited.
    environment = {name: value for name, value in environment.items() if name in _BASE_ENVIRONMENT}
    environment.update({
        "HOME": str(layout.home),
        "PATH": str(layout.home / "bin") + os.pathsep + environment.get("PATH", os.defpath),
        "XDG_CONFIG_HOME": str(layout.home / ".config"),
        "XDG_DATA_HOME": str(layout.home / ".local" / "share"),
        "XDG_STATE_HOME": str(layout.home / ".local" / "state"),
        "XDG_CACHE_HOME": str(layout.home / ".cache"),
        "SCIVER_ISOLATED_WORKSPACE": "1",
        "SCIVER_WORKSPACE": str(layout.search_root),
        "THEORY_MASTER_SEARCH_ROOT": str(layout.search_root),
        "THEORY_MASTER_DATA_ROOT": str(layout.search_root / ".theory-master-tasks"),
        "THEORYMASTER_CODEX_HOME": str(layout.home / ".codex-theory-master"),
        "THEORYMASTER_RUNTIME_PYTHON": runtime_python,
        "THEORYMASTER_COMPUTE_PYTHON": compute_python,
        "THEORYMASTER_SCHOLARLY_PYTHON": scholarly_python,
        "SCIVER_DOCUMENT_CONVERSION_CONFIG": str(layout.state_dir / "document-conversion.json"),
        # Use the service's existing proxy; the host's codex-net CLI controls
        # host resources and must not be invoked by a workspace backend.
        "THEORYMASTER_NETWORK_CHECK": "0",
    })
    return environment


class BackendProcesses:
    def __init__(self) -> None:
        self.processes: list[subprocess.Popen] = []
        self._output_threads: list[threading.Thread] = []

    def start(self, layout: AccountWorkspace, account: BasicAuthCredentials, *, cache_ttl: float,
              archify_root: Path, static_refresh: float) -> tuple[str, int]:
        import theory_master

        settings = {"search_root": str(layout.search_root), "state_dir": str(layout.state_dir),
                    "isolated_workspace": layout.isolated, "cache_ttl": cache_ttl,
                    "archify_root": str(archify_root), "static_refresh": static_refresh,
                    "username": account.username, "password": account.password}
        environment = backend_environment(layout)
        module_paths = [str(Path(__file__).resolve().parents[1]),
                        str(Path(theory_master.__file__).resolve().parents[1])]
        environment["PYTHONPATH"] = os.pathsep.join([*module_paths, environment.get("PYTHONPATH", "")]).rstrip(os.pathsep)
        process = subprocess.Popen(
            [sys.executable, "-m", "tm_observer.account_workspaces"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            env=environment,
            cwd=layout.search_root if layout.isolated else None,
        )
        self.processes.append(process)
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(settings) + "\n")
        process.stdin.close()
        result: queue.Queue = queue.Queue(maxsize=1)

        def read_ready() -> None:
            assert process.stdout is not None
            line = process.stdout.readline()
            result.put(line)
            # Backend startup messages are informational; never echo the
            # private stdin configuration or credentials into service logs.
            for _line in process.stdout:
                pass

        thread = threading.Thread(target=read_ready, daemon=True)
        self._output_threads.append(thread)
        thread.start()
        try:
            line = result.get(timeout=30)
            port = json.loads(line)["port"]
            if not isinstance(port, int) or not 1 <= port <= 65535:
                raise ValueError("invalid backend port")
        except (queue.Empty, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"Web backend failed to start for account {account.username}") from exc
        return "127.0.0.1", port

    def close(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
        for process in self.processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
        self.processes.clear()


def serve_accounts(auth: AuthRealm, search_root: Path, host: str, port: int, cache_ttl: float, *,
                   state_dir: Path, archify_root: Path, static_refresh: float, open_browser: bool) -> None:
    from .account_gateway import make_gateway_handler
    from .web import _open_ready_browser
    from theory_master.codex_accounts import runtime_home

    layouts = workspace_layout(auth, search_root, state_dir)
    source_auth = runtime_home() / "auth.json"
    if not source_auth.is_file():
        source_auth = Path.home() / ".codex" / "auth.json"
    direct = Path.home() / "bin" / "codex.real"
    binary = direct if direct.is_file() else (Path(found) if (found := shutil.which("codex")) else None)
    processes = BackendProcesses()
    server = None
    try:
        targets = {}
        for layout, account in zip(layouts, auth_accounts(auth), strict=True):
            prepare_workspace(layout, provider_auth=source_auth, codex_binary=binary)
            targets[account.username] = processes.start(layout, account, cache_ttl=cache_ttl,
                                                        archify_root=archify_root, static_refresh=static_refresh)
        server = ThreadingHTTPServer((host, int(port)), make_gateway_handler(auth, targets))
        browser_host = "127.0.0.1" if host in {"0.0.0.0", "127.0.0.1"} else host
        url = f"http://{browser_host}:{server.server_port}/"
        print(f"Sciver Web UI: {url}\nIndependent account workspaces: {len(targets)}", flush=True)
        if open_browser:
            threading.Thread(target=_open_ready_browser, args=(url,), daemon=True).start()

        def check_backends() -> None:
            # Let the outer service restart the complete, consistent routing
            # table rather than silently leave a dead account backend.
            while not stopped.wait(1):
                if any(process.poll() is not None for process in processes.processes):
                    server.shutdown()
                    return

        stopped = threading.Event()
        monitor = threading.Thread(target=check_backends, daemon=True)
        monitor.start()
        try:
            server.serve_forever()
        finally:
            stopped.set()
        if any(process.poll() is not None for process in processes.processes):
            raise RuntimeError("An account Web backend exited; restart the service")
    finally:
        if server is not None:
            server.server_close()
        processes.close()


def _backend_main() -> None:
    from .web import _serve_single

    settings = json.loads(sys.stdin.readline())

    def ready(port: int) -> None:
        print(json.dumps({"port": port}), flush=True)

    _serve_single(Path(settings["search_root"]), "127.0.0.1", 0, settings["cache_ttl"],
                  state_dir=Path(settings["state_dir"]), archify_root=Path(settings["archify_root"]),
                  static_refresh=settings["static_refresh"],
                  auth=BasicAuthCredentials(settings["username"], settings["password"]),
                  isolated_workspace=settings["isolated_workspace"], ready=ready)


if __name__ == "__main__":
    _backend_main()
