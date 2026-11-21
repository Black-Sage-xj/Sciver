"""Private account pins for an existing single-task Investigator Goal."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import tomllib

from .backend_config import ACCOUNT_PROVIDERS
from .codex_accounts import ACCOUNT_NAMES, _auth_identity_bytes, _write_auth, read_layout
from .invocations import stable_hash
from .quota_provider_fallback import investigator_native_goal_in_flight

_ROLLOUT_PREFIX_LIMIT = 65_536


def retained_goal_binding(cycle: Path, state: dict) -> dict[str, str] | None:
    recovery = state.get("recovery")
    goal = state.get("native_goal")
    lock = state.get("hypothesis_lock")
    if (
        state.get("status") != "paused"
        or state.get("execution_active") is not False
        or state.get("resumable") is not True
        or state.get("worker_id")
        or state.get("abandoned_cycle")
        or not isinstance(lock, dict)
        or lock.get("status") != "locked"
        or str(state.get("cycle_id") or cycle.name) != cycle.name
        or cycle.is_symlink()
        or (cycle / "recovery_control.json").exists()
        or (cycle / "recovery_control.json").is_symlink()
        or not isinstance(recovery, dict)
        or recovery.get("code") != "provider_quota_exhausted"
        or not isinstance(recovery.get("provider"), str)
        or recovery["provider"] not in ACCOUNT_PROVIDERS
        or not investigator_native_goal_in_flight(state)
    ):
        return None
    thread = goal.get("thread_id")
    accounts = {str(value) for value in (
        goal.get("account"), recovery.get("codex_account"), state.get("runtime_codex_account"),
    ) if value is not None and value != ""}
    if not isinstance(thread, str) or not thread or len(accounts) != 1:
        return None
    (account,) = accounts
    if account not in ACCOUNT_NAMES:
        return None
    threads = state.get("threads")
    if isinstance(threads, dict) and threads.get("investigator") not in (None, thread):
        return None
    original = state.get("native_goal_quota_origin")
    if (not isinstance(original, dict) or original.get("thread_id") != thread
            or original.get("account") != account or original.get("provider") != recovery["provider"]
            or not _sha256(original.get("account_identity_sha256"))
            or not _sha256(original.get("objective_sha256"))
            or type(original.get("created_at")) is not int
            or not isinstance(original.get("sqlite_home"), str)
            or not Path(original["sqlite_home"]).is_absolute()
            or not isinstance(original.get("sqlite_identity"), str)
            or not isinstance(original.get("rollout_path"), str)
            or not Path(original["rollout_path"]).is_absolute()
            or not isinstance(original.get("rollout_identity"), str)
            or not isinstance(original.get("rollout_prefix_bytes"), str)
            or not original["rollout_prefix_bytes"].isascii()
            or not original["rollout_prefix_bytes"].isdecimal()
            or len(original["rollout_prefix_bytes"]) > 5
            or not 0 < int(original["rollout_prefix_bytes"]) <= _ROLLOUT_PREFIX_LIMIT
            or not _sha256(original.get("rollout_prefix_sha256"))):
        return None  # A historical slot number alone cannot prove the original account.
    return {
        "cycle": cycle.name, "account": account, "thread_id": thread,
        "provider": recovery["provider"],
        "account_identity_sha256": original["account_identity_sha256"],
        "objective_sha256": original["objective_sha256"],
        "created_at": str(original["created_at"]),
        "sqlite_home": original["sqlite_home"],
        "sqlite_identity": original["sqlite_identity"],
        "rollout_path": original["rollout_path"],
        "rollout_identity": original["rollout_identity"],
        "rollout_prefix_bytes": original["rollout_prefix_bytes"],
        "rollout_prefix_sha256": original["rollout_prefix_sha256"],
        "failure": stable_hash([recovery, goal, lock, original]),
    }


def _sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _read_regular(path: Path, *, private: bool = False, max_bytes: int = 1_048_576) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or (private and info.st_mode & 0o077)):
            raise ValueError("Unsafe retained Goal runtime file")
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Retained Goal runtime file is too large")
    return data


def _identity_digest(data: bytes) -> str:
    identity = _auth_identity_bytes(data)
    if not identity:
        raise ValueError("Retained Goal requires a verifiable Codex account identity")
    return hashlib.sha256(identity.encode()).hexdigest()


def _sqlite_home(source: Path, config: bytes, environment: dict[str, str] | None = None) -> Path:
    sqlite_config = tomllib.loads(config.decode()).get("sqlite_home")
    sqlite_env = (environment or {}).get("CODEX_SQLITE_HOME", os.environ.get("CODEX_SQLITE_HOME"))
    if sqlite_config and sqlite_env and sqlite_config != sqlite_env:
        raise ValueError("Ambiguous original Codex SQLite home")
    home = Path(sqlite_config or sqlite_env or source)
    if not home.is_absolute() or not home.is_dir():
        raise ValueError("Original Codex SQLite home is unavailable")
    return home.resolve(strict=True)


def _sqlite_identity(path: Path) -> str:
    info = path.stat()
    return f"{info.st_dev}:{info.st_ino}"


def goal_rollout_identity(path: str | None, *, prefix_bytes: int | None = None) -> dict[str, str]:
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ValueError("Original Codex Goal has no persisted transcript path")
    canonical = Path(path).resolve(strict=True)
    descriptor = os.open(canonical, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("Original Codex Goal transcript is unavailable")
        length = min(info.st_size, _ROLLOUT_PREFIX_LIMIT) if prefix_bytes is None else prefix_bytes
        if not 0 < length <= _ROLLOUT_PREFIX_LIMIT:
            raise ValueError("Original Codex Goal transcript prefix is unavailable")
        prefix = stream.read(length)
    if len(prefix) != length:
        raise ValueError("Original Codex Goal transcript was truncated")
    return {"rollout_path": str(canonical), "rollout_identity": f"{info.st_dev}:{info.st_ino}",
            "rollout_prefix_bytes": str(length), "rollout_prefix_sha256": hashlib.sha256(prefix).hexdigest()}


def retained_goal_rollout_matches(path: str | None, binding: dict[str, str]) -> bool:
    original = goal_rollout_identity(path, prefix_bytes=int(binding["rollout_prefix_bytes"]))
    return all(binding.get(key) == value for key, value in original.items())


def capture_goal_account_origin(account: str | None, environment: dict[str, str] | None = None) -> dict[str, str] | None:
    """Snapshot the account behind the original app-server under its selector lock."""
    if account not in ACCOUNT_NAMES:
        return None
    try:
        layout = read_layout()
        runtime = layout["runtime"].resolve(strict=True)
        active_home = (environment or {}).get("CODEX_HOME", os.environ.get("CODEX_HOME"))
        if active_home and Path(active_home).resolve(strict=True) != runtime:
            return None
        config = _read_regular(runtime / "config.toml")
        if tomllib.loads(config.decode()).get("cli_auth_credentials_store", "file") != "file":
            return None
        app_auth = (runtime / "auth.json").resolve(strict=True)
        account_identity = _identity_digest(_read_regular(layout[account] / "auth.json", private=True))
        if _identity_digest(_read_regular(app_auth, private=True)) != account_identity:
            return None
        sqlite_home = _sqlite_home(runtime, config, environment)
        origin = {"account": account, "account_identity_sha256": account_identity,
                  "sqlite_home": str(sqlite_home), "sqlite_identity": _sqlite_identity(sqlite_home)}
        # The SDK merges its environment overrides onto the caller environment.
        # Capture the inherited home too, as used by the normal Codex backend.
        if active_home:
            origin.update(codex_home=str(runtime), codex_home_identity=_sqlite_identity(runtime),
                          codex_config_sha256=hashlib.sha256(config).hexdigest())
        return origin
    except (OSError, TypeError, ValueError):
        return None  # Legacy/custom authentication continues normally without auto-pin authority.


def _boot_source(layout: dict[str, Path], binding: dict[str, str]) -> tuple[bytes, Path]:
    source = layout["runtime"].resolve(strict=True)
    config = _read_regular(source / "config.toml")
    if (str(source) != binding.get("codex_home")
            or _sqlite_identity(source) != binding.get("codex_home_identity")
            or hashlib.sha256(config).hexdigest() != binding.get("codex_config_sha256")
            or tomllib.loads(config.decode()).get("cli_auth_credentials_store", "file") != "file"):
        raise ValueError("Original Codex home or configuration changed")
    recorded_sqlite = binding.get("sqlite_home")
    if not recorded_sqlite:
        raise ValueError("Original Codex SQLite home is unavailable")
    sqlite_home = _sqlite_home(source, config, {"CODEX_SQLITE_HOME": recorded_sqlite})
    if str(sqlite_home) != recorded_sqlite or _sqlite_identity(sqlite_home) != binding.get("sqlite_identity"):
        raise ValueError("Original Codex SQLite history changed")
    if not retained_goal_rollout_matches(binding.get("rollout_path"), binding):
        raise ValueError("Original Codex Goal transcript changed")
    account = binding.get("account")
    if account not in ACCOUNT_NAMES or _identity_digest(
        _read_regular(layout[account] / "auth.json", private=True)
    ) != binding.get("account_identity_sha256"):
        raise ValueError("Stored account changed since the original Goal was bound")
    return config, sqlite_home


def boot_goal_account_binding(cycle: Path, state: dict, origin: dict) -> dict[str, str] | None:
    """Check the original Goal auth and runtime files without mutating the selector."""
    goal = state.get("native_goal")
    recovery = state.get("recovery") or {}
    original = state.get("native_goal_quota_origin")
    persisted = origin.get("persistent_goal")
    lock = state.get("hypothesis_lock") or {}
    if (state.get("status") not in ("active", "running") or state.get("execution_active") is not True
            or state.get("worker_id") or state.get("abandoned_cycle")
            or state.get("cycle_id") != cycle.name or cycle.is_symlink()
            or not isinstance(lock, dict) or lock.get("status") != "locked" or not state.get("hypothesis_locked")
            or (cycle / "recovery_control.json").exists() or (cycle / "recovery_control.json").is_symlink()
            or not isinstance(recovery, dict)
            or recovery.get("action") in ("pause", "interrupt", "kill", "abandon_cycle")
            or recovery.get("code") == "provider_quota_exhausted"
            or not isinstance(goal, dict) or goal.get("status") != "active"
            or not isinstance(original, dict) or not isinstance(persisted, dict)):
        return None
    observed = goal.get("origin_identity")
    thread = origin.get("thread_id")
    account = original.get("account")
    if (not isinstance(thread, str) or not thread or goal.get("thread_id") != thread
            or original.get("thread_id") != thread or persisted.get("thread_id") != thread
            or original.get("objective_sha256") != origin.get("objective_sha256")
            or persisted.get("objective_sha256") != origin.get("objective_sha256")
            or not _sha256(original.get("objective_sha256"))
            or type(original.get("created_at")) is not int or original["created_at"] != persisted.get("created_at")
            or not isinstance(observed, dict)
            or any(observed.get(key) != persisted.get(key) for key in
                   ("thread_id", "created_at", "objective_sha256", "token_budget"))
            or not isinstance(account, str) or account not in ACCOUNT_NAMES
            or any(value not in (None, "", account) for value in
                (goal.get("account"), state.get("runtime_codex_account")))
            or not _sha256(original.get("account_identity_sha256"))):
        return None
    snapshots = state.get("runtime_role_routes") or {}
    hot = state.get("runtime_hot_role_routes") or {}
    if not isinstance(snapshots, dict) or not isinstance(hot, dict):
        return None
    route = hot.get("investigator", snapshots.get("investigator"))
    if (not isinstance(route, dict) or not isinstance(route.get("provider"), str)
            or route["provider"] not in ACCOUNT_PROVIDERS
            or route["provider"] != original.get("provider")
            or not isinstance(route.get("model"), str) or not route["model"]):
        return None
    fields = ("sqlite_home", "sqlite_identity", "rollout_path", "rollout_identity",
              "rollout_prefix_bytes", "rollout_prefix_sha256",
              "codex_home", "codex_home_identity", "codex_config_sha256")
    if (any(not isinstance(original.get(key), str) or not original[key] for key in fields)
            or not _sha256(original["codex_config_sha256"])
            or not _sha256(original["rollout_prefix_sha256"])):
        return None  # Captures without the exact original home/config are quota-only.
    binding = {key: original[key] for key in fields}
    binding.update(cycle=cycle.name, account=account, thread_id=thread, provider=route["provider"],
                   model=route["model"], account_identity_sha256=original["account_identity_sha256"],
                   objective_sha256=original["objective_sha256"], created_at=str(original["created_at"]))
    try:
        _boot_source(read_layout(), binding)
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return binding


@dataclass(frozen=True)
class RetainedGoalHome:
    account: str
    thread_id: str
    provider: str
    binding: dict[str, str]
    home: Path
    sqlite_home: Path

    @property
    def auth_path(self) -> Path:
        return self.home / "auth.json"

    def verify_auth(self) -> str:
        if ("codex_config_sha256" in self.binding
                and hashlib.sha256(_read_regular(self.home / "config.toml", private=True)).hexdigest()
                != self.binding["codex_config_sha256"]):
            raise ValueError("Retained Codex configuration changed")
        if _sqlite_identity(self.sqlite_home) != self.binding["sqlite_identity"]:
            raise ValueError("Original Codex SQLite history has changed")
        if not retained_goal_rollout_matches(self.binding["rollout_path"], self.binding):
            raise ValueError("Original Codex Goal transcript has changed")
        data = _read_regular(self.auth_path, private=True)
        if _identity_digest(data) != self.binding["account_identity_sha256"]:
            raise ValueError("Private Codex credential is no longer the original Goal identity")
        return hashlib.sha256(data).hexdigest()


def _prepare_goal_home(root: Path, binding: dict[str, str], *, create: bool, boot: bool) -> RetainedGoalHome:
    account = binding["account"]
    layout = read_layout()
    if boot:
        config, sqlite_home = _boot_source(layout, binding)
    else:
        source = layout["runtime"].resolve(strict=True)
        config = _read_regular(source / "config.toml")
        sqlite_home = _sqlite_home(source, config)
        if str(sqlite_home) != binding["sqlite_home"] or _sqlite_identity(sqlite_home) != binding["sqlite_identity"]:
            raise ValueError("Original Codex SQLite history has changed")
        if not retained_goal_rollout_matches(binding["rollout_path"], binding):
            raise ValueError("Original Codex Goal transcript has changed")

    account_root = layout["root"]
    directory = account_root / "retained-goal-auth"
    task = str(root.resolve(strict=True))
    home = directory / stable_hash([task, binding["cycle"], binding["thread_id"], account])
    if any(path.is_symlink() for path in (account_root, directory, home)):
        raise ValueError("Unsafe retained Goal credential directory")
    root_info = account_root.stat()
    if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.getuid() or root_info.st_mode & 0o077:
        raise ValueError("Retained Goal recovery requires a private account root")
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
        home.mkdir(mode=0o700, exist_ok=True)
    for path in (directory, home):
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Retained Goal credentials require a private directory")

    result = RetainedGoalHome(account, binding["thread_id"], binding["provider"], binding, home, sqlite_home)
    if create:
        # Single-owner Supervisor admission protects initialization; every
        # write is atomic and private. Later SDK refreshes stay in this home.
        if not result.auth_path.exists() and not result.auth_path.is_symlink():
            source_auth = _read_regular(layout[account] / "auth.json", private=True)
            if _identity_digest(source_auth) != binding["account_identity_sha256"]:
                raise ValueError("Stored account changed since the original Goal was bound")
            _write_auth(source_auth, result.auth_path)
        _write_auth(config, home / "config.toml")
    result.verify_auth()
    return result


def prepare_retained_goal_home(root: Path, binding: dict[str, str], *, create: bool = False) -> RetainedGoalHome:
    return _prepare_goal_home(root, binding, create=create, boot=False)


def prepare_boot_goal_home(root: Path, binding: dict[str, str]) -> RetainedGoalHome:
    """Open a private home with the exact original account, config and history."""
    return _prepare_goal_home(root, binding, create=True, boot=True)


def verify_retained_quota_launch(root: Path, approval: dict, *, managed_child: bool = False) -> RetainedGoalHome:
    from .runtime_manager import latest_cycle_name

    cycle_name = latest_cycle_name(root)
    if not cycle_name or cycle_name != approval.get("cycle"):
        raise ValueError("Retained quota recovery Cycle changed")
    cycle = root / cycle_name
    state = json.loads(_read_regular(cycle / "research_state.json", max_bytes=16 * 1_048_576))
    if not isinstance(state, dict):
        raise ValueError("Malformed retained Goal Cycle state")
    binding = retained_goal_binding(cycle, state)
    if binding is None or approval.get("binding") != binding:
        raise ValueError("Retained quota recovery Goal or account changed")
    home = prepare_retained_goal_home(root, binding)
    if (approval.get("auth_sha256") != home.verify_auth()
            or approval.get("sqlite_home") != str(home.sqlite_home)):
        raise ValueError("Retained quota recovery credential or history changed after its probe")
    if managed_child:
        from .runtime_manager import load_registry, process_identity_alive
        registry = load_registry(root)
        attempt = registry.get("retained_quota_attempt") or {}
        if (not registry.get("launch_id") or attempt.get("launch_id") != registry["launch_id"]
                or attempt.get("approval_sha256") != stable_hash(approval)
                or not process_identity_alive(registry.get("supervisor_pid"),
                                              registry.get("supervisor_start_ticks"))):
            raise ValueError("Retained quota recovery has no matching managed Supervisor authorization")
    return home
