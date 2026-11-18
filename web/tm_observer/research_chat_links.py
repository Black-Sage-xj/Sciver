"""Resolve a chat citation through the task's existing read-only document boundary."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from .canvas_md import DOC_DIRS, ExplorerError, document_paths, vault_root


def document_destination(search_root: Path, state: dict[str, Any], task_id: str, target: str) -> str:
    root = vault_root(search_root, task_id, state)
    if not target or len(target) > 4096 or any(ord(c) < 32 for c in target):
        raise ExplorerError("文档引用无效", 400)
    try:
        parsed = urlsplit(re.sub(r"(?<=\.md):\d+(?::\d+)?(?=#|$)", "", target))
    except ValueError as exc:
        raise ExplorerError("文档引用无效", 400) from exc
    if parsed.scheme not in {"", "file"} or parsed.netloc not in {"", "localhost"}:
        raise ExplorerError("此入口只打开当前任务的文档", 400)
    path = unquote(parsed.path)
    path = re.sub(r":\d+(?::\d+)?$", "", path)
    if not path or "\x00" in path or "\\" in path:
        raise ExplorerError("文档引用无效", 400)
    # /notes/... is vault-relative; /absolute/task/notes/... retains its identity.
    if path.startswith("/") and path.lstrip("/").split("/", 1)[0] in DOC_DIRS | {"research_plan.md"}:
        path = path.lstrip("/")
    try:
        file = (root / path).resolve()
    except (OSError, RuntimeError) as exc:
        raise ExplorerError("文档路径不可读取", 403) from exc
    if not file.is_relative_to(root):
        raise ExplorerError("文档不属于当前任务", 403)
    relative = file.relative_to(root).as_posix()
    if any(part.startswith(".") for part in Path(relative).parts):
        raise ExplorerError("该文档不可在此处预览", 403)
    anchor = unquote(parsed.fragment)
    if re.fullmatch(r"L\d+(?:-L?\d+)?", anchor):
        anchor = ""
    from theory_master.notebook import graph_database_path
    from theory_master.research_report import report_cycle_for_path
    cycle = report_cycle_for_path(graph_database_path(root), relative)
    if cycle:
        reference = "report:" + cycle
    elif relative in document_paths(root):
        reference = "doc:" + relative
    else:
        raise ExplorerError("此文件暂时无法预览，请确认文档是否仍在当前任务中", 404)
    if anchor:
        reference += "#" + anchor
    return "/research-explorer/?observer_id=" + quote(task_id, safe="") + "#" + quote(reference, safe="")
