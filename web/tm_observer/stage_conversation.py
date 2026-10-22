"""Read-only, bounded Stage Conversation projection of canonical research logs.

The Observatory never invokes the scientific SDK to construct this view.
Stages are identified by AGENT_STARTED/invocation ledger, not inferred from
six fixed roles. Turn and context claims only use recorded identifiers.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .run_monitor import CYCLE_NAME, redact

MAX_LOG_BYTES = 32_000_000
MAX_LINE_BYTES = 2_000_000
MAX_EVENTS = 35_000
PAGE_SIZE = 45
INV_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
SOURCES = ("runtime_events.jsonl", "invocations.jsonl", "events.jsonl")
ROLES = frozenset({"research_lead","challenger","investigator","literature","judge","reporter"})

def _clean(value: Any, limit: int = 120000) -> str:
    return redact(str(value if value is not None else ""))[:limit]

def _root(run: dict[str, Any], cycle: str = "") -> Path:
    research = Path(str(run["research_root"])).resolve(strict=True)
    name = cycle or str(run.get("cycle") or "")
    if not CYCLE_NAME.fullmatch(name):
        raise ValueError("unknown cycle")
    directory = (research / name).resolve(strict=True)
    if directory.parent != research or not directory.is_dir():
        raise ValueError("invalid cycle path")
    return directory

def cycles(run: dict[str, Any]) -> list[str]:
    research = Path(str(run["research_root"])).resolve(strict=True)
    found = []
    for entry in research.iterdir():
        if entry.is_dir() and not entry.is_symlink() and CYCLE_NAME.fullmatch(entry.name):
            found.append(entry.name)
    def order(v: str) -> tuple[int, str]:
        return (int(v.removeprefix("cycle-")), v)
    return sorted(found, key=order)

def _read_jsonl(folder: Path, name: str) -> tuple[list[dict[str, Any]], list[str]]:
    if name not in SOURCES:
        raise ValueError("invalid source")
    file = folder / name
    warnings = []
    if not file.exists():
        return [], [f"{name}: unavailable"]
    if not file.is_file() or file.is_symlink():
        return [], [f"{name}: rejected"]
    records = []
    size = file.stat().st_size
    if size > MAX_LOG_BYTES:
        warnings.append(f"{name}: source exceeds {MAX_LOG_BYTES} bytes; showing bounded prefix")
    try:
        with file.open("rb") as handle:
            read_bytes = 0
            while read_bytes < MAX_LOG_BYTES and len(records) < MAX_EVENTS:
                line = handle.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                read_bytes += len(line)
                if len(line) > MAX_LINE_BYTES:
                    warnings.append(f"{name}: oversized record omitted")
                    # Consume remaining bytes of one oversize line without decoding.
                    while line and not line.endswith(b"\n"):
                        line = handle.readline(MAX_LINE_BYTES + 1)
                        read_bytes += len(line)
                    continue
                try:
                    record = json.loads(line.decode("utf-8"))
                except (UnicodeError, ValueError):
                    continue
                if isinstance(record, dict):
                    records.append(record)
            if len(records) == MAX_EVENTS:
                warnings.append(f"{name}: event cap reached")
    except OSError:
        warnings.append(f"{name}: unreadable")
    return records, warnings

def _index(folder: Path) -> dict[str, Any]:
    runtime, wr = _read_jsonl(folder, "runtime_events.jsonl")
    invocations, wi = _read_jsonl(folder, "invocations.jsonl")
    sdk, ws = _read_jsonl(folder, "events.jsonl")
    stage_list: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    by_thread: dict[str, str] = {}
    latest_by_role: dict[str, str] = {}
    latest_thread_by_role: dict[str, str] = {}
    counter_by_role: dict[str, int] = {}
    active: str | None = None
    # Stage invocation metadata: authoritative ledger can improve state.
    ledger: dict[str, dict[str, Any]] = {}
    for record in invocations:
        inv = record.get("invocation_id")
        if isinstance(inv, str) and INV_ID.fullmatch(inv):
            entry = ledger.setdefault(inv, {"state": "prepared", "events": []})
            entry["events"].append({"event":_clean(record.get("event"), 100),
                                     "at":_clean(record.get("recorded_at"), 64),
                                     "state":_clean(record.get("state"), 50)})
            for key in ("state", "stage", "role", "thread_id", "execution_mode",
                        "predecessor_invocation_id", "commit_state", "contract_ref"):
                if record.get(key) is not None:
                    entry[key] = record[key]
    events_by_stage: dict[str, list[dict[str, Any]]] = {}
    for seq, event in enumerate(runtime):
        kind = str(event.get("event") or event.get("type") or "")
        role = str(event.get("role") or event.get("agent") or "").lower()
        thread = str(event.get("thread_id") or "")
        if kind == "AGENT_STARTED" and role in ROLES:
            inv = str(event.get("invocation_id") or f"legacy-{folder.name}-{role}-{seq}")
            if not INV_ID.fullmatch(inv):
                inv = f"legacy-{folder.name}-{role}-{seq}"
            counter_by_role[role] = counter_by_role.get(role, 0) + 1
            prev = latest_by_role.get(role)
            resumed_thread = str(event.get("resume_thread_id") or "")
            meta = ledger.get(inv, {})
            prior_thread = latest_thread_by_role.get(role, "")
            # A matching thread ID is proof of native continuity; a declared
            # resume_thread_id also establishes an explicit reuse intent.
            same = bool((thread and prior_thread and thread == prior_thread) or
                        (resumed_thread and prior_thread and resumed_thread == prior_thread))
            relation = ("same_thread" if same else "new_thread" if thread and prior_thread and
                        thread != prior_thread else "unknown")
            stage_name = _clean(event.get("stage") or meta.get("stage"),90)
            previous_entry = by_id.get(prev or "")
            stage = {"id":inv, "role":role, "stage":stage_name,
                     "previous_stage_name":previous_entry.get("stage", "") if previous_entry else "",
                     "round":counter_by_role[role], "start":_clean(event.get("ts"),64),
                     "end":"", "state":_clean(meta.get("state") or "running",48),
                     "thread_id":_clean(thread or meta.get("thread_id"),128),
                     "resume_thread_id":_clean(resumed_thread,128),
                     "predecessor":_clean(meta.get("predecessor_invocation_id") or prev,128),
                     "context_mode":relation, "previous_stage":prev or "",
                     "contract":_clean(event.get("contract_id") or "",140),
                     "prompt_chars":event.get("prompt_chars"), "count":0,
                     "invocation_events":meta.get("events",[]),
                     "commit_state":_clean(meta.get("commit_state"),50)}
            stage_list.append(stage)
            by_id[inv] = stage
            events_by_stage[inv] = []
            active = inv
            latest_by_role[role] = inv
            if thread:
                latest_thread_by_role[role] = thread
                by_thread[thread] = inv
        assigned = ""
        if thread and thread in by_thread:
            assigned = by_thread[thread]
        elif role and role in latest_by_role:
            assigned = latest_by_role[role]
        elif active and role in ("", "system"):
            # A non-agent event is never silently presented as Agent's reply.
            assigned = ""
        if assigned in events_by_stage:
            tagged = dict(event)
            tagged["_seq"] = seq
            events_by_stage[assigned].append(tagged)
        if kind == "AGENT_COMPLETED" and assigned in by_id:
            by_id[assigned]["end"] = _clean(event.get("ts"),64)
            # Only the invocation ledger commits the stage, not this event.
    # Some legacy or interrupted invocations have ledger entries but no
    # runtime AGENT_STARTED. Preserve them with explicit missing-log status.
    for inv, meta in ledger.items():
        if inv in by_id:
            continue
        role = str(meta.get("role") or "")
        if role not in ROLES:
            continue
        counter_by_role[role] = counter_by_role.get(role, 0) + 1
        original = next((entry.get("at") for entry in meta.get("events", [])
                         if entry.get("event") == "invocation.prepared"), "")
        stage = {"id":inv,"role":role,"stage":_clean(meta.get("stage"),90),
                 "previous_stage_name":"", "round":counter_by_role[role],"start":_clean(original,64),"end":"",
                 "state":_clean(meta.get("state"),48),"thread_id":_clean(meta.get("thread_id"),128),
                 "resume_thread_id":"","predecessor":_clean(meta.get("predecessor_invocation_id"),128),
                 "context_mode":"unknown","previous_stage":"","contract":"",
                 "prompt_chars":None,"count":0,"invocation_events":meta.get("events",[]),
                 "commit_state":_clean(meta.get("commit_state"),50),"missing_runtime_events":True}
        stage_list.append(stage)
        by_id[inv] = stage
        events_by_stage[inv] = []
    stage_list.sort(key=lambda x:x.get("start") or "")
    for stage in stage_list:
        meta = ledger.get(stage["id"], {})
        if meta.get("state"):
            stage["state"] = _clean(meta["state"],48)
        if meta.get("commit_state"):
            stage["commit_state"] = _clean(meta["commit_state"],48)
        if meta.get("execution_mode") == "resume_thread":
            stage["context_mode"] = "same_thread"
        stage["count"] = len(events_by_stage[stage["id"]])
    # SDK records are independent transitions between the scientific Agents.
    transitions = []
    for record in sdk:
        kind = str(record.get("type") or "")
        if kind == "role.started":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        transitions.append({"time":_clean(record.get("at"),64),
                            "kind":_clean(kind,110), "payload":payload,
                            "seq":record.get("seq")})
    return {"stages":stage_list, "messages":events_by_stage,
            "transitions":transitions, "warnings":wr+wi+ws}

def _rework_source(index: dict[str, Any], stage: dict[str, Any]) -> dict[str, str] | None:
    if stage["round"] <= 1:
        return None
    # Rework status is not guessed from repeated role visits. Relate only
    # to an actual recorded Judge requested_resolution / rework event.
    starts_at = stage.get("start", "")
    if stage.get("role") == "judge":
        return None  # Judge's previous objection targets the next researcher.
    previous = next((entry for entry in index["stages"]
                     if entry["id"] == stage.get("previous_stage")), None)
    lower = previous.get("end") or previous.get("start") if previous else ""
    candidates = []
    for item in index["transitions"]:
        kind = str(item.get("kind") or "").lower()
        payload = item.get("payload") or {}
        judge_output = payload.get("output") if payload.get("role") == "judge" else None
        if not isinstance(judge_output, dict):
            judge_output = {}
        objection = judge_output.get("objection")
        recorded_request = (objection.get("requested_resolution") if isinstance(objection,dict) else "")
        if ("rework" in kind or (str(payload.get("verdict") or "").lower() == "rework")
                or (payload.get("role") == "judge" and bool(recorded_request))):
            if lower <= str(item.get("time") or "") <= starts_at:
                candidates.append(item)
    if not candidates:
        return None
    candidate = candidates[-1]
    payload = candidate.get("payload") or {}
    output = payload.get("output") if isinstance(payload.get("output"),dict) else {}
    objection = output.get("objection") if isinstance(output.get("objection"),dict) else {}
    return {"kind":candidate["kind"], "time":candidate["time"],
            "confirmed_rework":("rework" in candidate["kind"].lower()
                 or str(payload.get("verdict") or "").lower()=="rework"),
            "requested_resolution":_clean(payload.get("requested_resolution")
                    or objection.get("requested_resolution") or payload.get("reason"),6000)}

def _public_stage(stage: dict[str, Any], index: dict[str, Any]) -> dict[str, Any]:
    out = dict(stage)
    out["rework"] = _rework_source(index, stage)
    return out

def overview(run: dict[str, Any], cycle: str = "") -> dict[str, Any]:
    folder = _root(run, cycle)
    index = _index(folder)
    return {"cycle":folder.name, "cycles":cycles(run),
            "stages":[_public_stage(s,index) for s in index["stages"]],
            "transitions":[{"kind":item["kind"],"time":item["time"],"seq":item["seq"],
                            "summary":_clean(item["payload"],1300)}
                           for item in index["transitions"]],
            "warnings":index["warnings"], "source":"runtime_events.jsonl + invocations.jsonl + events.jsonl"}

def sdk_detail(run: dict[str, Any], *, cycle: str, sequence: int) -> dict[str, Any]:
    """Read verified SDK ledger event by sequence, never a client path."""
    if sequence < 0 or sequence > MAX_EVENTS:
        raise ValueError("invalid sdk sequence")
    folder = _root(run, cycle)
    events, warnings = _read_jsonl(folder, "events.jsonl")
    event = next((row for row in events if row.get("seq") == sequence), None)
    if event is None:
        raise ValueError("SDK event not found")
    return {"cycle":folder.name, "seq":sequence,
            "event":_clean(json.dumps(event, ensure_ascii=False, default=str),120_000),
            "warnings":warnings}


def transcript(run: dict[str, Any], *, cycle: str, stage_id: str,
               offset: int = 0) -> dict[str, Any]:
    if not INV_ID.fullmatch(stage_id):
        raise ValueError("invalid stage id")
    if offset < 0 or offset > MAX_EVENTS:
        raise ValueError("invalid event cursor")
    folder = _root(run, cycle)
    index = _index(folder)
    stage = next((s for s in index["stages"] if s["id"] == stage_id), None)
    if stage is None:
        raise ValueError("stage not found")
    source = index["messages"][stage_id]
    selected = source[offset:offset+PAGE_SIZE]
    messages = []
    for rec in selected:
        kind = str(rec.get("event") or rec.get("type") or "")
        content = rec.get("content")
        if not content and kind == "TOOL_ACTIVITY":
            content = rec.get("detail")
        if not content and kind == "AGENT_COMPLETED":
            content = rec.get("output")
        # A turn marker has no body by definition; a structured output may
        # be a dict rather than human prose. Preserve readable valid JSON.
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False, indent=2, default=str)
        recorded_chars = len(str(content if content is not None else ""))
        truncated = recorded_chars > (MAX_LINE_BYTES // 2)
        content = _clean(content, MAX_LINE_BYTES // 2)
        if kind == "AGENT_INPUT" and str(rec.get("role") or rec.get("agent") or "") == "investigator":
            # Display annotation only. The raw runtime event remains unchanged,
            # and content_chars still reports the unmodified source length.
            input_kind = str(rec.get("input_kind") or "")
            input_labels = {
                "native_goal_objective": "实际发送：Investigator Native Goal",
                "native_goal_setup_turn": "实际发送：SDK Setup Turn",
                "developer_instructions": "实际发送：Developer Instructions",
                "task": "历史逻辑 Stage Prompt（不能作为 Native Goal 实际输入）",
                "continuation": "历史逻辑 Stage Prompt（不能作为 Native Goal 实际输入）",
            }
            if input_kind in input_labels:
                content = f"[{input_labels[input_kind]}]\n{content}"
        if kind == "AGENT_INPUT" and str(rec.get("role") or "") in {"research_lead", "challenger"}:
            input_kind = str(rec.get("input_kind") or "")
            if input_kind in {"task", "continuation"}:
                # Ordinary Stage Turns are actual prompts (unlike old Investigator
                # logical projections). Keep raw content unchanged in event log.
                content = "[实际发送：普通 Stage Turn｜Stage / Procedure / Contract / Context]\n" + content
        # Preserve raw structured metadata for audit; no binary blobs, paths
        # are displayed as text rather than fetched from arbitrary targets.
        detail = {k: _clean(v, 3000) for k,v in rec.items()
                  if k not in {"content", "output", "detail"} and not k.startswith("_")}
        messages.append({"seq":rec.get("_seq"),"kind":_clean(kind,96),
                         "side":"prompt" if kind=="AGENT_INPUT" else
                                "system" if kind in {"AGENT_STARTED","AGENT_COMPLETED","TOKEN_USAGE","SKILL_LOADED"} else "agent",
                         "time":_clean(rec.get("ts"),64),
                         "thread_id":_clean(rec.get("thread_id"),128),
                         "turn_id":_clean(rec.get("turn_id"),128),
                         "content":content, "content_chars":recorded_chars,
                         "content_truncated":truncated, "meta":detail})
    next_cursor = offset + len(selected)
    return {"cycle":folder.name,"stage":_public_stage(stage,index),
            "messages":messages,"next_cursor":next_cursor if next_cursor<len(source) else None,
            "total":len(source),"warnings":index["warnings"],
            "tool_output_warning":"Tool activity records may omit stdout/stderr; no output is invented."}


def recent_event_detail(run: dict[str, Any], *, timestamp: str, kind: str,
                        role: str = "", hint: str = "") -> dict[str, Any]:
    """Resolve a Run-history row against exact persisted runtime events.

    Never infer from a neighboring timestamp. For same-second skill reads,
    the Skill identifier and load index in the canonical action text are
    required to disambiguate the matching source event.
    """
    from datetime import datetime
    timestamp = str(timestamp)[:64]
    kind = str(kind)[:96]
    role = str(role)[:48].lower()
    hint = str(hint)[:240]
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid event timestamp") from exc
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", kind):
        raise ValueError("invalid event kind")
    if role and role not in ROLES:
        raise ValueError("invalid event role")
    candidate_cycles = cycles(run)[-24:]
    for cycle in reversed(candidate_cycles):
        folder = _root(run, cycle)
        records, warnings = _read_jsonl(folder, "runtime_events.jsonl")
        matched = []
        for idx, entry in enumerate(records):
            recorded_ts = str(entry.get("ts") or "")
            recorded_role = str(entry.get("role") or entry.get("agent") or "").lower()
            if (recorded_ts != timestamp or str(entry.get("event") or "") != kind or
                (role and recorded_role != role)):
                continue
            if kind == "SKILL_LOADED":
                name = str(entry.get("skill") or "")
                if f"skill loaded · {name}" not in hint:
                    continue
                load_index = entry.get("load_index")
                index_mark = re.search(r"· load #(\d+)", hint)
                if index_mark and str(load_index) != index_mark.group(1):
                    continue
                if not index_mark and load_index not in (None, 1):
                    continue
            matched.append(idx)
        if not matched:
            continue
        if len(matched) != 1:
            return {"found": False, "reason": "多个事件具有相同标识，无法确认唯一来源，未进行猜测。"}
        selected = matched[0]
        def public(record: dict[str, Any], idx: int) -> dict[str, Any]:
            # The full source event is kept as escaped text, never used as
            # executable markup. Limit pathological outputs and redact secrets.
            data = json.dumps(record, ensure_ascii=False, indent=2, default=str)
            return {
                "index": idx, "time": _clean(record.get("ts"), 64),
                "kind": _clean(record.get("event"), 96),
                "role": _clean(record.get("role") or record.get("agent"), 50),
                "content": _clean(data, 12000),
                "truncated": len(data) > 12000,
            }
        # Resolve the same persisted event to its scientific Invocation and
        # zero-based Stage-page offset, never to a nearby, guessed message.
        stage_id = ""
        stage_offset = None
        index = _index(folder)
        for inv, stage_messages in index["messages"].items():
            for position, event in enumerate(stage_messages):
                if event.get("_seq") == selected:
                    stage_id, stage_offset = inv, position
                    break
            if stage_id:
                break
        start = max(0, selected - 3)
        stop = min(len(records), selected + 4)
        return {
            "found": True, "cycle": folder.name, "target": selected,
            "stage_id": stage_id, "stage_offset": stage_offset,
            "events": [public(records[i], i) for i in range(start,stop)],
            "warnings": warnings,
        }
    return {"found": False, "reason": "保存的执行事件中未找到与本行标识完全一致的记录。"}
