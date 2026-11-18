"""Task-scoped notebook discovery, portable downloads and explicit file actions."""
from __future__ import annotations

import io
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from .canvas_md import ExplorerError, document_paths, vault_root

DOWNLOAD_ACTIONS = {"vault-download", "canvas-download", "document-download"}
FILE_ACTIONS = DOWNLOAD_ACTIONS | {"vault", "vault-directories"}
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_FILES = 20000
MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf",
                  ".mp3", ".mp4", ".wav", ".ogg", ".m4a", ".webm"}


def route_action(route: str) -> tuple[str, str] | None:
    parts = route.strip("/").split("/")
    if len(parts) == 5 and parts[:3] == ["api", "canvas-md", "tasks"]:
        return parts[3], parts[4]
    return None


def local_file_actions(*, host: str, client: str, forwarded: bool = False) -> bool:
    """A reverse tunnel's loopback peer is not evidence of a local browser."""
    try:
        hostname = urlsplit("http://" + host).hostname or ""
        loopback_host = hostname == "localhost" or ipaddress.ip_address(hostname).is_loopback
        loopback_client = ipaddress.ip_address(client).is_loopback
    except ValueError:
        return False
    desktop = sys.platform in {"darwin", "win32"} or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return bool(loopback_host and loopback_client and not forwarded and desktop)


def vault_metadata(search_root: Path, state: dict, ident: str, *, local: bool = False) -> dict[str, Any]:
    from theory_master.notebook import graph_database_path, notebook_config
    root = vault_root(search_root, ident, state)
    database = graph_database_path(root)
    from theory_master.vault_canvas import canvas_status
    statuses = canvas_status(database)
    full = next((s for s in statuses if not s.get("branch_root") and Path(s["vault_root"]) == root), {})
    canvas = Path(full.get("canvas_path") or root / "exploration.canvas")
    registered = notebook_config(root)
    available = False
    reason = "此任务尚未登记可迁移的笔记库"
    if registered:
        from theory_master.notebook_move import notebook_is_busy
        available = not notebook_is_busy(Path(registered[1]["research_root"]))
        reason = "请先暂停任务和 Worker 调度器" if not available else ""
    return {"task_id": ident, "vault_root": str(root), "vault_name": root.name,
            "graph_database": str(database), "canvas_path": str(canvas),
            "canvas_relative_path": canvas.relative_to(root).as_posix(),
            "updated_at": full.get("updated_at"), "status": full.get("status", "pending"),
            "revision": full.get("revision"), "canvases": statuses,
            "local_actions_available": local, "move_available": available,
            "move_disabled_reason": reason,
            "obsidian_uri": "obsidian://open?" + urlencode({"vault": root.name, "file": canvas.name})}


def browse_directories(raw_path: str, default: Path) -> dict[str, Any]:
    if not isinstance(raw_path, str) or len(raw_path) > 4096 or "\x00" in raw_path:
        raise ExplorerError("无效目录", 400)
    candidate = Path(raw_path).expanduser() if raw_path else default
    if not candidate.is_absolute():
        raise ExplorerError("请选择绝对路径目录", 400)
    try:
        path = candidate.resolve(strict=True)
        if not path.is_dir():
            raise ExplorerError("目录不存在", 404)
        children = sorted((p for p in path.iterdir() if p.is_dir() and not p.is_symlink()
                           and not p.name.startswith(".")), key=lambda p: p.name.casefold())
    except (PermissionError, OSError) as exc:
        raise ExplorerError("无法浏览此目录", 403) from exc
    if len(children) > 5000:
        raise ExplorerError("目录项目过多，请输入更具体的路径", 413)
    return {"path": str(path), "parent": str(path.parent) if path.parent != path else None,
            "directories": [{"name": p.name, "path": str(p)} for p in children]}


