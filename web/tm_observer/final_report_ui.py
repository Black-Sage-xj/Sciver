"""Read-only report metadata shared by task list and console presentation."""
from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlencode

from .final_report_routes import report_metadata


_STATES = {
    "ready": "已生成", "needs_revision": "待修订", "queued": "排队中",
    "running": "生成中", "collecting": "生成中", "composing": "生成中",
    "reviewing": "审核中", "failed": "生成失败", "missing": "完成后生成",
    "unavailable": "暂不可用",
}


def task_is_complete(run: Mapping[str, Any]) -> bool:
    """Use the collector's task lifecycle, not historical Cycle status."""
    return str(run.get("state") or "").strip().upper() in {"COMPLETE", "COMPLETED"}


def report_view(run: Mapping[str, Any], cache: MutableMapping[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Project a registered task's report metadata for a UI entry.

    ``cache`` belongs to one page render, so repeated task/worker rows share a
    metadata read while the next page snapshot still sees publication changes.
    The caller supplies the authoritative task record, never a browser path.
    """
    if not task_is_complete(run):
        return {"visible": False, "available": False, "state": "not_complete", "label": "",
                "title": "", "href": "", "message": "", "latest_job_state": None}
    ident = str(run.get("observer_id") or "")
    raw_root = run.get("research_root")
    valid_binding = (re.fullmatch(r"[a-f0-9]{16}", ident) is not None
                     and isinstance(raw_root, (str, Path)) and bool(str(raw_root)))
    root = Path(raw_root) if valid_binding else None
    valid_binding = bool(valid_binding and root and root.is_absolute())
    if not valid_binding:
        return {"visible": False, "available": False, "state": "missing", "label": _STATES["missing"],
                "title": "科研总报告", "href": "", "message": "完成后生成总报告",
                "latest_job_state": None}

    key = str(root)
    if cache is not None and key in cache:
        metadata = cache[key]
    else:
        metadata = report_metadata(root)
        if cache is not None:
            cache[key] = metadata
    state = str(metadata.get("state") or "missing")
    if state not in _STATES:
        state = "unavailable"
    available = bool(metadata.get("available")) and state in {"ready", "needs_revision"}
    latest = metadata.get("latest_job_state")
    latest = latest if isinstance(latest, str) and latest in _STATES else None
    message = {
        "ready": "阅读已生成的最终总报告", "needs_revision": "当前报告有待修订的审核意见",
        "queued": "总报告已排队", "running": "总报告正在生成", "collecting": "总报告正在整理来源",
        "composing": "总报告正在撰写", "reviewing": "总报告正在独立审核",
        "failed": "总报告生成失败", "missing": "完成后生成总报告", "unavailable": "总报告暂时无法读取",
    }[state]
    if available and latest in {"queued", "running", "collecting", "composing", "reviewing"}:
        message = "新版本正在生成，当前报告仍可阅读"
    elif available and latest == "failed":
        message = "新版本生成失败，当前报告仍可阅读"
    label = _STATES[state]
    if state == "missing" and str(run.get("state") or "").upper() in {"COMPLETE", "COMPLETED"}:
        label, message = "尚未生成", "此任务尚未生成总报告"
    return {"visible": True, "available": available, "state": state, "label": label,
            "title": str(metadata.get("title") or "科研总报告"),
            "href": "/research-explorer/?" + urlencode({"observer_id": ident, "final_report": "1"}) if available else "",
            "message": message, "latest_job_state": latest}
