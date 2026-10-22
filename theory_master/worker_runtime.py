"""Explicit per-Master multi-Worker process control; no implicit scientific runs.

Only the operator-issued ``workers start`` starts an autonomous supervisor loop.
Until launch, all APIs are read-only. The legacy single-owner runner and this
scheduler cannot coexist within one Master. Real-model regression tests require
separate permission; deterministic tests inject fake child executables.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .goal_terminal import GoalSealedError
from .multi_worker import (
    CycleClaims, ProposalGate, _read_json, _save_json, attach_owner, finish_owned_claim, utcnow,
)
from .runtime_manager import (
    _runtime_env, process_identity_alive, process_start_ticks, runtime_health,
    runtime_source_provenance,
)

def _launch_detached_service(args: list[str], *, root: Path, env: dict[str, str], output: Any, kind: str) -> None:
    """Launch each TM executor as its own systemd user service, not an Observer child.

    setsid() does not leave the Observer systemd cgroup. A scope launcher may
    wait for its payload, so use a transient service whose independently owned
    process self-registers the authoritative PID/start ticks.
    """
    unit = f"tm-{kind}-{uuid.uuid4().hex[:12]}"
    forwarded = ("PYTHONPATH", "PATH", "HOME", "LANG", "LC_ALL", "HTTP_PROXY",
                 "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy",
                 "all_proxy", "no_proxy", "THEORYMASTER_CODEX_HOME", "THEORYMASTER_ENV_FILE",
                 "THEORYMASTER_QUOTA_PROVIDER_FALLBACK")
    values = [(k, v) for k, v in env.items() if k in forwarded or
              k.startswith(("THEORY_MASTER", "THEORYMASTER_WORKER"))]
    cmd = ["systemd-run", "--user", "--collect", "--unit", unit,
           "--working-directory", str(root)]
    cmd.extend(f"--setenv={key}={value}" for key, value in values)
    if getattr(output, "name", None) and str(output.name).startswith("/"):
        cmd.append(f"--property=StandardOutput=append:{output.name}")
        cmd.append("--property=StandardError=inherit")
    proc = subprocess.run([*cmd, *args], cwd=root, env=env,
                          stdin=subprocess.DEVNULL, stdout=output,
                          stderr=subprocess.STDOUT, check=False, timeout=15)
    if proc.returncode:
        raise RuntimeError(f"independent TM service failed to start ({kind}, exit {proc.returncode})")


SCHEMA = "theory-master/worker-runtime/v1"
RUNNING = frozenset({"STARTING", "RUNNING", "PAUSE_REQUESTED", "STOP_REQUESTED"})
RESUMABLE = frozenset({"PAUSED", "FAILED", "NEEDS_ATTENTION", "INTERRUPTED"})
# Neither is a failure: no Supervisor repair, no automatic resume.
WAITING_RESEARCH_DIRECTION = "WAITING_RESEARCH_DIRECTION"
STOPPED_GOAL_COMPLETED = "STOPPED_GOAL_COMPLETED"
NON_INCIDENT_STOPS = frozenset({WAITING_RESEARCH_DIRECTION, STOPPED_GOAL_COMPLETED})
DIRECTION_FINGERPRINT_SCHEMA = "theory-master/research-direction-fingerprint/v1"
DIRECTION_FINGERPRINT_FIELDS = ("committed_outcome_count", "committed_outcome_ids_sha256",
                                "scientific_model_version", "research_plan_sha256")
RETRY_DIRECTION_SCHEMA = "theory-master/worker-retry-direction/v1"
# A Worker/account migration may temporarily have no observable live PID.
SCHEDULER_NO_PROGRESS_GRACE_SECONDS = 120.0


def worker_dir(root: Path, worker_id: str) -> Path:
    if not worker_id.startswith("worker-") or not worker_id[7:].isdigit() or int(worker_id[7:]) < 1:
        raise ValueError("invalid Worker ID")
    return Path(root).resolve() / ".theory-master" / "workers" / worker_id


def read_worker(root: Path, worker_id: str) -> dict[str, Any]:
    return _read_json(worker_dir(root, worker_id) / "runtime.json")


def save_worker(root: Path, worker_id: str, **changes: Any) -> dict[str, Any]:
    directory = worker_dir(root, worker_id)
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / "runtime.lock", "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        payload = _read_json(directory / "runtime.json") or {"schema": SCHEMA, "worker_id": worker_id}
        if payload.get("schema") != SCHEMA:
            raise ValueError("incompatible worker runtime schema")
        payload.update(changes)
        if "runtime_source_provenance" not in payload or changes.get("status") == "RUNNING":
            provenance = runtime_source_provenance()
            payload["runtime_source_provenance"] = provenance
            history = payload.get("runtime_source_history")
            history = history if isinstance(history, list) else []
            last = history[-1] if history and isinstance(history[-1], dict) else {}
            if (not history or last.get("sdk_git_sha") != provenance.get("sdk_git_sha")
                    or last.get("module_sha256") != provenance.get("module_sha256")):
                history.append({"observed_at": utcnow().isoformat(), **provenance})
            payload["runtime_source_history"] = history[-8:]
        payload["updated_at"] = utcnow().isoformat()
        _save_json(directory / "runtime.json", payload)
        return payload


def list_workers(root: Path) -> list[dict[str, Any]]:
    directory = Path(root).resolve() / ".theory-master" / "workers"
    result = []
    for path in directory.glob("worker-*/runtime.json") if directory.exists() else []:
        data = _read_json(path)
        if data.get("schema") != SCHEMA:
            continue
        active = process_identity_alive(data.get("pid"), data.get("pid_start_ticks"))
        result.append({**data, "process_alive": active,
                       "effective_status": data.get("status") if active or data.get("status") not in RUNNING else "ORPHANED"})
    return sorted(result, key=lambda d: int(d.get("worker_id", "worker-0").split("-")[-1]))



def reconcile_released_worker_cycle(root: Path, worker: dict[str, Any]) -> dict[str, Any] | None:
    """Align a released Worker's persisted Cycle lifecycle without changing science.

    Releasing a frontier is an explicit choice not to resume that exact Cycle.
    A dead Worker must therefore never leave its authoritative research state
    claiming active execution. Preserve the frozen hypothesis, evidence and
    checkpoint; record the prior runtime identity; never fabricate a Judge/Goal
    verdict or turn a nonterminal Cycle into OVER.
    """
    root = Path(root).resolve()
    cycle_id = str(worker.get("current_cycle") or "")
    if not cycle_id:
        return None
    if not cycle_id.isascii() or not cycle_id.isdecimal() or int(cycle_id) < 1:
        raise ValueError("Worker has an invalid assigned Cycle; refusing to release")
    cycle_dir = root / cycle_id
    if cycle_dir.parent != root or cycle_dir.is_symlink() or not cycle_dir.is_dir():
        raise ValueError("Worker Cycle path is unsafe or missing; refusing to release")

    state_path = cycle_dir / "research_state.json"
    if state_path.is_symlink() or not state_path.is_file():
        raise ValueError("Worker Cycle lacks authoritative research_state.json; refusing to release")
    state = _read_json(state_path)
    worker_id = str(worker.get("worker_id") or "")
    if not worker_id or state.get("worker_id") != worker_id:
        raise ValueError("Worker/Cycle identity mismatch; refusing to release")

    run_path = cycle_dir / "run_state.json"
    if run_path.is_symlink():
        raise ValueError("Worker Cycle run_state.json is a symlink; refusing to release")
    run = _read_json(run_path) if run_path.is_file() else {}
    original_state = dict(state)
    original_run = dict(run)
    science_status = str(state.get("status") or "").lower()
    run_status = str(run.get("status") or "").lower()
    active_labels = {"active", "running", "starting", "unresponsive"}
    if science_status in active_labels or run_status in active_labels:
        owner_pid = state.get("owner_pid") or run.get("owner_pid")
        worker_pid = worker.get("pid")
        try:
            same_owner = owner_pid is not None and worker_pid is not None and int(owner_pid) == int(worker_pid)
        except (TypeError, ValueError):
            same_owner = False
        owner_ticks = (
            worker.get("pid_start_ticks") if same_owner
            else state.get("owner_pid_start_ticks") or state.get("pid_start_ticks")
                 or run.get("owner_pid_start_ticks") or run.get("pid_start_ticks")
        )
        if process_identity_alive(owner_pid, owner_ticks):
            raise ValueError("Cycle owner process is still live; refusing to reconcile a released Worker")
        if process_identity_alive(worker_pid, worker.get("pid_start_ticks")):
            raise ValueError("Worker process is still live; refusing to reconcile its Cycle")

    when = utcnow().isoformat()
    prior_recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else {}
    prior_status = science_status or "unknown"
    prior_owner_pid = state.get("owner_pid") or run.get("owner_pid")
    prior_turn = state.get("current_turn_id") or run.get("current_turn_id")
    stopped_statuses = {"paused", "failed", "needs_attention", "interrupted"}
    terminal_statuses = {"over", "complete", "completed", "cancelled", "stopped"}
    if science_status in {"active", "running"}:
        state["status"] = "interrupted"
        state["phase"] = "interrupted"
        state["runtime_state"] = "interrupted"
    elif science_status in stopped_statuses:
        # RELEASED is deliberately not resumable; the frozen artifacts remain
        # inspectable and a later Cycle may choose a frontier independently.
        pass
    elif science_status in terminal_statuses:
        # Preserve the terminal scientific result and verdict exactly.
        pass
    else:
        raise ValueError(f"Unknown Cycle lifecycle status {science_status!r}; refusing to release")

    recovery = {
        "action": "worker_release",
        "reason": "Worker frontier was explicitly released after verifying its process was absent",
        "at": when,
        "worker_id": worker_id,
        "cycle": cycle_id,
        "previous_status": prior_status,
        "previous_owner_pid": prior_owner_pid,
        "previous_current_turn_id": prior_turn,
        "previous_execution_active": state.get("execution_active"),
        "previous_resumable": state.get("resumable"),
        "prior_recovery": prior_recovery,
    }
    state["execution_active"] = False
    state["resumable"] = False
    state["owner_pid"] = None
    state["recovery"] = recovery
    backup_state_path = cycle_dir / "research_state.before-worker-release-reconcile.json"
    if backup_state_path.is_symlink():
        raise ValueError("Worker Cycle reconciliation backup is a symlink; refusing to release")
    if not backup_state_path.exists():
        _save_json(backup_state_path, original_state)
    if run_path.is_file():
        backup_run_path = cycle_dir / "run_state.before-worker-release-reconcile.json"
        if backup_run_path.is_symlink():
            raise ValueError("Worker Cycle run-state backup is a symlink; refusing to release")
        if not backup_run_path.exists():
            _save_json(backup_run_path, original_run)
    _save_json(state_path, state)

    status_map = {
        "active": "INTERRUPTED", "running": "INTERRUPTED",
        "paused": "PAUSED", "failed": "FAILED", "needs_attention": "NEEDS_ATTENTION",
        "interrupted": "INTERRUPTED", "over": "OVER", "complete": "COMPLETE",
        "completed": "COMPLETED", "cancelled": "CANCELLED", "stopped": "STOPPED",
    }
    projected_status = status_map[science_status]
    if run:
        run["status"] = projected_status
        run["phase"] = state.get("phase") or projected_status.lower()
        run["execution_active"] = False
        run["resumable"] = False
        run["owner_pid"] = None
        run["recovery"] = recovery
        for key in ("current_agent", "current_turn_id", "current_thread_id", "current_tool", "current_activity"):
            if key in run:
                run[key] = None
        _save_json(run_path, run)

    event = {
        "event": "worker_cycle_reconciled",
        "at": when,
        "worker_id": worker_id,
        "cycle": cycle_id,
        "previous_status": prior_status,
        "status": state["status"],
        "execution_active": False,
        "resumable": False,
        "scientific_verdict_changed": False,
        "evidence_or_checkpoint_deleted": False,
    }
    event_path = cycle_dir / "worker_lifecycle_events.jsonl"
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return {"worker_id": worker_id, "cycle": cycle_id, "status": state["status"],
            "run_state_status": projected_status, "resumable": False, "reconciled": True}


def available_worker_name(root: Path, *, exclude: frozenset[str] = frozenset(),
                          eligible_ids: frozenset[str] | None = None) -> str:
    workers = list_workers(root)
    claims = set(exclude) | {w["worker_id"] for w in workers if w.get("process_alive")
                               or w.get("effective_status") in (RESUMABLE | RUNNING)}
    for index in range(1, len(workers) + 2):
        name = f"worker-{index}"
        if name not in claims and (eligible_ids is None or name in eligible_ids):
            return name
    raise RuntimeError("Worker name allocation failed")


def _task_scoped_process_env(root: Path) -> dict[str, str]:
    """Honor task-scoped Supervisor/email OFF in detached Worker processes.

    Only recognized on/off controls are transferred; never import arbitrary
    task .env lines, provider credentials, or owner-local secrets.
    """
    env = _runtime_env()
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


def _pinned_codex_home(root: Path, worker_id: str, account: str) -> Path:
    """Private SDK app-server home pinned to an account, never host selector."""
    from .codex_accounts import ACCOUNT_NAMES, ensure_layout

    if account not in ACCOUNT_NAMES:
        raise ValueError("invalid account for pinned Worker")
    layout = ensure_layout()
    auth = (layout[account] / "auth.json").resolve()
    if not auth.is_file():
        raise RuntimeError("selected account has no stored authentication")
    home = worker_dir(root, worker_id) / "codex-home"
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
        from .codex_accounts import _copy_auth, account_switch_lock
        with account_switch_lock():
            _copy_auth(link, auth)
            link.unlink()
            link.symlink_to(auth)
    elif link.exists():
        raise RuntimeError("invalid Worker auth file type")
    else:
        link.symlink_to(auth)
    (home / "skills").mkdir(exist_ok=True)
    return home


def worker_route_overrides(root: Path, worker_id: str) -> dict[str, Any]:
    from .backend_config import _route_for, REASONING_EFFORTS, ROUTE_ROLES, task_route_catalog
    from dataclasses import replace

    path = worker_dir(root, worker_id) / "role_routes.json"
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_size > 16000:
        raise ValueError("unsafe Worker route settings")
    data = _read_json(path)
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
    path = worker_dir(root, worker_id) / "role_routes.json"
    if not path.exists():
        return {}
    worker_route_overrides(root, worker_id)
    data = _read_json(path)
    return {role: row.get("activation", "next_cycle")
            for role, row in data["roles"].items()}


def save_worker_route(root: Path, worker_id: str, *, role: str, model: str,
                      provider: str, effort: str,
                      activation: str = "next_cycle") -> dict[str, str]:
    from .backend_config import REASONING_EFFORTS, ROUTE_ROLES, task_route_catalog

    if (role not in ROUTE_ROLES or effort not in REASONING_EFFORTS
            or activation not in {"next_cycle", "next_invocation"} or (
            model, provider) not in {(x["model"], x["provider"]) for x in task_route_catalog()}):
        raise ValueError("unsupported Worker model selection")
    directory = worker_dir(root, worker_id)
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
        _save_json(directory / "role_routes.json", data)
    return {"worker_id": worker_id, "role": role, "model": model,
            "provider": provider, "reasoning_effort": effort,
            "activation": activation,
            "effective_at": ("next_safe_role_invocation" if activation == "next_invocation"
                             else "next_worker_cycle")}


def read_worker_profile(root: Path, worker_id: str) -> str | None:
    """Task-local preset, pinned to a Worker rather than a global account."""
    from .backend_config import normalize_model_profile
    path = worker_dir(root, worker_id) / "model_profile.json"
    if not path.is_file():
        return None
    if path.is_symlink() or path.stat().st_size > 4096:
        raise ValueError("unsafe Worker model profile")
    data = _read_json(path)
    if data.get("schema") != "theory-master/worker-model-profile/v1" or not isinstance(data.get("profile"), str):
        raise ValueError("incompatible Worker model profile")
    return normalize_model_profile(data["profile"])


def save_worker_profile(root: Path, worker_id: str, profile: str) -> dict[str, str]:
    """Select six role routes for the next safe invocation or new Cycle.

    A full-profile selection *replaces* earlier per-role overrides, which
    otherwise silently win over the selected tier and mislead the UI.
    Both files share route.lock with per-role edits; retain old files on error.
    """
    from .backend_config import normalize_model_profile
    directory = worker_dir(root, worker_id)
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
            _save_json(preset_path, {
                "schema": "theory-master/worker-model-profile/v1", "profile": profile})
            _save_json(routes_path, {
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
        profile_path = worker_dir(root, worker_id) / "model_profile.json"
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


# Per-account concurrent science requests in explicit sharing mode. Each
# Worker still receives a unique isolated CODEX_HOME/SDK client.
MAX_SHARED_ACCOUNT_WORKERS = 2


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
    from collections import Counter
    from .codex_accounts import account_rate_limits, quota_available
    counts = Counter(str(w.get("account")) for w in active if w.get("account"))
    counts.update(str(w.get("account")) for w in launched if w.get("account"))
    options = []
    for account in accounts:
        if counts[account] >= MAX_SHARED_ACCOUNT_WORKERS:
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




WORKER_ACCOUNT_PREFERENCES_SCHEMA = "theory-master/worker-account-preferences/v1"


def worker_account_preferences(root: Path) -> dict[str, str]:
    """Operator intent for future Cycle allocation, never a live credential switch."""
    path = Path(root).resolve() / ".theory-master" / "worker_account_preferences.json"
    if path.is_symlink() or (path.exists() and path.stat().st_size > 16384):
        raise ValueError("unsafe Worker account preferences")
    if not path.exists():
        return {}
    data = _read_json(path)
    values = data.get("accounts")
    if (data.get("schema") != WORKER_ACCOUNT_PREFERENCES_SCHEMA
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
        worker = next((w for w in list_workers(root) if w["worker_id"] == worker_id
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
        _save_json(root / ".theory-master" / "worker_account_preferences.json",
                   {"schema": WORKER_ACCOUNT_PREFERENCES_SCHEMA, "accounts": values})
        return {"worker_id": worker_id, "planned_account": values.get(worker_id),
                "current_account": worker.get("account"), "effective_at": "next_new_cycle",
                "current_cycle_unchanged": True}


def _rebind_idle_worker_home(root: Path, worker_id: str, old: str | None, new: str) -> None:
    """Change a completed Worker's auth link only before a new claim is made."""
    if not old or old == new:
        return
    previous = read_worker(root, worker_id)
    if (process_identity_alive(previous.get("pid"), previous.get("pid_start_ticks"))
            or previous.get("status") in (RUNNING | RESUMABLE)):
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


