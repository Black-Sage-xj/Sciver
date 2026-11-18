"""Filesystem and host-service boundaries for independent account workspaces."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


class WorkspaceScopeError(ValueError):
    status = 403


def workspace_path(raw: Any, root: Path) -> Path:
    """Require an absolute path inside this workspace, without symlink escapes."""
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise WorkspaceScopeError("请选择当前账户工作区内的绝对目录")
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        raise WorkspaceScopeError("请选择当前账户工作区内的绝对目录")
    try:
        boundary = root.expanduser().resolve()
        resolved = path.resolve()
        if not resolved.is_relative_to(boundary):
            raise WorkspaceScopeError("该目录不属于当前账户工作区")
        # Do not allow a symlink to become a different tenant after validation.
        relative = path.absolute().relative_to(boundary)
        if any(boundary.joinpath(*relative.parts[:i]).is_symlink()
               for i in range(1, len(relative.parts) + 1)):
            raise WorkspaceScopeError("工作区目录不能包含符号链接")
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, WorkspaceScopeError):
            raise
        raise WorkspaceScopeError("该目录不属于当前账户工作区") from exc
    return resolved


def host_route(path: str, *, method: str) -> bool:
    """These endpoints inspect or mutate shared host identities/configuration."""
    if path.startswith(("/codex-account/", "/api/services", "/services-selection")):
        return True
    if path in {"/services", "/services/", "/services-workers.json", "/codex-usage",
                "/codex-usage/", "/codex-usage.json", "/codex-usage-cached.json",
                "/api/settings/access",
                "/change-requests", "/change-requests/", "/change-requests/create",
                "/artifacts/changes.html"}:
        return True
    return method != "GET" and path == "/workflow-field-notes/update"


def _bound_path(raw: Any, roots: tuple[Path, ...]) -> Path:
    for root in roots:
        try:
            return workspace_path(raw, root)
        except WorkspaceScopeError:
            pass
    raise WorkspaceScopeError("任务绑定不属于当前账户工作区")


def _task_home(run: dict, search_root: Path) -> Path | None:
    """Reject discovery pointers/config paths that lead to another workspace.

    Runtime data intentionally lives outside the human notebook. Its sole extra
    boundary is the account process's explicitly configured data root.
    """
    root = search_root.expanduser().resolve()
    data_root = os.environ.get("THEORY_MASTER_DATA_ROOT", "").strip()
    runtime_roots = (root, Path(data_root).expanduser().resolve()) if data_root else (root,)
    try:
        task = workspace_path(run.get("task_root"), root)
        research = _bound_path(run.get("research_root"), runtime_roots)
        if run.get("observer_id") != hashlib.sha256(str(research).encode()).hexdigest()[:16]:
            return None
        marker = task / ".theory-master" / "task.json"
        workspace_path(str(marker), root)
        if not marker.is_file() or marker.stat().st_size > 512 * 1024:
            return None
        binding = json.loads(marker.read_text(encoding="utf-8"))
        config = workspace_path(binding.get("config_path"), root)
        if not config.is_file() or config.stat().st_size > 512 * 1024:
            return None
        values = json.loads(config.read_text(encoding="utf-8"))
        if (binding.get("schema") != "theory-master/task/v2"
                or values.get("schema") != "theory-master/system/v1"
                or _bound_path(values.get("research_root"), runtime_roots) != research):
            return None
        home = workspace_path(values.get("task_home"), root)
        if task not in {home, research}:
            return None
        # Discovery may prefer the runtime mirror when the account's data area
        # is inside its search root. Bind it back to the actual notebook home.
        home_marker = home / ".theory-master" / "task.json"
        workspace_path(str(home_marker), root)
        if not home_marker.is_file() or home_marker.stat().st_size > 512 * 1024:
            return None
        home_binding = json.loads(home_marker.read_text(encoding="utf-8"))
        if (home_binding.get("schema") != "theory-master/task/v2"
                or workspace_path(home_binding.get("config_path"), root) != config):
            return None
        for key in ("plan_path", "branch_canvas", "notes_dir", "vault_root"):
            if values.get(key):
                workspace_path(values[key], root)
        for key in ("graph_database", "benchmark_root", "legacy_research_root"):
            if values.get(key):
                _bound_path(values[key], runtime_roots)
        return home
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
        return None


def task_in_workspace(run: dict, search_root: Path) -> bool:
    return _task_home(run, search_root) is not None


def scoped_state(state: dict, search_root: Path) -> dict:
    runs = []
    for run in state.get("runs", []):
        if not isinstance(run, dict):
            continue
        home = _task_home(run, search_root)
        if home is not None:
            runs.append(run if run.get("task_root") == str(home) else {**run, "task_root": str(home)})
    return {**state, "search_root": str(search_root.resolve()), "runs": runs,
            "system": {**state.get("system", {}), "task_count": len(runs)}}
