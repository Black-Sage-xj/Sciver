from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import __version__

SCHEMA = "tm-observer/state/v1"
IGNORED_DIRS = {
    ".git",
    "__pycache__",
    "load-snapshots",
    ".tmlab",
    ".pytest_cache",
}
TERMINAL_STATES = {
    "OVER",
    "COMPLETED",
    "STOPPED",
    "NEEDS_ATTENTION",
    "CANCELLED",
    "FAILED",
    "FAILED_RECOVERY",
    "INTERRUPTED",
    "PAUSED",
}


@dataclass(frozen=True)
class Fact:
    value: Any
    source: str
    field: str
    method: str = "direct"

    def provenance(self) -> dict[str, str]:
        return {"source": self.source, "field": self.field, "method": self.method}


@dataclass(frozen=True)
class TaskLocation:
    task_root: Path
    research_root: Path
    binding_path: Path
    config_path: Path | None
    config: dict[str, Any]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _event_tail(path: Path, limit: int = 300) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _latest_stamp(values: Iterable[Any]) -> str | None:
    pairs = [(dt, raw) for raw in values if (dt := _iso(raw)) is not None]
    if not pairs:
        return None
    dt, _ = max(pairs, key=lambda item: item[0])
    return dt.astimezone().isoformat(timespec="seconds")


_CYCLE_DIR = re.compile(r"(?:([1-9][0-9]*)|cycle-([0-9]+))\Z")


def _cycle_key(path: Path) -> tuple[int, int, str] | None:
    """Current numeric Cycle directories supersede equal-number legacy ones.

    This mirrors the SDK's modern Cycle numbering without importing its
    runtime into the read-only Observer process.
    """
    match = _CYCLE_DIR.fullmatch(path.name)
    if match is None:
        return None
    number = int(match.group(1) or match.group(2))
    return (number, 1 if match.group(1) is not None else 0, path.name)


def _proc_start_ticks(pid: int | None) -> int | None:
    if not pid:
        return None
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        tail = text[text.rfind(")") + 2 :].split()
        return int(tail[19])
    except (OSError, ValueError, IndexError):
        return None