def _graph_path(spec: Any) -> Path:
    if getattr(spec, "branch_canvas", None) is None:
        raise ValueError("Task configuration must define a Canvas projection path")
    return Path(spec.branch_canvas).with_name("science_graph.sqlite3")


def _live_cycle_claims(root: Path) -> list[dict[str, Any]]:
    """Non-terminal Cycle Claims (claimed/paused), read without taking the lock."""
    registry = _read_json(Path(root) / ".theory-master" / "cycle_claims.json")
    return [c for c in (registry.get("claims") or {}).values() if isinstance(c, dict)]


def _worker_live_claim(claims: Any, worker: dict[str, Any]) -> dict[str, Any] | None:
    """The live Cycle Claim exactly matching the Worker's recorded fencing token."""
    claim_id, epoch = worker.get("claim_id"), worker.get("epoch")
    if not claim_id or epoch is None:
        return None
    for claim in claims.list_claims():
        if (claim.get("claim_id") == claim_id and claim.get("worker_id") == worker.get("worker_id")
                and int(claim.get("fencing_epoch") or 0) == int(epoch)):
            return claim
    return None


def _claim_owns_cycle(claim: dict[str, Any] | None, cycle: Any) -> bool:
    text = str(cycle or "")
    return bool(claim) and text.isascii() and text.isdecimal() and int(text) >= 1 \
        and claim.get("cycle_id") == str(int(text))


def _checkpoint_matches_claim(state: dict[str, Any], worker_id: str,
                              claim: dict[str, Any] | None, cycle: Any) -> bool:
    """Cycle identity = Worker + Cycle Claim (+ bound H), not a claimed historical H."""
    if state.get("worker_id") != worker_id or not _claim_owns_cycle(claim, cycle):
        return False
    locked = str((state.get("hypothesis_lock") or {}).get("id") or "")
    bound = claim.get("hypothesis_id")
    return not (bound and locked and locked != bound)


def _recover_failed_cycle_pointer(
    root: Path, worker: dict[str, Any], claim: dict[str, Any] | None,
) -> str | None:
    """Recover only a FAILED preflight's existing, safely fenced Cycle ID."""
    if (worker.get("status") != "FAILED" or not isinstance(claim, dict)
            or claim.get("worker_id") != worker.get("worker_id")):
        return None
    cycle = str(claim.get("cycle_id") or "")
    if not _claim_owns_cycle(claim, cycle):
        return None
    state_path = Path(root) / cycle / "research_state.json"
    if state_path.is_symlink() or not state_path.is_file():
        return None
    state = _read_json(state_path)
    if (state.get("resumable") is not True
            or str(state.get("status") or "").lower() not in {"paused", "failed", "interrupted", "needs_attention"}
            or not _checkpoint_matches_claim(state, str(worker.get("worker_id") or ""), claim, cycle)):
        return None
    return cycle


def _ensure_legacy_claims_migrated(root: Path) -> dict[str, Any] | None:
    """First use after V1.1: convert unmigrated legacy H claims into Cycle Claims.

    Fail closed when migration skips any entry. Worker records holding the
    legacy token are re-pointed to the new paused Cycle Claim token.
    """
    root = Path(root).resolve()
    legacy = _read_json(root / ".theory-master" / "branch_claims.json")
    entries = [c for c in (legacy.get("claims") or {}).values() if isinstance(c, dict)]
    if not entries:
        return None
    registry = CycleClaims(root)
    migrated_claims = [*registry.list_claims(), *registry.history()]
    known = {(c.get("migrated_from") or {}).get("claim_id") for c in migrated_claims} - {None}
    if all(c.get("claim_id") in known for c in entries):
        return None
    from .worker_migration import migrate_branch_claims
    result = migrate_branch_claims(root)
    if result.get("skipped"):
        raise RuntimeError("legacy branch claim migration skipped entries; manual review required: "
                           + json.dumps(result["skipped"], ensure_ascii=False)[:700])
    for claim in registry.list_claims():
        source = claim.get("migrated_from") or {}
        if not source.get("claim_id"):
            continue
        try:
            record = read_worker(root, str(claim.get("worker_id")))
        except ValueError:
            continue
        if record.get("claim_id") == source["claim_id"]:
            save_worker(root, claim["worker_id"], claim_id=claim["claim_id"],
                        epoch=claim["fencing_epoch"], legacy_claim_id=source["claim_id"],
                        cycle_claims_migrated_at=utcnow().isoformat())
    return result


def _living_workers(root: Path) -> list[dict[str, Any]]:
    """Return Workers that reserve a slot, not necessarily live processes.

    Any non-terminal Cycle Claim reserves Agent/account capacity until a
    validated explicit Resume/Release. This includes FAILED and NEEDS_ATTENTION
    Workers whose frozen Cycle may still be resumed; it prevents a later resume
    from exceeding the configured parallel limit. The scheduler separately
    reports dead failed claims as requiring manual recovery, not as live work.
    """
    claim_owners = {(c.get("worker_id"), c.get("claim_id"), c.get("fencing_epoch"))
                    for c in _live_cycle_claims(root)}
    return [w for w in list_workers(root) if
            (w.get("process_alive") and w.get("status") in RUNNING) or
            (w.get("worker_id"), w.get("claim_id"), w.get("epoch")) in claim_owners]


def _master_complete(root: Path, *, spec: Any | None = None) -> bool:
    """Goal terminal = SQLite seal (G1); verified legacy receipt is a read-only fallback."""
    from .goal_terminal import goal_sealed
    from .specification import TaskSpec, list_cycle_dirs

    root = Path(root).resolve()
    if spec is None:
        spec = TaskSpec.from_task_root(root)
    graph = _graph_path(spec) if getattr(spec, "branch_canvas", None) is not None else None
    if graph is not None and goal_sealed(root, graph) is not None:
        return True
    # No candidate receipt => skip resolving the legacy Goal authority, which
    # preserves lightweight Worker planning for unfinished/fixture task roots.
    if graph is None or not any(((_read_json(c / "research_state.json").get("goal_completion") or {})
                                 .get("verdict") == "passed")
                                for c in list_cycle_dirs(root, task_mode=True)):
        return False
    return goal_sealed(root, graph, spec=spec) is not None


def research_direction_fingerprint(spec: Any) -> dict[str, Any]:
    """G6 deterministic wake signal: committed Outcomes, Scientific Model version, plan hash."""
    import hashlib
    import sqlite3
    root = Path(spec.research_root).resolve()
    ids: list[str] = []
    graph = _graph_path(spec)
    if graph.is_file():
        from .science_graph_store import ScienceGraphStore
        with ScienceGraphStore(graph, readonly=True) as store:
            try:
                ids = sorted(str(row[0]) for row in store.db.execute("SELECT id FROM cycle_evaluations"))
            except sqlite3.OperationalError:
                ids = []
    from .scientific_model import state_path
    model_state = state_path(root)
    version = _read_json(model_state).get("version") if model_state.is_file() else None
    plan = Path(spec.document)
    return {
        "schema": DIRECTION_FINGERPRINT_SCHEMA,
        "committed_outcome_count": len(ids),
        "committed_outcome_ids_sha256": hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest(),
        "scientific_model_version": version if isinstance(version, int) else None,
        "research_plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest() if plan.is_file() else None,
    }


def _direction_values(fingerprint: Any) -> tuple[Any, ...] | None:
    if not isinstance(fingerprint, dict) or not all(k in fingerprint for k in DIRECTION_FINGERPRINT_FIELDS):
        return None
    return tuple(fingerprint[k] for k in DIRECTION_FINGERPRINT_FIELDS)


def _retry_direction_path(root: Path, worker_id: str) -> Path:
    return worker_dir(root, worker_id) / "retry_direction.json"


def _pending_retry_direction(root: Path, worker_id: str) -> dict[str, Any] | None:
    request = _read_json(_retry_direction_path(root, worker_id))
    return request if request.get("status") == "pending" else None


