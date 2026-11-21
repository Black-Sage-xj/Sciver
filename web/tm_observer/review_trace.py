"""Read-only excerpts for reviewing one Cycle without replaying the SDK."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .run_events_ui import ROLE_NAMES
from .run_monitor import read_json, redact
from .run_views import _cost_label, _tokens
from .stage_conversation import INV_ID, _index, _read_jsonl, _root, cycles

PAGE_SIZE = 8
COMPLETIONS = {"AGENT_COMPLETED", "AGENT_TURN_COMPLETED", "JUDGE_COMPLETED"}


def _text(value) -> str:
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return redact(str(value)) if value is not None else ""


def _excerpt(value: str, limit: int) -> str:
    # Keep link labels in excerpts. Paths and URLs remain available in full output.
    value = re.sub(r"\[([^]\n]+)\]\((?:/|https?://)[^\s)]+\)", r"\1", value)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def _decoded(value):
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return json.loads(value)
        except (ValueError, RecursionError):
            pass
    return value


def _summary(value) -> str:
    """Quote decision-bearing fields, retaining labels and explicit omissions."""
    value = _decoded(value)
    if not isinstance(value, (list, dict)):
        return _excerpt(_text(value), 420)
    parts = []
    fields = {"verdict", "rationale", "hypothesis", "scope", "statement", "result",
              "reason", "summary", "title", "conclusions", "status", "canvas_claim",
              "requested_resolution", "decision", "needs_literature"}

    def collect(node, depth=0):
        if depth > 8 or len(parts) >= 4:
            return
        if isinstance(node, list):
            for child in node:
                collect(child, depth + 1)
                if len(parts) >= 4:
                    break
        elif isinstance(node, dict):
            for key, item in node.items():
                if key in fields and not isinstance(item, dict) and item not in (None, "", []):
                    text = item[0] if isinstance(item, list) else item
                    parts.append(f"{key} · {_excerpt(_text(text), 180)}")
                elif isinstance(item, (dict, list)):
                    collect(item, depth + 1)
                if len(parts) >= 4:
                    break

    collect(value)
    return _excerpt(" / ".join(parts) or _text(value), 420)


def _outputs(messages, delivery):
    result = []
    for event in messages:
        if (event.get("event") == "LLM_MESSAGE" and
                event.get("message_role") in (None, "", "assistant") and event.get("content")):
            result.append(("模型", event["content"]))
    # The completion ledger often stores the very same JSON with new whitespace.
    if delivery and (not result or _decoded(result[-1][1]) != _decoded(delivery)):
        result.append(("阶段交付", delivery))
    return result


def _brief_outputs(outputs):
    selected = outputs[-2:]
    if len(selected) == 2 and selected[-1][0] == "阶段交付":
        raw, delivery = selected[0][1], selected[-1][1]
        # When a model appends prose to valid contract JSON, the SDK may accept
        # its JSON prefix. Expose the discarded suffix without repeating the JSON.
        if isinstance(raw, str) and raw.lstrip().startswith(("{", "[")):
            try:
                stripped = raw.lstrip()
                parsed, offset = json.JSONDecoder().raw_decode(stripped)
                suffix = stripped[offset:].strip()
                if parsed == _decoded(delivery) and suffix:
                    return [("阶段交付", _summary(delivery)), ("模型附文", _excerpt(_text(suffix), 180))]
            except (ValueError, RecursionError):
                pass
    return [("模型摘录" if label == "模型" else "交付摘录", _summary(value)) for label, value in selected]


def _stage_inputs(stage, messages):
    return [(e.get("input_kind") or "input", e["content"]) for e in messages
            if e.get("event") == "AGENT_INPUT" and e.get("stage") != "thread_initialization"
            and isinstance(e.get("content"), str) and e.get("stage") in (None, "", stage["stage"])]


def _prompts(folder, stage, messages):
    prompts = _stage_inputs(stage, messages)
    # Thread bootstrap instructions precede AGENT_STARTED and may sit outside
    # its indexed messages. Match the next start for that role in ledger order.
    rows, _ = _read_jsonl(folder, "runtime_events.jsonl")
    pending = []
    for i, event in enumerate(rows):
        if (event.get("role") or event.get("agent")) != stage["role"]:
            continue
        if event.get("event") == "AGENT_INPUT" and event.get("stage") == "thread_initialization":
            pending.append((event.get("input_kind") or "thread_initialization", event.get("content")))
        if event.get("event") == "AGENT_STARTED":
            ident = event.get("invocation_id") or f"legacy-{folder.name}-{stage['role']}-{i}"
            if ident == stage["id"]:
                prompts = pending + prompts
                break
            pending = []
    result = []
    for kind, value in prompts:
        label = kind
        if stage["role"] == "investigator" and kind in {"task", "continuation"}:
            label += "（历史逻辑 Stage Prompt）"
        result.append((f"{label} · {len(value):,} 字符" if isinstance(value, str) else label, _text(value)))
    return result


def _money(cost):
    if not isinstance(cost, dict) or cost.get("amount") is None:
        return "未计价"
    label = _cost_label(cost)
    if cost.get("source") == "provider_reported":
        label += " 实报"
    if cost.get("confidence") == "partial":
        label += f"（仅 {cost.get('priced_model_calls')}/{cost.get('model_calls')} 已计价）"
    return label


def _billing(run, folder):
    # Both sources are Cycle scoped. Never sum cumulative TOKEN_USAGE updates.
    state = read_json(dict(run, cycle_dir=str(folder)), "run_state.json")
    summary = {}
    file = folder / "cost_summary.json"
    if not file.is_symlink() and file.is_file() and file.stat().st_size <= 1_000_000:
        try:
            data = json.loads(file.read_bytes())
            if isinstance(data, dict):
                summary = data
        except (ValueError, OSError):
            pass
    cost = summary.get("cost") or state.get("cycle_cost")
    usage = summary.get("usage") or {}
    total = (f"{_money(cost)} · 输入 {_tokens(usage.get('input_tokens', state.get('cycle_tokens_in')))}"
             f" / 输出 {_tokens(usage.get('output_tokens', state.get('cycle_tokens_out')))} tokens")
    if isinstance(usage.get("cached_input_tokens"), int):
        total += f" · 其中缓存 {_tokens(usage['cached_input_tokens'])}"
    roles = []
    agents = {**(state.get("agents") or {}), **(state.get("auxiliary_agents") or {})}
    costs_by_role = summary.get("by_agent") or {}
    for role in dict.fromkeys([*ROLE_NAMES, *costs_by_role, *agents]):
        name = ROLE_NAMES.get(role) or _text(role)
        agent = agents.get(role) or {}
        charge = costs_by_role.get(role) or agent.get("cost") or {}
        if not (charge.get("model_calls") or agent.get("turn_count") or charge.get("amount") is not None):
            continue
        records = agent.get("cost_records") or []
        models = list(dict.fromkeys(_text(r["model"]) for r in records if isinstance(r, dict) and r.get("model")))
        label = " / ".join(models)
        usage_row = (summary.get("usage_by_role") or {}).get(role) or {}
        counts = ""
        tokens_in = usage_row.get("input_tokens", agent.get("tokens_in"))
        tokens_out = usage_row.get("output_tokens", agent.get("tokens_out"))
        if tokens_in is not None or tokens_out is not None:
            counts = f"输入 {_tokens(tokens_in)} / 输出 {_tokens(tokens_out)}"
        roles.append(" · ".join(filter(None, [name, label, _money(charge), counts])))
    return total, roles


def project(run, *, cycle="", page=1, stage_id="", view=""):
    catalog = cycles(run) if run.get("research_root") and Path(run["research_root"]).is_dir() else []
    chosen = cycle or (str(run.get("cycle")) if str(run.get("cycle")) in catalog else "")
    chosen = chosen or (catalog[-1] if catalog else "")
    if (chosen and chosen not in catalog) or (stage_id and not INV_ID.fullmatch(stage_id)):
        raise ValueError("找不到所选 Cycle 或阶段")
    if view not in {"", "prompt", "output"} or bool(view) != bool(stage_id) or page < 1:
        raise ValueError("轨迹查询参数无效")
    trace = dict(cycle=chosen, cycles=catalog, task=_text(run.get("task_name")), stages=[],
                 warnings=[], billing="", role_costs=[], total=0, page=page, pages=1, view=view)
    if not chosen:
        if stage_id or page != 1:
            raise ValueError("此任务尚无阶段记录")
        return trace
    folder = _root(run, chosen)
    index = _index(folder)
    trace["warnings"] = list(dict.fromkeys(index["warnings"]))
    stages = index["stages"]
    trace["total"] = len(stages)
    trace["pages"] = max(1, (len(stages) + PAGE_SIZE - 1) // PAGE_SIZE)
    if stage_id:
        position = next((i for i, s in enumerate(stages) if s["id"] == stage_id), None)
        if position is None:
            raise ValueError("找不到所选阶段")
        trace["page"] = position // PAGE_SIZE + 1
        positions = [position]
    else:
        if page > trace["pages"]:
            raise ValueError("找不到所选轨迹页")
        positions = range((page - 1) * PAGE_SIZE, min(page * PAGE_SIZE, len(stages)))
        trace["billing"], trace["role_costs"] = _billing(run, folder)

    deliveries = {}
    routes = []
    for event in index["transitions"]:
        payload = event["payload"]
        if event["kind"] == "role.completed" and payload.get("invocation_id"):
            deliveries[payload["invocation_id"]] = (payload.get("output"), event["time"])
        if event["kind"] == "investigation.routing":
            routes.append(event)
    # Older traces may only contain a runtime Judge / Investigator completion.
    for stage in stages:
        ident = stage["id"]
        if ident not in deliveries:
            last = next((e for e in reversed(index["messages"][ident])
                         if e.get("event") in COMPLETIONS and e.get("invocation_id") in (None, "", ident)), None)
            if last:
                deliveries[ident] = (last.get("output"), _text(last.get("ts")))
    for pos in positions:
        stage = stages[pos]
        ident = stage["id"]
        role = stage["role"]
        messages = [e for e in index["messages"][ident] if e.get("invocation_id") in (None, "", ident)]
        delivered, end = deliveries.get(ident, (None, ""))
        outputs = _outputs(messages, delivered)
        handoff = ""
        if pos and stages[pos - 1]["role"] != role:
            prev = stages[pos - 1]
            prev_end = deliveries.get(prev["id"], (None, ""))[1] or prev["end"]
            if prev_end and prev_end <= stage["start"]:
                handoff = f"{ROLE_NAMES[prev['role']]} → {ROLE_NAMES[role]}"
                matches = [e for e in routes if prev_end <= e["time"] <= stage["start"]
                           and e["payload"].get("selected_executor") == role]
                if len(matches) == 1:
                    reason = matches[0]["payload"].get("decision")
                    if reason:
                        handoff += " · " + _excerpt(_text(reason), 80)
        if view == "prompt":
            lines = _prompts(folder, stage, messages)
        elif view == "output":
            lines = [(name, _text(value)) for name, value in outputs]
        else:
            lines = _brief_outputs(outputs)
        stage_inputs = [value for kind, value in _stage_inputs(stage, messages)
                        if not (role == "investigator" and kind in {"task", "continuation"})]
        prompt = ""
        if stage_inputs:
            prompt = f"阶段输入 {sum(map(len, stage_inputs)):,} 字符"
        elif isinstance(stage["prompt_chars"], int):
            prompt = f"启动登记 {stage['prompt_chars']:,} 字符"
        state = stage["state"]
        if state in {"running", "prepared"} and (end or stage["end"]):
            state = "completed"
        trace["stages"].append(dict(id=ident, role=ROLE_NAMES[role], stage=stage["stage"],
                                    time=stage["start"], state=state, prompt=prompt,
                                    handoff=handoff, lines=lines, omitted=max(0, len(outputs) - 2)))
    return trace
