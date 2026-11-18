"""Worker model routes, profiles and stable account bindings."""
from __future__ import annotations
import fcntl
import re
from pathlib import Path
from typing import Any


def _pinned_codex_home(root: Path, worker_id: str, account: str) -> Path:
    """Private SDK app-server home pinned to an account, never host selector."""
    from . import worker_store
    from .codex_accounts import ACCOUNT_NAMES, ensure_layout

    if account not in ACCOUNT_NAMES:
        raise ValueError("invalid account for pinned Worker")
    layout = ensure_layout()
    auth = (layout[account] / "auth.json").resolve()
    if not auth.is_file():
        raise RuntimeError("selected account has no stored authentication")
    home = worker_store.worker_dir(root, worker_id) / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    # Config/skills are runtime, not per-account identity; auth is symlinked
    # to the exact immutable account directory rather than root/current.
    config = home / "config.toml"
    from .codex_accounts import _RUNTIME_CONFIG
    if not config.exists():
        config.write_text(_RUNTIME_CONFIG, encoding="utf-8")
    link = home / "auth.json"
    if link.is_symlink():
        if link.resolve() != auth:
            raise RuntimeError("pinned Worker Codex auth points to another identity")
    elif link.is_file():
        # The Codex SDK may atomically replace auth.json when refreshing a
        # token. Reconcile that refreshed identity into the SAME pinned account
        # before creating a fresh private runtime; never rotate host selection.
        from .codex_accounts import _reconcile_auth_refresh, account_switch_lock
        with account_switch_lock():
            _reconcile_auth_refresh(link, layout["root"])
            link.symlink_to(auth)
    elif link.exists():
        raise RuntimeError("invalid Worker auth file type")
    else:
        link.symlink_to(auth)
    (home / "skills").mkdir(exist_ok=True)
    return home


def worker_route_overrides(root: Path, worker_id: str) -> dict[str, Any]:
    from . import multi_worker, worker_store
    from .backend_config import _route_for, REASONING_EFFORTS, ROUTE_ROLES, task_route_catalog
    from dataclasses import replace

    path = worker_store.worker_dir(root, worker_id) / "role_routes.json"
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_size > 16000:
        raise ValueError("unsafe Worker route settings")
    data = multi_worker._read_json(path)
    if data.get("schema") != "theory-master/worker-role-routes/v1":
        raise ValueError("incompatible Worker role settings")
    records = data.get("roles")
    if not isinstance(records, dict) or not set(records).issubset(ROUTE_ROLES):
        raise ValueError("invalid Worker model roles")
    catalog = {(m["model"], m["provider"]) for m in task_route_catalog()}
    output = {}
    for role, row in records.items():
        if not isinstance(row, dict) or set(row) not in (
                {"model", "provider", "reasoning_effort"},
                {"model", "provider", "reasoning_effort", "activation"}):
            raise ValueError("invalid Worker model setting")
        if row.get("activation", "next_cycle") not in {"next_cycle", "next_invocation"}:
            raise ValueError("invalid Worker route activation")
        if (row["model"], row["provider"]) not in catalog or row["reasoning_effort"] not in REASONING_EFFORTS:
            raise ValueError("unsupported Worker model/effort")
        output[role] = replace(_route_for(row["model"], row["provider"]),
                               reasoning_effort=row["reasoning_effort"])
    return output


def worker_route_activations(root: Path, worker_id: str) -> dict[str, str]:
    """Validated per-role timing; historical Worker settings remain next-Cycle."""
    from . import multi_worker, worker_store
    path = worker_store.worker_dir(root, worker_id) / "role_routes.json"
    if not path.exists():
        return {}
    worker_route_overrides(root, worker_id)
    data = multi_worker._read_json(path)
    return {role: row.get("activation", "next_cycle")
            for role, row in data["roles"].items()}


