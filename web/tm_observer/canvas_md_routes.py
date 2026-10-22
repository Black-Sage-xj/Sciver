"""The reserved /api/canvas-md/tasks/{observer_id}/... GET boundary.

Independent of the Run Web UI and its handler. The caller authenticates first.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from .canvas_md import ExplorerError, build_index_payload, read_document, search_payload, vault_root

def running_worker_markers(root: Path, observer_id: str, state: dict[str, Any]) -> list[dict[str, str]]:
    """Live read-only Worker -> H assignment for a Research Explorer list.

    A Worker's current frozen Cycle H supersedes its originally claimed parent H.
    Never equate a persisted RUNNING string with process liveness.
    """
    from .worker_console import registered_workers
    from theory_master.science_graph_store import ScienceGraphStore

    run = next((run for run in state.get("runs", [])
                if isinstance(run, dict) and run.get("observer_id") == observer_id
                and Path(str(run.get("task_root") or "")).resolve() == root), None)
    if run is None:
        return []
    workers = [worker for worker in registered_workers(run)
               if worker.get("status") == "RUNNING"]
    if not workers:
        return []
    owned: dict[str, str] = {}
    graph = root / "science_graph.sqlite3"
    if graph.is_file() and not graph.is_symlink():
        try:
            with ScienceGraphStore(graph, readonly=True) as store:
                for row in store.db.execute(
                    "SELECT cycle_id,hypothesis_id FROM hypothesis_cycle_owners"
                ):
                    owned[str(row["cycle_id"])] = str(row["hypothesis_id"])
        except (OSError, ValueError, RuntimeError):
            # Missing or temporarily unavailable ownership never licenses
            # guessing a frozen H by numeric suffix.
            owned = {}
    result = []
    for worker in workers:
        cycle = str(worker.get("current_cycle") or "")
        frozen_h = owned.get(cycle)
        claimed_h = str(worker.get("target_h_id") or "")
        node_id = frozen_h or claimed_h
        if not node_id.startswith("H-") or not node_id[2:].isdigit():
            continue
        result.append({"worker_id": worker["worker_id"], "node_id": node_id,
                       "cycle_id": cycle, "association": "frozen" if frozen_h else "claimed"})
    return result



def canvas_md_get(
    route: str, raw_query: str, search_root: Path, state: dict[str, Any],
) -> tuple[bytes, int]:
    parts = route.strip("/").split("/")
    if len(parts) != 5 or parts[:3] != ["api", "canvas-md", "tasks"]:
        raise ExplorerError("API 不存在", 404)
    observer_id, action = parts[3:]
    if action not in {"index", "document", "search", "workers"}:
        raise ExplorerError("API 不存在", 404)
    root = vault_root(search_root, observer_id, state)
    query = parse_qs(raw_query, keep_blank_values=True)
    if action == "workers":
        result = {"workers": running_worker_markers(root, observer_id, state)}
    elif action == "index":
        # Reuse the serialized graph payload as well as the parsed index.
        return build_index_payload(root, observer_id), 200
    elif action == "document":
        raw_offset = (query.get("offset") or ["0"])[0]
        try:
            offset = int(raw_offset)
        except (TypeError, ValueError) as exc:
            raise ExplorerError("无效文档分页偏移量", 400) from exc
        result = read_document(root, (query.get("path") or [""])[0], offset=offset)
    else:
        result = search_payload(root, (query.get("q") or [""])[0])
    return (json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"), 200
