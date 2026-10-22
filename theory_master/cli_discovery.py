from __future__ import annotations

"""Task discovery, resume-target resolution, and run inventory for the CLI."""

import json
import os
from datetime import datetime
from pathlib import Path

from .specification import TaskSpec, cycle_number, latest_cycle_dir
from .task import TaskBinding, load_discovered, master_id_for_root, summary as task_summary

def spec_from_task(task_root: Path | None = None, master_id: str | int | None = None) -> TaskSpec:
    """Resolve the current task, or an explicit Task ID from the repository search root.

    Commands without an ID remain cwd-scoped.  Once an operator supplies a Task ID,
    that ID is authoritative and may be resolved outside the current working
    directory, matching resume/tui behavior.
    """
    if task_root is not None:
        binding = TaskBinding.load(task_root)
        if master_id is not None and str(master_id).strip() != str(binding.master_id):
            raise ValueError(f"task ID {master_id!r} is not registered in {binding.task_root}")
        return TaskSpec.from_task_root(binding.task_root)

    if master_id is None:
        binding = load_discovered()
        return TaskSpec.from_task_root(binding.task_root)

    wanted = str(master_id).strip()
    try:
        binding = load_discovered()
    except (OSError, ValueError, json.JSONDecodeError):
        return spec_from_resume_identifier(wanted, default_search_root())

    if wanted == str(binding.master_id):
        return TaskSpec.from_task_root(binding.task_root)
    return spec_from_resume_identifier(wanted, default_search_root())