def save_worker_route(root: Path, worker_id: str, *, role: str, model: str,
                      provider: str, effort: str,
                      activation: str = "next_cycle") -> dict[str, str]:
    from . import multi_worker, worker_store
    from .backend_config import REASONING_EFFORTS, ROUTE_ROLES, task_route_catalog

    if (role not in ROUTE_ROLES or effort not in REASONING_EFFORTS
            or activation not in {"next_cycle", "next_invocation"} or (
            model, provider) not in {(x["model"], x["provider"]) for x in task_route_catalog()}):
        raise ValueError("unsupported Worker model selection")
    directory = worker_store.worker_dir(root, worker_id)
    if not (directory / "runtime.json").is_file():
        raise ValueError("Worker not registered")
    with open(directory / "route.lock", "a+", encoding="utf-8") as fp:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        old = worker_route_overrides(root, worker_id)
        modes = worker_route_activations(root, worker_id)
        modes[role] = activation
        from dataclasses import replace
        from .backend_config import _route_for
        old[role] = replace(_route_for(model, provider), reasoning_effort=effort)
        data = {"schema": "theory-master/worker-role-routes/v1", "roles": {
            key: {"model": item.model, "provider": item.provider, "reasoning_effort": item.reasoning_effort,
                  "activation": modes[key]}
            for key, item in sorted(old.items())}}
        multi_worker._save_json(directory / "role_routes.json", data)
    return {"worker_id": worker_id, "role": role, "model": model,
            "provider": provider, "reasoning_effort": effort,
            "activation": activation,
            "effective_at": ("next_safe_role_invocation" if activation == "next_invocation"
                             else "next_worker_cycle")}


def read_worker_profile(root: Path, worker_id: str) -> str | None:
    """Task-local preset, pinned to a Worker rather than a global account."""
    from . import multi_worker, worker_store
    from .backend_config import normalize_model_profile
    path = worker_store.worker_dir(root, worker_id) / "model_profile.json"
    if not path.is_file():
        return None
    if path.is_symlink() or path.stat().st_size > 4096:
        raise ValueError("unsafe Worker model profile")
    data = multi_worker._read_json(path)
    if data.get("schema") != "theory-master/worker-model-profile/v1" or not isinstance(data.get("profile"), str):
        raise ValueError("incompatible Worker model profile")
    return normalize_model_profile(data["profile"])


def save_worker_profile(root: Path, worker_id: str, profile: str) -> dict[str, str]:
    """Select six role routes for the next safe invocation or new Cycle.

    A full-profile selection *replaces* earlier per-role overrides, which
    otherwise silently win over the selected tier and mislead the UI.
    Both files share route.lock with per-role edits; retain old files on error.
    """
    from . import multi_worker, worker_store
    from .backend_config import normalize_model_profile
    directory = worker_store.worker_dir(root, worker_id)
    if not (directory / "runtime.json").is_file():
        raise ValueError("Worker not registered")
    profile = normalize_model_profile(profile)
    preset_path = directory / "model_profile.json"
    routes_path = directory / "role_routes.json"
    for item in (preset_path, routes_path):
        if item.is_symlink():
            raise ValueError("unsafe Worker profile/route symlink")
    with open(directory / "route.lock", "a+", encoding="utf-8") as fp:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        # Validate existing overrides rather than erasing corrupt settings.
        prior = worker_route_overrides(root, worker_id)
        before_profile = preset_path.read_bytes() if preset_path.is_file() else None
        before_routes = routes_path.read_bytes() if routes_path.is_file() else None
        try:
            multi_worker._save_json(preset_path, {
                "schema": "theory-master/worker-model-profile/v1", "profile": profile})
            multi_worker._save_json(routes_path, {
                "schema": "theory-master/worker-role-routes/v1", "roles": {}})
        except Exception:
            for path, original in ((preset_path, before_profile), (routes_path, before_routes)):
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    import tempfile
                    with tempfile.NamedTemporaryFile(dir=directory, prefix=".restore-", delete=False) as out:
                        out.write(original)
                        restore_path = Path(out.name)
                    restore_path.replace(path)
            raise
    return {"worker_id": worker_id, "profile": profile,
            "cleared_worker_role_overrides": len(prior),
            "effective_at": "next_safe_role_invocation"}


