"""Await explicit literature requests in the host, without an idle Chat model.

General questions still reach Chat normally. Its CLI delegation remains available
for contextual or follow-up work not covered by this narrow explicit intent.
"""
from __future__ import annotations

from pathlib import Path
import re
import uuid


def explicit_literature_request(text: str) -> bool:
    if re.search(r"(?:不要|不用|先别|无需|不需要).{0,10}(?:搜|查|检索|调研|文献)|(?:do not|don't|without)\s+(?:search|research)", text, re.I):
        return False
    return bool(re.search(r"(?:搜搜|搜一下|搜索|查查|查一下|查阅|检索|调研).{0,20}(?:文献|论文|相关工作)|(?:文献|论文).{0,8}(?:检索|调研)|\b(?:search|find|survey|review)\b.{0,40}\b(?:papers|literature|publications|related work)\b", text, re.I))


def prepare_literature(state_dir: Path, search_root: Path | None, row: dict, *, cancel_event=None, on_event=None) -> dict | None:
    if row.get("mode") not in {"preparation", "project", "frontier"} or search_root is None:
        return None
    user_messages = [message for message in row.get("messages") or [] if message.get("role") == "user"]
    latest = user_messages[-1] if user_messages else {}
    retry_job = (row.get("active_run") or {}).get("literature_job_id")
    # Contextual/project requests are interpreted by Chat with its selected
    # sources and history. Only a fresh standalone request takes this shortcut.
    if not retry_job and (row.get("mode") != "preparation" or len(user_messages) != 1
                         or latest.get("sources") or latest.get("attachments")):
        return None
    text = str(latest.get("content") or "").strip()
    if not explicit_literature_request(text):
        return None
    from .research_chat_literature import run_study, read_report
    run_id = str((row.get("active_run") or {}).get("id") or latest.get("id") or "")
    if not run_id:
        return None
    job_id = retry_job or uuid.uuid5(uuid.NAMESPACE_URL, "sciver:chat-literature:" + str(row["id"]) + ":" + run_id).hex
    row["active_run"]["literature_job_id"] = job_id
    from . import research_chat
    store = research_chat._store(state_dir)
    if (store / (str(row["id"]) + ".json")).is_file():
        research_chat._write(store, row)
    labels = {"literature.planning_started": "正在规划文献检索方向", "literature.planning_completed": "检索方向已形成",
        "literature.retrieval_started": "正在检索与获取文献", "literature.retrieval_completed": "文献材料已更新",
        "literature.agent_started": "Literature 正在阅读与综合", "literature.agent_completed": "Literature 已完成一项分析",
        "literature.job_finished": "文献调研已返回"}
    def progress(event):
        label = labels.get(event.get("event"))
        if label and callable(on_event):
            on_event({"type": "tool", "id": "literature-" + job_id, "label": label,
                      "status": "complete" if event.get("event") == "literature.job_finished" else "running"})
    result = run_study(state_dir, search_root, row["id"], {"query": text,
        "job_id": job_id}, cancel_event=cancel_event, on_event=progress)
    if cancel_event is not None and cancel_event.is_set():
        from theory_master.codex_backend import CodexTurnCancelled
        raise CodexTurnCancelled("Literature work cancelled")
    report = read_report(state_dir, row["id"], job_id)
    return {"job_id": job_id, "status": result.get("status"), "agent_turns": result.get("agent_turns"),
        "source_count": result.get("source_count"), "source_access_counts": result.get("source_access_counts"),
        "report_href": result.get("report_href"), "report_markdown": report[:24000]}
