from __future__ import annotations

import fcntl
import json
import os
import select
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


ACCOUNT_NAMES = {"1": "account-1", "2": "account-2"}
_RUNTIME_CONFIG = '''# TheoryMaster-owned Codex runtime.\n# Account switching must change authentication only.\nproject_root_markers = [".theory-master-codex-root"]\n\napproval_policy = "never"\nsandbox_mode = "danger-full-access"\n\n[features]\napps = false\nplugins = false\nremote_plugin = false\nrecommended_plugins = false\n'''
_LEGACY_SHARED_NAMES = ("config.toml", "yun.config.toml", "deepseek.models.json", "rules", "skills")


def account_root() -> Path:
    return Path.home() / ".codex-accounts"


def runtime_home() -> Path:
    """Stable TheoryMaster Codex runtime home, independent of account identity."""
    override = (os.environ.get("THEORYMASTER_CODEX_HOME") or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".codex-theory-master"

@contextmanager
def account_switch_lock():
    """Serialize host-wide reads/switches of the shared Codex account selector."""
    root = account_root()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    path = root / ".switch.lock"
    handle = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _codex_binary() -> Path:
    preferred = Path.home() / "bin" / "codex.real"
    if preferred.is_file():
        return preferred
    discovered = shutil.which("codex")
    if not discovered:
        raise RuntimeError("codex executable was not found on PATH")
    return Path(discovered)


def _rate_window(window: object) -> dict[str, object] | None:
    if not isinstance(window, dict):
        return None
    used = window.get("usedPercent")
    remaining = None
    if isinstance(used, (int, float)):
        remaining = max(0.0, min(100.0, 100.0 - float(used)))
        if remaining.is_integer():
            remaining = int(remaining)
    return {
        "used_percent": used,
        "remaining_percent": remaining,
        "window_duration_mins": window.get("windowDurationMins"),
        "resets_at": window.get("resetsAt"),
    }


def account_rate_limits(number: str, *, timeout_s: float = 8.0) -> dict[str, object]:
    """Read one account's quota without creating a scientific/model turn.

    The probe runs in a disposable CODEX_HOME containing only a symlink to the
    chosen identity. This keeps account-1/account-2 identity-only even if Codex
    app-server decides to create logs, state, or default config on startup.
    """
    if number not in ACCOUNT_NAMES:
        raise ValueError(f"unknown Codex account: {number}")
    layout = ensure_layout()
    auth = layout[number] / "auth.json"
    if not auth.is_file():
        return {"account": number, "ok": False, "error": "auth_missing", "queried_at": int(time.time())}

    with tempfile.TemporaryDirectory(prefix=f"tm-quota-probe-{number}-") as temp:
        probe_home = Path(temp)
        (probe_home / "auth.json").symlink_to(auth.resolve())
        env = os.environ.copy()
        env["CODEX_HOME"] = str(probe_home)
        proc = subprocess.Popen(
            [str(_codex_binary()), "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            bufsize=1,
        )
        try:
            assert proc.stdin is not None
            assert proc.stdout is not None
            for message in (
                {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "theory-master-quota-controller", "version": "1.0.0"}}},
                {"id": 2, "method": "account/rateLimits/read", "params": {"excludeResetCreditDetails": True, "supportsLunaReserve": False}},
            ):
                proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
                proc.stdin.flush()
            deadline = time.monotonic() + max(0.1, float(timeout_s))
            while time.monotonic() < deadline:
                ready, _, _ = select.select([proc.stdout], [], [], min(0.25, max(0.0, deadline - time.monotonic())))
                if not ready:
                    if proc.poll() is not None:
                        break
                    continue
                line = proc.stdout.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("id") != 2:
                    continue
                result = message.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError(str(message.get("error") or "rate-limit read failed"))
                rate_limits = result.get("rateLimits") if isinstance(result.get("rateLimits"), dict) else {}
                return {
                    "account": number,
                    "ok": True,
                    "ordinary_usage_allowed": result.get("ordinaryUsageAllowed"),
                    "rate_limit_reached_type": rate_limits.get("rateLimitReachedType"),
                    "primary": _rate_window(rate_limits.get("primary")),
                    "secondary": _rate_window(rate_limits.get("secondary")),
                    "queried_at": int(time.time()),
                }
            raise TimeoutError("Codex rate-limit query timed out")
        except Exception as exc:
            return {
                "account": number,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "queried_at": int(time.time()),
            }
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.5)


def quota_available(snapshot: dict[str, object]) -> bool | None:
    """Tri-state availability: True/False when quota is known, None when probe failed."""
    if not snapshot.get("ok"):
        return None
    ordinary = snapshot.get("ordinary_usage_allowed")
    if isinstance(ordinary, bool):
        return ordinary
    reached = snapshot.get("rate_limit_reached_type")
    if reached not in {None, "", "none", "None"}:
        return False
    known: list[float] = []
    for key in ("primary", "secondary"):
        window = snapshot.get(key)
        if isinstance(window, dict) and isinstance(window.get("used_percent"), (int, float)):
            known.append(float(window["used_percent"]))
    if not known:
        return None
    return all(value < 100.0 for value in known)