def _partition_waiting_workers(spec: Any, workers: list[dict[str, Any]]
                               ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    """Split WAITING Workers into woken (signal changed / retry requested) and dormant.

    A stored or current fingerprint that cannot be evaluated never wakes a
    Worker: an unknown signal must not turn into a busy loop of model calls.
    """
    root = Path(spec.research_root).resolve()
    waiting = [w for w in workers if w.get("status") == WAITING_RESEARCH_DIRECTION
               and not w.get("dismissed") and not w.get("process_alive")]
    if not waiting:
        return [], [], None
    try:
        current = research_direction_fingerprint(spec)
    except (OSError, ValueError):
        current = None
    woken, dormant = [], []
    for worker in waiting:
        retry = _pending_retry_direction(root, worker["worker_id"])
        stored = _direction_values(worker.get("wake_fingerprint"))
        changed = (current is not None and stored is not None
                   and stored != _direction_values(current))
        if retry or changed:
            woken.append({**worker, "wake_reason": "retry_direction" if retry
                          else "direction_fingerprint_changed"})
        else:
            dormant.append(worker)
    return woken, dormant, current


def _notify_all_waiting(root: Path, dormant: list[dict[str, Any]],
                        current: dict[str, Any] | None) -> dict[str, Any]:
    """One important event per all-waiting episode (deduplicated by registry key)."""
    import hashlib
    from .observability.incident_registry import register_cycle_incident
    ids = sorted(w["worker_id"] for w in dormant)
    episode = json.dumps({"workers": ids, "fingerprint": _direction_values(current),
                          "waiting": sorted(str(w.get("waiting_since") or "") for w in dormant)},
                         sort_keys=True, default=str)
    incident, created = register_cycle_incident(
        root, severity="important", kind="all_workers_waiting_research_direction",
        source_event="sdk.worker_runtime",
        summary=("All living Workers are waiting for a research direction: " + ", ".join(ids)
                 + ". Change the Research Plan, add operator guidance or request retry_direction."),
        code="all_workers_waiting_research_direction",
        discriminator=hashlib.sha256(episode.encode("utf-8")).hexdigest()[:20],
        fields={"worker_ids": ids, "event_class": "research_direction_notice"},
    )
    return {"incident_id": incident.get("incident_id"), "created": created, "worker_ids": ids}


def _recover_proposal_gate(root: Path) -> dict[str, Any] | None:
    """G2: reclaim the Proposal Gate only from a provably dead holder."""
    recovered = ProposalGate(root).recover_dead_holder()
    if recovered is None:
        return None
    return {key: recovered.get(key) for key in
            ("worker_id", "claim_id", "fencing_epoch", "gate_token", "owner_pid", "acquired_at")}


def schedule_once(spec: Any, *, resource_slots: int,
                  preferred_hypothesis: str | None = None,
                  eligible_worker_ids: frozenset[str] | None = None) -> dict[str, Any]:
    """One bounded allocation transaction; each Worker independently owns 1 Cycle.

    Workers claim Cycles, not historical H: capacity never depends on active
    H frontiers, and ``preferred_hypothesis`` is accepted but ignored (I-12).
    """
    if resource_slots < 1:
        raise ValueError("resource_slots must be >=1")
    root = Path(spec.research_root).resolve()
    scheduler_lock = root / ".theory-master" / "scheduler.lock"
    scheduler_lock.parent.mkdir(parents=True, exist_ok=True)
    with open(scheduler_lock, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        notes: dict[str, Any] = {}
        recovered = _recover_proposal_gate(root)
        if recovered is not None:
            notes["proposal_gate_recovered"] = recovered
        if preferred_hypothesis:
            notes["preferred_hypothesis_ignored"] = {
                "value": preferred_hypothesis,
                "deprecation": "Preferred no longer influences scheduling (V1.1 I-12)"}
        if runtime_health(root).get("active"):
            return {"started": [], "reason": "legacy_runtime_active", **notes}
        if _master_complete(root, spec=spec):
            return {"started": [], "reason": "master_complete", **notes}
        _ensure_legacy_claims_migrated(root)
        registry = CycleClaims(root, _graph_path(spec))
        capacity, accounts = _route_capacity(spec, resource_slots)
        sharing = _share_enabled(root)
        active = _living_workers(root)
        woken, dormant, direction = _partition_waiting_workers(spec, list_workers(root))
        preferences = worker_account_preferences(root)
        occupied_accounts = {w.get("account") for w in [*active, *dormant] if w.get("account")}
        account_required = any(a is not None for a in accounts)
        available_accounts = ([a for a in accounts if a is not None]
                              if sharing else [a for a in accounts if a is not None and a not in occupied_accounts])
        concurrency = max(0, capacity - len(active) - len(dormant))
        dormant_ids = frozenset(w["worker_id"] for w in dormant)
        woken_by_id = {w["worker_id"]: w for w in woken}
        started = []
        chosen_ids: set[str] = set()
        for _slot in range(concurrency):
            # A completed Worker retains its immutable account binding across
            # Cycle allocations. Choose a usable Worker/account PAIR, rather
            # than selecting the highest-quota account first and failing on
            # the historical pinned CODEX_HOME identity.
            skipped: set[str] = set()
            while True:
                try:
                    worker = available_worker_name(root,
                        exclude=frozenset(chosen_ids | skipped | dormant_ids),
                        eligible_ids=eligible_worker_ids)
                except RuntimeError:
                    worker = None
                    break
                previous_account = read_worker(root, worker).get("account")
                desired_account = preferences.get(worker)
                options = ([desired_account] if desired_account else
                           [previous_account] if previous_account else available_accounts)
                if sharing and account_required:
                    account = _shared_account_candidate(options, [*active, *dormant], started)

                elif account_required:
                    account = next((a for a in options if a in available_accounts), None)
                else:
                    account = None
                if not account_required or account is not None:
                    break
                skipped.add(worker)
            if worker is None:
                break
            if not sharing and account_required:
                available_accounts.remove(account)
            chosen_ids.add(worker)
            claim = None
            try:
                try:
                    claim = registry.claim_cycle(worker)
                except GoalSealedError:
                    return {"started": started, "reason": "master_complete", **notes}
                directory = worker_dir(root, worker)
                directory.mkdir(parents=True, exist_ok=True)
                if account and previous_account and account != previous_account:
                    _rebind_idle_worker_home(root, worker, str(previous_account), account)
                env = _task_scoped_process_env(root)
                if account:
                    home = _pinned_codex_home(root, worker, account)
                    env["THEORYMASTER_WORKER_CODEX_HOME"] = str(home)
                    env["THEORYMASTER_WORKER_PINNED_ACCOUNT"] = account
                wake = woken_by_id.get(worker)
                if wake is not None:
                    retry_path = _retry_direction_path(root, worker)
                    retry = _pending_retry_direction(root, worker)
                    if retry is not None:
                        _save_json(retry_path, {**retry, "status": "consumed",
                                                "consumed_at": utcnow().isoformat(),
                                                "consumed_by_claim_id": claim["claim_id"]})
                save_worker(root, worker, status="RESERVED", dismissed=False,
                            target_h_id=None, entry_node_id=None,
                            claim_id=claim["claim_id"], epoch=claim["fencing_epoch"],
                            account=account, current_cycle=None,
                            resource_slots_configured=resource_slots,
                            error_kind=None, critical_knowledge_notice=None, heartbeat_at=None,
                            wake_fingerprint=None, waiting_since=None,
                            last_wake_reason=(wake or {}).get("wake_reason"))
                output = open(directory / "runner.log", "a", encoding="utf-8")
                try:
                    args = [sys.executable, "-m", "theory_master.worker_runtime", "run-one",
                            "--research-root", str(root), "--worker", worker,
                            "--claim-id", claim["claim_id"], "--epoch", str(claim["fencing_epoch"])]
                    _launch_detached_service(args, root=root, env=env, output=output, kind=worker)
                finally:
                    output.close()
                # The scope runner registers its own real PID. Never overwrite
                # that with the short-lived systemd-run launcher PID.
                current = read_worker(root, worker)
                if current.get("status") == "RESERVED":
                    save_worker(root, worker, status="STARTING", pid=None, pid_start_ticks=None)
                started.append({"worker_id": worker, "claim_id": claim["claim_id"],
                                "epoch": claim["fencing_epoch"],
                                "pid": read_worker(root, worker).get("pid"), "account": account,
                                "wake_reason": (wake or {}).get("wake_reason")})
            except BaseException:
                # If no child was created, safely undo the reservation. Once
                # Popen succeeded the OS child may be doing science and must
                # retain the claim even if our registry update fails.
                if read_worker(root, worker).get("pid") is None and claim is not None:
                    try:
                        registry.update(worker, claim["claim_id"], claim["fencing_epoch"], action="release")
                        save_worker(root, worker, status="FAILED_START", claim_id=None, epoch=None)
                    except (OSError, ValueError):
                        pass
                raise
        result = {"started": started, "concurrency": concurrency, "resource_slots": capacity,
                  "waiting_workers": sorted(dormant_ids), **notes}
        if dormant and not active and not started:
            result["all_waiting_notice"] = _notify_all_waiting(root, dormant, direction)
        return result


def detect_critical_knowledge_change(spec: Any, *, plan_digest: str,
                                     hypothesis_id: str | None = None,
                                     cycle_id: str | None = None,
                                     entry_node_id: str | None = None) -> dict[str, Any] | None:
    """G10: only a Research Plan change, or this Cycle's OWN bound H deleted/closed.

    Parent H or other frontier changes are ordinary science (I-06). Closure by
    this Cycle's own committed Outcome or by the Goal seal is not a notice.
    """
    import hashlib
    hypothesis_id = hypothesis_id or entry_node_id
    plan = Path(spec.document)
    if hashlib.sha256(plan.read_bytes()).hexdigest() != plan_digest:
        return {"reason": "research_plan_changed", "hypothesis_id": hypothesis_id}
    if not hypothesis_id:
        return None
    graph_path = _graph_path(spec).resolve()
    if not graph_path.is_file():
        raise FileNotFoundError(f"Canonical SQLite graph is required: {graph_path}")
    from .science_graph_store import GOAL_COMPLETED_STOP, GOAL_CLOSE_REASON, ScienceGraphStore
    with ScienceGraphStore(graph_path, readonly=True) as store:
        if store.db.execute("SELECT 1 FROM hypotheses WHERE id=?", (hypothesis_id,)).fetchone() is None:
            return {"reason": "own_hypothesis_deleted", "hypothesis_id": hypothesis_id}
        frontier = store.frontier_view().get(hypothesis_id)
        if not frontier or frontier["frontier_status"] != "closed":
            return None
        branch = frontier["branches"][0] if frontier["branches"] else {}
        if branch.get("reason") in {GOAL_COMPLETED_STOP, GOAL_CLOSE_REASON}:
            return None
        if cycle_id and store.db.execute(
                "SELECT 1 FROM cycle_evaluations WHERE cycle_id=? AND hypothesis_id=?",
                (str(cycle_id), hypothesis_id)).fetchone() is not None:
            return None
    return {"reason": "own_hypothesis_closed", "hypothesis_id": hypothesis_id}


def _restore_cycle_operator_guidance(state: dict[str, Any], cycle_dir: Path) -> dict[str, Any]:
    """Inject pending human guidance when Worker resumes an existing Cycle."""
    from .attention_guidance import load_attention_guidance
    guidance = load_attention_guidance(cycle_dir)
    if guidance is not None and str(guidance.get("status") or "pending") != "consumed":
        state["operator_guidance"] = guidance
        state["operator_guidance_pending"] = True
        state.setdefault("recovery", {})["attention_guidance"] = {
            "source": guidance.get("source"),
            "path": guidance.get("path"),
            "attention_code": guidance.get("attention_code"),
        }
    return state


def _update_cycle_memory_index(spec: Any, root: Path, cycle_dir: Path | None, *, quiet: bool) -> None:
    """Derived H -> Cycle -> raw evidence index; never replaces the Cycle outcome."""
    if cycle_dir is None:
        return
    from .science_cycle_memory_index import index_task_cycle_memories
    db = _graph_path(spec)
    if not db.is_file():
        return
    try:
        index_task_cycle_memories(db, root)
    except (OSError, ValueError, GoalSealedError) as index_error:
        if quiet:
            return
        with (cycle_dir / "worker_runtime_warnings.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({"kind": "cycle_memory_index_update_failed",
                                  "detail": str(index_error), "at": utcnow().isoformat()}) + "\n")


def _wake_fingerprint_for(spec: Any, result: dict[str, Any]) -> dict[str, Any] | None:
    """Prefer the Workflow's fingerprint when it uses the shared G6 fields."""
    offered = result.get("wake_fingerprint")
    if _direction_values(offered) is not None:
        return {"schema": DIRECTION_FINGERPRINT_SCHEMA,
                **{key: offered[key] for key in DIRECTION_FINGERPRINT_FIELDS}}
    try:
        return research_direction_fingerprint(spec)
    except (OSError, ValueError):
        return None  # Unknown signal: only an explicit retry_direction wakes this Worker.


