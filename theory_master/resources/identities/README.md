# Sciver 身份与渐进披露

Sciver 把信息分成四层：

```text
Role Core      = thread 生命周期内稳定的职责、Authority、Boundary
Stage Card     = 当前这一次调用才需要的目标、允许动作、禁止动作、Exit/Handoff
Output Contract= 当前 stage 的机器 JSON 合同
Skill/Evidence = 当前任务确实需要时才读取
```

`identities/<role>.md` 只属于 **Role Core**。新 thread 创建时加载一次；同一 thread 后续调用不重复 identity。稳定附件按角色最小装载，而不是统一灌入：Investigator thread-init 只加载 `investigator.md` + `_invocation_handoff.md`，不自动加载 `_paths.md` 或 `_round_budget.md`；这些 workflow/navigation 细节由 SDK 自己掌握。Research Lead 的 `_research_lead_reading.md` / `_research_lead_protocol.md` 是设计维护资料，不在 thread-init 自动加载，等价要求由相应 Stage Card 渐进披露。

一个 role 不等于一次调用。同一 Cycle 内典型序列包括：

```text
Research Lead: frontier_proposal → selection_repair? → candidate_selection → ... → cycle_synthesis
Investigator:  investigate → integrate_literature? → judge_rework?
Literature:    landscape / targeted_source / novelty_check（外部学术任务；每个独立 task fresh）
Judge:         terminal_assessment
```

因此后续 turn 只追加当前 Stage Card、delta context 和当前 contract，不重新灌入所有未来 stage、全部 history 或完整 skill 内容。Skill 白名单只在 developer instructions 暴露名字与 `SKILL.md` 路径；Agent 需要该能力时才读取文件。

Research Lead 的 frontier 入口只给必要摘要、路径和节点 ID，不预注入报告正文或上一轮 transcript。Goal/Research Contract、Scientific Model 与 Frontier State 的权威都来自同一个 `research_plan.md`；探索 Canvas 只提供 waiting hypothesis 的可读图谱位置与导航，再沿直接链接读取必要材料。未实际打开的材料不能写成已读证据。

角色边界权威见 `_authority.md`。Canvas 是 SQLite 科研图谱的可视化投影，不向科学 Agent 暴露 Canvas 读写工具或导航附件。
