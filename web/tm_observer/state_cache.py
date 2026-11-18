"""Single-flight collection with explicit freshness and injected data sources."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .collector import SCHEMA, collect_git_info, collect_state
from .workspace_scope import WorkspaceScopeError, scoped_state, task_in_workspace


class StateCache:
    """Single-flight stale-while-revalidate cache for task state and slow Git metadata.

    Once a first snapshot exists, HTTP requests never wait for a refresh. The cold
    start has a bounded wait; if collection fails, a clearly marked empty snapshot
    is returned while a later request retries after a short backoff.
    """

    def __init__(self, search_root: Path, ttl_s: float = 5.0, *, git_ttl_s: float = 60.0, cold_wait_s: float = 8.0,
                 isolated_workspace: bool = False,
                 state_collector=collect_state, git_collector=collect_git_info):
        self._collect_state = state_collector
        self._collect_git_info = git_collector
        self._last_success_at: str | None = None
        self._git_last_error: str | None = None
        self.search_root = search_root
        self.isolated_workspace = isolated_workspace
        self.ttl_s = max(0.2, float(ttl_s))
        self.git_ttl_s = max(5.0, float(git_ttl_s))
        self.cold_wait_s = max(0.1, float(cold_wait_s))
        self._condition = threading.Condition()
        self._state: dict | None = None
        self._state_epoch = 0
        self._created_runs: dict[str, dict] = {}
        self._at = 0.0
        self._refreshing = False
        self._retry_after = 0.0
        self._cold_waiter = False
        self._cold_wait_completed = False
        self._last_error: str | None = None
        self._git_snapshot: dict | None = None
        self._git_at = 0.0
        self._git_refreshing = False
        self._git_retry_after = 0.0

    def _start_state_refresh_locked(self) -> None:
        if self._refreshing or time.monotonic() < self._retry_after:
            return
        self._refreshing = True
        threading.Thread(target=self._refresh_state, name="tm-observer-state-refresh", daemon=True).start()

    def _start_git_refresh_locked(self) -> None:
        now = time.monotonic()
        if (self._git_refreshing or now < self._git_retry_after
                or (self._git_at and now - self._git_at < self.git_ttl_s)):
            return
        self._git_refreshing = True
        threading.Thread(target=self._refresh_git, name="tm-observer-git-refresh", daemon=True).start()

    def refresh_after_write(self) -> None:
        """Publish newly created tasks before redirecting to Task List."""
        with self._condition:
            self._state_epoch += 1
        self._refresh_state()

    def _refresh_state(self) -> None:
        with self._condition:
            epoch = self._state_epoch
        try:
            state = self._collect_state(self.search_root, include_git_info=False)
            if not isinstance(state, dict) or not isinstance(state.get("runs"), list) or not isinstance(state.get("system"), dict):
                raise ValueError("invalid collected state snapshot")
        except Exception as exc:
            with self._condition:
                if epoch != self._state_epoch:
                    return
                self._last_error = type(exc).__name__
                self._retry_after = time.monotonic() + 1.0
                self._refreshing = False
                self._condition.notify_all()
            return
        try:
            with self._condition:
                if epoch != self._state_epoch:
                    return
                discovered = {row.get("observer_id") for row in state.get("runs", [])}
                additions = [row for ident, row in self._created_runs.items() if ident not in discovered]
                if additions:
                    state = {**state, "runs": [*state.get("runs", []), *additions],
                             "system": {**state.get("system", {}), "task_count": len(state.get("runs", [])) + len(additions)}}
                if self.isolated_workspace:
                    state = scoped_state(state, self.search_root)
                self._created_runs = {ident: row for ident, row in self._created_runs.items() if ident not in discovered}
                git_info = self._git_snapshot
                if git_info is None and self._state is not None:
                    previous_system = self._state.get("system", {})
                    git_info = {key: previous_system.get(key) for key in ("git_head", "git_branch", "git_dirty")}
                    git_info["provenance"] = {
                        key: (previous_system.get("provenance") or {}).get(key)
                        for key in ("git_head", "git_branch", "git_dirty")
                    }
                if git_info:
                    state["system"].update({key: value for key, value in git_info.items() if key != "provenance"})
                    state["system"].setdefault("provenance", {}).update(git_info.get("provenance") or {})
                self._state = state
                self._at = time.monotonic()
                self._last_success_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                self._last_error = None
                self._retry_after = 0.0
                self._refreshing = False
                self._start_git_refresh_locked()
                self._condition.notify_all()
        except Exception as exc:
            with self._condition:
                if epoch == self._state_epoch:
                    self._last_error = type(exc).__name__
                    self._retry_after = time.monotonic() + 1.0
                    self._refreshing = False
                    self._condition.notify_all()

    def _refresh_git(self) -> None:
        try:
            git_info = self._collect_git_info(self.search_root)
            if not isinstance(git_info, dict):
                raise ValueError("invalid collected Git snapshot")
        except Exception as exc:
            with self._condition:
                self._git_last_error = type(exc).__name__
                self._git_retry_after = time.monotonic() + self.git_ttl_s
                self._git_refreshing = False
                self._condition.notify_all()
            return
        with self._condition:
            self._git_snapshot = git_info
            self._git_last_error = None
            self._git_at = time.monotonic()
            self._git_retry_after = 0.0
            self._git_refreshing = False
            if self._state is not None:
                # Replace, rather than mutate, a snapshot already returned to a request.
                state = dict(self._state)
                system = dict(state.get("system") or {})
                system.update({key: value for key, value in git_info.items() if key != "provenance"})
                provenance = dict(system.get("provenance") or {})
                provenance.update(git_info.get("provenance") or {})
                system["provenance"] = provenance
                state["system"] = system
                self._state = state
            self._condition.notify_all()

    def _empty_state(self, status: str) -> dict:
        return {
            "schema": SCHEMA, "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "search_root": str(self.search_root), "refresh_status": status,
            "system": {"git_head": None, "git_branch": None, "git_dirty": None,
                       "observer_version": "unknown", "task_count": 0, "provenance": {}},
            "runs": [],
        }

    def include_created_task(self, task_root: Path) -> None:
        """Expose a confirmed local creation before the next workspace scan."""
        from .collector import collect_run
        run = collect_run(task_root)
        if self.isolated_workspace and not task_in_workspace(run, self.search_root):
            raise WorkspaceScopeError("新任务绑定不属于当前账户工作区")
        with self._condition:
            self._created_runs[run["observer_id"]] = run
            if self._state is not None:
                runs = [row for row in self._state.get("runs", []) if row.get("observer_id") != run["observer_id"]]
                runs.append(run)
                self._state = {**self._state, "runs": runs,
                               "system": {**self._state.get("system", {}), "task_count": len(runs)}}
            self._at = 0.0

    def get(self) -> dict:
        now = time.monotonic()
        with self._condition:
            if self._state is None:
                self._start_state_refresh_locked()
                self._start_git_refresh_locked()
                # Only one cold-start request may wait. Other simultaneous page/API
                # requests get an explicit loading snapshot instead of queueing behind it.
                if self._refreshing and not self._cold_wait_completed and not self._cold_waiter:
                    self._cold_waiter = True
                    deadline = time.monotonic() + self.cold_wait_s
                    try:
                        while self._state is None and self._refreshing:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                break
                            self._condition.wait(remaining)
                    finally:
                        self._cold_waiter = False
                        self._cold_wait_completed = True
                if self._state is None:
                    return self._empty_state("loading" if self._refreshing else "unavailable")
            elif now - self._at >= self.ttl_s:
                self._start_state_refresh_locked()
            self._start_git_refresh_locked()
            snapshot = scoped_state(self._state, self.search_root) if self.isolated_workspace else self._state
            age = max(0.0, time.monotonic() - self._at)
            stale = bool(self._last_error) or age >= self.ttl_s
            return {**snapshot, "refresh_status": "stale" if stale else "fresh",
                    "refresh_error": self._last_error, "refreshing": self._refreshing,
                    "last_success_at": self._last_success_at,
                    "cache_age_s": round(age, 3)}