def run_once(spec: Any, worker_id: str, claim_id: str, epoch: int, *,
             resume_cycle: str | None = None) -> int:
    """Only the matching freshly-owned OS process may run the claimed Cycle."""
    from .backends import CodexBackend, resolve_routes
    from .workflow import Workflow

    root = Path(spec.research_root).resolve()
    graph_path = _graph_path(spec)
    claims = CycleClaims(root, graph_path)
    claim = claims.attach_owner(worker_id, claim_id, epoch, owner_pid=os.getpid(),
                                owner_start_ticks=process_start_ticks(os.getpid()))
    stop = threading.Event()
    workflow = None
    import hashlib
    original_plan_digest = hashlib.sha256(Path(spec.document).read_bytes()).hexdigest()

    def heartbeat() -> None:
        pulses = 0
        last_live_workers: tuple[str, ...] | None = None
        last_notice: str | None = None
        while not stop.wait(3):
            try:
                current = claims.update(worker_id, claim_id, epoch, action="heartbeat")
                updates = {"heartbeat_at": utcnow().isoformat()}
                if current.get("hypothesis_id"):
                    updates["target_h_id"] = current["hypothesis_id"]
                if workflow and workflow.cycle_dir is not None:
                    updates["current_cycle"] = workflow.cycle_dir.name
                    peers = tuple(sorted(w["worker_id"] for w in list_workers(root)
                                         if w.get("process_alive") and
                                         w.get("effective_status") in RUNNING))
                    # The start snapshot is immutable; this append-only event
                    # stream records later observed changes in live concurrency.
                    if peers != last_live_workers:
                        event = {"observed_at": utcnow().isoformat(),
                                 "worker_id": worker_id,
                                 "live_worker_ids": list(peers),
                                 "concurrent_count": len(peers),
                                 "observation": "periodic_sample_not_exact_continuous_overlap"}
                        with (workflow.cycle_dir / "worker_concurrency_events.jsonl").open(
                                "a", encoding="utf-8") as timeline:
                            timeline.write(json.dumps(event, ensure_ascii=False) + "\n")
                        last_live_workers = peers
                pulses += 1
                if pulses % 4 == 0:
                    try:
                        notice = detect_critical_knowledge_change(
                            spec, plan_digest=original_plan_digest,
                            hypothesis_id=current.get("hypothesis_id"),
                            cycle_id=current.get("cycle_id"))
                    except (ValueError, OSError) as check_error:
                        notice = None
                        updates["critical_knowledge_check_error"] = str(check_error)[:300]
                    if notice and notice["reason"] != last_notice:
                        updates["critical_knowledge_notice"] = {**notice, "detected_at": utcnow().isoformat()}
                        last_notice = notice["reason"]
                save_worker(root, worker_id, **updates)
            except (ValueError, OSError):
                # A lost claim must fail CLOSED before any subsequent Canvas write.
                stop.set()
                return

    def stop_for_goal(detail: str | None) -> int:
        try:
            claims.update(worker_id, claim_id, epoch, action="stopped_goal")
        except ValueError:
            pass  # Already archived/fenced; the sealed Goal stays authoritative.
        save_worker(root, worker_id, status=STOPPED_GOAL_COMPLETED, error_kind=None,
                    claim_id=None, epoch=None, last_claim_id=claim_id, last_claim_epoch=epoch,
                    goal_stop={"at": utcnow().isoformat(), "detail": (detail or "")[:500]},
                    current_cycle=(workflow.cycle_dir.name if workflow and workflow.cycle_dir else None))
        return 0

    thread = threading.Thread(target=heartbeat, name=f"tm-{worker_id}-heartbeat", daemon=True)
    thread.start()
    try:
        routes = resolve_worker_routes(root, worker_id)
        backend = CodexBackend(root, routes=routes)
        from .observability.collector import Collector
        collector = Collector(spec, system_failure_actions=False, email_notifications=True,
                              cycle_audit_actions=False, attention_takeover_actions=False)
        workflow = Workflow(spec, backend, routes, collector=collector,
                            route_resolver=lambda: resolve_worker_routes(root, worker_id))
        workflow.worker_id = worker_id
        workflow.worker_claim_id = claim_id
        workflow.worker_claim_epoch = epoch
        workflow.worker_claims = claims
        workflow.worker_proposal_gate = ProposalGate(root, graph_path)
        backend.preflight_runtime()
        save_worker(root, worker_id, status="RUNNING", error_kind=None, pid=os.getpid(),
                    pid_start_ticks=process_start_ticks(os.getpid()))
        if resume_cycle:
            target = root / resume_cycle
            if not target.is_dir() or target.parent != root or not resume_cycle.isdigit():
                raise ValueError("invalid Worker resume Cycle")
            state = _read_json(target / "research_state.json")
            if not _checkpoint_matches_claim(state, worker_id, claim, resume_cycle):
                raise ValueError("resume checkpoint belongs to a different Worker or Cycle claim")
            if str(state.get("status") or "").lower() not in {"paused", "failed", "needs_attention", "interrupted"}:
                raise ValueError("Worker Cycle is not resumable")
            # The Worker must bind the old Cycle before reading frozen role
            # routes. Otherwise Workflow._restore_resumed_cycle_routes exits
            # early because its cycle_dir is still None.
            workflow._bind_cycle(target, create=False)
            workflow._restore_resumed_cycle_routes(state)
            # Worker resume bypasses Workflow.resume_cycle(); restore human
            # guidance explicitly or the approval is silently omitted.
            state = _restore_cycle_operator_guidance(state, target)
            result = workflow._run_cycle(resume_state=state)
        else:
            result = workflow._run_cycle()
        status = str(result.get("status") or "FAILED").upper()
        # Each committed Cycle automatically updates the H -> Cycle -> raw
        # evidence index (no manual CLI pass and no science state mutation).
        _update_cycle_memory_index(spec, root, workflow.cycle_dir, quiet=False)
        cycle_name = workflow.cycle_dir.name if workflow.cycle_dir else None
        if status == STOPPED_GOAL_COMPLETED:
            return stop_for_goal(str(result.get("reason") or result.get("detail") or ""))
        if status == "NO_QUALIFIED_CANDIDATE":
            fingerprint = _wake_fingerprint_for(spec, result)
            claims.update(worker_id, claim_id, epoch, action="wait_direction")
            save_worker(root, worker_id, status=WAITING_RESEARCH_DIRECTION, error_kind=None,
                        claim_id=None, epoch=None, last_claim_id=claim_id, last_claim_epoch=epoch,
                        wake_fingerprint=fingerprint, waiting_since=utcnow().isoformat(),
                        workflow_wake_fingerprint=result.get("wake_fingerprint"),
                        current_cycle=cycle_name)
            return 0
        save_worker(root, worker_id, status=status,
                    error_kind=None if status in {"OVER", "COMPLETED"} else read_worker(root, worker_id).get("error_kind"),
                    current_cycle=cycle_name)
        if status in {"PAUSED", "NEEDS_ATTENTION", "INTERRUPTED"}:
            claims.update(worker_id, claim_id, epoch, action="pause")
        if (status in {"PAUSED", "NEEDS_ATTENTION"}
                and workflow is not None and workflow.cycle_dir is not None):
            try:
                checkpoint = _read_json(workflow.cycle_dir / "research_state.json")
                if ((status == "PAUSED" and checkpoint.get("phase") == "provider_quota_exhausted")
                        or (status == "NEEDS_ATTENTION"
                            and _technical_review_quota_watch_eligible(checkpoint, worker_id))):
                    start_worker_quota_watch(spec, worker_id)
            except (OSError, ValueError, RuntimeError):
                pass  # Watcher setup cannot replace the durable science pause.
        if status in {"OVER", "CANCELLED"}:
            # Full terminal Cycle (including human withdrawal) is durable
            # before releasing this Worker-specific Cycle claim.
            finish_owned_claim(claims, worker_id=worker_id, claim_id=claim_id,
                               epoch=epoch, current_pid=os.getpid())
            return 0
        return 2
    except GoalSealedError as exc:
        # A late write rejected by the sealed Goal is completion, never failure.
        _update_cycle_memory_index(spec, root, workflow.cycle_dir if workflow else None, quiet=True)
        return stop_for_goal(str(exc))
    except BaseException as exc:
        # Do not misclassify scientific blocked or generic errors as quota.
        # Only a failed provider delivery plus fresh exhausted-account proof
        # can become a resource wait with an existing quota watcher.
        from .supervisor_incident import NativeGoalRuntimeFailure
        reason = str(getattr(exc, "error", "")).lower()
        delivery_fault = ("stream disconnected" in reason
                          or "response.completed" in reason
                          or "completed without assistant final response" in reason)
        from .backend_config import ACCOUNT_PROVIDERS
        routes_now = getattr(workflow, "routes", {}) if workflow is not None else {}
        codex_only = (bool(routes_now) and all(
            getattr(route, "provider", None) in ACCOUNT_PROVIDERS
            for route in routes_now.values()))
        if isinstance(exc, NativeGoalRuntimeFailure) and delivery_fault and codex_only:
            pinned_account = str(read_worker(root, worker_id).get("account") or "")
            resets = (_verified_codex_quota_wait()
                      or _verified_pinned_codex_quota_wait(pinned_account))
            cycle = workflow.cycle_dir.name if workflow and workflow.cycle_dir else resume_cycle
            if (resets and cycle and _persist_verified_quota_wait(
                    root, worker_id, cycle, claim, resets, "provider-delivery-failed-during-exhausted-quota")):
                claims.update(worker_id, claim_id, epoch, action="pause")
                save_worker(root, worker_id, status="PAUSED", error_kind=None, current_cycle=cycle)
                try:
                    start_worker_quota_watch(spec, worker_id)
                except (OSError, RuntimeError, ValueError):
                    # Science remains PAUSED; watcher launch needs operator repair.
                    pass
                return 0
        save_worker(root, worker_id, status="FAILED", error_kind=type(exc).__name__,
                    current_cycle=(workflow.cycle_dir.name if workflow and workflow.cycle_dir else resume_cycle))
        # A FAILED checkpoint is still research memory and must be traceable
        # to its H. Failure to index cannot replace the original exception.
        _update_cycle_memory_index(spec, root, workflow.cycle_dir if workflow else None, quiet=True)
        try:
            claims.update(worker_id, claim_id, epoch, action="pause")
        except ValueError:
            pass  # expired/externally fenced Worker must not revive old claim
        raise
    finally:
        stop.set()
        thread.join(timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run-one", "tick", "status", "daemon", "start", "stop", "pause", "resume", "release", "retry_direction", "preserve-job", "quota-watch", "quota-watch-start"])
    parser.add_argument("--research-root", required=True, type=Path)
    parser.add_argument("--worker")
    parser.add_argument("--account", choices=["1", "2"],
                        help="optional new login for safe paused-Cycle account migration")
    parser.add_argument("--claim-id")
    parser.add_argument("--epoch", type=int)
    parser.add_argument("--resource-slots", type=int, default=0)
    parser.add_argument("--preferred-hypothesis",
                        help="deprecated and ignored: Preferred no longer influences scheduling")
    parser.add_argument("--resume-cycle")
    args = parser.parse_args(argv)
    from .task import TaskBinding
    from .specification import TaskSpec
    binding = TaskBinding.load(args.research_root)
    spec = TaskSpec.from_task_root(binding.task_root)
    if args.action == "preserve-job":
        if not args.worker:
            parser.error("preserve-job requires an authorized Worker")
        return execute_preserve_release_job(spec, args.worker)
    if args.action in {"quota-watch", "quota-watch-start"}:
        if not args.worker:
            parser.error("quota watcher requires --worker")
        if args.action == "quota-watch":
            return watch_worker_quota(spec, args.worker)
        print(json.dumps(start_worker_quota_watch(spec, args.worker), ensure_ascii=False))
        return 0
    if args.action == "run-one":
        if not args.worker or not args.claim_id or args.epoch is None:
            parser.error("run-one requires worker, claim-id and epoch")
        return run_once(spec, args.worker, args.claim_id, args.epoch,
                        resume_cycle=args.resume_cycle)
    if args.action == "daemon":
        return scheduler_daemon(spec, args.resource_slots, args.preferred_hypothesis)
    if args.action == "start":
        response = start_scheduler(spec, args.resource_slots, preferred_hypothesis=args.preferred_hypothesis)
    elif args.action == "stop":
        response = stop_scheduler(spec)
    elif args.action in {"pause", "resume", "release", "retry_direction"}:
        if not args.worker:
            parser.error(f"{args.action} requires --worker worker-N")
        response = worker_action(spec, args.worker, args.action, account=args.account)
    elif args.action == "tick":
        response = schedule_once(spec, resource_slots=args.resource_slots,
                                 preferred_hypothesis=args.preferred_hypothesis)
    else:
        response = {"workers": list_workers(spec.research_root),
                    "cycle_claims": CycleClaims(spec.research_root, _graph_path(spec)).list_claims(),
                    "proposal_gate": ProposalGate(spec.research_root).status(),
                    "scheduler": scheduler_status(spec.research_root)}
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0





def start_worker_quota_watch(spec: Any, worker_id: str) -> dict[str, Any]:
    """Detached task-scoped quota watcher; never starts a new scientific Cycle."""
    from .quota_auto_adjust import quota_auto_adjust_enabled
    if not quota_auto_adjust_enabled(spec.research_root):
        return {"status": "disabled"}
    root = Path(spec.research_root).resolve()
    directory = worker_dir(root, worker_id)
    directory.mkdir(parents=True, exist_ok=True)
    from .multi_worker import _read_json
    watcher = directory / "quota_watcher.json"
    prior = _read_json(watcher)
    if process_identity_alive(prior.get("pid"), prior.get("pid_start_ticks")):
        return {"status": "already_watching", "pid": prior["pid"]}
    with (directory / "quota_watcher.log").open("a", encoding="utf-8") as output:
        _launch_detached_service([sys.executable, "-m", "theory_master.worker_runtime", "quota-watch",
                        "--research-root", str(root), "--worker", worker_id],
                       root=root, env=_task_scoped_process_env(root), output=output,
                       kind=f"quota-{worker_id}")
    # The watcher owns PID registration from inside its own process.
    return {"status": "starting", "worker_id": worker_id}


def _repair_missing_quota_watchers(spec: Any) -> list[dict[str, Any]]:
    """Reconcile dead quota watchers without touching a scientific Cycle.

    Detached watchers may disappear after machine restarts or service crashes.
    This scheduler-side check is rate limited so launch failures do not form a
    process storm. Existing Worker claims, model threads and budgets are inert.
    """
    from .quota_auto_adjust import quota_auto_adjust_enabled
    if not quota_auto_adjust_enabled(spec.research_root):
        return []
    root = Path(spec.research_root).resolve()
    claims = _live_cycle_claims(root)
    repairs: list[dict[str, Any]] = []
    for worker in list_workers(root):
        if (worker.get("status") not in {"PAUSED", "NEEDS_ATTENTION"}
                or worker.get("process_alive")
                or process_identity_alive(worker.get("pid"), worker.get("pid_start_ticks"))):
            continue
        wid = str(worker.get("worker_id") or "")
        cycle = str(worker.get("current_cycle") or "")
        if not (wid.startswith("worker-") and cycle.isascii() and cycle.isdecimal()
                and int(cycle) > 0):
            continue
        claim = next((
            item for item in claims
            if item.get("worker_id") == wid
            and item.get("claim_id") == worker.get("claim_id")
            and item.get("fencing_epoch") == worker.get("epoch")
        ), None)
        state_file = root / cycle / "research_state.json"
        if state_file.is_symlink() or not state_file.is_file():
            continue
        state = _read_json(state_file)
        recovery = state.get("recovery")
        paused_quota = (
            worker.get("status") == "PAUSED"
            and state.get("status") == "paused"
            and state.get("phase") == "provider_quota_exhausted"
            and isinstance(recovery, dict)
            and recovery.get("code") == "provider_quota_exhausted"
        )
        review_watch = (
            worker.get("status") == "NEEDS_ATTENTION"
            and _technical_review_quota_watch_eligible(state, wid)
        )
        if (state.get("resumable") is not True
                or state.get("execution_active") is not False
                or not _checkpoint_matches_claim(state, wid, claim, cycle)
                or not (paused_quota or review_watch)):
            continue
        directory = worker_dir(root, wid)
        if review_watch:
            budget = state.get("investigator_budget") or {}
            try:
                review_index = int(budget.get("review_index") or 0)
            except (ValueError, TypeError, OverflowError):
                continue
            if (_budget_review_technical_failures(root / cycle, review_index)
                    >= MAX_REPEATED_TECHNICAL_REVIEW_FAILURES):
                continue  # specialized bounded transport recovery owns it
            already = _read_json(directory / "budget_review_quota_resume.json")
            key = f"{cycle}:{review_index}:{state['last_safe_checkpoint']}"
            if already.get("key") == key:
                continue  # the existing watcher already consumed this attempt
        prior = _read_json(directory / "quota_watcher.json")
        if process_identity_alive(prior.get("pid"), prior.get("pid_start_ticks")):
            continue
        repair_path = directory / "quota_watch_repair.json"
        previous = _read_json(repair_path)
        watch_kind = "budget_review" if review_watch else "provider_quota"
        same = (previous.get("cycle") == cycle
                and previous.get("watch_kind", "provider_quota") == watch_kind)
        try:
            attempts = max(0, int(previous.get("attempts") or 0)) if same else 0
            retry_after = float(previous.get("retry_after_epoch") or 0) if same else 0
        except (TypeError, ValueError, OverflowError):
            # A broken watchdog receipt must not crash the whole scheduler.
            # Reset it to one bounded launch attempt, then overwrite safely.
            attempts, retry_after = 0, 0
        if same and time.time() < retry_after:
            continue
        # Retry in 1, 2, 4, ... minutes (bounded at 15), not once per
        # scheduler tick. Write before launch to survive a crash in-between.
        delay = min(900, 60 * (2 ** min(attempts, 4)))
        attempt = {"schema": "theory-master/quota-watch-repair/v1",
                   "worker_id": wid, "cycle": cycle,
                   "watch_kind": watch_kind,
                   "attempts": attempts + 1,
                   "attempted_at": utcnow().isoformat(),
                   "retry_after_epoch": time.time() + delay}
        _save_json(repair_path, attempt)
        try:
            result = start_worker_quota_watch(spec, wid)
            repairs.append({"worker_id": wid, "cycle": cycle,
                            "status": result.get("status"), "attempts": attempts + 1})
        except (OSError, RuntimeError, ValueError) as exc:
            _save_json(repair_path, {**attempt, "launch_error": type(exc).__name__})
            repairs.append({"worker_id": wid, "cycle": cycle,
                            "status": "launch_failed",
                            "error_type": type(exc).__name__})
    return repairs


def _flag_orphaned_preservation_jobs(spec: Any) -> list[dict[str, str]]:
    """Report, but never replay/release, a disappeared paid preservation job."""
    from .observability.incident_registry import register_cycle_incident
    root = Path(spec.research_root).resolve()
    incidents: list[dict[str, str]] = []
    for worker in list_workers(root):
        if worker.get("status") != "PRESERVING":
            continue
        if (worker.get("process_alive")
                or process_identity_alive(worker.get("preservation_job_pid"),
                                          worker.get("preservation_job_start_ticks"))):
            continue
        wid = str(worker.get("worker_id") or "")
        cycle = str(worker.get("current_cycle") or "")
        if (not re.fullmatch(r"worker-[1-9][0-9]*", wid)
                or not cycle.isascii() or not cycle.isdecimal()
                or int(cycle) < 1):
            continue
        # A launcher may have registered only Worker status so far. Do not
        # declare it orphaned while the process is still being established.
        try:
            updated = datetime.fromisoformat(
                str(worker.get("updated_at") or "").replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (ValueError, AttributeError, OverflowError):
            continue
        if (datetime.now(timezone.utc) - updated).total_seconds() < 300:
            continue
        cycle_dir = root / cycle
        if not cycle_dir.is_dir() or cycle_dir.is_symlink():
            continue
        released_receipt = worker_dir(root, wid) / f"preservation_orphaned_release_{cycle}.json"
        if (released_receipt.is_file() and not released_receipt.is_symlink()
                and _read_json(released_receipt).get("status") == "verified_released"):
            continue  # durable success, regardless of a stale UI projection
        # A preservation model can finish and seal its Cycle before its
        # wrapper dies without releasing the original Worker claim. Reconcile
        # an ALREADY verified closure without re-entering Investigator.
        # Unknown/missing closure never authorizes a paid retry.
        science_path = cycle_dir / "research_state.json"
        closure_path = cycle_dir / "preservation" / "closure.json"
        if (worker.get("preservation_job_pid") and science_path.is_file()
                and not science_path.is_symlink() and closure_path.is_file()
                and not closure_path.is_symlink()):
            science = _read_json(science_path)
            closure = _read_json(closure_path)
            claim = _worker_live_claim(CycleClaims(root, _graph_path(spec)), worker)
            if (claim is not None and _checkpoint_matches_claim(science, wid, claim, cycle)
                    and science.get("status") == "over"
                    and science.get("closure_reason") == "investigator_preservation"
                    and science.get("scientific_verdict") is None
                    and closure.get("cycle_id") == cycle
                    and closure.get("scientific_verdict") is None):
                from .research_preservation import finalize_preserved_cycle, PreservationGuard
                from .budget_auto_preservation import budget_preservation_notice
                try:
                    verified = finalize_preserved_cycle(
                        cycle_dir=cycle_dir, authorization="operator_approved", dry_run=True)
                    if verified.get("scientific_verdict") is not None:
                        raise PreservationGuard("preserved scientific closure has a verdict")
                    once = worker_dir(root, wid) / f"preservation_orphaned_release_{cycle}.json"
                    once.parent.mkdir(parents=True, exist_ok=True)
                    if not once.exists() and not once.is_symlink():
                        descriptor = os.open(once, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                            json.dump({"cycle": cycle, "worker_id": wid,
                                       "status": "requested_once", "at": utcnow().isoformat()}, handle)
                            handle.flush()
                            os.fsync(handle.fileno())
                        # Existing WorkerAction validates the locked claim and
                        # detects the closed authoritative Cycle; no model call.
                        result = worker_action(spec, wid, "preserve_release")
                        if result.get("status") == "RELEASED" and result.get("model_calls") == 0:
                            _save_json(once, {"cycle": cycle, "worker_id": wid,
                                              "status": "verified_released",
                                              "model_calls": 0, "at": utcnow().isoformat()})
                            try:
                                budget_preservation_notice(
                                    root, wid, cycle, success=True,
                                    hypothesis_id=str(closure.get("hypothesis_id") or ""))
                            except (OSError, ValueError, RuntimeError) as exc:
                                _save_json(worker_dir(root, wid) / f"preservation_orphan_notice_warning_{cycle}.json",
                                           {"error_type": type(exc).__name__,
                                            "at": utcnow().isoformat()})
                            incidents.append({"worker_id": wid, "cycle": cycle,
                                              "status": "verified_released"})
                            continue
                except (OSError, ValueError, RuntimeError, PreservationGuard) as exc:
                    _save_json(worker_dir(root, wid) / f"preservation_orphan_release_error_{cycle}.json",
                               {"cycle": cycle, "worker_id": wid,
                                "error_type": type(exc).__name__,
                                "at": utcnow().isoformat()})
                    # Do not bypass the ordinary orphan incident below.
        receipt = worker_dir(root, wid) / f"preservation_orphaned_{cycle}.json"
        if receipt.exists():
            continue
        record = {"schema": "theory-master/preservation-orphan/v1",
                  "cycle": cycle, "worker_id": wid,
                  "status": "manual_reconciliation_required",
                  "at": utcnow().isoformat()}
        # A dead job is NOT approval to repeat a potentially paid model call,
        # restore a checkpoint or free this reserved Cycle Claim.
        _save_json(receipt, record)
        register_cycle_incident(
            cycle_dir, severity="error", kind="system_failure",
            source_event="sdk.worker_scheduler",
            code="preservation_worker_orphaned",
            discriminator=f"{wid}:{cycle}",
            summary=(f"{wid} · Cycle {cycle} 保全进程未运行但状态仍为 PRESERVING；"
                     "科研前沿与 Claim 已保留，请核查缓存输出与闭合回执。"),
            fields={"worker_id": wid, "cycle": cycle,
                    "claim_retained": True, "requires_reconciliation": True},
        )
        incidents.append({"worker_id": wid, "cycle": cycle,
                          "status": "manual_reconciliation_required"})
    return incidents


def _technical_review_quota_watch_eligible(state: dict[str, Any], worker_id: str) -> bool:
    """Only genuine, resumable Reviewer delivery faults may have a quota watcher."""
    attention = state.get("attention") if isinstance(state.get("attention"), dict) else {}
    budget = state.get("investigator_budget") if isinstance(state.get("investigator_budget"), dict) else {}
    return bool(
        str(state.get("status") or "").lower() == "needs_attention"
        and state.get("phase") == "investigator:budget_review"
        and attention.get("code") == "investigator_budget_review_failed"
        and budget.get("rejected") is not True
        and state.get("worker_id") in (None, worker_id)
        and state.get("resumable") is True
        and state.get("last_safe_checkpoint")
    )


MAX_REPEATED_TECHNICAL_REVIEW_FAILURES = 2


def _budget_review_technical_failures(cycle_dir: Path, review_index: int) -> int:
    """Count persisted failed review sessions at one unresolved budget boundary.

    A different checkpoint must not reset this safety limit. Actual approved
    budget decisions reset the count; temporary model retries within one
    session are not counted separately.
    """
    events = Path(cycle_dir) / "events.jsonl"
    if not events.is_file() or events.is_symlink():
        return 0
    failures = 0
    with events.open(encoding="utf-8") as lines:
        for line in lines:
            try:
                event = json.loads(line)
                payload = event.get("payload") or {}
                if int(payload.get("review_index") or -1) != int(review_index):
                    continue
                if event.get("type") == "investigation.budget_review_decided":
                    failures = 0
                elif event.get("type") == "investigation.budget_review_failed":
                    failures += 1
            except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
                continue
    return failures


def _budget_reviewer_last_technical_error(cycle_dir: Path,
                                          review_index: int) -> tuple[str, str]:
    """Read the latest exact-index technical delivery failure, not a rejection."""
    events = cycle_dir / "events.jsonl"
    if not events.is_file() or events.is_symlink() or events.stat().st_size > 20_000_000:
        return "", ""
    error_type, detail = "", ""
    with events.open(encoding="utf-8") as source:
        for line in source:
            try:
                row = json.loads(line)
                payload = row.get("payload") or {}
                if (row.get("type") == "investigation.budget_review_failed"
                        and int(payload.get("review_index") or 0) == review_index):
                    error_type, detail = (
                        str(payload.get("error_type") or ""),
                        str(payload.get("detail") or "")[:900])
            except (ValueError, TypeError, AttributeError):
                continue
    return error_type, detail


def _dispatch_exhausted_budget_reviewer_transport(spec: Any) -> bool:
    """One extra Reviewer *delivery* attempt after new verified provider health.

    Technical failures do not imply rejection, approval or more Investigator
    budget. A malformed JSON/typed contract is a protocol fault, not a reason
    for endless paid reviews. After two failed sessions, only a recognized
    transient transport error may get ONE same-Worker guarded resume.
    """
    from .investigator_budget import budget_status
    from .observability.incident_registry import register_cycle_incident
    from .codex_accounts import account_rate_limits, quota_available
    root = Path(spec.research_root).resolve()
    for worker in list_workers(root):
        if (worker.get("status") != "NEEDS_ATTENTION" or worker.get("process_alive")
                or process_identity_alive(worker.get("pid"), worker.get("pid_start_ticks"))):
            continue
        wid, cycle = str(worker.get("worker_id") or ""), str(worker.get("current_cycle") or "")
        if not re.fullmatch(r"worker-[1-9][0-9]*", wid):
            continue
        if not cycle.isascii() or not cycle.isdecimal() or int(cycle) < 1:
            continue
        directory = root / cycle
        path = directory / "research_state.json"
        if not path.is_file() or path.is_symlink():
            continue
        state = _read_json(path)
        if not _technical_review_quota_watch_eligible(state, wid):
            continue
        budget = state.get("investigator_budget") or {}
        try:
            index = int(budget.get("review_index") or 0)
        except (ValueError, TypeError, OverflowError):
            continue
        failures = _budget_review_technical_failures(directory, index)
        if index < 1 or failures < MAX_REPEATED_TECHNICAL_REVIEW_FAILURES:
            continue  # existing quota watcher owns the first bounded attempt
        claim = _worker_live_claim(CycleClaims(root, _graph_path(spec)), worker)
        if claim is None or not _checkpoint_matches_claim(state, wid, claim, cycle):
            continue
        try:
            status = budget_status(directory, root, state)
            if status.get("rejected") is True or status.get("review_index") != index:
                continue
        except (ValueError, TypeError):
            register_cycle_incident(
                directory, severity="important", kind="system_failure",
                source_event="sdk.budget_reviewer_recovery",
                code="budget_reviewer_ledger_integrity_required",
                discriminator=f"{wid}:{cycle}:{index}",
                summary=(f"{wid} · Cycle {cycle} Budget Reviewer 账本验证失败；"
                         "停止自动重试，保留原科研预算和检查点。"),
                fields={"worker_id": wid, "cycle": cycle,
                        "review_index": index, "budget_granted": False},
            )
            continue
        except OSError:
            continue  # transient filesystem errors never authorize a grant
        error_type, detail = _budget_reviewer_last_technical_error(directory, index)
        transport = (error_type in {"RuntimeError", "ConnectionError", "TimeoutError"}
                     and any(token in detail.lower() for token in (
                         "completed without assistant final response",
                         "stream disconnected", "connection reset",
                         "temporarily unavailable", "server overloaded",
                     )))
        if not transport:
            register_cycle_incident(
                directory, severity="important", kind="system_failure",
                source_event="sdk.budget_reviewer_recovery",
                code="budget_reviewer_protocol_repair_required",
                discriminator=f"{wid}:{cycle}:{index}",
                summary=(f"{wid} · Cycle {cycle} 的 Budget Reviewer 连续技术失败，"
                         "未产生有效预算决定。需修复格式/协议/模型配置后显式重试；禁止自动批预算。"),
                fields={"worker_id": wid, "cycle": cycle, "review_index": index,
                        "failed_sessions": failures, "budget_granted": False},
            )
            continue
        receipt = worker_dir(root, wid) / f"budget_reviewer_transport_recovery_{cycle}_{index}.json"
        if receipt.exists() or receipt.is_symlink():
            prior = _read_json(receipt) if not receipt.is_symlink() else {}
            if (prior.get("cycle") == cycle
                    and prior.get("status") == "resume_dispatched"
                    and failures > int(prior.get("failed_sessions") or 0)):
                register_cycle_incident(
                    directory, severity="important", kind="system_failure",
                    source_event="sdk.budget_reviewer_recovery",
                    code="budget_reviewer_post_transport_recovery_failed",
                    discriminator=f"{wid}:{cycle}:{index}",
                    summary=(f"{wid} · Cycle {cycle} Budget Reviewer 在一次性恢复后"
                             "仍发生技术故障，已停止自动重试；未改变预算。"),
                    fields={"worker_id": wid, "cycle": cycle,
                            "review_index": index, "budget_granted": False},
                )
            continue  # one additional attempt at most
        account = str(worker.get("account") or "")
        if account not in {"1", "2"}:
            continue
        probe_path = worker_dir(root, wid) / "budget_reviewer_transport_probe.json"
        previous = _read_json(probe_path)
        try:
            next_probe = float(previous.get("next_probe_epoch") or 0)
        except (ValueError, TypeError, OverflowError):
            next_probe = 0
        if (previous.get("cycle") == cycle and previous.get("review_index") == index
                and time.time() < next_probe):
            continue
        _save_json(probe_path, {"cycle": cycle, "review_index": index,
                                "next_probe_epoch": time.time() + 60,
                                "at": utcnow().isoformat()})
        try:
            available = quota_available(account_rate_limits(account)) is True
        except (OSError, RuntimeError, ValueError, TimeoutError):
            continue
        if not available:
            continue  # wait for verified quota, no false Supervisor error
        payload = {
            "schema": "theory-master/budget-reviewer-post-transport-recovery/v1",
            "worker_id": wid, "cycle": cycle, "review_index": index,
            "failed_sessions": failures, "account": account,
            "status": "requested_once", "at": utcnow().isoformat(),
        }
        try:
            fd = os.open(receipt, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            launched = worker_action(spec, wid, "resume")
            _save_json(receipt, {**payload, "status": "resume_dispatched",
                                 "result_status": launched.get("status")})
        except (OSError, ValueError, RuntimeError) as exc:
            _save_json(receipt, {**payload, "status": "resume_blocked",
                                 "error_type": type(exc).__name__})
            register_cycle_incident(
                directory, severity="important", kind="system_failure",
                source_event="sdk.budget_reviewer_recovery",
                code="budget_reviewer_transport_retry_blocked",
                discriminator=f"{wid}:{cycle}:{index}",
                summary=f"{wid} · Cycle {cycle} Budget Reviewer 安全恢复未能启动；预算和 Claim 均保留。",
                fields={"worker_id": wid, "cycle": cycle, "budget_granted": False},
            )
        return True
    return False




def watch_worker_quota(spec: Any, worker_id: str) -> int:
    """Resume one paused quota Cycle on available Codex quota only.

    This is a Worker-specific watchdog: no global selector switching, no
    sibling termination, no new Cycle, and no cross-provider thread continuation.
    """
    from .quota_auto_adjust import quota_auto_adjust_enabled
    from .codex_accounts import account_rate_limits, quota_available
    from .quota_provider_fallback import investigator_native_goal_in_flight
    import time
    root = Path(spec.research_root).resolve()
    gate = worker_dir(root, worker_id) / "quota_watcher_owner.lock"
    with gate.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0  # Another watcher already owns this Worker.
        _save_json(worker_dir(root, worker_id) / "quota_watcher.json", {
            "pid": os.getpid(), "pid_start_ticks": process_start_ticks(os.getpid()),
            "started_at": utcnow().isoformat(), "worker_id": worker_id})
        while quota_auto_adjust_enabled(root):
            w = read_worker(root, worker_id)
            status = str(w.get("status") or "")
            if status in {"OVER", "COMPLETE", "RELEASED", "CANCELLED", "STOPPED", "FAILED_START"}:
                return 0
            if status == "RUNNING" or process_identity_alive(w.get("pid"), w.get("pid_start_ticks")):
                time.sleep(15)
                continue
            if status == "NEEDS_ATTENTION":
                # A Reviewer protocol failure is not a budget rejection.
                # Resume *only the same account and Cycle* once when that
                # account's quota is positively available. No thread migration.
                cycle = str(w.get("current_cycle") or "")
                if not cycle.isascii() or not cycle.isdecimal() or int(cycle) < 1:
                    return 1
                state = _read_json(root / cycle / "research_state.json")
                budget = state.get("investigator_budget") if isinstance(state.get("investigator_budget"), dict) else {}
                if not _technical_review_quota_watch_eligible(state, worker_id):
                    return 0
                repeated = _budget_review_technical_failures(
                    root / cycle, int(budget.get("review_index") or 0))
                if repeated >= MAX_REPEATED_TECHNICAL_REVIEW_FAILURES:
                    _save_json(worker_dir(root, worker_id) / "budget_review_recovery_exhausted.json", {
                        "schema": "theory-master/budget-review-recovery-exhausted/v1",
                        "cycle": cycle, "worker_id": worker_id,
                        "review_index": int(budget.get("review_index") or 0),
                        "failed_sessions": repeated,
                        "reason": "repeated_technical_budget_reviewer_failure",
                        "action_required": "manual_review_or_repair_before_explicit_resume",
                        "at": utcnow().isoformat(),
                    })
                    return 0  # Preserve checkpoint and budget; do not replay forever.
                account = str(w.get("account") or "")
                if account not in {"1", "2"}:
                    return 0
                key = f"{cycle}:{budget.get('review_index')}:{state['last_safe_checkpoint']}"
                receipt_path = worker_dir(root, worker_id) / "budget_review_quota_resume.json"
                previous = _read_json(receipt_path)
                if previous.get("key") == key:
                    return 0  # Durable once-only recovery, even after restart.
                try:
                    quota = account_rate_limits(account)
                except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
                    # A failed/unknown quota probe cannot authorize a model
                    # call, but must not kill the only recovery watcher.
                    _save_json(worker_dir(root, worker_id) / "quota_probe_error.json", {
                        "schema": "theory-master/quota-probe-error/v1",
                        "at": utcnow().isoformat(), "account": account,
                        "cycle": cycle, "error_type": type(exc).__name__,
                        "reason": str(exc)[:240],
                    })
                    time.sleep(60)
                    continue
                if quota_available(quota) is not True:
                    time.sleep(60)
                    continue
                attempt = {"schema": "theory-master/budget-review-quota-resume/v1",
                           "key": key, "cycle": cycle, "worker_id": worker_id,
                           "account": account, "at": utcnow().isoformat(),
                           "status": "attempted"}
                _save_json(receipt_path, attempt)  # Commit before resume.
                try:
                    outcome = worker_action(spec, worker_id, "resume")
                except (ValueError, RuntimeError, OSError) as exc:
                    _save_json(receipt_path, {**attempt, "status": "blocked",
                                              "error": f"{type(exc).__name__}: {str(exc)[:350]}"})
                    return 1
                _save_json(receipt_path, {**attempt, "status": "dispatched", "result": outcome})
                return 0
            if status != "PAUSED":
                return 0  # Other human attention and unrelated failures.
            cycle = str(w.get("current_cycle") or "")
            if not cycle.isascii() or not cycle.isdigit():
                return 1
            state = _read_json(root / cycle / "research_state.json")
            # worker_action("resume") re-verifies the exact Cycle Claim fencing.
            if (str(state.get("status") or "").lower() != "paused"
                    or state.get("phase") != "provider_quota_exhausted"
                    or state.get("worker_id") not in (None, worker_id)):
                return 0

            account = str(w.get("account") or "")
            snapshots = {number: account_rate_limits(number) for number in ("1", "2")}
            available = [number for number in ("1", "2")
                         if quota_available(snapshots.get(number) or {}) is True]
            if available:
                candidate = account if account in available else available[0]
                # Never transfer an in-flight Native Goal to another provider,
                # even when Codex quota has recovered.
                if (investigator_native_goal_in_flight(state)
                        and candidate != account):
                    # Preserve uncommitted Native Goals. A terminal Codex
                    # usageLimited Goal is different: the old physical Goal is
                    # already over, and same-account route restoration is safe.
                    time.sleep(60)
                    continue
                try:
                    result = worker_action(
                        spec, worker_id, "resume", account=candidate,
                        provider_fallback=None,
                    )
                    _save_json(worker_dir(root, worker_id) / "last_quota_recovery.json", {
                        "at": utcnow().isoformat(), "from": account, "to": candidate,
                        "provider_fallback": None,
                        "cycle": cycle, "result": result})
                    time.sleep(15)
                    continue
                except (ValueError, RuntimeError, OSError) as exc:
                    _save_json(worker_dir(root, worker_id) / "last_quota_recovery.json", {
                        "at": utcnow().isoformat(), "from": account, "to": candidate,
                        "cycle": cycle, "blocked": f"{type(exc).__name__}: {str(exc)[:350]}"})

            # Both Codex identities unavailable: remain paused. The watcher
            # never changes model provider without an explicit Services choice.
            time.sleep(60)
    return 0



def _migrate_paused_worker_account(root: Path, worker: dict[str, Any],
                                   target_account: str) -> dict[str, Any]:
    """Account-only relocation of a PAUSED Cycle at a verified safe boundary.

    Preserve scientific lock, H/claim/Cycle, and historical model transcripts.
    Old native Codex thread IDs are account-owned: archive the checkpoint and
    start fresh model threads in the *same* Cycle after relocation. Never send
    the old thread IDs to the other account, even if the ID happens to resolve.
    """
    import json
    import os
    from .codex_accounts import account_switch_lock, ensure_layout
    root = Path(root).resolve()
    old_account = str(worker.get("account") or "")
    if target_account not in {"1", "2"} or old_account not in {"1", "2"}:
        raise ValueError("account migration requires two known Codex identities")
    if target_account == old_account:
        return {"status": "unchanged", "account": target_account}
    if process_identity_alive(worker.get("pid"), worker.get("pid_start_ticks")):
        raise ValueError("cannot migrate account during active scientific Turn")
    cycle = str(worker.get("current_cycle") or "")
    if not cycle.isascii() or not cycle.isdecimal():
        raise ValueError("no valid frozen Cycle for account migration")
    path = root / cycle / "research_state.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Cycle checkpoint is unavailable")
    state = _read_json(path)
    # A stale Worker record must never migrate a Cycle claimed by another owner.
    ownership = next((c for c in _live_cycle_claims(root)
                      if c.get("claim_id") == worker.get("claim_id")), None) or {}
    if (ownership.get("worker_id") != worker.get("worker_id")
            or ownership.get("fencing_epoch") != worker.get("epoch")
            or not _claim_owns_cycle(ownership, cycle)):
        raise ValueError("Worker no longer owns the frozen science Cycle")
    if (str(state.get("status") or "").lower() != "paused"
            or not _checkpoint_matches_claim(state, str(worker.get("worker_id") or ""), ownership, cycle)):
        raise ValueError("only matching, safely paused Cycle can migrate")
    if not state.get("resumable"):
        raise ValueError("paused Cycle has no resumable checkpoint")
    # Native Goal may own provider-internal continuation state beyond the
    # ordinary role threads dictionary. Do not risk silently resuming it under
    # a different account/provider. Same-account quota refresh remains allowed.
    from .quota_provider_fallback import investigator_native_goal_in_flight
    if investigator_native_goal_in_flight(state):
        raise ValueError("in-flight Investigator Native Goal requires same-account recovery")
    layout = ensure_layout()
    auth = (layout[target_account] / "auth.json").resolve()
    if not auth.is_file():
        raise ValueError("target Codex account has no valid authentication")
    directory = worker_dir(root, str(worker["worker_id"]))
    home = _pinned_codex_home(root, str(worker["worker_id"]), old_account)
    link = home / "auth.json"
    with account_switch_lock():
        if not link.is_symlink() or link.resolve() != (layout[old_account] / "auth.json").resolve():
            raise ValueError("Worker private authentication has changed; migration rejected")
        archive = root / cycle / ("research_state.before-account-migration-" +
                                 utcnow().strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        if archive.exists() or archive.is_symlink():
            raise ValueError("migration checkpoint archive collision")
        _save_json(archive, state)
        updated = json.loads(json.dumps(state))
        old_threads = list((updated.get("threads") or {}).keys())
        updated["threads"] = {}
        updated["runtime_thread_providers"] = {}
        updated["research_lead_thread_id"] = None
        updated["research_lead_context_chars"] = 0
        updated.setdefault("recovery", {})["allow_thread_fallback"] = True
        migration = {"from": old_account, "to": target_account,
                     "at": utcnow().isoformat(), "before_checkpoint": str(archive),
                     "invalidated_thread_roles": old_threads,
                     "mode": "fresh_identity_threads_same_frozen_cycle"}
        updated.setdefault("account_migration_history", []).append(migration)
        # If this write succeeds but auth rebinding fails, the new state can
        # still safely resume under the old account with fresh threads.
        _save_json(path, updated)
        temp = home / (".auth-switch-" + str(os.getpid()))
        try:
            temp.symlink_to(auth)
            os.replace(temp, link)
            save_worker(root, str(worker["worker_id"]), account=target_account,
                        last_account_migration=migration)
        except BaseException:
            if temp.is_symlink():
                temp.unlink()
            # Return auth to old identity and restore checkpoint when safe.
            revert = home / (".auth-rollback-" + str(os.getpid()))
            revert.symlink_to((layout[old_account] / "auth.json").resolve())
            os.replace(revert, link)
            _save_json(path, state)
            save_worker(root, str(worker["worker_id"]), account=old_account)
            raise
    return {"status": "migrated", "account": target_account,
            "old_account": old_account, "cycle": cycle,
            "preserved_science": True, "fresh_model_threads_required": True}



class PreservationPreflightRejected(ValueError):
    """Pre-mutation preservation guard refused; no Worker/Cycle change occurred."""


def request_preserve_release(spec: Any, worker_id: str) -> dict[str, Any]:
    """Queue an approved read-only Investigator archive outside the Web timeout.

    A durable Worker status and independent systemd process keep the original
    claim reserved throughout the model call. No release occurs on failure.
    """
    from .research_preservation import blocked_checkpoint, PreservationGuard
    root = Path(spec.research_root).resolve()
    gate = root / ".theory-master" / "scheduler.lock"
    gate.parent.mkdir(parents=True, exist_ok=True)
    with gate.open("a+", encoding="utf-8") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        worker = read_worker(root, worker_id)
        if worker.get("status") not in {"NEEDS_ATTENTION", "PAUSED", "INTERRUPTED", "PRESERVING"}:
            raise PreservationPreflightRejected("only a stopped Worker with a scientific blocker may be preserved")
        if process_identity_alive(worker.get("pid"), worker.get("pid_start_ticks")):
            raise PreservationPreflightRejected("live research Worker cannot be archived")
        cycle = str(worker.get("current_cycle") or "")
        if not cycle.isdecimal() or int(cycle) < 1:
            raise PreservationPreflightRejected("Worker has no frozen Cycle")
        if worker.get("status") == "PRESERVING":
            job_pid = worker.get("preservation_job_pid")
            if (not job_pid or process_identity_alive(
                    job_pid, worker.get("preservation_job_start_ticks"))):
                raise PreservationPreflightRejected("preservation may still be running; refuse a second model call")
        state_path = root / cycle / "research_state.json"
        archived_state = _read_json(state_path)
        if str(archived_state.get("status") or "").lower() == "over":
            from .research_preservation import finalize_preserved_cycle
            verified = finalize_preserved_cycle(
                cycle_dir=root / cycle, authorization="operator_approved", dry_run=True)
            if verified.get("scientific_verdict") is not None:
                raise ValueError("archived Cycle unexpectedly has a scientific verdict")
            _ensure_legacy_claims_migrated(root)
            claims = CycleClaims(root, _graph_path(spec))
            claim_token = (worker["claim_id"], int(worker["epoch"]))
            reconciled = reconcile_released_worker_cycle(root, worker)
            claims.update(worker_id, *claim_token, action="release")
            save_worker(root, worker_id, status="RELEASED", claim_id=None, epoch=None,
                        pid=None, pid_start_ticks=None)
            return {"worker_id": worker_id, "status": "RELEASED", "cycle": cycle,
                    "reconciled": reconciled, "reused_verified_preservation": True,
                    "model_calls": 0}
        # The run_state.json returned by blocked_checkpoint is only a Web
        # projection. Worker identity/branch ownership are intentionally stored
        # in authoritative research_state.json, and may be absent from run_state.
        try:
            projected = blocked_checkpoint(root / cycle)
        except PreservationGuard as exc:
            raise PreservationPreflightRejected(
                "frozen scientific Cycle is not eligible for preservation") from exc
        if (archived_state.get("worker_id") != worker_id
                or projected.get("resumable") is not True
                or archived_state.get("resumable") is not True
                or not worker.get("claim_id")):
            raise PreservationPreflightRejected("Worker claim and authoritative frozen Cycle do not match")
        if any(projected.get(key) is not None and projected[key] != archived_state.get(key)
               for key in ("worker_id", "claimed_frontier_id")):
            raise PreservationPreflightRejected("Web projection conflicts with authoritative Worker owner")
        _ensure_legacy_claims_migrated(root)
        worker = read_worker(root, worker_id)
        claim = _worker_live_claim(CycleClaims(root, _graph_path(spec)), worker)
        if claim is None or not _checkpoint_matches_claim(archived_state, worker_id, claim, cycle):
            raise PreservationPreflightRejected("Worker claim fencing changed; archive not authorized")
        original_status = str(worker["status"])
        save_worker(root, worker_id, status="PRESERVING",
                    preservation_job_pid=None, preservation_job_start_ticks=None)
        args = [sys.executable, "-m", "theory_master.worker_runtime",
                "preserve-job", "--research-root", str(root), "--worker", worker_id]
        env = _task_scoped_process_env(root)
        if worker.get("account"):
            env["THEORYMASTER_WORKER_CODEX_HOME"] = str(
                _pinned_codex_home(root, worker_id, worker["account"]))
            env["THEORYMASTER_WORKER_PINNED_ACCOUNT"] = worker["account"]
        log_path = worker_dir(root, worker_id) / "preservation.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("a", encoding="utf-8") as log:
                _launch_detached_service(args, root=root, env=env, output=log,
                                         kind="research-preservation")
        except Exception:
            # A refused launcher has not touched any scientific files or claims.
            save_worker(root, worker_id, status=original_status)
            raise
        return {"worker_id": worker_id, "status": "PRESERVING",
                "cycle": cycle, "claim_preserved": True,
                "requested_operation": "preserve_research_then_release"}


def execute_preserve_release_job(spec: Any, worker_id: str) -> int:
    """Preserve via existing Workflow, and release ONLY after verified closure."""
    root = Path(spec.research_root).resolve()
    worker = read_worker(root, worker_id)
    cycle = str(worker.get("current_cycle") or "")
    if worker.get("status") != "PRESERVING" or not cycle.isdecimal():
        raise ValueError("Worker is not in an authorized preservation transition")
    progress_path = worker_dir(root, worker_id) / "preservation_result.json"
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    save_worker(root, worker_id,
                preservation_job_pid=os.getpid(),
                preservation_job_start_ticks=process_start_ticks(os.getpid()))
    try:
        command = [sys.executable, "-m", "theory_master.runner",
                   "--task-root", str(root), "--operation", "preserve",
                   "--identifier", cycle, "--approve-preservation",
                   "--managed-child"]
        # Unlike a scientific resume, preserve uses only one read-only
        # Investigator Stage. The runner itself enforces evidence/Canvas guards.
        with (worker_dir(root, worker_id) / "preservation_child.log").open("a") as log:
            finished = subprocess.run(command, cwd=root, env=_task_scoped_process_env(root),
                                      stdin=subprocess.DEVNULL, check=False,
                                      stdout=log, stderr=subprocess.STDOUT,
                                      text=True, timeout=36000)
        if finished.returncode:
            raise RuntimeError("preserve_research Stage failed; original Cycle and claim retained")
        closed = _read_json(root / cycle / "preservation" / "closure.json")
        science = _read_json(root / cycle / "research_state.json")
        if (closed.get("cycle_id") != cycle
                or closed.get("scientific_verdict") is not None
                or science.get("scientific_verdict") is not None
                or str(science.get("status") or "").lower() != "over"
                or science.get("closure_reason") != "investigator_preservation"):
            raise RuntimeError("preservation closure is unverified; Worker claim retained")
        # Release reconciles the original Worker/Cycle and removes only the
        # claimed entry. Running scheduler next tick chooses the next frontier.
        result = worker_action(spec, worker_id, "release")
        _save_json(progress_path, {"status": "preserved_released", "cycle": cycle,
                                   "node_id": closed.get("canvas_node_id"),
                                   "worker": result, "at": utcnow().isoformat()})
        # After verified closure+release, always leave a persistent reminder.
        # Failure to display a notification must not undo an already released claim.
        try:
            from .budget_auto_preservation import budget_preservation_notice
            budget_preservation_notice(root, worker_id, cycle, success=True,
                                       hypothesis_id=str(closed.get("hypothesis_id") or ""))
        except (OSError, ValueError, RuntimeError) as exc:
            _save_json(worker_dir(root, worker_id) / "preservation_notice_warning.json",
                       {"cycle": cycle, "error_type": type(exc).__name__, "at": utcnow().isoformat()})
        from .worker_mode import read_mode
        if read_mode(root).get("enabled"):
            try:
                start_scheduler(spec, max(1, int(read_mode(root)["desired_agents"])))
            except (ValueError, OSError, RuntimeError) as exc:
                _save_json(worker_dir(root, worker_id) / "preservation_scheduler_warning.json",
                           {"error": type(exc).__name__, "at": utcnow().isoformat()})
        return 0
    except Exception as exc:
        current = read_worker(root, worker_id)
        if current.get("status") == "PRESERVING":
            save_worker(root, worker_id, status="NEEDS_ATTENTION")
        _save_json(progress_path, {"status": "failed_preservation_retained_claim",
                                   "cycle": cycle, "error_type": type(exc).__name__,
                                   "at": utcnow().isoformat()})
        try:
            from .budget_auto_preservation import budget_preservation_notice
            budget_preservation_notice(root, worker_id, cycle, success=False,
                                       detail=type(exc).__name__)
        except (OSError, ValueError, RuntimeError):
            pass  # Original Cycle remains retained; failure stays in progress_path.
        return 1


def _verified_codex_quota_wait() -> dict[str, int] | None:
    """Require fresh affirmative exhaustion of both Codex pools, never unknown."""
    from .codex_accounts import account_rate_limits
    now_ts = time.time()
    resets: dict[str, int] = {}
    try:
        for number in ("1", "2"):
            record = account_rate_limits(number)
            if (not isinstance(record, dict) or record.get("ok") is not True
                    or record.get("ordinary_usage_allowed") is not False):
                return None
            stamp = record.get("queried_at")
            if type(stamp) not in (int, float) or not now_ts - 300 <= stamp <= now_ts + 60:
                return None
            exhausted = [w["resets_at"] for w in
                         (record.get("primary"), record.get("secondary"))
                         if isinstance(w, dict)
                         and type(w.get("remaining_percent")) in (int, float)
                         and w["remaining_percent"] <= 0
                         and type(w.get("resets_at")) is int
                         and now_ts < w["resets_at"] < now_ts + 8 * 86400]
            if not exhausted:
                return None
            resets[number] = max(exhausted)
    except (OSError, ValueError, RuntimeError, TimeoutError):
        return None
    return resets


def _verified_pinned_codex_quota_wait(account: str) -> dict[str, int] | None:
    """One exhausted Worker account is enough to wait, even if its peer has quota.

    Native Goal identity forbids an automatic cross-account transfer, so a
    healthy unrelated account does not make this account's Turn possible.
    """
    from .codex_accounts import account_rate_limits
    if account not in {"1", "2"}:
        return None
    now_ts = time.time()
    try:
        record = account_rate_limits(account)
        if (not isinstance(record, dict) or record.get("ok") is not True
                or record.get("ordinary_usage_allowed") is not False):
            return None
        queried = record.get("queried_at")
        if (type(queried) not in (int, float)
                or not now_ts - 300 <= queried <= now_ts + 60):
            return None
        resets = [item["resets_at"] for item in
                  (record.get("primary"), record.get("secondary"))
                  if isinstance(item, dict)
                  and type(item.get("remaining_percent")) in (int, float)
                  and item["remaining_percent"] <= 0
                  and type(item.get("resets_at")) is int
                  and now_ts < item["resets_at"] < now_ts + 8*86400]
        return {account: max(resets)} if resets else None
    except (OSError, ValueError, RuntimeError, TimeoutError):
        return None


def _persist_verified_quota_wait(root: Path, worker_id: str, cycle: str,
                                 claim: dict[str, Any], resets: dict[str, int],
                                 reason: str) -> bool:
    """Keep previous failure provenance while pausing a fenced stopped Cycle."""
    folder = root / cycle
    science_path, run_path = folder / "research_state.json", folder / "run_state.json"
    if (not folder.is_dir() or folder.is_symlink()
            or science_path.is_symlink() or run_path.is_symlink()):
        return False
    science, run = _read_json(science_path), _read_json(run_path)
    if (not _checkpoint_matches_claim(science, worker_id, claim, cycle)
            or science.get("resumable") is not True
            or science.get("execution_active") is True
            or str(science.get("status") or "").lower() not in {"paused", "failed"}
            or str(run.get("status") or "").upper() not in {"PAUSED", "FAILED"}):
        return False
    previous = dict(science.get("recovery") or {})
    if previous.get("code") == "provider_quota_exhausted":
        previous = previous.get("previous_failure") or {}
    else:
        previous = {key: previous[key] for key in ("action", "kind", "reason", "message", "at")
                    if previous.get(key)}
    recovery = {"action": "resume", "code": "provider_quota_exhausted",
                "kind": "VerifiedCodexQuotaWait", "reason": reason,
                "previous_failure": previous, "resets_at": resets,
                "resume_when": "fresh quota is positively available",
                "at": utcnow().isoformat()}
    for entry, status in ((science, "paused"), (run, "PAUSED")):
        entry.update(status=status, phase="provider_quota_exhausted",
                     execution_active=False, resumable=True,
                     resource_disposition="provider_quota_exhausted", recovery=recovery)
    _save_json(science_path, science)
    _save_json(run_path, run)
    return True


def worker_action(
    spec: Any, worker_id: str, action: str, *, account: str | None = None,
    provider_fallback: str | None = None,
) -> dict[str, Any]:
    """Exact Worker-specific checkpoint control; never use Master-wide kill."""
    if action == "preserve_release":
        if account is not None or provider_fallback is not None:
            raise ValueError("preservation does not accept account/provider migration")
        return request_preserve_release(spec, worker_id)
    if action not in {"pause", "kill", "resume", "release", "dismiss", "retry_direction"}:
        raise ValueError("unknown Worker action")
    if provider_fallback not in {None, "Codex"} or (provider_fallback and action != "resume"):
        raise ValueError("provider fallback is valid only for a bounded Worker resume")
    root = Path(spec.research_root).resolve()
    scheduler_lock = root / ".theory-master" / "scheduler.lock"
    scheduler_lock.parent.mkdir(parents=True, exist_ok=True)
    with open(scheduler_lock, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        _ensure_legacy_claims_migrated(root)
        claims = CycleClaims(root, _graph_path(spec))
        w = read_worker(root, worker_id)
        if not w or w.get("schema") != SCHEMA:
            raise ValueError("Worker does not exist")
        if action == "retry_direction":
            if w.get("status") != WAITING_RESEARCH_DIRECTION:
                raise ValueError("retry_direction applies only to a Worker waiting for research direction")
            if process_identity_alive(w.get("pid"), w.get("pid_start_ticks")):
                raise ValueError("Worker is still live; wait for it to finish")
            request = {"schema": RETRY_DIRECTION_SCHEMA, "status": "pending",
                       "worker_id": worker_id, "requested_at": utcnow().isoformat(),
                       "source": "operator",
                       "waiting_cycle": w.get("current_cycle"),
                       "wake_fingerprint_at_request": w.get("wake_fingerprint")}
            _save_json(_retry_direction_path(root, worker_id), request)
            return {"worker_id": worker_id, "action": action, "status": "RETRY_REQUESTED",
                    "effective_at": "next_scheduler_tick"}
        if action == "dismiss":
            if w.get("dismissed"):
                raise ValueError("Worker already hidden")
            if sum(not row.get("dismissed") for row in list_workers(root)) <= 1:
                raise ValueError("Keep at least one visible Worker")
            if process_identity_alive(w.get("pid"), w.get("pid_start_ticks")):
                raise ValueError("Cannot hide an active Worker; pause and release it first")
            if w.get("status") not in {"OVER", "COMPLETE", "RELEASED", "STOPPED", "CANCELLED",
                                       "FAILED_START", *NON_INCIDENT_STOPS}:
                raise ValueError("Worker must finish or be released before hiding")
            # A terminal runtime label alone cannot prove that a Cycle claim
            # was released. A stranded claim must remain operable. Hold the
            # claim registry fence during the read and card change.
            with claims._locked() as claim_registry:
                owned_claims = (claim_registry.get("claims") or {}).values()
                if any(isinstance(claim, dict) and claim.get("worker_id") == worker_id
                       for claim in owned_claims):
                    raise ValueError("Worker still owns a research claim; release it first")
                save_worker(root, worker_id, dismissed=True)
                from .worker_mode import reduce_one_worker_request
                visible = sum(not row.get("dismissed") for row in list_workers(root))
                capacity = reduce_one_worker_request(root, registered_count=visible,
                                                     require_pending=False)
            return {"worker_id": worker_id, "action": action, "status": "hidden",
                    "history_preserved": True, **capacity}
        if action == "pause":
            if not process_identity_alive(w.get("pid"), w.get("pid_start_ticks")):
                raise ValueError("Worker is not live; use resume or release")
            cycle = w.get("current_cycle")
            if not cycle or not str(cycle).isdigit():
                raise ValueError("Worker has no assigned Cycle yet; try at next safe boundary")
            path = root / cycle / "recovery_control.json"
            if path.exists() or path.is_symlink():
                raise ValueError("Worker already has a pending control action")
            _save_json(path, {"action": "pause", "reason": "multi_worker_operator",
                              "requested_at": utcnow().isoformat()})
            save_worker(root, worker_id, status="PAUSE_REQUESTED")
            return {"worker_id": worker_id, "action": action, "status": "PAUSE_REQUESTED"}
        if action == "kill":
            if w.get("status") != "RUNNING" or not process_identity_alive(w.get("pid"), w.get("pid_start_ticks")):
                raise ValueError("Worker is not a verified RUNNING process")
            cycle = str(w.get("current_cycle") or "")
            if not cycle.isascii() or not cycle.isdigit() or int(cycle) < 1:
                raise ValueError("Worker has no valid Cycle to control")
            state = _read_json(root / cycle / "research_state.json")
            if (not _checkpoint_matches_claim(state, worker_id, _worker_live_claim(claims, w), cycle)
                    or str(state.get("status") or "").lower() not in {"active", "running"}
                    or state.get("execution_active") is not True):
                raise ValueError("Worker/Cycle identity or execution state changed")
            if (state.get("sdk_runtime_capabilities") or {}).get("abandon_cycle_v1") is not True:
                raise ValueError("Worker Cycle lacks safe kill checkpoint capability")
            path = root / cycle / "recovery_control.json"
            if path.exists() or path.is_symlink():
                raise ValueError("Worker already has a pending control action")
            # The Worker consumes this at a scientific safe boundary.
            # Never signal a process or mutate another Worker/Master Cycle.
            _save_json(path, {"action": "abandon_cycle", "source_action": action,
                              "reason": "multi_worker_operator",
                              "requested_at": utcnow().isoformat()})
            save_worker(root, worker_id, status="STOP_REQUESTED")
            return {"worker_id": worker_id, "action": action, "status": "STOP_REQUESTED"}
        if process_identity_alive(w.get("pid"), w.get("pid_start_ticks")):
            raise ValueError("Worker is still live; wait for it to finish")
        if action == "release":
            if not w.get("claim_id") and w.get("status") in NON_INCIDENT_STOPS:
                # Waiting/goal-stopped Workers already archived their Cycle claim.
                save_worker(root, worker_id, status="RELEASED", pid=None, pid_start_ticks=None,
                            wake_fingerprint=None, waiting_since=None)
                return {"worker_id": worker_id, "action": action, "status": "RELEASED",
                        "cycle_reconciliation": None}
            if not w.get("claim_id") or w.get("epoch") is None:
                raise ValueError("Worker has no Cycle claim to release")
            token = (w["claim_id"], int(w["epoch"]))
            reconciled = reconcile_released_worker_cycle(root, w)
            claims.update(worker_id, *token, action="release")
            save_worker(root, worker_id, status="RELEASED", claim_id=None, epoch=None,
                        pid=None, pid_start_ticks=None)
            return {"worker_id": worker_id, "action": action, "status": "RELEASED",
                    "cycle_reconciliation": reconciled}
        if not w.get("claim_id") or w.get("epoch") is None:
            raise ValueError("Worker has no Cycle claim to resume")
        token = (w["claim_id"], int(w["epoch"]))
        if action == "resume" and w.get("status") in RUNNING:
            # Explicit orphan recovery, not a silent scheduler repair. Preserve
            # the exact unfinished Cycle and its frozen science for SDK resume.
            cycle_id = str(w.get("current_cycle") or "")
            if not cycle_id.isdecimal() or not cycle_id or int(cycle_id) < 1:
                raise ValueError("orphaned Worker lacks a valid Cycle")
            cdir = root / cycle_id
            if not cdir.is_dir() or cdir.is_symlink():
                raise ValueError("orphaned Worker Cycle path is unsafe")
            state_file, run_file = cdir / "research_state.json", cdir / "run_state.json"
            if state_file.is_symlink() or run_file.is_symlink():
                raise ValueError("orphaned Worker checkpoint is unsafe")
            state, run = _read_json(state_file), _read_json(run_file)
            if (not _checkpoint_matches_claim(state, worker_id, _worker_live_claim(claims, w), cycle_id)
                    or str(state.get("status") or "").lower() not in {"active", "running"}
                    or int(state.get("owner_pid") or 0) != int(w.get("pid") or 0)
                    or str(run.get("status") or "").upper() != "RUNNING"
                    or run.get("execution_active") is not True):
                raise ValueError("orphaned Worker checkpoint does not match its frozen Cycle")
            if process_identity_alive(state.get("owner_pid"), w.get("pid_start_ticks")):
                raise ValueError("old scientific Worker is still alive")
            claims.update(worker_id, *token, action="pause")
            for path, data, backup in (
                (state_file, state, "research_state.before-orphan-resume.json"),
                (run_file, run, "run_state.before-orphan-resume.json"),
            ):
                backup_path = cdir / backup
                if backup_path.is_symlink():
                    raise ValueError("unsafe orphan recovery backup")
                if not backup_path.exists():
                    _save_json(backup_path, data)
                data["status"] = "interrupted" if path == state_file else "INTERRUPTED"
                data["phase"] = "interrupted"
                data["execution_active"] = False
                data["resumable"] = True
                if path == state_file:
                    data["runtime_state"] = "interrupted"
                    data["recovery"] = {"kind": "orphaned_process",
                                        "reason": "old Worker PID identity absent; explicit Resume",
                                        "at": utcnow().isoformat()}
                _save_json(path, data)
            save_worker(root, worker_id, status="INTERRUPTED", pid=None,
                        pid_start_ticks=None, heartbeat_at=None)
            w = read_worker(root, worker_id)
        if w.get("status") not in RESUMABLE:
            raise ValueError("only an interrupted Worker can resume its frozen Cycle")
        cycle = w.get("current_cycle")
        # A startup/preflight failure may precede Workflow._bind_cycle. Recover
        # only a FAILED Worker's lost display pointer from its exact fenced
        # claim and a matching resumable scientific checkpoint.
        claim_record = _worker_live_claim(claims, w)
        if not cycle or not str(cycle).isascii() or not str(cycle).isdigit():
            cycle = _recover_failed_cycle_pointer(root, w, claim_record)
            if cycle is None:
                raise ValueError("cannot recover a Worker without Cycle identity")
            save_worker(root, worker_id, current_cycle=cycle)
        # I-10: resume keeps the exact Cycle/H; only a live claim for THIS
        # Cycle may be reissued, never a stale or foreign fencing token.
        if claim_record is None:
            raise ValueError("paused scientific Cycle claim is stale or missing")
        old = _read_json(root / cycle / "research_state.json")
        if not _checkpoint_matches_claim(old, worker_id, claim_record, cycle):
            raise ValueError("Worker and scientific Cycle identity mismatch")
        if (provider_fallback == "Codex" and str(old.get("status") or "").lower() == "paused"
                and (old.get("recovery") or {}).get("reason") == "multi_worker_operator"):
            from .service_provider_switch import selected_service_preference
            from .backend_config import ACCOUNT_PROVIDERS
            from .codex_accounts import account_rate_limits, quota_available
            if selected_service_preference() != "codex":
                raise ValueError("operator Codex resume requires the current Services Codex selection")
            requested = resolve_worker_routes(root, worker_id)
            if not requested or any(route.provider not in ACCOUNT_PROVIDERS for route in requested.values()):
                raise ValueError("operator Codex resume requires a complete Worker Codex profile")
            if str(w.get("account") or "") not in {"1", "2"} or quota_available(
                    account_rate_limits(str(w["account"]))) is not True:
                raise ValueError("operator Codex resume requires verified pinned Codex account quota")
        if account is not None and account != w.get("account"):
            if not _share_enabled(root) and any(
                    other["worker_id"] != worker_id and other.get("account") == account
                    for other in _living_workers(root)):
                raise ValueError("target account is occupied and sharing is disabled")
            if sum(other["worker_id"] != worker_id and other.get("account") == account
                   for other in _living_workers(root)) >= MAX_SHARED_ACCOUNT_WORKERS:
                raise ValueError("target account has reached shared Worker limit")
            from .codex_accounts import account_rate_limits, quota_available
            if quota_available(account_rate_limits(account)) is not True:
                raise ValueError("target account quota is unavailable or unverified")
            migration = _migrate_paused_worker_account(root, w, account)
            w = read_worker(root, worker_id)
        else:
            migration = None
        # The user requested Resume, but neither Codex account can accept
        # a Turn. Preserve this exact Cycle and wait for quota instead of
        # launching a doomed process or raising an unrelated Supervisor fault.
        if (action == "resume"
                and str(old.get("status") or "").lower() == "paused"
                and ((old.get("recovery") or {}).get("reason") == "multi_worker_operator"
                     or old.get("phase") == "provider_quota_exhausted")):
            resets = (_verified_codex_quota_wait()
                      or _verified_pinned_codex_quota_wait(str(w.get("account") or "")))
            if resets and _persist_verified_quota_wait(
                    root, worker_id, str(cycle), claim_record, resets, "operator-resume-awaits-quota"):
                save_worker(root, worker_id, status="PAUSED", error_kind=None)
                try:
                    watcher = start_worker_quota_watch(spec, worker_id)
                except (OSError, RuntimeError, ValueError) as error:
                    watcher = {"status": "launch_failed", "reason": type(error).__name__}
                return {"worker_id": worker_id, "action": action, "status": "PAUSED",
                        "quota_waiting": True, "quota_resets_at": resets, "watcher": watcher,
                        "cycle": str(cycle), "hypothesis_id": claim_record.get("hypothesis_id")}
        peers = [other for other in _living_workers(root)
                 if other["worker_id"] != worker_id and other.get("account") == w.get("account")]
        if w.get("account") and peers and not _share_enabled(root):
            raise ValueError("pinned Codex account is occupied; resume after capacity is free")
        if w.get("account") and len(peers) >= MAX_SHARED_ACCOUNT_WORKERS:
            raise ValueError("shared Codex account concurrency limit reached")
        try:
            refreshed = claims.reissue_for_resume(worker_id, *token)
        except GoalSealedError as exc:
            raise ValueError(f"Goal already completed; Cycle {cycle} is not resumed: {exc}") from exc
        if refreshed.get("cycle_id") != claim_record.get("cycle_id") or \
                refreshed.get("hypothesis_id") != claim_record.get("hypothesis_id"):
            raise ValueError("reissued Cycle claim changed Cycle/H identity; manual recovery required")
        token = (refreshed["claim_id"], refreshed["fencing_epoch"])
        save_worker(root, worker_id, claim_id=token[0], epoch=token[1], pid=None, pid_start_ticks=None)
        env = _task_scoped_process_env(root)
        env.pop("THEORYMASTER_QUOTA_PROVIDER_FALLBACK", None)
        if provider_fallback:
            env["THEORYMASTER_QUOTA_PROVIDER_FALLBACK"] = provider_fallback
        if w.get("account"):
            home = _pinned_codex_home(root, worker_id, w["account"])
            env["THEORYMASTER_WORKER_CODEX_HOME"] = str(home)
            env["THEORYMASTER_WORKER_PINNED_ACCOUNT"] = str(w["account"])
        directory = worker_dir(root, worker_id)
        with open(directory / "runner.log", "a", encoding="utf-8") as output:
            _launch_detached_service([
                sys.executable, "-m", "theory_master.worker_runtime", "run-one",
                "--research-root", str(root), "--worker", worker_id,
                "--claim-id", token[0], "--epoch", str(token[1]),
                "--resume-cycle", str(cycle)], root=root, env=env,
                output=output, kind=worker_id)
        active_record = read_worker(root, worker_id)
        if active_record.get("status") == "PAUSED":
            save_worker(root, worker_id, status="STARTING", pid=None, pid_start_ticks=None)
        return {"worker_id": worker_id, "action": action, "status": "STARTING",
                "pid": read_worker(root, worker_id).get("pid"),
                "account": w.get("account"), "migration": migration,
                "cycle": str(cycle), "hypothesis_id": refreshed.get("hypothesis_id"),
                "claim_id": token[0], "epoch": token[1]}


def scheduler_status(root: Path) -> dict[str, Any]:
    path = Path(root).resolve() / ".theory-master" / "worker_scheduler.json"
    info = _read_json(path)
    alive = process_identity_alive(info.get("pid"), info.get("start_ticks"))
    if str(info.get("status") or "").upper() == "RUNNING" and not alive:
        # A dead PID cannot remain presented as a live scheduler. Preserve the
        # previous identity for diagnosis and fail closed rather than auto-restart.
        info = {**info, "status": "FAILED", "last_pid": info.get("pid"),
                "last_start_ticks": info.get("start_ticks"), "pid": None,
                "start_ticks": None, "ended_at": utcnow().isoformat(),
                "error_kind": "SchedulerProcessExited",
                "error_detail": "Scheduler metadata said RUNNING but its PID identity is not alive",
                "runtime_source_provenance": info.get("runtime_source_provenance") or runtime_source_provenance()}
        _save_json(path, info)
    return {**info, "process_alive": alive}



def _validate_scheduler_skills(spec: Any) -> None:
    """Fail before launch on mixed SDK sources or missing system/task Skills."""
    provenance = runtime_source_provenance()
    if not provenance.get("source_coherent", True):
        details = "; ".join(provenance.get("source_coherence_errors") or [])
        raise RuntimeError(f"Mixed TheoryMaster source roots detected: {details[:700]}")
    from .skills import ROLE_SKILLS, role_skills
    config_path = Path(spec.config_path).expanduser().resolve()
    for role in ROLE_SKILLS:
        role_skills(role, task_config_path=config_path)


def _record_scheduler_start_failure(root: Path, exc: Exception) -> None:
    path = Path(root).resolve() / ".theory-master" / "worker_scheduler.json"
    previous = _read_json(path)
    _save_json(path, {**previous, "status": "FAILED", "pid": None,
        "start_ticks": None, "ended_at": utcnow().isoformat(),
        "error_kind": type(exc).__name__, "error_detail": str(exc)[:1000],
        "runtime_source_provenance": runtime_source_provenance()})


def start_scheduler(spec: Any, slots: int, *, preferred_hypothesis: str | None = None) -> dict[str, Any]:
    """Explicit operator launcher, refuses coexistence with old Runner."""
    if slots < 1:
        raise ValueError("scheduler needs at least one verified resource slot")
    from .worker_mode import read_mode
    configured = read_mode(spec.research_root)
    if configured["configured"]:
        if not configured["enabled"]:
            raise ValueError("multi-Agent mode is OFF for this task")
        slots = configured["desired_agents"]
    root = Path(spec.research_root).resolve()
    if runtime_health(root).get("active"):
        raise ValueError("legacy Master runtime is still running; safe cutover required")
    if scheduler_status(root).get("process_alive"):
        return {"status": "already_running", **scheduler_status(root)}
    try:
        _validate_scheduler_skills(spec)
    except Exception as exc:
        _record_scheduler_start_failure(root, exc)
        raise
    # Catch the wrong Python environment before the scheduler claims a science
    # frontier: the child inherits this interpreter and needs the Codex SDK.
    import importlib.util
    from .backend_config import ACCOUNT_PROVIDERS
    from .backends import resolve_routes
    try:
        routes = resolve_routes(task_route_override_root=spec.research_root)
        if (any(route.provider in ACCOUNT_PROVIDERS for route in routes.values())
                and importlib.util.find_spec("openai_codex") is None):
            raise RuntimeError("Codex SDK is missing from the launcher Python; use the TheoryMaster SDK environment")
        _capacity, _accounts = _route_capacity(spec, slots)  # validate before detached work
        env = _task_scoped_process_env(root)
    except Exception as exc:
        _record_scheduler_start_failure(root, exc)
        raise
    path = root / ".theory-master" / "worker_scheduler.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as log:
        cmd = [sys.executable, "-m", "theory_master.worker_runtime", "daemon",
               "--research-root", str(root), "--resource-slots", str(slots)]
        if preferred_hypothesis:
            cmd += ["--preferred-hypothesis", preferred_hypothesis]
        try:
            _launch_detached_service(cmd, root=root, env=env, output=log, kind="scheduler")
        except Exception as exc:
            _record_scheduler_start_failure(root, exc)
            raise
    actual = scheduler_status(root)
    return {"status": "starting", "pid": actual.get("pid") if actual["process_alive"] else None,
            "resource_slots": slots}


def scheduler_daemon(spec: Any, slots: int, preferred: str | None = None) -> int:
    """Poll Canvas at Cycle boundaries, assigning new independent Worker processes."""
    root = Path(spec.research_root).resolve()
    lock_file = root / ".theory-master" / "scheduler_owner.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_file, "a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0  # Another daemon already owns the pool
        metadata_path = root / ".theory-master" / "worker_scheduler.json"
        source_provenance = runtime_source_provenance()
        from .worker_mode import read_mode
        mode = read_mode(root)
        worker_count = mode["desired_agents"] if mode["configured"] else slots
        worker_ids = tuple(f"worker-{i}" for i in range(1, worker_count + 1))
        previous = _read_json(metadata_path)
        max_cycles = int(spec.max_cycles)
        # A stopped, not-yet-exhausted batch is safe to resume without
        # resetting already consumed Worker allocations.
        previous_max = previous.get("max_cycles_per_worker")
        # Increasing a per-Worker batch limit must preserve already consumed
        # allocations; never reset historical counts just because the cap grew.
        resume = (previous.get("quota_mode") == "per_worker" and
                  previous.get("status") in {"STOPPED", "FAILED", "STALLED", "RUNNING"} and
                  isinstance(previous_max, int) and 0 < previous_max <= max_cycles and
                  previous.get("worker_ids") == list(worker_ids) and
                  previous.get("resume_batch") is True)
        raw_counts = previous.get("cycles_started_by_worker") if resume else {}
        raw_counts = raw_counts if isinstance(raw_counts, dict) else {}
        counts = {name: min(max_cycles, max(0, int(raw_counts.get(name, 0))))
                  for name in worker_ids}
        def save_scheduler(status: str, **extra: Any) -> None:
            _save_json(metadata_path, {
                "pid": os.getpid(), "start_ticks": process_start_ticks(os.getpid()),
                "status": status, "quota_mode": "per_worker",
                "cycles_started": sum(counts.values()),
                "cycles_started_by_worker": dict(counts),
                "max_cycles_per_worker": max_cycles,
                "worker_ids": list(worker_ids), "resource_slots": slots,
                "resume_batch": any(n < max_cycles for n in counts.values()),
                "runtime_source_provenance": source_provenance,
                **({"preferred_hypothesis_ignored": preferred,
                    "preferred_hypothesis_deprecation": "Preferred no longer influences scheduling (V1.1 I-12)"}
                   if preferred else {}),
                **extra})
        save_scheduler("RUNNING", started_at=utcnow().isoformat())
        no_progress_since: float | None = None
        stopping = threading.Event()
        def handle_signal(_signum: int, _frame: Any) -> None:
            stopping.set()
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)
        stop_reason = "stop_requested"
        while not stopping.is_set():
            if _master_complete(root):
                stop_reason = "goal_completed"
                break
            from .worker_mode import read_mode
            mode = read_mode(root)
            if mode["configured"] and not mode["enabled"]:
                stop_reason = "multi_agent_mode_off"
                break
            desired = mode["desired_agents"] if mode["configured"] else slots
            from .specification import TaskSpec
            fresh = TaskSpec.from_task_root(root)
            # A Worker gets max_cycles of its OWN new allocations per batch.
            # Completed/failed cycles consume a slot once they were started;
            # resumptions are routed by dispatch_pending and consume no slot.
            eligible = frozenset(name for name, count in counts.items()
                                 if count < max_cycles)
            try:
                recovered = _recover_proposal_gate(root)
                if recovered is not None:
                    save_scheduler("RUNNING", last_proposal_gate_recovery={
                        **recovered, "recovered_at": utcnow().isoformat()})
                repaired_watches = _repair_missing_quota_watchers(fresh)
                if repaired_watches:
                    save_scheduler("RUNNING", last_quota_watch_repairs=repaired_watches,
                                   last_quota_watch_repair_at=utcnow().isoformat())
                orphaned_preservations = _flag_orphaned_preservation_jobs(fresh)
                if orphaned_preservations:
                    save_scheduler("RUNNING",
                                   preservation_orphan_alerts=orphaned_preservations,
                                   preservation_orphan_alert_at=utcnow().isoformat())
                # A budget stop is not a runtime FAILED incident. Only an
                # exact SDK budget marker may enter the guarded Preserve path.
                from .budget_auto_preservation import dispatch_auto_budget_preservation
                if dispatch_auto_budget_preservation(fresh):
                    no_progress_since = None
                    stopping.wait(8)
                    continue
                if _dispatch_exhausted_budget_reviewer_transport(fresh):
                    no_progress_since = None
                    stopping.wait(8)
                    continue
                from .worker_supervisor_dispatch import dispatch_pending
                if dispatch_pending(root):
                    no_progress_since = None
                    stopping.wait(8)
                    continue
                if not eligible:
                    stop_reason = "cycle_budget_exhausted"
                    break
                outcome = schedule_once(fresh, resource_slots=desired,
                                        eligible_worker_ids=eligible)
                started = outcome.get("started", ())
                for worker in started:
                    name = worker["worker_id"]
                    if name not in eligible:
                        raise RuntimeError("scheduler allocated a Worker beyond its Cycle quota")
                    counts[name] += 1
                if started:
                    no_progress_since = None
                    save_scheduler("RUNNING", last_progress_at=utcnow().isoformat(),
                                   last_started_workers=[w.get("worker_id") for w in started])
                else:
                    # During account migration the old PID may be gone before
                    # the replacement registers. Durable claims also reserve
                    # capacity for a paused/recovering Worker.
                    live = [w for w in list_workers(root) if w.get("process_alive")]
                    claimed = _living_workers(root)
                    manual_recovery = [
                        worker for worker in claimed
                        if not worker.get("process_alive")
                        and str(worker.get("status") or "").upper()
                            in {"FAILED", "NEEDS_ATTENTION", "INTERRUPTED"}
                    ]
                    if manual_recovery:
                        # Keep the claim and slot reserved, but do not report a
                        # dead failed Worker as if it were actively progressing.
                        # Operator action is required; never auto-release or
                        # silently replay a frozen scientific Cycle.
                        no_progress_since = None
                        details = ", ".join(
                            f"{worker.get('worker_id') or 'unknown'} "
                            f"{str(worker.get('status') or 'UNKNOWN').upper()}"
                            + (f" Cycle {worker.get('current_cycle')}"
                               if worker.get('current_cycle') else "")
                            for worker in manual_recovery
                        )
                        save_scheduler(
                            "RUNNING",
                            last_no_progress_at=utcnow().isoformat(),
                            last_no_progress_reason=(
                                f"workers_require_manual_recovery: {details}; "
                                "claim retained; explicit Resume/Release required"
                            )[:500],
                            workers_requiring_manual_recovery=[
                                {
                                    "worker_id": str(worker.get("worker_id") or ""),
                                    "status": str(worker.get("status") or "").upper(),
                                    "cycle": str(worker.get("current_cycle") or ""),
                                    "error_kind": str(worker.get("error_kind") or "")[:120],
                                }
                                for worker in manual_recovery
                            ],
                        )
                    elif live or claimed:
                        no_progress_since = None
                        save_scheduler("RUNNING", last_no_progress_at=utcnow().isoformat(),
                                       last_no_progress_reason="workers_active_or_claimed",
                                       workers_requiring_manual_recovery=[])
                    elif outcome.get("waiting_workers") and not outcome.get("reason"):
                        # G6: dormant Workers wake only on a changed direction
                        # signal or explicit retry_direction; no model calls.
                        no_progress_since = None
                        save_scheduler("RUNNING", last_no_progress_at=utcnow().isoformat(),
                                       last_no_progress_reason="awaiting_research_direction",
                                       waiting_workers=list(outcome["waiting_workers"]),
                                       workers_requiring_manual_recovery=[],
                                       remaining_cycles_by_worker={
                                           name: max(0, max_cycles-count) for name, count in counts.items()})
                    elif outcome.get("eligible") == 0 and not outcome.get("reason"):
                        # No active research frontier is not Goal Completion,
                        # nor grounds to silently discard the remaining quota.
                        # Poll until a frontier is made eligible or stopped.
                        no_progress_since = None
                        save_scheduler("RUNNING", last_no_progress_at=utcnow().isoformat(),
                                       last_no_progress_reason="awaiting_eligible_frontier",
                                       workers_requiring_manual_recovery=[],
                                       remaining_cycles_by_worker={
                                           name: max(0, max_cycles-count) for name, count in counts.items()})
                    else:
                        # A single empty PID snapshot must not terminate the
                        # batch: account failover can span several polls.
                        now = time.monotonic()
                        if no_progress_since is None:
                            no_progress_since = now
                        reason = outcome.get("reason") or "no_worker_started_and_no_live_worker"
                        remaining = {name: max(0, max_cycles-count) for name, count in counts.items()}
                        no_progress_seconds = max(0.0, now - no_progress_since)
                        if no_progress_seconds < SCHEDULER_NO_PROGRESS_GRACE_SECONDS:
                            save_scheduler("RUNNING", last_no_progress_at=utcnow().isoformat(),
                                           last_no_progress_reason=str(reason)[:500],
                                           workers_requiring_manual_recovery=[],
                                           no_progress_seconds=no_progress_seconds,
                                           remaining_cycles_by_worker=remaining)
                        else:
                            save_scheduler("STALLED", ended_at=utcnow().isoformat(),
                                           stall_reason=str(reason)[:500],
                                           workers_requiring_manual_recovery=[],
                                           no_progress_seconds=no_progress_seconds,
                                           remaining_cycles_by_worker=remaining)
                            return 2
            except Exception as exc:
                save_scheduler("FAILED", error_kind=type(exc).__name__,
                               error_detail=str(exc)[:500])
                return 1
            stopping.wait(8)
        save_scheduler("STOPPED", ended_at=utcnow().isoformat(), stop_reason=stop_reason)
    return 0


def stop_scheduler(spec: Any) -> dict[str, Any]:
    """Stop allocation only; existing Worker Cycles continue unaffected."""
    status = scheduler_status(spec.research_root)
    if status["process_alive"]:
        os.kill(int(status["pid"]), signal.SIGTERM)
    return {"status": "stop_requested" if status["process_alive"] else "not_running",
            "existing_worker_cycles_unaffected": True}


if __name__ == "__main__":
    raise SystemExit(main())