def effective_worker_profile(root: Path, worker_id: str) -> str | None:
    """Resolve the latest explicit Worker preset or Services provider choice.

    Earlier Worker choices inherit a newer Services selection. A later Worker
    preset save selects its own provider until Services is switched again.
    Neither choice interrupts an in-flight model invocation.
    """
    from . import worker_store
    preset = read_worker_profile(root, worker_id)
    if not preset:
        return None
    from .service_provider_switch import (
        selected_service_preference, preference_path, TIER_BY_PROFILE,
        CODEX_PROFILES,
    )
    chosen = selected_service_preference()
    if not chosen:
        return preset
    try:
        service_changed = preference_path().stat().st_mtime_ns
        profile_path = worker_store.worker_dir(root, worker_id) / "model_profile.json"
        # A synthetic/legacy profile without a timestamp cannot supersede an
        # explicit Services selection. Normal saved Worker profiles have one.
        worker_changed = profile_path.stat().st_mtime_ns if profile_path.is_file() else 0
    except OSError:
        return preset
    if worker_changed >= service_changed:
        return preset
    tier = TIER_BY_PROFILE[preset]
    return CODEX_PROFILES[tier]


def resolve_worker_routes(root: Path, worker_id: str) -> dict[str, Any]:
    from .backends import resolve_routes
    from .backend_config import build_model_profile
    # Last explicit provider choice wins; task overrides never shadow a
    # Worker six-role preset. Per-role Worker overrides remain stronger.
    preset = effective_worker_profile(root, worker_id)
    routes = (build_model_profile(preset) if preset
              else resolve_routes(task_route_override_root=root))
    routes.update(worker_route_overrides(root, worker_id))
    return routes


def _share_enabled(root: Path) -> bool:
    from .worker_mode import read_mode
    mode = read_mode(root)
    return bool(mode["enabled"] and mode["share_accounts"])


def _shared_account_candidate(accounts: list[str], active: list[dict[str, Any]],
                              launched: list[dict[str, Any]]) -> str | None:
    """Select a healthy authenticated account with an available shared slot.

    An account is one quota pool even when N isolated Workers use it.
    Never launch new scientific Turns against a positively exhausted account.
    """
    from . import worker_state
    from collections import Counter
    from .codex_accounts import account_rate_limits, quota_available
    counts = Counter(str(w.get("account")) for w in active if w.get("account"))
    counts.update(str(w.get("account")) for w in launched if w.get("account"))
    options = []
    for account in accounts:
        if counts[account] >= worker_state.MAX_SHARED_ACCOUNT_WORKERS:
            continue
        quota = account_rate_limits(account)
        if quota_available(quota) is not True:
            continue
        windows = [item.get("remaining_percent") for item in
                   (quota.get("primary") or {}, quota.get("secondary") or {})
                   if isinstance(item.get("remaining_percent"), (int,float))]
        available = min(windows) if windows else 0
        options.append((counts[account], -available, account))
    return sorted(options)[0][2] if options else None


def worker_account_preferences(root: Path) -> dict[str, str]:
    """Operator intent for future Cycle allocation, never a live credential switch."""
    from . import multi_worker, worker_state
    path = Path(root).resolve() / ".theory-master" / "worker_account_preferences.json"
    if path.is_symlink() or (path.exists() and path.stat().st_size > 16384):
        raise ValueError("unsafe Worker account preferences")
    if not path.exists():
        return {}
    data = multi_worker._read_json(path)
    values = data.get("accounts")
    if (data.get("schema") != worker_state.WORKER_ACCOUNT_PREFERENCES_SCHEMA
            or not isinstance(values, dict)
            or len(values) > 1024
            or any(not isinstance(w, str) or not re.fullmatch(r"worker-[1-9][0-9]*", w)
                   or a not in {"1", "2"} for w, a in values.items())):
        raise ValueError("invalid Worker account preference registry")
    return values


