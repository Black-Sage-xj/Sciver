from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


FIELD_NOTES_SCHEMA = "tm-observer/workflow-field-notes/v2"
LEGACY_FIELD_NOTES_SCHEMA = "tm-observer/workflow-field-notes/v1"
FIELD_NOTES_FILENAME = "workflow_field_notes.json"
_LOCK = threading.Lock()


def field_key(role: str, stage: str, section: str, field: str) -> str:
    return "::".join((str(role), str(stage), str(section), str(field)))


_INPUT_DESCRIPTIONS = {
    "goal": "任务的人类定义总研究目标；用于约束本阶段提出的科学问题不能偏离原始研究目的。",
    "research_contract": "任务的人类冻结研究约束与成功边界；用于限定允许研究什么、最终需要回答什么。",
    "scientific_model_summary": "当前长期 Scientific Model 的紧凑摘要；帮助 Agent 避免重复已建立或已排除的科学结论。",
    "frontier_scientific_context": "不再单独注入。科学图谱输入来自 SQLite 派生的 agent_graph，Canvas 仅用于可视化。",
    "qualification_feedback": "上一轮候选资格审查的紧凑反馈；只在需要重新提出或修正候选时使用。",
    "lead_literature_result": "Research Lead 临时请求 Literature 后返回的外部学术结果摘要；用于完成同一次前沿判断。",
    "cycle_dir": "当前 Cycle 的工作目录路径；供 Agent 在确有需要时定位本轮产物。",
    "workspace_dir": "当前任务工作区路径；作为自主查找相关本地材料的稳定入口。",
    "notes_vault_root": "科研笔记库根目录；供 Agent 按需沿已有笔记与报告继续查找。",
    "repo_root": "项目仓库根目录；供 Agent 在科学任务需要时定位代码、配置或本地材料。",
    "qualified_candidates": "已通过 Challenger qualification 的候选集合；Research Lead 只能从这些候选中选择。",
    "repair_options": "需要修复的冻结候选及其 blocker；用于在不改变科学边界的前提下完成最小修复。",
    "active_hypothesis": "当前 Cycle 正在裁决的稳定 Hypothesis 科学内容。",
    "judge_decision": "Judge 对当前 Cycle 的终局裁决或终局科学判断。",
    "scientific_access": "Cycle 结束时访问 Scientific Model、Evidence 等长期科学材料的导航入口。",
    "candidate": "当前待 Challenger 审查的候选 Hypothesis 科学内容。",
    "workspace_access": "当前工作区的最小导航入口；Agent 可按需打开相关本地材料。",
    "agent_graph": "由 canonical science_graph.sqlite3 派生的只读科研图谱上下文。",
    "agent_graph_reuse": "同一 Cycle/Stage 续接时复用已注入的 SQLite 图谱上下文。",
    "science_graph_tools": "SQLite 图谱只读查询工具入口，用于按需展开 H/External 详情。",
    "frozen_objective": "Hypothesis Lock 后冻结的本轮科学目标与 decisive test；Investigator 不得擅自改变。",
    "previous_scientific_delta": "上一科学执行轮新增的最小科学变化；用于连续推进而不重放全部历史。",
    "judge_objections": "Judge 授权返工时给出的具体科学 objection；Investigator 只需解决该缺口。",
    "search_target": "本次 Literature 调用要解决的明确外部学术问题或检索目标。",
    "source_lock": "定向文献任务中锁定的具体来源线索，如 DOI、题名、作者或明确 claim。",
    "scholarly_retrieval_root": "SDK 已物化学术检索材料的根目录；Literature 在该目录下自主搜索和打开所需来源。",
    "hypothesis_lock": "当前冻结 Hypothesis、scope 与 decisive test 的权威锁定状态。",
    "investigation_result": "本轮 Investigation 汇总后的科学结果，供 Judge 做裁决。",
    "prior_objection": "同一 Judge 返工链上此前提出的 objection，用于判断是否已被解决。",
    "prior_adjudicated_context": "同一 Hypothesis 过去已经裁决过的科学结果，用于跨 Cycle 累积判断。",
    "hypothesis": "Reporter 要叙述的当前 Hypothesis。",
    "scientific_synthesis": "Cycle 终局后的科学综合结论，供 Reporter 形成可读报告。",
    "evidence_scope_digest": "已裁决 Evidence 的范围与限制摘要，防止 Reporter 把结论写得过强。",
    "terminal_decision": "已经确定的终局 Decision；Reporter 只能整理表达，不能改 verdict。",
    "report_title": "已经生成的人类可读报告标题。",
    "report_summary": "已经生成的人类可读报告摘要。",
    "research_plan_access": "Research Plan 的只读导航入口，用于 Goal-level 判断时读取权威 Goal、Contract 与长期科学状态。",
    "final_gate": "任务 Final Gate；用于判断 Candidate 或 tip 是否推进尚未满足的完成条件。",
}


