"""Task-wide Goal terminal: science commit barrier and SQLite seal readers.

Lock order (system wide): ``science_commit_barrier`` -> ``canvas_write_lock``
-> SQLite transaction. The Goal Judge holds the barrier exclusively; every
other scientific writer holds it shared, so no science write can interleave
with a Goal verdict and its seal.
"""
from __future__ import annotations

import fcntl
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal


class GoalSealedError(Exception):
    """The task Goal is sealed as passed; no further scientific write is allowed.

    Deliberately not a ValueError so broad ``except ValueError`` handlers
    (protocol violations, replay conflicts) cannot swallow it.
    """


_held = threading.local()


def _barrier_path(research_root: Path) -> Path:
    return Path(research_root).resolve() / ".theory-master" / "science_commit.lock"


@contextmanager
def science_commit_barrier(
    research_root: Path, mode: Literal["shared", "exclusive"],
) -> Iterator[None]:
    """fcntl barrier on ``.theory-master/science_commit.lock``.

    Re-entrancy is per thread (each acquisition opens its own file
    description, so other threads of the same process are excluded like
    other processes). Nested requests in the holding thread never re-lock:
    exclusive->shared/exclusive and shared->shared only bump the depth.
    shared->exclusive is refused with RuntimeError because an flock upgrade
    is not atomic and two upgraders would deadlock.
    """
    if mode not in {"shared", "exclusive"}:
        raise ValueError(f"Unknown science commit barrier mode: {mode}")
    path = _barrier_path(research_root)
    key = str(path)
    held: dict[str, dict[str, Any]] = getattr(_held, "locks", None) or {}
    _held.locks = held
    current = held.get(key)
    if current is not None and current["pid"] != os.getpid():
        # Inherited across fork: the child does not own the parent's barrier.
        held.pop(key, None)
        current = None
    if current is not None:
        if mode == "exclusive" and current["mode"] == "shared":
            raise RuntimeError("science_commit_barrier cannot upgrade shared -> exclusive")
        current["depth"] += 1
        try:
            yield
        finally:
            current["depth"] -= 1
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX if mode == "exclusive" else fcntl.LOCK_SH)
        held[key] = {"mode": mode, "depth": 1, "pid": os.getpid()}
        try:
            yield
        finally:
            held.pop(key, None)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def goal_sealed(research_root: Path, graph_path: Path, *, spec: Any | None = None) -> dict | None:
    """Pure read of the task Goal terminal; SQLite first, legacy receipt fallback.

    The legacy fallback (verified historical receipt) is consulted only when
    ``spec`` is given. Nothing is written or migrated on read.
    """
    from .science_graph_store import ScienceGraphStore

    graph = Path(graph_path)
    if graph.is_file():
        try:
            with ScienceGraphStore(graph, readonly=True) as store:
                terminal = store.goal_terminal()
        except ValueError:
            terminal = None
        if terminal is not None:
            return {**terminal, "source": "sqlite"}
    if spec is None:
        return None
    from .workflow_goal import verified_completed_goal_state
    state = verified_completed_goal_state(spec, graph)
    if state is None:
        return None
    completion = state.get("goal_completion") if isinstance(state.get("goal_completion"), dict) else {}
    return {
        "goal_id": str(getattr(spec, "goal_id", "") or ""),
        "verdict": "passed",
        "source_cycle": str(completion.get("source_cycle") or ""),
        "source": "legacy_receipt",
        "state": state,
    }


def recovery_goal_blocker(research_root: Path, *, spec: Any | None = None) -> str | None:
    """Resolve task-wide terminal authority without loading plans or model routes.

    A bound task with unreadable or mismatched authority cannot be classified
    as open. Unbound legacy diagnostic roots may have no graph yet.
    """
    from sqlite3 import Error as SQLiteError
    from types import SimpleNamespace

    from .notebook import notebook_config
    from .science_graph_store import ScienceGraphStore
    from .task import absolute, effective_path_model
    from .workflow_goal import verified_completed_goal_state

    root = Path(research_root).resolve()
    try:
        if spec is None:
            binding = notebook_config(root)
            if binding is not None:
                _, config = binding
                _, bound_root, _ = effective_path_model(config)
                if bound_root != root:
                    return "goal_authority_unavailable"
                canvas = absolute(config.get("branch_canvas"), "branch_canvas")
                graph = Path(config.get("graph_database") or canvas.parent / "science_graph.sqlite3").expanduser()
                spec = SimpleNamespace(
                    research_root=root, task_mode=True,
                    document=absolute(config.get("plan_path"), "plan_path"), goal_id=config["goal_id"],
                )
            else:
                pointer = root / ".theory-master" / "task.json"
                if pointer.is_symlink() or pointer.parent.is_symlink():
                    return "goal_authority_unavailable"
                graph = root / "science_graph.sqlite3"
        else:
            if Path(spec.research_root).resolve() != root:
                return "goal_authority_unavailable"
            graph = Path(getattr(spec, "graph_database", None)
                         or Path(spec.branch_canvas).resolve().parent / "science_graph.sqlite3")
        if not graph.is_absolute() or any(part.is_symlink() for part in (graph, *graph.parents)):
            return "goal_authority_unavailable"
        if not graph.is_file():
            return "goal_authority_unavailable" if spec is not None or graph.exists() else None
        with ScienceGraphStore(graph, readonly=True) as store:
            terminal = store.goal_terminal()
        if terminal is not None:
            if spec is not None and str(terminal["goal_id"]) != str(spec.goal_id):
                return "goal_authority_unavailable"
            return "goal_completed"
        if spec is not None and verified_completed_goal_state(spec, graph) is not None:
            return "goal_completed"
    except (OSError, ValueError, KeyError, TypeError, AttributeError, SQLiteError):
        return "goal_authority_unavailable"
    return None


def assert_goal_open(research_root: Path, graph_path: Path, *, spec: Any | None = None) -> None:
    terminal = goal_sealed(research_root, graph_path, spec=spec)
    if terminal is not None:
        raise GoalSealedError(
            f"Goal already completed by Cycle {terminal.get('source_cycle') or '?'} "
            f"({terminal['source']})"
        )