def set_worker_account_preference(root: Path, worker_id: str, account: str) -> dict[str, Any]:
    """Save a future-Cycle choice under the same lock as scheduler allocation.

    This intentionally does not migrate a frozen Cycle, pause a Worker, or
    modify CODEX_HOME in a live process.
    """
    from . import multi_worker, worker_state, worker_store
    root = Path(root).resolve()
    if not re.fullmatch(r"worker-[1-9][0-9]*", worker_id) or account not in {"1", "2", "keep"}:
        raise ValueError("invalid Worker/account assignment")
    from .codex_accounts import account_status
    from .worker_mode import read_mode
    lock_path = root / ".theory-master" / "scheduler.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        mode = read_mode(root)
        if int(worker_id[7:]) > mode["desired_agents"]:
            raise ValueError("Worker is outside the configured active pool")
        worker = next((w for w in worker_store.list_workers(root) if w["worker_id"] == worker_id
                       and not w.get("dismissed")), None)
        if worker is None:
            raise ValueError("Worker is not registered; refresh after allocation")
        if account != "keep" and not (account_status()["accounts"].get(account) or {}).get("auth_present"):
            raise ValueError("selected Codex account is not authenticated")
        values = worker_account_preferences(root)
        if account == "keep":
            values.pop(worker_id, None)
        else:
            if not mode["share_accounts"] and any(
                    wid != worker_id and chosen == account for wid, chosen in values.items()):
                raise ValueError("this Codex account is already assigned to another Worker")
            values[worker_id] = account
        multi_worker._save_json(root / ".theory-master" / "worker_account_preferences.json",
                   {"schema": worker_state.WORKER_ACCOUNT_PREFERENCES_SCHEMA, "accounts": values})
        return {"worker_id": worker_id, "planned_account": values.get(worker_id),
                "current_account": worker.get("account"), "effective_at": "next_new_cycle",
                "current_cycle_unchanged": True}


def _rebind_idle_worker_home(root: Path, worker_id: str, old: str | None, new: str) -> None:
    """Change a completed Worker's auth link only before a new claim is made."""
    from . import runtime_manager, worker_state, worker_store
    if not old or old == new:
        return
    previous = worker_store.read_worker(root, worker_id)
    if (runtime_manager.process_identity_alive(previous.get("pid"), previous.get("pid_start_ticks"))
            or previous.get("status") in (worker_state.RUNNING | worker_state.RESUMABLE)):
        raise ValueError("cannot rebind a running or resumable Worker account")
    from .codex_accounts import account_switch_lock, ensure_layout
    old_home = _pinned_codex_home(root, worker_id, old)
    target = (ensure_layout()[new] / "auth.json").resolve()
    if not target.is_file():
        raise ValueError("target account authentication unavailable")
    with account_switch_lock():
        link = old_home / "auth.json"
        if not link.is_symlink() or link.resolve() != (ensure_layout()[old] / "auth.json").resolve():
            raise ValueError("Worker auth identity changed; refusing rebind")
        link.unlink()
        link.symlink_to(target)


def _route_capacity(spec: Any, slots: int) -> tuple[int, list[str | None]]:
    from .backends import resolve_routes
    from .backend_config import ACCOUNT_PROVIDERS
    from .codex_accounts import account_status

    routes = resolve_routes(task_route_override_root=spec.research_root)
    needs_account = any(route.provider in ACCOUNT_PROVIDERS for route in routes.values())
    if not needs_account:
        return slots, [None] * slots
    registered = account_status()["accounts"]
    accounts = [i for i in ("1", "2") if (registered.get(i) or {}).get("auth_present")]
    if _share_enabled(spec.research_root):
        return (slots if accounts else 0), accounts
    return min(slots, len(accounts)), accounts[:slots]