_EXACT_OUTPUT_DESCRIPTIONS = {
    "selected_candidate_id": "Research Lead 最终选中的 qualified candidate 标识。",
    "selection_reason": "说明为什么在已合格候选中选择该候选作为本 Cycle 的 Hypothesis。",
    "selected_parent_candidate_id": "selection repair 中被选中继续修复的原候选标识。",
    "outcome": "说明本次最小修复是否成功关闭 blocker，或确认无法在原科学边界内修复。",
    "cannot_repair_reason": "当修复会改变冻结科学边界时，说明为什么必须停止而不能继续补丁。",
    "needs_literature": "表示 Research Lead 是否因外部学术知识缺口而需要一次 Literature 旁路。",
    "remaining_scientific_question": "当前阶段结束后仍必须解决的科学问题；为空表示本阶段目标已无必需缺口。",
    "verdict": "该 Judge 阶段给出的正式裁决结果。",
    "rationale": "支撑当前裁决或 Canvas 文案的简要理由。",
    "title": "面向人的报告标题。",
    "summary": "面向人的报告正文摘要。",
}


def _describe_output(field: str) -> str:
    if field in _EXACT_OUTPUT_DESCRIPTIONS:
        return _EXACT_OUTPUT_DESCRIPTIONS[field]
    rules = (
        ("candidates[].hypothesis", "候选的核心科学命题；应当可以独立被支持、反驳或继续推进。"),
        ("candidates[].scope", "候选 Hypothesis 的科学适用范围与边界。"),
        ("candidates[].hypothesis_id", "当候选继续同一个既有 Hypothesis 时引用其稳定 H ID；新 Hypothesis 则省略。"),
        ("candidates[].why_now", "解释为什么这个候选在当前科学前沿上值得现在研究。"),
        ("candidates[].goal_advance", "说明该候选若被研究，会如何推进总 Goal 的科学认识。"),
        ("candidates[].decisive_test", "本 Cycle 可执行、可区分结果的关键科学测试。"),
        ("candidates[].evolution_operator", "可选的 Hypothesis 谱系操作符；仅当 Candidate 是由已有 Hypothesis 派生出的 NEW Hypothesis 时使用。root/首个 Hypothesis、继续同一 Hypothesis、或没有合法 parent 时必须省略。"),
        ("literature_request.task_type", "Research Lead 请求的 Literature 工作类型，用于 SDK 选择 landscape 或 targeted source。"),
        ("literature_request.search_target", "交给 Literature 的外部学术检索目标。"),
        ("literature_request.why_external_needed", "解释为什么当前本地信息不足，必须请求外部学术材料。"),
        ("literature_request.expected_external_evidence", "说明什么外部学术证据足以让 Lead 完成当前 frontier 判断。"),
        ("literature_request.new_search_lead", "定向检索时提供的具体 DOI、题名、作者或来源线索。"),
        ("reviews[].verdict", "Challenger 对候选资格的结论：可直接进入、需要修复或应拒绝。"),
        ("reviews[].reason", "对资格结论的简要科学理由。"),
        ("reviews[].blocking_issues[].problem", "阻止候选通过 qualification 的具体科学问题。"),
        ("reviews[].blocking_issues[].required_change", "在不改变候选核心科学边界前提下，关闭该 blocker 所需的最小修改。"),
        ("repair.blocker_closures[].blocker_id", "指出当前修复针对哪个冻结 blocker。"),
        ("repair.blocker_closures[].resolution", "说明该 blocker 如何在原候选科学边界内被关闭。"),
        ("repair.hypothesis_clarification", "只有确有必要时，对冻结 Hypothesis 做不改变科学命题的定义澄清。"),
        ("repair.estimand_or_operational_definition", "只有确有必要时，补充可操作的 estimand 或定义。"),
        ("repair.repaired_decisive_test", "当原 decisive test 本身造成 blocker 时，给出保持原科学边界的修正版测试。"),
        ("findings[].statement", "Investigator 本轮建立的自包含科学结论，包含必要的成立前提、数据来源事实、方法与适用限制；格式化不得拆分已建立的 Finding。"),
        ("findings[].provenance.source", "只有强证据身份需要时，声明数据或证据的来源身份。"),
        ("findings[].provenance.split", "只有强证据身份需要时，声明 fit、validation、holdout、gate 等数据分割身份。"),
        ("findings[].provenance.declaration_ref", "被冻结的受保护证据身份所对应的权威声明引用。"),
        ("evidence_records[].source_ref", "本条 Literature evidence 对应的真实学术来源引用。"),
        ("evidence_records[].scientific_insight", "从该来源中实际建立、且与当前检索目标相关的科学结论。"),
        ("evidence_records[].exact_quote", "支持该 Literature evidence 的原文短引句，用于核验来源是否真的这样说。"),
        ("evidence_records[].locator.page", "原文证据所在页码。"),
        ("evidence_records[].locator.section", "原文证据所在章节。"),
        ("evidence_records[].locator.figure", "原文证据对应的图编号。"),
        ("evidence_records[].locator.equation", "原文证据对应的公式编号。"),
        ("evidence_records[].locator.paragraph", "原文证据所在段落位置。"),
        ("evidence_records[].interpretation", "说明该来源对当前 search target 实际能证明什么。"),
        ("evidence_records[].limitations", "该来源在验证程度、范围或解释上的重要限制。"),
        ("evidence_records[].source_access", "记录来源目前被核验到 metadata、abstract、full text 或 unavailable 的哪一级。"),
        ("landscape.field_scope", "Literature landscape 实际覆盖的学术领域范围。"),
        ("landscape.major_routes[].name", "该领域一条主要研究路线或方法路线的名称。"),
        ("landscape.major_routes[].focus", "该研究路线主要解决的问题或关注点。"),
        ("landscape.major_routes[].representative_papers[].source_ref", "该研究路线的代表性论文来源引用。"),
        ("landscape.major_routes[].representative_papers[].relevance", "解释该代表论文为什么属于这条研究路线、与当前问题有什么关系。"),
        ("landscape.major_routes[].representative_papers[].source_access", "该代表论文当前实际核验到的访问层级。"),
        ("landscape.representative_papers[].source_ref", "Landscape 中全局代表论文的来源引用。"),
        ("landscape.representative_papers[].relevance", "该代表论文与当前研究前沿的关系。"),
        ("landscape.representative_papers[].source_access", "该代表论文当前实际核验到的访问层级。"),
        ("landscape.open_questions[]", "文献中仍然公开未解决、且可能影响当前课题的科学问题。"),
        ("landscape.relation_to_current_research", "把外部文献地图与当前 Sciver 研究问题连接起来。"),
        ("landscape.recommended_reading_order[]", "建议后续优先阅读的来源顺序，只作为阅读导航。"),
        ("positioning.assessment", "对当前结果相对于已有工作的总体新颖性/重叠位置判断。"),
        ("positioning.closest_prior_work[].source_ref", "与当前结果最接近的已有工作来源。"),
        ("positioning.closest_prior_work[].overlap", "当前结果与最近邻已有工作重叠的部分。"),
        ("positioning.closest_prior_work[].difference", "当前结果相对于最近邻已有工作的真正差异。"),
        ("positioning.closest_prior_work[].source_access", "最近邻已有工作当前被核验到的访问层级。"),
        ("positioning.priority_risks[]", "可能削弱 novelty 或 priority 主张的已知风险。"),
        ("positioning.required_citations[]", "当前结果对外表述时必须引用的关键来源。"),
        ("objection.requested_resolution", "Judge 允许一次返工时，明确要求解决的最小科学问题。"),
        ("objection.needs_external_scholarship", "说明该 objection 是否必须依赖新的外部学术证据才能解决。"),
        ("scientific_synthesis.conclusions[]", "Cycle 终局后可以带入后续研究的已建立科学结论。"),
        ("scientific_synthesis.open_questions[]", "Cycle 终局后仍然没有解决的科学问题。"),
        ("scientific_model_update.action", "决定长期 Scientific Model 保持不变还是用新版本整体替换。"),
        ("scientific_model_update.model.established_knowledge[].id", "长期 Scientific Model 中一条已建立知识的稳定 ID。"),
        ("scientific_model_update.model.established_knowledge[].statement", "长期保留的已建立科学知识陈述。"),
        ("scientific_model_update.model.established_knowledge[].scope", "该长期知识成立的范围与边界。"),
        ("scientific_model_update.model.established_knowledge[].evidence_refs[]", "支撑该长期知识的已登记 Evidence 引用。"),
        ("scientific_model_update.model.established_knowledge[].limitations", "该长期知识的重要限制。"),
        ("scientific_model_update.model.established_knowledge[].status", "该长期知识在 Scientific Model 中的当前状态。"),
        ("scientific_model_update.model.current_scientific_picture", "对当前整体科学图景的长期、跨 Cycle 概括。"),
        ("decision.title", "Canvas 中终局 Decision 节点的人类可读标题。"),
        ("decision.summary", "Canvas 中终局 Decision 节点的人类可读摘要。"),
        ("frontier.title", "已授权 frontier 在 Canvas 上的人类可读标题。"),
        ("frontier.summary", "已授权 frontier 在 Canvas 上的人类可读摘要。"),
        ("updates[].node_id", "当前 Canvas scientific branch tip 的节点 id。"),
        ("updates[].frontier_status", "该 tip 的前沿状态：active、parked 或 closed。active 只表示仍有资格，不是下一 Cycle 必选。"),
        ("updates[].reason", "为什么该 tip 现在应为 active、parked 或 closed。"),
    )
    for key, description in rules:
        if field == key:
            return description
    if field.endswith(".title"):
        return "为该对象提供简短的人类可读标题。"
    if field.endswith(".summary"):
        return "为该对象提供简短的人类可读摘要。"
    if field.endswith(".scope"):
        return "说明该对象的科学适用范围和边界。"
    if field.endswith(".limitations"):
        return "说明影响该对象解释强度的重要限制。"
    if field.endswith(".source_ref"):
        return "记录该对象对应的真实来源引用，便于核验与追踪。"
    if field.endswith(".source_access"):
        return "记录该来源目前实际核验到的访问层级。"
    if field.endswith("[]"):
        return "返回该类信息的列表；每一项都应直接服务于当前 Stage 的科学任务。"
    return "当前 Stage 正式输出合同中的字段；用于把该阶段的科学判断传递给后续 SDK 或 Agent。"


