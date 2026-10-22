"""Managed TheoryMaster runtime ownership, supervision metadata, and TUI reconnects."""
from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

RUNTIME_SCHEMA = "theory-master/runtime/v1"
HEARTBEAT_INTERVAL_S = 2.0
HEARTBEAT_STALE_S = 12.0
LAUNCH_GRACE_S = 8.0


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _package_root() -> Path:
    return Path(__file__).resolve().parents[1]


_RUNTIME_SOURCE_MODULES = {
    "runtime_manager.py": "theory_master.runtime_manager",
    "worker_runtime.py": "theory_master.worker_runtime",
    "worker_supervisor_dispatch.py": "theory_master.worker_supervisor_dispatch",
    "workflow.py": "theory_master.workflow",
    "workflow_qualification.py": "theory_master.workflow_qualification",
    "canvas_projection.py": "theory_master.canvas_projection",
    "workflow_state.py": "theory_master.workflow_state",
    "skills.py": "theory_master.skills",
}


def _git_source_identity(start: Path) -> tuple[str | None, str | None]:
    """Read Git root/HEAD without spawning a process or interfering with launch guards."""
    for candidate in (start, *start.parents):
        marker = candidate / ".git"
        common_dir: Path | None = None
        if marker.is_dir():
            git_dir = marker.resolve()
            common_dir = git_dir
        elif marker.is_file():
            try:
                value = marker.read_text(encoding="utf-8").strip()
                if not value.lower().startswith("gitdir:"):
                    continue
                raw = value.split(":", 1)[1].strip()
                git_dir = (candidate / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
                common_dir = git_dir
                common_file = git_dir / "commondir"
                if common_file.is_file():
                    raw_common = common_file.read_text(encoding="utf-8").strip()
                    common_dir = (git_dir / raw_common).resolve()
            except OSError:
                continue
        else:
            continue
        try:
            head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        sha = head if len(head) in {40, 64} and all(c in "0123456789abcdefABCDEF" for c in head) else None
        if head.startswith("ref: "):
            ref = head[5:].strip()
            bases = [git_dir] + ([common_dir] if common_dir and common_dir != git_dir else [])
            for base in bases:
                try:
                    value = (base / ref).read_text(encoding="utf-8").strip()
                    if len(value) in {40, 64} and all(c in "0123456789abcdefABCDEF" for c in value):
                        sha = value
                        break
                except OSError:
                    pass
            if sha is None:
                for base in bases:
                    try:
                        packed = (base / "packed-refs").read_text(encoding="utf-8").splitlines()
                    except OSError:
                        continue
                    for line in packed:
                        fields = line.split()
                        if len(fields) == 2 and fields[1] == ref:
                            sha = fields[0]
                            break
                    if sha is not None:
                        break
        return str(candidate.resolve()), sha
    return None, None


def runtime_source_provenance() -> dict[str, Any]:
    """Identify SDK source paths and hashes available to this process, without a model call."""
    package_root = _package_root().resolve()
    module_root = package_root / "theory_master"
    module_paths: dict[str, str] = {}
    module_sha256: dict[str, str] = {}
    for name, import_name in _RUNTIME_SOURCE_MODULES.items():
        loaded = sys.modules.get(import_name)
        loaded_path = getattr(loaded, "__file__", None) if loaded is not None else None
        path = Path(loaded_path).resolve() if loaded_path else (module_root / name).resolve()
        module_paths[name] = str(path)
        try:
            module_sha256[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    expected_module_root = module_root.resolve()
    coherence_errors = [
        f"{name}: {path}" for name, path in module_paths.items()
        if Path(path).parent != expected_module_root
    ]
    git_root, git_sha = _git_source_identity(package_root)
    return {
        "schema": "theory-master/runtime-source-provenance/v1",
        "source_root": str(package_root),
        "sdk_git_root": git_root,
        "sdk_git_sha": git_sha,
        "module_paths": module_paths,
        "module_sha256": module_sha256,
        "source_coherent": not coherence_errors,
        "source_coherence_errors": coherence_errors,
    }


def runtime_dir(research_root: Path) -> Path:
    path = Path(research_root).expanduser().resolve() / ".theory-master"
    path.mkdir(parents=True, exist_ok=True)
    return path


def registry_path(research_root: Path) -> Path:
    return runtime_dir(research_root) / "runtime.json"


def owner_lock_path(research_root: Path) -> Path:
    return runtime_dir(research_root) / "runner.lock"


def launch_lock_path(research_root: Path) -> Path:
    return runtime_dir(research_root) / "launch.lock"


def _registry_lock_path(research_root: Path) -> Path:
    return runtime_dir(research_root) / "runtime.json.lock"


def branch_claim_lock_path(research_root: Path) -> Path:
    """Shared with BranchClaims: serialize old runtime startup vs new claims."""
    return runtime_dir(research_root) / "branch_claims.lock"


def _reject_legacy_when_claimed(research_root: Path) -> None:
    """Called while branch_claims.lock is held; fail closed on registry damage."""
    path = runtime_dir(research_root) / "branch_claims.json"
    if not path.exists():
        return
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != "theory-master/branch-claims/v1":
        raise RuntimeError("invalid branch claim registry; legacy runtime startup blocked")
    claims = value.get("claims")
    if not isinstance(claims, dict):
        raise RuntimeError("invalid branch claims; legacy runtime startup blocked")
    if claims:
        raise RuntimeError("existing multi-Worker branch claims block legacy single-owner runtime")


@contextmanager
def _file_lock(path: Path, *, nonblocking: bool = False) -> Iterator[Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fp = open(path, "a+", encoding="utf-8")
    flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
    try:
        fcntl.flock(fp.fileno(), flags)
    except BlockingIOError:
        fp.close()
        raise
    try:
        yield fp
    finally:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        finally:
            fp.close()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_registry(research_root: Path) -> dict[str, Any]:
    path = registry_path(research_root)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def update_registry(research_root: Path, **fields: Any) -> dict[str, Any]:
    root = Path(research_root).expanduser().resolve()
    with _file_lock(_registry_lock_path(root)):
        payload = load_registry(root)
        if payload.get("schema") != RUNTIME_SCHEMA:
            payload = {"schema": RUNTIME_SCHEMA}
        payload.update(fields)
        # Storage moves can preserve an old runtime.json for provenance.
        # The live registry must always identify its *current* physical root.
        payload["research_root"] = str(root)
        payload["updated_at"] = _now()
        _write_json_atomic(registry_path(root), payload)
        return payload


def process_start_ticks(pid: int | None) -> int | None:
    if not pid:
        return None
    try:
        text = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
        tail = text[text.rfind(")") + 2 :].split()
        return int(tail[19])
    except (OSError, ValueError, IndexError):
        return None


def process_identity_alive(pid: int | None, start_ticks: int | None = None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    # kill(pid, 0) also succeeds for a zombie. A reaped/not-yet-reaped
    # Worker cannot write science, own an account or hold an active slot.
    # Read /proc in one pass so the PID identity and process state agree.
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
        fields = stat[stat.rfind(")") + 2:].split()
        if not fields or fields[0] in {"Z", "X", "x"}:
            return False
        if start_ticks is not None:
            return len(fields) > 19 and int(fields[19]) == int(start_ticks)
    except FileNotFoundError:
        # On Linux a missing /proc entry always means no live process.
        if Path("/proc").is_dir():
            return False
    except (OSError, ValueError, IndexError):
        return False  # Do not mistake an unreadable identity for a live owner.
    return start_ticks is None


def _age_seconds(value: Any) -> float | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds())
    except ValueError:
        return None


def runtime_health(research_root: Path, *, stale_after_s: float = HEARTBEAT_STALE_S) -> dict[str, Any]:
    payload = load_registry(research_root)
    if not payload:
        return {"state": "ABSENT", "active": False, "registry": {}}
    supervisor_pid = payload.get("supervisor_pid")
    supervisor_alive = process_identity_alive(supervisor_pid, payload.get("supervisor_start_ticks"))
    runner_pid = payload.get("runner_pid")
    runner_alive = process_identity_alive(runner_pid, payload.get("runner_start_ticks"))
    heartbeat_age = _age_seconds(payload.get("heartbeat_at"))
    state = str(payload.get("state") or "UNKNOWN").upper()
    launch_age = _age_seconds(payload.get("launch_requested_at"))
    if supervisor_alive:
        if heartbeat_age is not None and heartbeat_age > stale_after_s:
            health = "UNRESPONSIVE"
        else:
            health = "RUNNING" if runner_alive or state in {"RUNNING", "STARTING", "RECOVERING"} else state
        return {"state": health, "active": True, "supervisor_alive": True, "runner_alive": runner_alive, "heartbeat_age_s": heartbeat_age, "registry": payload}
    if runner_alive:
        health = "RUNNING" if payload.get("launcher") == "foreground" else "UNSUPERVISED"
        return {"state": health, "active": True, "supervisor_alive": False, "runner_alive": True, "heartbeat_age_s": heartbeat_age, "registry": payload}
    if state == "LAUNCHING" and launch_age is not None and launch_age <= LAUNCH_GRACE_S:
        return {"state": "LAUNCHING", "active": True, "supervisor_alive": False, "runner_alive": False, "heartbeat_age_s": heartbeat_age, "registry": payload}
    terminal = state in {"OVER", "COMPLETE", "COMPLETED", "STOPPED", "NEEDS_ATTENTION", "CANCELLED", "FAILED_RECOVERY"}
    return {"state": state if terminal else "ORPHANED", "active": False, "supervisor_alive": False, "runner_alive": False, "heartbeat_age_s": heartbeat_age, "registry": payload}


@contextmanager
def foreground_runtime(research_root: Path, *, master_id: int, operation: str) -> Iterator[dict[str, Any]]:
    """Register a foreground runner in the normal TheoryMaster control plane.

    TMLab and other isolated launchers may need the workflow to stay in the
    current process tree, but the operator-facing runtime registry must still
    expose a live PID, heartbeat, recovery episode, and accumulated active time.
    Offline gaps are deliberately excluded from ``active_elapsed_s``.
    """
    root = Path(research_root).expanduser().resolve()
    lock_path = owner_lock_path(root)
    try:
        owner = _file_lock(lock_path, nonblocking=True)
        lock = owner.__enter__()
    except BlockingIOError as exc:
        raise RuntimeError("TheoryMaster foreground runtime refused duplicate owner: runner.lock is held") from exc

    pid = os.getpid()
    ticks = process_start_ticks(pid)
    prior = load_registry(root)
    base_elapsed = max(0.0, float(prior.get("active_elapsed_s") or 0.0))
    segments = list(prior.get("runtime_segments") or [])
    if prior.get("active_segment_started_at"):
        prior_base = max(0.0, float(prior.get("active_segment_base_elapsed_s") or 0.0))
        segments.append({
            "started_at": prior.get("active_segment_started_at"),
            "ended_at": prior.get("heartbeat_at") or prior.get("updated_at"),
            "operation": prior.get("requested_operation"),
            "elapsed_s": max(0.0, base_elapsed - prior_base),
            "reason": "orphaned_before_resume",
        })
    segment_started_at = _now()
    segment_started_mono = time.monotonic()
    resume_count = int(prior.get("resume_count") or 0) + (1 if operation == "resume" else 0)
    launch_id = uuid.uuid4().hex
    try:
        with _file_lock(branch_claim_lock_path(root)):
            _reject_legacy_when_claimed(root)
            update_registry(
                root,
                state="RUNNING",
                master_id=int(master_id),
                launch_id=launch_id,
                requested_operation=operation,
                launcher="foreground",
                supervisor_pid=None,
                supervisor_start_ticks=None,
                runner_pid=pid,
                runner_start_ticks=ticks,
                heartbeat_at=_now(),
                current_cycle=latest_cycle_name(root),
                active_elapsed_s=base_elapsed,
                active_segment_started_at=segment_started_at,
                active_segment_base_elapsed_s=base_elapsed,
                runtime_segments=segments,
                resume_count=resume_count,
            )
    except BaseException:
        owner.__exit__(None, None, None)
        raise

    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(HEARTBEAT_INTERVAL_S):
            elapsed = base_elapsed + max(0.0, time.monotonic() - segment_started_mono)
            update_registry(
                root,
                state="RUNNING",
                runner_pid=pid,
                runner_start_ticks=ticks,
                heartbeat_at=_now(),
                current_cycle=latest_cycle_name(root),
                active_elapsed_s=elapsed,
            )

    thread = threading.Thread(target=beat, name="theory-master-foreground-heartbeat", daemon=True)
    thread.start()
    terminal_state = "STOPPED"
    try:
        yield load_registry(root)
    except BaseException:
        terminal_state = "FAILED"
        raise
    finally:
        stop.set()
        thread.join(timeout=max(1.0, HEARTBEAT_INTERVAL_S))
        total = base_elapsed + max(0.0, time.monotonic() - segment_started_mono)
        closed_segments = list(segments)
        closed_segments.append({
            "started_at": segment_started_at,
            "ended_at": _now(),
            "operation": operation,
            "elapsed_s": max(0.0, total - base_elapsed),
            "reason": "graceful_exit" if terminal_state == "STOPPED" else "runner_failed",
        })
        update_registry(
            root,
            state=terminal_state,
            runner_pid=None,
            runner_start_ticks=None,
            heartbeat_at=_now(),
            current_cycle=latest_cycle_name(root),
            active_elapsed_s=total,
            active_segment_started_at=None,
            active_segment_base_elapsed_s=None,
            runtime_segments=closed_segments,
        )
        owner.__exit__(None, None, None)


def latest_cycle_name(research_root: Path) -> str | None:
    try:
        from theory_master.specification import latest_cycle_dir
        cycle = latest_cycle_dir(Path(research_root), task_mode=True)
    except Exception:
        return None
    return cycle.name if cycle is not None else None


def legacy_live_owner(spec: Any) -> dict[str, Any] | None:
    try:
        from theory_master.specification import latest_cycle_dir
        cycle = latest_cycle_dir(spec.research_root, task_mode=bool(getattr(spec, "task_mode", False)))
        if cycle is None:
            return None
        path = cycle / "research_state.json"
        if not path.is_file():
            return None
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    pid = state.get("owner_pid")
    if bool(state.get("execution_active")) and process_identity_alive(pid):
        return {"pid": int(pid), "cycle": cycle.name, "state": state}
    return None


def unit_name(research_root: Path, master_id: int) -> str:
    digest = hashlib.sha1(str(Path(research_root).resolve()).encode("utf-8")).hexdigest()[:10]
    return f"theory-master-{int(master_id)}-{digest}"


def runtime_python() -> str:
    """Choose an interpreter that can create account-backed Codex threads.

    The desktop shell can expose a lightweight Python without the optional
    ``openai_codex`` SDK.  A managed supervisor must not inherit that
    interpreter merely because it launched the CLI.
    """
    requested = os.environ.get("THEORYMASTER_RUNTIME_PYTHON", "").strip()
    candidates = [requested] if requested else []
    candidates.extend([sys.executable, str(Path.home() / "miniconda3/bin/python")])
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        if path.resolve() == Path(sys.executable).resolve():
            if importlib.util.find_spec("_ssl") and importlib.util.find_spec("openai_codex"):
                return str(path)
            continue
        return str(path)
    raise RuntimeError(
        "No TheoryMaster SDK runtime is available; set THEORYMASTER_RUNTIME_PYTHON "
        "to a Python with _ssl and openai_codex installed."
    )


def _supervisor_command(spec: Any, *, operation: str, identifier: str, launch_id: str, model: str | None, provider: str | None, routes: list[str] | None, cycle_audit: bool | None, error_repair: bool | None, attention_takeover: bool | None, check_literature: bool) -> list[str]:
    cmd = [runtime_python(), "-m", "theory_master.supervisor", "--task-root", str(Path(spec.research_root).resolve()), "--operation", operation, "--identifier", str(identifier), "--launch-id", launch_id]
    if model:
        cmd += ["--model", model]
    if provider:
        cmd += ["--provider", provider]
    for route in routes or []:
        cmd += ["--route", route]
    if cycle_audit is True:
        cmd.append("--cycle-audit")
    elif cycle_audit is False:
        cmd.append("--no-cycle-audit")
    if error_repair is True:
        cmd.append("--error-repair")
    elif error_repair is False:
        cmd.append("--no-error-repair")
    if attention_takeover is True:
        cmd.append("--attention-takeover")
    elif attention_takeover is False:
        cmd.append("--no-attention-takeover")
    if check_literature:
        cmd.append("--check-literature")
    return cmd


def _runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    parts = [str(_package_root()), str(_repo_root()), str(_repo_root() / "gs_study/_tool")]
    existing = env.get("PYTHONPATH")
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = ":".join(parts)
    env.setdefault("THEORY_MASTER_EMAIL_TERMINAL", "1")
    return env


def launch_managed_runtime(spec: Any, *, operation: str, identifier: str, model: str | None = None, provider: str | None = None, routes: list[str] | None = None, cycle_audit: bool | None = None, error_repair: bool | None = None, attention_takeover: bool | None = None, check_literature: bool = False) -> dict[str, Any]:
    root = Path(spec.research_root).resolve()
    with _file_lock(launch_lock_path(root)), _file_lock(branch_claim_lock_path(root)):
        _reject_legacy_when_claimed(root)
        health = runtime_health(root)
        if health.get("active"):
            return {"status": "already_running", "health": health, "registry": health.get("registry") or {}}
        legacy = legacy_live_owner(spec)
        if legacy is not None:
            payload = update_registry(root, state="LEGACY_RUNNING", master_id=int(getattr(spec, "master_id", 0) or 0), runner_pid=legacy["pid"], runner_start_ticks=process_start_ticks(legacy["pid"]), current_cycle=legacy["cycle"], heartbeat_at=None, launcher="legacy-adopted")
            return {"status": "already_running", "legacy": True, "registry": payload}
        launch_id = uuid.uuid4().hex
        initial_cycle = latest_cycle_name(root)
        master_id = int(getattr(spec, "master_id", 0) or 0)
        unit = unit_name(root, master_id)
        # A new launch_id is a fresh supervisor episode. Reset the
        # per-launch attempt/recovery counters so an ordinary future "run"
        # is not mistaken for a restarted supervisor and silently converted
        # to "resume". The supervisor increments supervisor_attempt itself;
        # only restarts within this same launch_id should see attempt > 1.
        update_registry(
            root,
            state="LAUNCHING",
            master_id=master_id,
            launch_id=launch_id,
            launch_requested_at=_now(),
            requested_operation=operation,
            initial_latest_cycle=initial_cycle,
            current_cycle=initial_cycle,
            supervisor_pid=None,
            supervisor_start_ticks=None,
            runner_pid=None,
            runner_start_ticks=None,
            heartbeat_at=None,
            systemd_unit=unit,
            supervisor_attempt=0,
            recovery_count=0,
        )
        cmd = _supervisor_command(spec, operation=operation, identifier=identifier, launch_id=launch_id, model=model, provider=provider, routes=routes, cycle_audit=cycle_audit, error_repair=error_repair, attention_takeover=attention_takeover, check_literature=check_literature)
        env = _runtime_env()
        launched = False
        detail = ""
        if shutil.which("systemd-run") and shutil.which("systemctl"):
            systemd_cmd = ["systemd-run", "--user", f"--unit={unit}", "--collect", "--property=Restart=on-failure", "--property=RestartSec=2s", "--property=StartLimitBurst=3", "--property=StartLimitIntervalSec=120s", "--property=KillMode=control-group", f"--setenv=PATH={env.get('PATH', '')}", f"--setenv=PYTHONPATH={env['PYTHONPATH']}", "--setenv=THEORY_MASTER_EMAIL_TERMINAL=1", *cmd]
            proc = subprocess.run(systemd_cmd, cwd=_repo_root(), text=True, capture_output=True, check=False)
            if proc.returncode == 0:
                launched = True
                detail = (proc.stdout or "").strip()
                update_registry(root, launcher="systemd-user", systemd_unit=unit)
            else:
                detail = (proc.stderr or proc.stdout or "systemd-run failed").strip()
        if not launched:
            log_path = runtime_dir(root) / "supervisor.log"
            log = open(log_path, "a", encoding="utf-8")
            proc = subprocess.Popen(cmd, cwd=_repo_root(), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            log.close()
            detail = f"detached supervisor pid={proc.pid}; systemd detail={detail}".strip()
            update_registry(root, launcher="detached", supervisor_pid=proc.pid, supervisor_start_ticks=process_start_ticks(proc.pid))
        return {"status": "started", "launch_id": launch_id, "unit": unit, "detail": detail, "command": cmd}


def _tmux_windows() -> list[dict[str, str]]:
    proc = subprocess.run(["tmux", "list-windows", "-a", "-F", "#{window_id}\t#{session_name}\t#{window_name}\t#{pane_pid}"], text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        return []
    rows = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 4:
            rows.append({"window_id": parts[0], "session": parts[1], "window_name": parts[2], "pane_pid": parts[3]})
    return rows


def _window_by_id(window_id: str | None) -> dict[str, str] | None:
    if not window_id:
        return None
    for row in _tmux_windows():
        if row["window_id"] == window_id:
            return row
    return None


def _ensure_tmux_session(session: str) -> None:
    probe = subprocess.run(["tmux", "has-session", "-t", session], text=True, capture_output=True, check=False)
    if probe.returncode == 0:
        return
    proc = subprocess.run(["tmux", "new-session", "-d", "-s", session, "-n", "shell"], text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"cannot create tmux session {session}")


def ensure_canonical_tui(spec: Any, *, tmux_session: str | None = None) -> dict[str, Any]:
    root = Path(spec.research_root).resolve()
    registry = load_registry(root)
    existing = _window_by_id(((registry.get("tui") or {}).get("window_id") if isinstance(registry.get("tui"), dict) else None))
    if existing is not None:
        return {"status": "existing", **existing}
    legacy = legacy_live_owner(spec)
    if legacy is not None:
        for row in _tmux_windows():
            if str(row.get("pane_pid")) == str(legacy["pid"]):
                update_registry(
                    root,
                    state="LEGACY_RUNNING",
                    master_id=int(getattr(spec, "master_id", 0) or 0),
                    runner_pid=legacy["pid"],
                    runner_start_ticks=process_start_ticks(legacy["pid"]),
                    current_cycle=legacy["cycle"],
                    launcher="legacy-adopted",
                    tui={**row, "created_at": _now(), "adopted_legacy": True},
                )
                return {"status": "adopted", **row}
    if shutil.which("tmux") is None:
        raise RuntimeError("tmux is required for the canonical TheoryMaster TUI")
    session = tmux_session or os.environ.get("THEORY_MASTER_TMUX_SESSION") or "agent"
    _ensure_tmux_session(session)
    before = {row["window_id"] for row in _tmux_windows()}
    window_name = f"TheoryMaster-{int(getattr(spec, 'master_id', 0) or 0)}"
    env = _runtime_env()
    # Reconnect through the task binding. A TUI is an observer
    # and must not become unavailable merely because the human-facing Canvas
    # projection is temporarily stale relative to research_plan.md.  Keep both
    # stdout and stderr attached to the tmux pty: Textual uses the terminal
    # streams for screen rendering, so redirecting stderr makes the pane blank.
    command = (
        f"cd {shlex.quote(str(root))} && exec env "
        f"PYTHONPATH={shlex.quote(env['PYTHONPATH'])} "
        f"{shlex.quote(sys.executable)} -m theory_master tui "
        f"{int(getattr(spec, 'master_id', 0) or 0)} --direct"
    )
    proc = subprocess.run(["tmux", "new-window", "-d", "-t", f"{session}:", "-n", window_name, command], cwd=root, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "failed to create TheoryMaster TUI window")
    after = _tmux_windows()
    created = [row for row in after if row["window_id"] not in before and row["session"] == session]
    if not created:
        created = [row for row in after if row["session"] == session and row["window_name"] == window_name]
    if not created:
        raise RuntimeError("tmux created the TUI window but its window id could not be resolved")
    row = created[-1]
    # Catch immediate startup failures before persisting a dead canonical ID.
    time.sleep(0.2)
    live = _window_by_id(row["window_id"])
    if live is None:
        update_registry(root, tui=None)
        raise RuntimeError("TheoryMaster TUI exited during startup")
    update_registry(root, tui={**live, "created_at": _now()})
    return {"status": "created", **live}


def focus_tui_window(target: dict[str, Any], *, allow_exec: bool = True) -> int:
    window_id = str(target.get("window_id") or "")
    session = str(target.get("session") or "")
    if not window_id or not session:
        raise ValueError("canonical TUI target is incomplete")
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if os.environ.get("TMUX") and interactive:
        proc = subprocess.run(["tmux", "switch-client", "-t", window_id], text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"cannot switch to tmux window {window_id}")
        return 0
    if allow_exec and interactive:
        subprocess.run(["tmux", "select-window", "-t", window_id], check=False)
        os.execvp("tmux", ["tmux", "attach-session", "-t", session])
    print(f"TheoryMaster TUI: {session}:{target.get('window_name') or window_id} ({window_id})")
    return 0


def reconnect_tui(spec: Any, *, focus: bool = True) -> dict[str, Any]:
    target = ensure_canonical_tui(spec)
    if focus:
        if (target.get("status") in {"existing", "adopted"}
                and sys.stdin.isatty() and sys.stdout.isatty()):
            # A reused tmux pane does not re-run Textual on_mount. Trigger
            # the same notice on each interactive re-entry without restarting
            # the task or its TUI; fresh windows display it on mount instead.
            subprocess.run(
                ["tmux", "send-keys", "-t", target["window_id"], "F12"],
                text=True, capture_output=True, check=False,
            )
        focus_tui_window(target)
    return target