def _plain_file(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
        return (not any(part.startswith(".") for part in relative.parts)
                and not any((root.joinpath(*relative.parts[:i])).is_symlink()
                            for i in range(1, len(relative.parts) + 1))
                and path.resolve().is_relative_to(root) and path.is_file())
    except (OSError, ValueError):
        return False


def notebook_archive(root: Path) -> bytes:
    from theory_master.notebook import notebook_files
    candidates = notebook_files(root)
    output = io.BytesIO()
    total = 0
    count = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in candidates:
            if not _plain_file(root, path):
                continue
            count += 1
            remaining = MAX_ARCHIVE_BYTES - total
            if path.stat().st_size > remaining or count > MAX_ARCHIVE_FILES:
                raise ExplorerError("笔记库超过下载大小限制，请使用本地笔记库目录", 413)
            with path.open("rb") as stream:
                data = stream.read(remaining + 1)
            if len(data) > remaining:
                raise ExplorerError("笔记库超过下载大小限制，请使用本地笔记库目录", 413)
            total += len(data)
            archive.writestr(path.relative_to(root).as_posix(), data)
    return output.getvalue()


def vault_get(route: str, raw_query: str, search_root: Path, state: dict,
              *, local: bool = False) -> tuple[bytes, str, str | None]:
    matched = route_action(route)
    if not matched or matched[1] not in FILE_ACTIONS:
        raise ExplorerError("API 不存在", 404)
    ident, action = matched
    root = vault_root(search_root, ident, state)
    query = parse_qs(raw_query, keep_blank_values=True)
    if action == "vault":
        result = vault_metadata(search_root, state, ident, local=local)
    elif action == "vault-directories":
        result = browse_directories((query.get("path") or [""])[0], root.parent)
    elif action == "vault-download":
        return notebook_archive(root), "application/zip", f"notebook-{ident}.zip"
    elif action == "document-download":
        ref = (query.get("path") or [""])[0]
        path = document_paths(root).get(ref)
        if path is None or not _plain_file(root, path):
            raise ExplorerError("文档不存在", 404)
        return path.read_bytes(), "text/markdown; charset=utf-8", path.name
    else:
        from theory_master.notebook import graph_database_path
        from theory_master.science_graph_store import ScienceGraphStore
        from theory_master.vault_canvas import vault_canvas_projection
        branch = (query.get("branch") or [""])[0] or None
        if branch and not re.fullmatch(r"H-\d{4,}", branch):
            raise ExplorerError("请选择有效假说分支", 400)
        try:
            with ScienceGraphStore(graph_database_path(root), readonly=True) as store:
                store.db.execute("BEGIN")
                projection = vault_canvas_projection(store, vault_root=root, branch_root=branch)
        except (KeyError, ValueError) as exc:
            raise ExplorerError("假说分支不存在", 404) from exc
        filename = f"branch-{branch}.canvas" if branch else "exploration.canvas"
        return (json.dumps(projection, ensure_ascii=False, indent=2) + "\n").encode(), "application/json; charset=utf-8", filename
    return (json.dumps(result, ensure_ascii=False) + "\n").encode(), "application/json; charset=utf-8", None


def vault_post(route: str, payload: dict, search_root: Path, state: dict,
               *, local: bool = False) -> dict:
    matched = route_action(route)
    if not matched or matched[1] != "vault-action":
        raise ExplorerError("API 不存在", 404)
    ident = matched[0]
    root = vault_root(search_root, ident, state)
    if not isinstance(payload, dict):
        raise ExplorerError("无效请求", 400)
    action = payload.get("action")
    if action == "open_folder":
        if not local:
            raise ExplorerError("远程网页请在本机打开同步的笔记库，或下载整个库", 409)
        ref = payload.get("path", "")
        if ref:
            target = document_paths(root).get(ref)
            if target is None:
                raise ExplorerError("文档不存在", 404)
            folder = target.parent
        else:
            folder = root
        if sys.platform == "darwin":
            command = ["open", "-R", str(target)] if ref else ["open", str(folder)]
        elif sys.platform == "win32":
            command = ["explorer.exe", str(folder)]
        else:
            opener = shutil.which("xdg-open")
            if not opener:
                raise ExplorerError("此系统没有可用的文件管理器", 409)
            command = [opener, str(folder)]
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return {"ok": True}
    from theory_master.notebook import graph_database_path
    database = graph_database_path(root)
    if action == "move":
        destination = payload.get("destination")
        if not isinstance(destination, str) or not destination or "\x00" in destination:
            raise ExplorerError("请选择目标文件夹", 400)
        from theory_master.notebook_move import move_notebook
        try:
            moved = move_notebook(root, Path(destination))
        except ValueError as exc:
            raise ExplorerError(str(exc), 409) from exc
        return {"ok": True, **moved}
    from theory_master.vault_canvas import refresh_registered_canvases, refresh_canvas, register_canvas_binding
    if action == "create_branch":
        branch = payload.get("branch")
        if not isinstance(branch, str) or not re.fullmatch(r"H-\d{4,}", branch):
            raise ExplorerError("请选择有效假说分支", 400)
        try:
            metadata = refresh_canvas(database, vault_root=root, branch_root=branch)
            register_canvas_binding(database, vault_root=root, branch_root=branch)
        except (ValueError, KeyError) as exc:
            raise ExplorerError("假说分支不存在", 404) from exc
        return {"ok": True, **metadata, "canvas_relative_path": Path(metadata["canvas_path"]).name}
    if action == "refresh_canvas":
        statuses = refresh_registered_canvases(database)
        return {"ok": all(s.get("status") == "ready" for s in statuses), "canvases": statuses}
    raise ExplorerError("未知笔记库操作", 400)
