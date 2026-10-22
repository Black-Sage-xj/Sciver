"""Read-only, per-approval Budget Reviewer execution trace projection.

The separate Reviewer-native SDK archive is not the Investigator research
dialogue. Never infer Reviewer speech/tools from Investigator source snapshots.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .budget_review_ui import read_budget_reviews
from .run_monitor import redact
from .stage_conversation import _root

REVIEW_FILE = re.compile(r"reviewer_dialogue_[0-9]{4}_[a-f0-9]{32}\.json\Z")
THREAD_FILE = re.compile(r"reviewer_visible_thread_[0-9]{4}_[a-f0-9]{32}\.jsonl\Z")
MAX_RECEIPT = 3_000_000
MAX_THREAD = 100_000_000
MAX_LINE = 9_000_000
PAGE_SIZE = 15
MAX_CURSOR = 35_000


def _regular_file(folder: Path, name: str, pattern: re.Pattern[str], cap: int) -> Path | None:
    if not pattern.fullmatch(name):
        return None
    path = folder / name
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > cap:
            return None
        if path.resolve(strict=True).parent != folder.resolve(strict=True):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return path


SECRET_FIELD = re.compile(r"(?:api[_-]?key|password|secret|credential|authorization|access[_-]?token|refresh[_-]?token)", re.I)


def _scrub_structure(value: Any, depth: int = 0) -> Any:
    if depth > 16:
        return "[nested data omitted]"
    if isinstance(value, dict):
        return {str(key): ("[redacted]" if SECRET_FIELD.search(str(key)) else
                           _scrub_structure(item, depth + 1))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub_structure(v, depth + 1) for v in value]
    return redact(value) if isinstance(value, str) else value


def _safe_text(value: Any, limit: int = 750_000) -> tuple[str, bool]:
    text = value if isinstance(value, str) else json.dumps(
        _scrub_structure(value), ensure_ascii=False, default=str, indent=2,
    )
    return redact(text[:limit]), len(text) > limit


def _receipt(folder: Path, review: dict[str, Any], index: int) -> dict[str, Any] | None:
    # Some historic retry receipts have a _ledgerNNNN suffix. Bind by
    # ledger's actual decision and reason, never another approval at same index.
    candidates = [folder / f"internal_review_{index:04d}.json"]
    candidates.extend(sorted(folder.glob(f"internal_review_{index:04d}_ledger[0-9][0-9][0-9][0-9].json")))
    for path in candidates[:40]:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RECEIPT:
                continue
            row = json.loads(path.read_text(encoding="utf-8"))
            if (isinstance(row, dict) and row.get("schema") == "theory-master/internal-budget-review/v1"
                    and row.get("review_index") == index
                    and row.get("decision") == review.get("decision")
                    and row.get("reason") == review.get("reason")):
                return row
        except (ValueError, OSError, UnicodeError):
            continue
    return None


def review_trace(run: dict[str, Any], *, cycle: str, review_index: int,
                 cursor: int = 0, full_item: int | None = None) -> dict[str, Any]:
    if type(review_index) is not int or not 1 <= review_index <= 9999:
        raise ValueError("invalid review index")
    if type(cursor) is not int or not 0 <= cursor <= MAX_CURSOR:
        raise ValueError("invalid review cursor")
    if full_item is not None and (type(full_item) is not int or not 0 <= full_item <= MAX_CURSOR):
        raise ValueError("invalid raw item offset")
    cycle_path = _root(run, cycle)
    selected_run = dict(run, cycle_dir=str(cycle_path))
    rows = read_budget_reviews(selected_run)
    chosen = next((r for r in rows if r.get("review_index") == review_index), None)
    if chosen is None:
        raise ValueError("review not present in the selected Cycle ledger")
    budget = cycle_path / "budget"
    receipt = _receipt(budget, chosen, review_index)
    unavailable = {
        "cycle":cycle_path.name, "review_index":review_index, "available":False,
        "status":"not_recorded", "messages":[], "next_cursor":None,
        "notice":"这次历史审批没有保存 Reviewer 自身的完整对话与工具执行记录。"
                 "已有 Investigator 对话快照是审查输入，不是 Reviewer 审查轨迹；不能据此重建。"
    }
    if receipt is None:
        return unavailable
    # Historical reviews already retained source-file read receipts and model
    # identity, but NOT the model's native Reviewer dialogue. Surface these
    # as a clearly labeled partial audit index, never as synthesized tool turns.
    paths = receipt.get("read_paths_reported_by_sdk") or []
    partial = {
        "model": redact(str(receipt.get("model") or ""))[:120],
        "provider": redact(str(receipt.get("provider") or ""))[:120],
        "reasoning_effort": redact(str(receipt.get("reasoning_effort") or ""))[:50],
        "reported_read_paths": [redact(str(item))[:500] for item in paths[:100]
                                if isinstance(item, str)] if isinstance(paths, list) else [],
    }
    trace = receipt.get("reviewer_trace") or {}
    name = trace.get("file") if isinstance(trace, dict) else None
    if not isinstance(trace, dict) or trace.get("status") != "available" or not isinstance(name, str):
        return dict(unavailable, partial=partial)
    recorded = _regular_file(budget, name, REVIEW_FILE, MAX_RECEIPT)
    if recorded is None:
        return dict(unavailable, status="archive_unavailable",
                    notice="Reviewer 轨迹索引存在，但原始归档已缺失或不可安全读取。")
    try:
        trace_info = json.loads(recorded.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError, OSError):
        return dict(unavailable, status="archive_unavailable")
    if (not isinstance(trace_info, dict)
            or trace_info.get("schema") != "theory-master/budget-reviewer-dialogue/v1"
            or trace_info.get("review_index") != review_index):
        return dict(unavailable, status="archive_invalid")
    summary = {
        "cycle":cycle_path.name,"review_index":review_index,"available":True,
        "model":redact(str(trace_info.get("model") or "")),
        "provider":redact(str(trace_info.get("provider") or "")),
        "thread_id":redact(str(trace_info.get("thread_id") or "")),
    }
    native = trace_info.get("visible_thread") or {}
    thread_name = native.get("file") if isinstance(native, dict) else None
    thread = (_regular_file(budget, thread_name, THREAD_FILE, MAX_THREAD)
              if isinstance(thread_name, str) else None)
    if full_item is not None and thread is None:
        raise ValueError("no retained Reviewer-native item")
    if full_item is None and cursor == 0:
        prompt, trunc = _safe_text(trace_info.get("prompt") or "")
        response, rtrunc = _safe_text(trace_info.get("model_output") or "")
        summary.update({
            "prompt":prompt, "prompt_truncated":trunc,
            "model_output":response, "model_output_truncated":rtrunc,
            "native_status":redact(str(native.get("status", "unavailable"))),
            "native_notice":("Provider-visible turns and available tool outputs are archived; "
                             "private reasoning is never included." if thread is not None else
                             "No Reviewer-native thread snapshot was saved; only actual prompt/final output."),
            "native_excluded_private":native.get("excluded_private_items",0),
            "native_archive_truncated":bool(native.get("archive_truncated")),
        })
    if thread is None:
        return dict(summary, messages=[], next_cursor=None)
    events: list[dict[str, Any]] = []
    position = 0
    with thread.open("rb") as handle:
        for line in handle:
            if len(line) > MAX_LINE or position > MAX_CURSOR + PAGE_SIZE:
                raise ValueError("archive item exceeds reading limits")
            if position < (full_item if full_item is not None else cursor):
                position += 1
                continue
            if full_item is None and len(events) >= PAGE_SIZE + 1:
                break
            if full_item is not None and position > full_item:
                break
            position += 1
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                raise ValueError("corrupt archived Reviewer item") from None
            if not isinstance(row, dict):
                raise ValueError("invalid archived Reviewer item")
            data = row.get("payload")
            content, cut = _safe_text(data, MAX_LINE if full_item is not None else 180_000)
            events.append({
                "index": position - 1, "turn":row.get("turn_index"),
                "item_index":row.get("item_index"),
                "type":redact(str(row.get("item_type") or ""))[:60],
                "content":content, "truncated":cut,
            })
            if full_item is not None:
                break
    if full_item is not None:
        if not events or events[0]["index"] != full_item:
            raise ValueError("Reviewer item does not exist")
        return {**summary, "messages":events,"next_cursor":None}
    more = len(events) > PAGE_SIZE
    return {**summary, "messages":events[:PAGE_SIZE],
            "next_cursor": cursor + PAGE_SIZE if more else None}