def _describe_sdk(field: str, raw_description: str = "") -> str:
    if field == "remaining_gap.kind":
        return "SDK 用 remaining_gap.kind 做本 Cycle 内 next-executor 路由，不是下一 Cycle 的科学事实。"
    text = f"{field} {raw_description}".lower()
    if "budget" in text:
        return "SDK 用它检查剩余执行/返工预算，决定当前路径是否还能继续。"
    if "routing" in text or "next-executor" in text or "executor" in text:
        return "SDK 用它决定当前结果接下来应交给哪个执行路径或是否进入下一阶段。"
    if "evidence" in text and ("registration" in text or "id" in text):
        return "SDK 负责为科学结果登记稳定 Evidence 身份，并保证后续引用可追踪。"
    if "evidence" in text and ("admissibility" in text or "validation" in text or "binding" in text):
        return "SDK 校验 Evidence 是否可用于当前科学状态，并完成与 criterion/objection 等机器状态的绑定。"
    if "hypothesis" in text or "h catalog" in text:
        return "SDK 维护 Hypothesis 的稳定身份、锁定状态与跨 Cycle 连续性，Agent 不能自行改写这些机器身份。"
    if "candidate" in text and "id" in text:
        return "SDK 为候选维护稳定 ID，并校验候选与审查结果之间的正确配对。"
    if "scientific model" in text or "model" in text and "commit" in text:
        return "SDK 校验长期 Scientific Model 更新并以原子方式持久化，避免半提交状态。"
    if "canvas" in text:
        return "SDK 负责 Canvas 的确定性结构校验、绑定或落盘；Agent 只提供科学内容或可读文案。"
    if "continuation" in text or "cross-cycle" in text or "prior_cycle" in text:
        return "SDK 保存并投影跨 Cycle 需要延续的最小科学状态，避免把完整历史重新塞给 Agent。"
    if "qualification" in text:
        return "SDK 根据 Challenger 的资格判断推进 qualified、revise、reject 等流程分支。"
    if "literature" in text or "scholarly" in text:
        return "SDK 负责 Literature 调用所需的外部检索、材料物化或旁路控制，不把这些 transport 细节交给 Agent。"
    if "source metadata" in text or "verification" in text:
        return "SDK 回填并校验来源元数据与核验等级，防止未核实来源被当成正式 Evidence。"
    if "forced-open" in text:
        return "当授权返工无法继续时，SDK 强制把本 Cycle 终结为 open，避免无限循环。"
    if "criterion" in text or "c-1" in text:
        return "SDK 维护本 Cycle 的判据绑定，保证 Agent 科学输出与冻结 criterion 对应正确。"
    if "frontier" in text:
        return "SDK 保持 frontier 的机器权威与持久状态，不允许 Reporter 等角色越权改写研究方向。"
    return "SDK 内部关键控制项；影响校验、路由、身份或持久化，但不会作为字段发送给 Agent。"