def _pid_alive(pid: int | None, expected_start_ticks: int | None = None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
        parts = raw[raw.rfind(")") + 2:].split()
        if not parts or parts[0] in {"Z", "X", "x"}:
            return False
        if expected_start_ticks is not None:
            return len(parts) > 19 and int(parts[19]) == int(expected_start_ticks)
    except (OSError, ValueError, IndexError):
        return False
    return expected_start_ticks is None


_ACTIVE_WORKER_STATES = frozenset({"RUNNING", "STARTING", "PAUSE_REQUESTED", "STOP_REQUESTED"})


def _verified_active_worker(research_root: Path, entry: dict[str, Any], worker_id: str) -> bool:
    """A persisted RUNNING label is not liveness: verify Worker PID and Cycle ownership.

    In multi-Worker mode a different Worker may own the Task's newest Cycle.
    Use the registered Worker's own Cycle instead of the global newest Cycle.
    """
    if (entry.get("schema") != "theory-master/worker-runtime/v1"
            or entry.get("worker_id") != worker_id
            or str(entry.get("status") or "").upper() not in _ACTIVE_WORKER_STATES):
        return False
    try:
        pid = int(entry.get("pid"))
        ticks = int(entry.get("pid_start_ticks"))
    except (ValueError, TypeError):
        return False
    if pid <= 0 or ticks < 0 or not _pid_alive(pid, ticks):
        return False
    cycle = str(entry.get("current_cycle") or "")
    if not re.fullmatch(r"[1-9][0-9]*", cycle):
        return False
    cycle_root = research_root / cycle
    if not cycle_root.is_dir() or cycle_root.is_symlink():
        return False
    science_path = cycle_root / "research_state.json"
    if science_path.is_symlink():
        return False
    science = _safe_json(science_path)
    if (science.get("worker_id") != worker_id
            or str(science.get("status") or "").lower() not in {"active", "running"}):
        return False
    return True


def _walk_task_bindings(search_root: Path) -> list[Path]:
    found: list[Path] = []
    for current, dirs, files in os.walk(search_root):
        dirs[:] = [
            name
            for name in dirs
            if name not in IGNORED_DIRS and not name.startswith(".git")
        ]
        current_path = Path(current)
        # Nested .theory-master/research mirrors are implementation artifacts,
        # not independent operator-facing tasks.
        if ".theory-master" in current_path.parts and "research" in current_path.parts:
            dirs[:] = []
            continue
        if current_path.name == ".theory-master" and "task.json" in files:
            found.append(current_path / "task.json")
            dirs[:] = []
    return sorted(found)


def _resolve_from_task_root(task_root: Path, raw: Any) -> Path | None:
    if raw is None or str(raw).strip() == "":
        return None
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = task_root / path
    return path.resolve()


def discover_tasks(search_root: Path) -> list[TaskLocation]:
    by_research_root: dict[Path, TaskLocation] = {}
    for binding_path in _walk_task_bindings(search_root):
        task_root = binding_path.parent.parent.resolve()
        binding = _safe_json(binding_path)
        config_path = _resolve_from_task_root(task_root, binding.get("config_path"))
        config = _safe_json(config_path) if config_path is not None else {}
        research_root = _resolve_from_task_root(task_root, config.get("research_root")) or task_root
        candidate = TaskLocation(
            task_root=task_root,
            research_root=research_root,
            binding_path=binding_path.resolve(),
            config_path=config_path,
            config=config,
        )
        previous = by_research_root.get(research_root)
        # Multiple operator-facing aliases may point at one persisted research
        # root. Prefer the canonical binding whose task_root is the research
        # root itself; otherwise keep the first deterministic binding.
        if previous is None or (
            candidate.task_root == candidate.research_root
            and previous.task_root != previous.research_root
        ):
            by_research_root[research_root] = candidate
    return sorted(by_research_root.values(), key=lambda item: str(item.task_root))


def discover_task_roots(search_root: Path) -> list[Path]:
    """Compatibility helper returning operator-facing task roots."""
    return [item.task_root for item in discover_tasks(search_root)]


def _task_display_name(location: TaskLocation) -> str:
    explicit = location.config.get("task_name") or location.config.get("name")
    if explicit:
        return str(explicit)
    root = location.task_root
    if root.name == "task" and root.parent.name.startswith("run-") and root.parent.parent.name == "runs":
        experiment = root.parent.parent.parent.name
        return f"{experiment}/{root.parent.name}"
    return root.name


def _git_info(search_root: Path) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            proc = subprocess.run(
                ["git", "-C", str(search_root), *args],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()

    head = run("rev-parse", "HEAD")
    branch = run("branch", "--show-current")
    dirty_text = run("status", "--porcelain")
    return {
        "git_head": head,
        "git_branch": branch or None,
        "git_dirty": None if dirty_text is None else bool(dirty_text),
        "provenance": {
            "git_head": {"source": str(search_root / ".git"), "field": "HEAD", "method": "git"},
            "git_branch": {"source": str(search_root / ".git"), "field": "branch", "method": "git"},
            "git_dirty": {"source": str(search_root), "field": "worktree", "method": "git"},
        },
    }


def collect_git_info(search_root: Path) -> dict[str, Any]:
    """Collect slow repository metadata separately from live task state."""
    return _git_info(search_root)


def _choose(
    provenance: dict[str, dict[str, str]],
    key: str,
    choices: list[tuple[dict[str, Any], str, str, str]],
) -> Any:
    for data, field, source, method in choices:
        value = data.get(field)
        if value is not None and value != "":
            provenance[key] = Fact(value, source, field, method).provenance()
            return value
    provenance[key] = {"source": "none", "field": "none", "method": "unknown"}
    return None


def _last_event_facts(events: list[dict[str, Any]], source: str) -> tuple[Any, Any, Any]:
    active_agent = None
    token_total = None
    last_ts = None
    for event in events:
        if event.get("ts"):
            last_ts = event.get("ts")
        role = event.get("role") or event.get("agent")
        kind = str(event.get("event") or "")
        if kind in {"AGENT_STARTED", "LLM_TURN_STARTED"} and role:
            active_agent = role
        elif kind in {"AGENT_COMPLETED", "AGENT_FAILED", "LLM_TURN_COMPLETED"}:
            # run_state is authoritative for current activity; event-derived
            # active_agent is only a fallback and is cleared on terminal agent events.
            active_agent = None
        if event.get("tokens_total") is not None:
            token_total = event.get("tokens_total")
    return active_agent, token_total, last_ts


def collect_run(task: TaskLocation | Path) -> dict[str, Any]:
    if isinstance(task, TaskLocation):
        location = task
    else:
        task_root = task.resolve()
        binding_path = task_root / ".theory-master" / "task.json"
        binding = _safe_json(binding_path)
        config_path = _resolve_from_task_root(task_root, binding.get("config_path"))
        config = _safe_json(config_path) if config_path is not None else {}
        research_root = _resolve_from_task_root(task_root, config.get("research_root")) or task_root
        location = TaskLocation(task_root, research_root, binding_path, config_path, config)

    task_root = location.task_root
    research_root = location.research_root
    binding_path = location.binding_path
    binding = _safe_json(binding_path)
    config = location.config
    research_runtime_path = research_root / ".theory-master" / "runtime.json"
    task_runtime_path = task_root / ".theory-master" / "runtime.json"
    runtime_path = research_runtime_path if research_runtime_path.is_file() else task_runtime_path
    runtime = _safe_json(runtime_path)

    # The SDK now stores Cycles as research/1, research/2, ... . Keeping only
    # the historical cycle-* glob caused active tasks to appear Cycle-less.
    cycles = sorted(
        (p for p in research_root.iterdir() if p.is_dir() and _cycle_key(p) is not None)
        if research_root.is_dir() else (),
        key=_cycle_key,
    )
    cycle_dir = cycles[-1] if cycles else None
    run_state_path = cycle_dir / "run_state.json" if cycle_dir else None
    research_state_path = cycle_dir / "research_state.json" if cycle_dir else None
    events_path = cycle_dir / "runtime_events.jsonl" if cycle_dir else None
    run_state = _safe_json(run_state_path) if run_state_path else {}
    research_state = _safe_json(research_state_path) if research_state_path else {}
    events = _event_tail(events_path) if events_path else []
    event_agent, event_tokens, event_ts = _last_event_facts(events, str(events_path) if events_path else "none")

    provenance: dict[str, dict[str, str]] = {}
    run_source = str(run_state_path) if run_state_path else "none"
    research_source = str(research_state_path) if research_state_path else "none"
    runtime_source = str(runtime_path)
    binding_source = str(binding_path)
    events_source = str(events_path) if events_path else "none"

    run_id = _choose(
        provenance,
        "run_id",
        [
            (run_state, "master_id", run_source, "direct"),
            (run_state, "master_run_id", run_source, "fallback"),
            (runtime, "master_id", runtime_source, "fallback"),
            (config, "task_id", str(location.config_path) if location.config_path else binding_source, "config_fallback"),
        ],
    )
    task_name = _task_display_name(location)
    task_name_source = str(location.config_path) if location.config_path else binding_source
    observer_id = hashlib.sha256(str(research_root).encode("utf-8")).hexdigest()[:16]
    provenance["observer_id"] = Fact(observer_id, str(research_root), "canonical research_root", "derived_hash").provenance()
    provenance["task_name"] = Fact(task_name, task_name_source, "task identity", "derived").provenance()
    provenance["task_root"] = Fact(str(task_root), binding_source, "task_root", "direct").provenance()
    provenance["research_root"] = Fact(str(research_root), task_name_source, "research_root", "direct" if config.get("research_root") else "fallback").provenance()

    cycle = cycle_dir.name if cycle_dir else None
    provenance["cycle"] = Fact(cycle, str(cycle_dir) if cycle_dir else "none", "directory", "direct" if cycle else "unknown").provenance()

    raw_status = _choose(
        provenance,
        "raw_status",
        [
            (run_state, "status", run_source, "direct"),
            (research_state, "status", research_source, "fallback"),
            (runtime, "state", runtime_source, "fallback"),
        ],
    )
    raw_status_upper = str(raw_status).upper() if raw_status is not None else None
    # Old Cycle artifacts used COMPLETED for ordinary Cycle closure. Preserve the
    # raw value for provenance, but expose the current Cycle-level state as OVER.
    cycle_status_upper = "OVER" if raw_status_upper == "COMPLETED" and cycle_dir is not None else raw_status_upper

    phase = _choose(
        provenance,
        "phase",
        [
            (run_state, "phase", run_source, "direct"),
            (research_state, "phase", research_source, "fallback"),
        ],
    )

    active_agent = _choose(
        provenance,
        "active_agent",
        [
            (run_state, "current_agent", run_source, "direct"),
            (run_state, "current_turn_role", run_source, "fallback"),
            (research_state, "active_role", research_source, "fallback"),
            ({"event_agent": event_agent}, "event_agent", events_source, "event_fallback"),
        ],
    )

    pid = _choose(
        provenance,
        "pid",
        [
            (runtime, "runner_pid", runtime_source, "direct"),
            (runtime, "supervisor_pid", runtime_source, "fallback"),
            (run_state, "owner_pid", run_source, "fallback"),
            (research_state, "owner_pid", research_source, "fallback"),
        ],
    )
    try:
        pid_int = int(pid) if pid is not None else None
    except (TypeError, ValueError):
        pid_int = None
    expected_ticks = runtime.get("runner_start_ticks") if runtime.get("runner_pid") == pid else runtime.get("supervisor_start_ticks")
    pid_alive = _pid_alive(pid_int, expected_ticks)
    provenance["pid_alive"] = {
        "source": f"/proc/{pid_int}" if pid_int else "/proc",
        "field": "process_identity",
        "method": "derived",
    }

    execution_active = _choose(
        provenance,
        "execution_active",
        [
            (run_state, "execution_active", run_source, "direct"),
            (research_state, "execution_active", research_source, "fallback"),
        ],
    )

    # Task List aggregates *all* registered Workers, not only the Worker
    # owning the newest numeric Cycle. Otherwise a completed Worker-2 Cycle
    # can mask a still-live Worker-1 working on an older Cycle.
    live_worker = None
    selected_worker = ""
    workers_root = research_root / ".theory-master" / "workers"
    if workers_root.is_dir() and not workers_root.is_symlink():
        for worker_runtime in sorted(workers_root.glob("worker-*/runtime.json")):
            worker_id = worker_runtime.parent.name
            if (not re.fullmatch(r"worker-[1-9][0-9]*", worker_id)
                    or worker_runtime.is_symlink() or worker_runtime.parent.is_symlink()):
                continue
            entry = _safe_json(worker_runtime)
            if _verified_active_worker(research_root, entry, worker_id):
                if live_worker is None or int(entry["current_cycle"]) > int(live_worker["current_cycle"]):
                    live_worker, selected_worker = entry, worker_id
        if live_worker is not None:
            pid_int, pid_alive, execution_active = int(live_worker["pid"]), True, True
            provenance["pid"] = {"source": str(workers_root / selected_worker / "runtime.json"),
                "field": "pid", "method": "worker_identity_verified"}
            provenance["pid_alive"] = {"source": str(workers_root / selected_worker / "runtime.json"),
                "field": "pid + pid_start_ticks", "method": "worker_identity_verified"}
            provenance["execution_active"] = {"source": str(workers_root / selected_worker / "runtime.json"),
                "field": "Worker current_cycle + process identity", "method": "worker_identity_verified"}
            pid = pid_int
    if live_worker or (bool(execution_active) and pid_alive):
        state = "LIVE"
        provenance["state"] = {
            "source": (str(research_root / ".theory-master" / "workers" / selected_worker / "runtime.json")
                       if live_worker else f"{run_source}; {provenance['pid_alive']['source']}"),
            "field": "verified_worker_identity" if live_worker else "execution_active + process_identity",
            "method": "derived",
        }
    elif cycle_status_upper in {"RUNNING", "ACTIVE", "STARTING", "RECOVERING"} and not pid_alive:
        state = "ORPHANED"
        provenance["state"] = {
            "source": f"{provenance['raw_status']['source']}; {provenance['pid_alive']['source']}",
            "field": "raw_status + process_identity",
            "method": "derived",
        }
    elif cycle_status_upper:
        state = cycle_status_upper
        provenance["state"] = dict(provenance["raw_status"])
        if raw_status_upper == "COMPLETED" and cycle_status_upper == "OVER":
            provenance["state"]["method"] = "legacy_cycle_status_normalization"
    else:
        state = "UNKNOWN"
        provenance["state"] = {"source": "none", "field": "none", "method": "unknown"}

    if state != "LIVE" and not pid_alive:
        # A stale Cycle's current_agent is historical, not an active executor.
        active_agent = None
        provenance["active_agent"] = {
            "source": provenance["state"]["source"],
            "field": "terminal/non-active state",
            "method": "derived_clear",
        }

    started_at = _choose(
        provenance,
        "started_at",
        [
            (run_state, "started_at", run_source, "direct"),
            (run_state, "cycle_started_at", run_source, "fallback"),
            (runtime, "launch_requested_at", runtime_source, "fallback"),
        ],
    )

    tokens_total = _choose(
        provenance,
        "tokens_total",
        [
            (run_state, "project_tokens_total", run_source, "direct"),
            (run_state, "cycle_tokens_total", run_source, "fallback"),
            (run_state, "tokens_total", run_source, "fallback"),
            ({"event_tokens": event_tokens}, "event_tokens", events_source, "event_fallback"),
        ],
    )

    current_round = _choose(
        provenance,
        "current_round",
        [
            (run_state, "investigation_round_count", run_source, "direct"),
            (research_state, "investigation_round", research_source, "fallback"),
            (run_state, "round_count", run_source, "fallback"),
        ],
    )

    last_activity = _latest_stamp(
        [
            runtime.get("heartbeat_at"),
            runtime.get("updated_at"),
            event_ts,
            run_state.get("updated_at"),
            run_state.get("cycle_started_at"),
            started_at,
        ]
    )
    provenance["last_activity_at"] = {
        "source": f"{runtime_source}; {events_source}; {run_source}",
        "field": "latest timestamp",
        "method": "derived",
    }

    warnings: list[dict[str, str]] = []
    if state == "ORPHANED":
        warnings.append(
            {
                "code": "ORPHANED",
                "message": "Persisted run status indicates activity but the recorded process identity is not alive.",
                "source": provenance["state"]["source"],
            }
        )
    if state == "NEEDS_ATTENTION":
        reason = run_state.get("human_attention") or research_state.get("human_attention")
        attention = run_state.get("attention") or research_state.get("attention")
        if not reason and isinstance(attention, dict):
            reason = attention.get("reason") or attention.get("code")
        warnings.append(
            {
                "code": "NEEDS_ATTENTION",
                "message": str(reason or "Sciver persisted NEEDS_ATTENTION."),
                "source": run_source if run_state else research_source,
            }
        )
    if not cycle_dir:
        warnings.append(
            {
                "code": "NO_CYCLE",
                "message": "Task binding exists but no numeric or cycle-* directory was found.",
                "source": binding_source,
            }
        )
    if not run_state and cycle_dir:
        warnings.append(
            {
                "code": "MISSING_RUN_STATE",
                "message": "Latest Cycle has no readable run_state.json.",
                "source": run_source,
            }
        )

    return {
        "observer_id": observer_id,
        "run_id": run_id,
        "task_name": task_name,
        "task_root": str(task_root),
        "research_root": str(research_root),
        "cycle": cycle,
        "cycle_dir": str(cycle_dir) if cycle_dir else None,
        "state": state,
        "raw_status": raw_status_upper,
        "phase": phase,
        "active_agent": active_agent,
        "pid": pid_int,
        "pid_alive": pid_alive,
        "started_at": started_at,
        "last_activity_at": last_activity,
        "tokens_total": tokens_total,
        "current_round": current_round,
        "warnings": warnings,
        "provenance": provenance,
    }


def collect_state(search_root: Path, *, include_git_info: bool = True) -> dict[str, Any]:
    """Collect task state; callers may defer slow Git metadata to a separate refresh."""
    search_root = search_root.expanduser().resolve()
    tasks = discover_tasks(search_root)
    runs = [collect_run(task) for task in tasks]
    runs.sort(
        key=lambda item: (
            _iso(item.get("last_activity_at")) or datetime.min.replace(tzinfo=timezone.utc),
            str(item.get("task_root")),
        ),
        reverse=True,
    )
    if include_git_info:
        git_info = _git_info(search_root)
    else:
        git_info = {
            "git_head": None, "git_branch": None, "git_dirty": None,
            "provenance": {
                "git_head": {"source": str(search_root / ".git"), "field": "HEAD", "method": "deferred"},
                "git_branch": {"source": str(search_root / ".git"), "field": "branch", "method": "deferred"},
                "git_dirty": {"source": str(search_root), "field": "worktree", "method": "deferred"},
            },
        }
    return {
        "schema": SCHEMA,
        "generated_at": _now(),
        "search_root": str(search_root),
        "system": {
            **git_info,
            "observer_version": __version__,
            "task_count": len(runs),
        },
        "runs": runs,
    }


def state_digest(state: dict[str, Any]) -> str:
    payload = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