def default_search_root() -> Path:
    """Return a stable discovery root independent of the caller's cwd."""
    configured = (os.environ.get("THEORY_MASTER_SEARCH_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    source = Path(__file__).resolve()
    repositories = [parent for parent in source.parents if (parent / ".git").exists()]
    # A standalone TheoryMaster repository can also be checked out as a
    # gel_network submodule. Task workspaces stay in the *parent* research
    # project, outside the source package; prefer that Git root when present.
    for parent in reversed(repositories):
        if (parent / "gs_study").is_dir():
            return parent
    if repositories:
        return repositories[0]
    return Path.cwd().resolve()


def _ignored_discovery_path(path: Path) -> bool:
    """Ignore archived/snapshot copies that are not canonical runnable state."""
    parts = set(path.parts)
    return bool({".git", ".tmlab", "load-snapshots", "interrupted_runs"} & parts)


_DISCOVERY_PRUNE_DIRS = {".git", ".tmlab", "load-snapshots", "interrupted_runs"}


def _discovery_paths(root: Path) -> tuple[list[Path], list[Path]]:
    """Find task pointers and run states in one pruned filesystem walk."""
    task_paths: list[Path] = []
    state_paths: list[Path] = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in _DISCOVERY_PRUNE_DIRS]
        base_path = Path(base)
        if base_path.name == ".theory-master" and "task.json" in files:
            task_paths.append(base_path / "task.json")
        if "run_state.json" in files:
            state_paths.append(base_path / "run_state.json")
    return task_paths, state_paths


def enter_task() -> int:
    binding = load_discovered()
    print(task_summary(binding))
    from .core import latest_cycle_dir
    latest = latest_cycle_dir(binding.task_root, task_mode=True)
    print(f"Latest Cycle   : {latest.name if latest else '-'}")
    if latest is not None:
        state_path = latest / "research_state.json"
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            print(f"Latest status  : {state.get('status', '-')} / {state.get('phase', '-')}")
    return 0


def spec_from_resume_identifier(identifier: str | int, search_root: Path | None = None) -> TaskSpec:
    """Resolve a resume target through a task pointer or historical state."""
    root = (search_root or default_search_root()).resolve()
    wanted = str(identifier).strip()
    task_bindings: dict[Path, TaskBinding] = {}
    for binding_path in root.rglob(".theory-master/task.json"):
        try:
            binding = TaskBinding.load(binding_path.parent.parent)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        task_bindings[binding.task_root.resolve()] = binding

    # Human-facing Task IDs are authoritative. A numeric Cycle directory such as
    # `1` exists under many tasks, so Cycle-name fallback must never make an
    # otherwise unique Task ID ambiguous.
    exact_task_matches = {
        path: binding for path, binding in task_bindings.items()
        if wanted == str(binding.master_id)
    }
    if len(exact_task_matches) == 1:
        return TaskSpec.from_task_root(next(iter(exact_task_matches.values())).task_root)
    if len(exact_task_matches) > 1:
        details = ", ".join(str(path) for path in sorted(exact_task_matches))
        raise ValueError(f"task ID {identifier!r} is ambiguous: {details}; use a task workspace")

    legacy_matches = {
        path: binding for path, binding in task_bindings.items()
        if wanted == str(master_id_for_root(binding.task_root))
    }
    if len(legacy_matches) == 1:
        return TaskSpec.from_task_root(next(iter(legacy_matches.values())).task_root)
    if len(legacy_matches) > 1:
        details = ", ".join(str(path) for path in sorted(legacy_matches))
        raise ValueError(f"legacy task ID {identifier!r} is ambiguous: {details}; use a task workspace")

    cycle_task_matches = {
        path: binding for path, binding in task_bindings.items()
        if (binding.task_root / wanted).is_dir()
    }
    if len(cycle_task_matches) == 1:
        return TaskSpec.from_task_root(next(iter(cycle_task_matches.values())).task_root)
    if len(cycle_task_matches) > 1:
        details = ", ".join(str(path) for path in sorted(cycle_task_matches))
        raise ValueError(f"Cycle {identifier!r} is ambiguous across tasks: {details}; use a task workspace")
    matches: list[tuple[Path, Path]] = []
    for state_path in root.rglob("run_state.json"):
        if _ignored_discovery_path(state_path):
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        scientific_path = state_path.parent / "research_state.json"
        if scientific_path.is_file():
            try:
                scientific = json.loads(scientific_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                scientific = {}
            for key in ("status", "phase", "resumable", "owner_pid", "execution_active", "goal_id"):
                if key in scientific:
                    state[key] = scientific[key]
        cycle_dir = state_path.parent
        state_id = state.get("master_id", state.get("master_run_id"))
        task_bound = cycle_dir.parent.resolve() in task_bindings
        # In task mode the binding is authoritative. Embedded state IDs may be
        # stale legacy zeros from a process started before global IDs existed.
        if wanted != cycle_dir.name and (task_bound or wanted != str(state_id)):
            continue
        plan_value = state.get("plan_path")
        if plan_value:
            matches.append((cycle_dir, Path(str(plan_value)).expanduser()))
    if not matches:
        raise ValueError(f"cannot find Task ID/Cycle {identifier!r} under {root}")
    unique = {(cycle.resolve(), canvas.resolve()) for cycle, canvas in matches}
    if len(unique) != 1:
        details = ", ".join(str(cycle) for cycle, _ in sorted(unique))
        raise ValueError(f"Task ID/Cycle {identifier!r} is ambiguous: {details}; use a task workspace or unique Cycle ID")
    cycle_dir, plan = next(iter(unique))
    # A historical run_state may point at a plain Markdown file with no task
    # binding. Calling TaskSpec.load() there would bootstrap a *new* task and
    # silently reinterpret an old run as fresh work. Resumption is allowed only
    # through a registered task pointer; Canvas-only history requires explicit
    # migration before it can enter the runtime.
    from .task import TASK_CONFIG_NAME, TASK_DIR_NAME
    pointer = plan.parent / TASK_DIR_NAME / TASK_CONFIG_NAME
    if not pointer.is_file():
        raise ValueError(
            f"unregistered legacy run {cycle_dir.name!r} cannot be resumed; "
            "create an explicit TheoryMaster task binding and migrate its scientific graph to SQLite first"
        )
    return TaskSpec.load(plan)


def spec_from_tui_identifier(identifier: str | int | None, search_root: Path | None = None) -> TaskSpec:
    """Resolve a TUI ID, preferring the current task before repository-wide lookup."""
    if identifier is None:
        return spec_from_task()
    try:
        return spec_from_task(master_id=identifier)
    except (OSError, ValueError, json.JSONDecodeError):
        return spec_from_resume_identifier(identifier, search_root)


def resumable_runs(search_root: Path | None = None, *, limit: int | None = None) -> list[dict[str, object]]:
    """Discover registered tasks plus legacy live/recoverable runs without mutating state."""
    root = (search_root or default_search_root()).resolve()
    binding_paths, state_paths = _discovery_paths(root)
    task_bindings: dict[Path, TaskBinding] = {}
    for binding_path in binding_paths:
        if _ignored_discovery_path(binding_path):
            continue
        try:
            binding = TaskBinding.load(binding_path.parent.parent)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        task_bindings[binding.task_root.resolve()] = binding

    # V2 stores Cycle workspaces in a non-sync research_root that may be outside
    # task_home. A task-local `ls` must follow the registered binding to that
    # root instead of reporting an empty inventory merely because os.walk(task_home)
    # cannot see the external data area.
    known_state_paths = {path.resolve() for path in state_paths}
    for binding in task_bindings.values():
        try:
            _, bound_state_paths = _discovery_paths(binding.research_root)
        except OSError:
            bound_state_paths = []
        for state_path in bound_state_paths:
            resolved = state_path.resolve()
            if resolved not in known_state_paths:
                state_paths.append(state_path)
                known_state_paths.add(resolved)

    found: list[dict[str, object]] = []
    for state_path in state_paths:
        if _ignored_discovery_path(state_path):
            continue
        cycle = state_path.parent
        # Legacy pre-task runs used tm-* directory names. Keep them on disk and
        # directly resumable by exact ID, but do not mix them into the default
        # operator-facing `theory-master ls` inventory.
        if cycle.name.startswith("tm-"):
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # research_state.json is the lifecycle authority.  run_state.json is an
        # observability projection and older/newly introduced terminal states
        # may not yet have been projected when discovery runs.
        scientific_path = cycle / "research_state.json"
        if scientific_path.is_file():
            try:
                scientific = json.loads(scientific_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                scientific = {}
            for key in (
                "status", "phase", "resumable", "owner_pid", "execution_active",
                "goal_id", "goal_status", "goal_completion", "started_at", "cycle_started_at",
            ):
                if key in scientific:
                    state[key] = scientific[key]
        status = str(state.get("status") or "").upper()
        goal_completion = state.get("goal_completion") if isinstance(state.get("goal_completion"), dict) else {}
        goal_passed = (
            str(state.get("goal_status") or "").lower() == "passed"
            or str(goal_completion.get("verdict") or "").lower() == "passed"
        )
        if goal_passed:
            # Task-level terminal projection. The underlying Cycle remains OVER.
            status = "COMPLETE"
        elif status == "COMPLETED":
            # Legacy Cycle terminal spelling; Cycle-level inventory now exposes OVER.
            status = "OVER"
        resumable = bool(state.get("resumable"))
        cycle_parent = cycle.parent.resolve()
        task_bound = cycle_parent in task_bindings
        # `tm ls` is the operator-facing task inventory. A registered task remains
        # visible after successful completion/cancellation; only unbound legacy
        # runs retain the historical live-or-resumable filter.
        if not task_bound and (
            status in {"OVER", "CANCELLED"} or (
                status not in {"RUNNING", "ACTIVE", "PAUSED", "INTERRUPTED", "FAILED", "CHECKPOINT_REACHED"}
                and not resumable
            )
        ):
            continue
        owner_pid = state.get("owner_pid")
        live = False
        if bool(state.get("execution_active")) and owner_pid:
            try:
                os.kill(int(owner_pid), 0)
            except (OSError, ValueError):
                pass
            else:
                live = True
        started_at = str(state.get("started_at") or state.get("cycle_started_at") or "").strip()
        if not started_at:
            events_path = cycle / "runtime_events.jsonl"
            if events_path.is_file():
                try:
                    for line in events_path.read_text(encoding="utf-8").splitlines():
                        event = json.loads(line)
                        if event.get("event") in {"RUN_STARTED", "CYCLE_STARTED"} and event.get("ts"):
                            started_at = str(event["ts"])
                            break
                except (OSError, json.JSONDecodeError):
                    pass
        display_status = "ORPHANED" if not live and status in {"RUNNING", "ACTIVE"} else status
        found.append({
            "resume_id": cycle.name,
            "cycle": str(cycle_number(cycle.name)) if cycle_number(cycle.name) is not None else cycle.name,
            "started_at": started_at,
            "master_id": state.get("master_id", state.get("master_run_id")),
            "status": "RUNNING" if live else display_status,
            "live": live,
            "pid": owner_pid if live else None,
            "phase": state.get("phase") or "-",
            "goal_id": state.get("goal_id") or "-",
            "cycle_dir": str(cycle),
            "_task_root": str(cycle_parent) if task_bound else None,
        })
    # Task-local mode exposes one stable compact Task ID, not every
    # historical cycle directory.  Collapse those cycles to the newest
    # recoverable entry so every TASK_ID printed below is the command the
    # operator should actually use.
    for binding in sorted(task_bindings.values(), key=lambda item: str(item.task_root)):
        binding_root = str(binding.task_root)
        task_items = [item for item in found if item.get("_task_root") == binding_root]
        if not task_items:
            continue
        latest = max(
            task_items,
            key=lambda item: (
                cycle_number(Path(str(item["cycle_dir"])).name) or -1,
                str(item.get("started_at") or ""),
            ),
        )
        found = [item for item in found if item not in task_items]
        latest["resume_id"] = str(binding.master_id)
        try:
            from theory_master.runtime_manager import runtime_health
            health = runtime_health(binding.task_root)
        except Exception:
            health = {"state": "ABSENT", "active": False, "registry": {}}
        live_worker = None
        if latest.get("cycle_dir") and latest.get("status") in {"ACTIVE", "RUNNING", "STARTING", "RECOVERING", "ORPHANED"}:
            try:
                from theory_master.worker_runtime import list_workers
                live_worker = next((w for w in list_workers(binding.task_root)
                                    if str(w.get("current_cycle") or "") == Path(str(latest["cycle_dir"])).name
                                    and w.get("status") in {"RUNNING", "STARTING", "PAUSE_REQUESTED", "STOP_REQUESTED"}
                                    and w.get("process_alive")), None)
            except (OSError, ValueError):
                live_worker = None
        if live_worker:
            latest["live"] = True
            latest["status"] = "RUNNING"
            latest["pid"] = live_worker.get("pid")
        elif health.get("active"):
            registry = health.get("registry") if isinstance(health.get("registry"), dict) else {}
            latest["live"] = True
            latest["status"] = str(health.get("state") or "RUNNING")
            latest["pid"] = registry.get("runner_pid") or registry.get("supervisor_pid") or latest.get("pid")
        elif health.get("state") == "ORPHANED":
            latest["live"] = False
            latest["status"] = "ORPHANED"
            latest["pid"] = None
        found.append(latest)
    def sort_key(item: dict[str, object]) -> tuple[float, str]:
        started_at = str(item.get("started_at") or "").strip()
        try:
            started_timestamp = datetime.fromisoformat(started_at).timestamp()
        except ValueError:
            started_timestamp = float("-inf")
        raw = str(item["resume_id"])
        return (started_timestamp, raw)

    ordered = sorted(found, key=sort_key, reverse=True)
    if limit is not None:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        ordered = ordered[:limit]
    for item in ordered:
        item.pop("_task_root", None)
    return ordered


def print_runs(search_root: Path | None = None) -> int:
    runs = resumable_runs(search_root, limit=20)
    if not runs:
        print("No running or resumable TheoryMaster tasks.")
        return 0
    rows: list[tuple[str, str, str, str, str]] = []
    for item in runs:
        state = "LIVE" if item["live"] else str(item["status"])
        started = str(item.get("started_at") or "-")
        if started != "-":
            try:
                started = datetime.fromisoformat(started).astimezone().strftime("%m-%d %H:%M")
            except ValueError:
                started = started[:16]
        rows.append((
            str(item["resume_id"]),
            str(item["cycle"]),
            started,
            state,
            str(item["phase"]),
        ))

    headers = ("TASK_ID", "CYCLE", "STARTED", "STATE", "PHASE")
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers) - 1)
    ]
    separator = "  "
    print(separator.join(
        f"{headers[index]:<{widths[index]}}" if index < len(widths) else headers[index]
        for index in range(len(headers))
    ))
    for row in rows:
        print(separator.join(
            f"{row[index]:<{widths[index]}}" if index < len(widths) else row[index]
            for index in range(len(row))
        ))
    return 0
