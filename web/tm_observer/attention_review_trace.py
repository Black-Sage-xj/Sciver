"""Read-only, strictly incident/ref-bound SDK-internal Attention Reviewer archives."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .important_events import read_important_events
from .run_monitor import redact
from .stage_conversation import _root
from .budget_review_trace import _safe_text

INCIDENT = re.compile(r"INC-[0-9a-f]{16}\Z")
ARCHIVE = re.compile(r"sdk_internal_review_[0-9]{2,4}(?:_[0-9a-f]{32})?\.json\Z")
NATIVE = re.compile(r"reviewer_visible_thread_[a-f0-9]{32}\.jsonl\Z")
MAX_RECEIPT = 200_000
MAX_THREAD = 100_000_000
MAX_LINE = 9_000_000
PAGE_SIZE = 15
MAX_CURSOR = 35_000


def _safe_file(folder: Path, name: str, pattern: re.Pattern[str], limit: int) -> Path | None:
    if not pattern.fullmatch(name):
        return None
    path = folder / name
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            return None
        if path.resolve(strict=True).parent != folder.resolve(strict=True):
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    return path


def attention_review_trace(run: dict[str, Any], *, cycle: str, incident: str,
                           review_ref: str = "", cursor: int = 0,
                           full_item: int | None = None) -> dict[str, Any]:
    if not INCIDENT.fullmatch(incident) or (review_ref and not ARCHIVE.fullmatch(review_ref)):
        raise ValueError("invalid incident/review reference")
    if type(cursor) is not int or not 0 <= cursor <= MAX_CURSOR:
        raise ValueError("invalid item cursor")
    if full_item is not None and (type(full_item) is not int or not 0 <= full_item <= MAX_CURSOR):
        raise ValueError("invalid full item index")
    folder = _root(run, cycle)
    events = read_important_events(dict(run, cycle_dir=str(folder)))
    registered = [
        row for row in events
        if row.get("_cycle_name") == folder.name and row.get("incident_id") == incident
        and row.get("lifecycle") == "repair_handoff"
        and isinstance(row.get("repair"), dict)
        and row["repair"].get("target") == "sdk:internal_attention"
        and (not review_ref or row["repair"].get("review_ref") == review_ref)
    ]
    if not registered:
        raise ValueError("no registered internal Agent handoff for this review")
    empty = {
        "available": False, "cycle": folder.name, "incident_id": incident,
        "notice": "本次接管没有可读取的内部 Reviewer 归档；不能由 Investigator 对话重建。"
    }
    directory = folder / "attention"
    try:
        if directory.is_symlink() or not directory.is_dir():
            return empty
        paths = ([directory / review_ref] if review_ref else
                 sorted(p for p in directory.iterdir() if ARCHIVE.fullmatch(p.name)))
    except OSError:
        return empty
    matches: list[dict[str, Any]] = []
    for path in paths[:100]:
        verified = _safe_file(directory, path.name, ARCHIVE, MAX_RECEIPT)
        if verified is None:
            continue
        try:
            record = json.loads(verified.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
        if (isinstance(record, dict)
                and record.get("schema") == "theory-master/internal-attention-review/v1"
                and record.get("incident_id") == incident):
            matches.append(record)
    # Legacy handoffs don't identify a unique receipt. Never substitute
    # another invocation's dialogue if multiple receipts share the incident.
    if len(matches) != 1:
        if len(matches) > 1:
            return dict(empty, notice="旧事件存在多次同一 Incident 接管，但没有独立归档引用；无法安全识别该次对话。")
        return empty
    record = matches[0]
    exchange = record.get("exchange") if isinstance(record.get("exchange"), dict) else {}
    prompt, prompt_cut = _safe_text(exchange.get("prompt") or "", 100_000)
    output, output_cut = _safe_text(exchange.get("model_output") or "", 100_000)
    native = exchange.get("visible_thread") if isinstance(exchange.get("visible_thread"), dict) else {}
    thread_name = native.get("file")
    thread = _safe_file(directory, thread_name, NATIVE, MAX_THREAD) if isinstance(thread_name, str) else None
    if full_item is not None and thread is None:
        raise ValueError("no saved native message at requested index")
    response = {
        "available": True, "cycle": folder.name, "incident_id": incident,
        "at": redact(str(record.get("at") or ""))[:80],
        "attention_code": redact(str(record.get("attention_code") or ""))[:120],
        "model": redact(str(record.get("model") or ""))[:120],
        "action": redact(str(record.get("action") or ""))[:80],
        "reason": redact(str(record.get("reason") or ""))[:8000],
        "guidance": redact(str(record.get("guidance") or ""))[:8000],
        "exchange_recorded": bool(prompt and output),
        "native_status": "available" if thread is not None else "not_recorded",
        "native_archive_truncated": bool(native.get("archive_truncated")),
        "native_notice": ("已归档 Provider 可见的多轮消息和可用工具结果；模型私有推理不包含在内。"
                          if thread is not None else
                          "没有留存 Reviewer 原生多轮/工具记录；仅展示实际保存的 Prompt 与最终答复。"),
        "notice": ("实际 SDK 输入输出已保存；不包含私有推理。"
                   if prompt and output else
                   "此次历史记录只留存决策与理由，未归档 Reviewer 原始问答；不可重建。"),
        "messages": [], "next_cursor": None,
    }
    if cursor == 0 and full_item is None:
        response.update({"prompt":prompt, "model_output":output,
                         "prompt_truncated":prompt_cut, "model_output_truncated":output_cut})
    if thread is None:
        return response
    position = 0
    messages: list[dict[str, Any]] = []
    with thread.open("rb") as stream:
        for line in stream:
            if len(line) > MAX_LINE or position > MAX_CURSOR + PAGE_SIZE:
                raise ValueError("Reviewer archive exceeds reading limits")
            target = full_item if full_item is not None else cursor
            if position < target:
                position += 1
                continue
            if full_item is not None and position > full_item:
                break
            if full_item is None and len(messages) >= PAGE_SIZE + 1:
                break
            try:
                item = json.loads(line)
            except (ValueError, UnicodeError):
                raise ValueError("invalid native Reviewer archive item") from None
            if not isinstance(item, dict):
                raise ValueError("invalid Reviewer archive entry")
            content, cut = _safe_text(item.get("payload"), MAX_LINE if full_item is not None else 180_000)
            messages.append({
                "index":position, "turn":item.get("turn_index"),
                "item_index":item.get("item_index"),
                "type":redact(str(item.get("item_type") or ""))[:60],
                "content":content, "truncated":cut,
            })
            position += 1
            if full_item is not None:
                break
    if full_item is not None:
        if not messages or messages[0]["index"] != full_item:
            raise ValueError("native Reviewer item not found")
        return dict(response, messages=messages)
    more = len(messages) > PAGE_SIZE
    return dict(response, messages=messages[:PAGE_SIZE],
                next_cursor=cursor + PAGE_SIZE if more else None)