def default_description(section: str, field: str, *, hint: str = "", sdk_description: str = "") -> str:
    if section == "input":
        return _INPUT_DESCRIPTIONS.get(
            field,
            "当前 Stage 的输入字段；向 Agent 提供完成本阶段任务所需的最小上下文或导航入口。",
        )
    if section == "output":
        return _describe_output(field)
    if section == "sdk":
        return _describe_sdk(field, sdk_description)
    raise ValueError(f"unsupported field-note section: {section}")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_overrides(state_dir: Path) -> dict[str, dict[str, Any]]:
    path = Path(state_dir) / FIELD_NOTES_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict) or raw.get("schema") not in {FIELD_NOTES_SCHEMA, LEGACY_FIELD_NOTES_SCHEMA}:
        return {}
    items = raw.get("items")
    if not isinstance(items, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in items.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        if raw.get("schema") == LEGACY_FIELD_NOTES_SCHEMA:
            description = value.get("description")
            edited_at = value.get("edited_at")
            if isinstance(description, str) and description.strip() and isinstance(edited_at, str) and edited_at.strip():
                result[key] = {
                    "machine_default": "",
                    "human_value": description,
                    "history": [{"description": description, "edited_at": edited_at, "actor": "human"}],
                }
            continue
        machine_default = value.get("machine_default")
        human_value = value.get("human_value")
        history = value.get("history")
        clean_history: list[dict[str, str]] = []
        if isinstance(history, list):
            for entry in history:
                if not isinstance(entry, dict):
                    continue
                description = entry.get("description")
                edited_at = entry.get("edited_at")
                actor = entry.get("actor")
                if (
                    isinstance(description, str) and description.strip()
                    and isinstance(edited_at, str) and edited_at.strip()
                    and actor in {"human", "machine"}
                ):
                    clean_history.append({"description": description, "edited_at": edited_at, "actor": actor})
        result[key] = {
            "machine_default": machine_default if isinstance(machine_default, str) else "",
            "human_value": human_value if isinstance(human_value, str) and human_value.strip() else None,
            "history": clean_history,
        }
    return result


def sync_machine_defaults(state_dir: Path, defaults: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    path = Path(state_dir) / FIELD_NOTES_FILENAME
    with _LOCK:
        items = load_overrides(state_dir)
        changed = False
        for key, description in defaults.items():
            normalized = str(description).strip()
            if not normalized:
                continue
            item = items.setdefault(key, {"machine_default": "", "human_value": None, "history": []})
            if item.get("machine_default") == normalized:
                continue
            item["machine_default"] = normalized
            item.setdefault("history", []).append({
                "description": normalized,
                "edited_at": _now(),
                "actor": "machine",
            })
            changed = True
        if changed:
            _atomic_write(path, {"schema": FIELD_NOTES_SCHEMA, "items": items})
        return items


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_override(
    state_dir: Path,
    *,
    key: str,
    description: str,
    known_keys: set[str],
) -> dict[str, Any]:
    if key not in known_keys:
        raise ValueError("unknown or non-displayed workflow field")
    normalized = str(description).strip()
    if not normalized:
        raise ValueError("description must not be empty")
    if len(normalized) > 4000:
        raise ValueError("description is too long")
    path = Path(state_dir) / FIELD_NOTES_FILENAME
    with _LOCK:
        items = load_overrides(state_dir)
        item = items.setdefault(key, {"machine_default": "", "human_value": None, "history": []})
        edited_at = _now()
        item["human_value"] = normalized
        item.setdefault("history", []).append({
            "description": normalized,
            "edited_at": edited_at,
            "actor": "human",
        })
        _atomic_write(path, {"schema": FIELD_NOTES_SCHEMA, "items": items})
        return item
