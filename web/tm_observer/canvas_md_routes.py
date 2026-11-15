"""The reserved /api/canvas-md/tasks/{observer_id}/... GET boundary.

Independent of the Run Web UI and its handler. The caller authenticates first.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from .canvas_md import ExplorerError, build_index_payload, read_document, search_payload, vault_root


IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def canvas_md_image_get(route: str, raw_query: str, search_root: Path,
                        state: dict[str, Any]) -> tuple[bytes, str]:
    parts = route.strip("/").split("/")
    if len(parts) != 5 or parts[:3] != ["api", "canvas-md", "tasks"] or parts[4] != "image":
        raise ExplorerError("API 不存在", 404)
    root = vault_root(search_root, parts[3], state)
    run = next(r for r in state["runs"] if r.get("observer_id") == parts[3])
    research = Path(run["research_root"]).resolve()
    query = parse_qs(raw_query, keep_blank_values=True)
    target = (query.get("path") or [""])[0]
    source = (query.get("source") or [""])[0]
    if not target or "\x00" in target or "\x00" in source:
        raise ExplorerError("无效图片路径", 400)
    # Only the registered task and its bound research directory are readable.
    roots = (root, research)
    candidates = [root / target, research / target, search_root.resolve() / target]
    if source and (query.get("vault") or [""])[0] != "1":
        candidates.insert(0, root / Path(source).parent / target)
    allowed = False
    for candidate in candidates:
        path = candidate.resolve()
        if not any(path.is_relative_to(anchor) for anchor in roots):
            continue
        allowed = True
        if not path.is_file():
            continue
        mime = IMAGE_TYPES.get(path.suffix.lower())
        if not mime:
            raise ExplorerError("不支持的图片类型", 415)
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_IMAGE_BYTES + 1)
        except OSError as exc:
            raise ExplorerError("无法读取图片", 404) from exc
        if len(data) > MAX_IMAGE_BYTES:
            raise ExplorerError("图片超过预览大小限制", 413)
        return data, mime
    raise ExplorerError("图片不存在" if allowed else "图片路径不在此任务内", 404 if allowed else 403)


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
