"""Read-only, provenance-checked native Investigator research dialogue for budget review.

The Budget Reviewer must see *source* conversation events, not just optional
Findings or a hand-picked set of workspace directories. This module provides
stable, de-duplicated conversation references; it makes no scientific judgement.
The provider thread snapshot is supplementary because historical observability
records do not retain command stdout or all native-tool results.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

DIALOGUE_EVENTS = frozenset({"LLM_MESSAGE", "TOOL_ACTIVITY"})
# Never archive private model reasoning / compaction text.
VISIBLE_THREAD_ITEMS = frozenset({
    "usermessage", "agentmessage", "commandexecution",
    "functioncalloutput", "mcptoolcall", "dynamictoolcall",
    "filechange", "websearch",
})
CONVERSATION_REF = re.compile(
    r"\Aconversation-sha256:(?P<first>[1-9]\d*)-(?P<last>[1-9]\d*):(?P<digest>[a-f0-9]{64})\Z"
)
MAX_SDK_ITEM_BYTES = 8 * 1024 * 1024
MAX_SDK_ARCHIVE_BYTES = 96 * 1024 * 1024


def _dialogue(cycle_dir: Path) -> list[dict[str, Any]]:
    """The exact persisted assistant messages and tool-command activity, in order.

    No summarization, topical filtering, or truncation. Token usage and other
    SDK bookkeeping are not scientific progress. A corrupt line is an input
    integrity failure, not evidence that no progress occurred.
    """
    path = Path(cycle_dir) / "runtime_events.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for number, raw in enumerate(source, 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid Investigator dialogue at {path}:{number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"nonobject Investigator dialogue at {path}:{number}")
            if row.get("agent") == "investigator" and row.get("event") in DIALOGUE_EVENTS:
                rows.append(row)
    return rows


def _hash_dialogue_segment(events: list[dict[str, Any]], first: int, last: int) -> str:
    digest = hashlib.sha256()
    for event in events[first - 1:last]:
        digest.update(json.dumps(
            event, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def verified_conversation_refs(
    cycle_dir: Path, refs: Iterable[str], *,
    events: list[dict[str, Any]] | None = None,
) -> set[str]:
    """Recompute SHA over literal persisted events for every claimed review ref."""
    current = _dialogue(cycle_dir) if events is None else events
    verified: set[str] = set()
    for ref in refs:
        match = CONVERSATION_REF.fullmatch(str(ref))
        if match is None:
            continue
        first, last = int(match["first"]), int(match["last"])
        if first > last or last > len(current):
            continue
        if _hash_dialogue_segment(current, first, last) == match["digest"]:
            verified.add(str(ref))
    return verified


def conversation_review_progress(
    cycle_dir: Path, reviewed_refs: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """One fresh SHA-verified suffix, not a new ref for unchanged history.

    Previously reviewed refs are accepted only after verifying their original
    segment. This prevents a second tranche being granted for the same dialogue,
    while allowing a still-running native Goal to accumulate further science.
    """
    events = _dialogue(cycle_dir)
    if not events:
        return []
    history = {str(ref) for ref in reviewed_refs if CONVERSATION_REF.fullmatch(str(ref))}
    old = verified_conversation_refs(cycle_dir, history, events=events)
    if history != old:
        # A previously approved source was deleted or changed. NEVER rebase
        # the history at event 1 and reward altered old science as new work.
        raise ValueError("previously reviewed Investigator conversation was modified or truncated")
    last_seen = max(
        (int(CONVERSATION_REF.fullmatch(ref)["last"]) for ref in old),
        default=0,
    )
    if last_seen >= len(events):
        return []
    first, last = last_seen + 1, len(events)
    stamp = _hash_dialogue_segment(events, first, last)
    selected = events[first - 1:last]
    return [{
        "invocation_id": f"conversation-sha256:{first}-{last}:{stamp}",
        "kind": "unfinished_native_goal_original_dialogue_not_finding",
        "first_event": first,
        "last_event": last,
        "assistant_messages": sum(e.get("event") == "LLM_MESSAGE" for e in selected),
        "tool_activities": sum(e.get("event") == "TOOL_ACTIVITY" for e in selected),
        "source": str((Path(cycle_dir) / "runtime_events.jsonl").resolve()),
    }]


def dialogue_source(cycle_dir: Path) -> dict[str, Any]:
    """Explain the real coverage of the existing human-visible event log."""
    path = Path(cycle_dir) / "runtime_events.jsonl"
    events = _dialogue(cycle_dir)
    return {
        "path": str(path.resolve()), "exists": path.is_file(),
        "format": "JSONL, chronological Investigator LLM_MESSAGE and TOOL_ACTIVITY",
        "event_count": len(events),
        "assistant_messages": sum(x.get("event") == "LLM_MESSAGE" for x in events),
        "tool_activities": sum(x.get("event") == "TOOL_ACTIVITY" for x in events),
        "coverage": "real assistant messages and tool activity labels; NOT full command stdout",
    }


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return value
    if hasattr(value, "__dict__"):
        return vars(value)
    raise TypeError(f"unsupported SDK thread item: {type(value).__name__}")


def write_sdk_thread_snapshot(response: Any, *, thread_id: str, destination: Path) -> dict[str, Any]:
    """Materialize provider-visible turns, including available tool results.

    Exclude private reasoning and compaction contents. Oversized SDK items are
    represented by size+hash placeholders, NEVER silently shortened. Reviewers
    can still inspect the separate scientific workspace and persisted event log.
    """
    thread = getattr(response, "thread", None)
    if thread is None and isinstance(response, dict):
        thread = response.get("thread")
    if thread is None:
        raise ValueError("SDK thread/read lacks thread")
    turns = getattr(thread, "turns", None) if not isinstance(thread, dict) else thread.get("turns")
    if turns is None:
        raise ValueError("SDK thread/read lacks turns")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".review-transcript-", suffix=".jsonl", dir=target.parent)
    retained = 0
    skipped_private = 0
    oversize = 0
    skipped_due_archive_limit = 0
    total_bytes = 0
    turn_count = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for turn_no, turn in enumerate(turns, 1):
                turn_count += 1
                # Read individual items only; serializing an entire native Turn
                # would duplicate huge tool outputs and private reasoning in RAM.
                turn_id = turn.get("id") if isinstance(turn, dict) else getattr(turn, "id", None)
                items = getattr(turn, "items", None) if not isinstance(turn, dict) else turn.get("items")
                items = items or []
                for item_no, item in enumerate(items, 1):
                    raw = getattr(item, "root", item) if not isinstance(item, dict) else item.get("root", item)
                    payload = _model_dump(raw)
                    kind = str(payload.get("type") or "").replace("_", "").replace("-", "").lower()
                    if kind not in VISIBLE_THREAD_ITEMS:
                        skipped_private += 1
                        continue
                    entry = {
                        "turn_index": turn_no, "turn_id": turn_id,
                        "item_index": item_no, "item_type": kind, "source_thread_id": thread_id,
                        "payload": payload,
                    }
                    rendered = json.dumps(entry, ensure_ascii=False, sort_keys=True)
                    item_bytes = rendered.encode("utf-8")
                    if len(item_bytes) > MAX_SDK_ITEM_BYTES or total_bytes + len(item_bytes) > MAX_SDK_ARCHIVE_BYTES:
                        oversize += 1
                        entry["payload"] = {
                            "omitted_oversize": True, "raw_bytes": len(item_bytes),
                            "sha256": hashlib.sha256(item_bytes).hexdigest(),
                        }
                        item_bytes = (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
                    else:
                        item_bytes += b"\n"
                    if total_bytes + len(item_bytes) > MAX_SDK_ARCHIVE_BYTES:
                        # Source archive is bounded. Report omitted items in the
                        # manifest rather than claiming complete tool-output coverage.
                        skipped_due_archive_limit += 1
                        continue
                    stream.write(item_bytes.decode("utf-8"))
                    retained += 1
                    total_bytes += len(item_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return {
        "path": str(target.resolve()), "status": "available",
        "source": "Codex SDK thread/read(include_turns=True)",
        "thread_id": thread_id, "turn_count": turn_count,
        "visible_items": retained, "excluded_private_items": skipped_private,
        "oversized_items_with_hash_only": oversize,
        "unrecorded_due_archive_limit": skipped_due_archive_limit,
        "archive_truncated": bool(oversize or skipped_due_archive_limit),
        "bytes": total_bytes,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "coverage": "visible SDK turns and available tool outputs; private reasoning excluded",
    }