def quota_reset_at(snapshot: dict[str, object]) -> object | None:
    resets: list[object] = []
    for key in ("primary", "secondary"):
        window = snapshot.get(key)
        if isinstance(window, dict) and window.get("resets_at") is not None:
            resets.append(window["resets_at"])
    if not resets:
        return None
    numeric = [value for value in resets if isinstance(value, (int, float))]
    return min(numeric) if numeric else resets[0]


def _legacy_home() -> Path:
    return Path.home() / ".codex"


def _restart_direct_app_servers() -> tuple[int, ...]:
    """Restart the managed daily Codex daemon so it reloads the selected auth."""
    codex_bin = Path.home() / "bin" / "codex.real"
    if not codex_bin.exists():
        discovered = shutil.which("codex")
        if not discovered:
            return ()
        codex_bin = Path(discovered)
    env = os.environ.copy()
    env["CODEX_HOME"] = str(_legacy_home())
    result = subprocess.run(
        [str(codex_bin), "app-server", "daemon", "restart"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "Codex App daemon restart failed").strip()
        raise RuntimeError(message)
    return ()


def _copy_auth(source: Path, target: Path) -> bool:
    if not source.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    target.chmod(0o600)
    return True


def _selector_number(current: Path) -> str | None:
    if not current.is_symlink():
        return None
    try:
        name = Path(os.readlink(current)).name
    except OSError:
        return None
    for number, account_name in ACCOUNT_NAMES.items():
        if name == account_name:
            return number
    return None


def _ensure_identity_dir(path: Path, *, seed_auth: Path | None = None) -> None:
    """Ensure an account identity directory without inheriting runtime configuration."""
    preserved_auth: bytes | None = None
    if path.is_symlink():
        auth = path / "auth.json"
        if auth.is_file():
            preserved_auth = auth.read_bytes()
        path.unlink()
    elif path.exists() and not path.is_dir():
        raise RuntimeError(f"refusing to replace non-directory Codex account identity: {path}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    auth = path / "auth.json"
    if not auth.is_file():
        if preserved_auth is not None:
            auth.write_bytes(preserved_auth)
            auth.chmod(0o600)
        elif seed_auth is not None:
            _copy_auth(seed_auth, auth)
    # Old layouts linked account-neutral runtime state into each account home.
    # Remove only those inherited links; historical local sessions/state are left
    # untouched but are no longer used by TheoryMaster's stable runtime home.
    for name in _LEGACY_SHARED_NAMES:
        candidate = path / name
        if candidate.is_symlink():
            candidate.unlink()


def _seed_runtime_state(source: Path, target: Path) -> None:
    """Preserve useful selected-account runtime history on first migration.

    Copy only account-neutral Codex state. Skills/plugins/rules/config/auth are
    intentionally excluded so the new TheoryMaster runtime starts isolated.
    """
    if not source.is_dir() or any(target.iterdir()):
        return
    excluded = {
        "auth.json", "config.toml", "yun.config.toml", "deepseek.models.json",
        "rules", "skills", "plugins", ".tmp", "tmp", "thread-writer-locks",
    }
    for child in source.iterdir():
        if child.name in excluded or child.is_symlink():
            continue
        destination = target / child.name
        try:
            if child.is_dir():
                shutil.copytree(child, destination, symlinks=True)
            elif child.is_file():
                shutil.copy2(child, destination)
        except OSError:
            # Runtime-history preservation is best-effort and must never block
            # account migration or TheoryMaster startup.
            continue


def _ensure_runtime(root: Path, selected_number: str, legacy: Path) -> Path:
    home = runtime_home()
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    home.chmod(0o700)

    # Preserve current TM/Codex history once, but never inherit daily Codex
    # skills/plugins/rules/configuration.
    selected_home = root / ACCOUNT_NAMES[selected_number]
    _seed_runtime_state(selected_home, home)

    config = home / "config.toml"
    if not config.exists():
        config.write_text(_RUNTIME_CONFIG, encoding="utf-8")

    # Provider metadata is account-neutral. Copy once rather than symlink so
    # later daily-Codex edits cannot change TheoryMaster behavior.
    for name in ("yun.config.toml", "deepseek.models.json"):
        destination = home / name
        source = legacy / name
        if not destination.exists() and source.is_file():
            shutil.copy2(source, destination)

    # The runtime stays fixed. Its only account-varying component is auth.json,
    # which follows the atomic account selector.
    auth = home / "auth.json"
    desired = root / "current" / "auth.json"
    if auth.is_symlink():
        try:
            correct = auth.resolve(strict=False) == desired.resolve(strict=False)
        except OSError:
            correct = False
        if not correct:
            auth.unlink()
    elif auth.exists():
        # A Codex refresh may replace a symlink atomically. Preserve that newer
        # credential into the selected identity before restoring the selector link.
        _copy_auth(auth, selected_home / "auth.json")
        auth.unlink()
    if not auth.exists() and not auth.is_symlink():
        auth.symlink_to(desired)

    (home / "skills").mkdir(exist_ok=True)
    return home


def ensure_layout() -> dict[str, Path]:
    """Create identity-only accounts plus one shared TheoryMaster runtime home."""
    root = account_root()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    legacy = _legacy_home()
    legacy.mkdir(parents=True, exist_ok=True, mode=0o700)
    current = root / "current"
    selected_before = _selector_number(current) or "1"

    migration_marker = root / ".identity-runtime-v2"
    account1 = root / ACCOUNT_NAMES["1"]
    account2 = root / ACCOUNT_NAMES["2"]
    _ensure_identity_dir(account1, seed_auth=(legacy / "auth.json") if not migration_marker.exists() else None)
    _ensure_identity_dir(account2)

    if current.exists() and not current.is_symlink():
        raise RuntimeError(f"refusing to replace non-symlink selector: {current}")
    if current.is_symlink() and _selector_number(current) is None:
        current.unlink()
    if not current.exists() and not current.is_symlink():
        current.symlink_to(ACCOUNT_NAMES[selected_before], target_is_directory=True)

    selected = _selector_number(current) or selected_before

    # Daily Codex keeps its stable ~/.codex runtime too. Only auth follows the
    # shared selector; config/skills/plugins/workspace state stay in ~/.codex.
    daily_auth = legacy / "auth.json"
    desired_auth = current / "auth.json"
    if daily_auth.is_symlink():
        try:
            correct = daily_auth.resolve(strict=False) == desired_auth.resolve(strict=False)
        except OSError:
            correct = False
        if not correct:
            daily_auth.unlink()
    elif daily_auth.exists():
        if migration_marker.exists():
            # Recover an auth file atomically replaced by Codex token refresh.
            _copy_auth(daily_auth, root / ACCOUNT_NAMES[selected] / "auth.json")
        daily_auth.unlink()
    if not daily_auth.exists() and not daily_auth.is_symlink():
        daily_auth.symlink_to(desired_auth)
    migration_marker.touch(exist_ok=True)

    shared_runtime = _ensure_runtime(root, selected, legacy)
    return {
        "root": root, "1": account1, "2": account2, "current": current,
        "daily_runtime": legacy, "runtime": shared_runtime,
    }


def selected_account() -> str | None:
    layout = ensure_layout()
    return _selector_number(layout["current"])


def use_account(number: str, *, restart_app: bool = True, _lock_held: bool = False) -> Path:
    """Switch authentication identity only; runtime state stays fixed."""
    if number not in ACCOUNT_NAMES:
        raise ValueError(f"unknown Codex account: {number}")
    if not _lock_held:
        with account_switch_lock():
            return use_account(number, restart_app=restart_app, _lock_held=True)
    layout = ensure_layout()
    if not (layout[number] / "auth.json").is_file():
        raise RuntimeError("target Codex account is not authenticated; selector unchanged")
    current = layout["current"]
    target_name = ACCOUNT_NAMES[number]
    temp = layout["root"] / f".current-{os.getpid()}"
    if temp.exists() or temp.is_symlink():
        temp.unlink()
    temp.symlink_to(target_name, target_is_directory=True)
    os.replace(temp, current)
    # Revalidate the runtime auth link in case Codex replaced it while refreshing.
    _ensure_runtime(layout["root"], number, _legacy_home())
    if restart_app:
        _restart_direct_app_servers()
    return layout[number]


def account_status() -> dict[str, object]:
    layout = ensure_layout()
    selected = _selector_number(layout["current"])
    accounts: dict[str, dict[str, object]] = {}
    for number in ("1", "2"):
        path = layout[number]
        accounts[number] = {
            "path": str(path),
            "auth_present": (path / "auth.json").is_file(),
        }
    return {
        "selected": selected,
        "selector": str(layout["current"]),
        "runtime_home": str(layout["runtime"]),
        "runtime_auth": str(layout["runtime"] / "auth.json"),
        "theorymaster_codex_home_override": os.environ.get("THEORYMASTER_CODEX_HOME"),
        "ambient_codex_home": os.environ.get("CODEX_HOME"),
        "accounts": accounts,
    }


def login_account(number: str) -> int:
    """Login an identity in isolation, then keep only its auth.json."""
    if number not in ACCOUNT_NAMES:
        raise ValueError(f"unknown Codex account: {number}")
    layout = ensure_layout()
    target = layout[number]
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("codex executable was not found on PATH")
    with tempfile.TemporaryDirectory(prefix=f"tm-codex-login-{number}-") as temp_dir:
        env = os.environ.copy()
        env["CODEX_HOME"] = temp_dir
        result = subprocess.call([executable, "login", "--device-auth"], env=env)
        if result != 0:
            return result
        fresh_auth = Path(temp_dir) / "auth.json"
        if not fresh_auth.is_file():
            raise RuntimeError("Codex login succeeded but did not create auth.json")
        _copy_auth(fresh_auth, target / "auth.json")
    # If this is the selected account, the stable runtime link already points here.
    return 0
