# Sciver 身份与阶段指令

Role Core / Identity 只维护同一角色所有 Stage 共用的身份、权限、科学诚信与数据边界；新线程创建时加载一次，续轮不重复。当前任务入口（Stage 或 Investigator Native Goal）规定本次目标、允许操作和完成条件；输出 Contract 维护 JSON 字段、字段语义和条件关系，SDK 校验并持久化。

`theory_master/resources/identities/` 是默认打包运行源；根目录 `identities/` 提供可配置 override 的同步副本。角色文件与实际使用的共享附件必须保持一致。Lead、Challenger、Judge 使用 `_invocation_handoff.md` 与 `_paths.md`；Investigator 使用 `_investigator_handoff.md`；其他科研角色使用 `_invocation_handoff.md`。`BUDGET_ROLES` 当前为空，`_round_budget.md` 不自动加载。

Stage 由实际 role / phase / mode 决定。Stage Card 的目标、权限、出口与内嵌 procedure 由 `stage_definition_text()` 一次渲染；已取消独立 `PROCEDURE_CARDS` 注册表。初始调用和新线程恢复接收完整当前阶段定义；同线程续轮仍明确当前阶段，并仅投影必要的新对象或身份引用。稳定科学原则放在 Identity，阶段定义无需重复整个角色权限。

Lead 的提案、有限修订、合格候选选择有独立边界；综合使用新的认识 episode，前沿维护只标记现有 H。Challenger 的原审查和一次修订复核不能互相替代。Investigator 的 investigate / integrate_literature / judge_rework 使用 Native Goal，普通 Stage 文本仅供观察或离线兼容；preserve_research 是正式普通 Stage，只保全既有工作。Literature 每个独立外部任务使用新线程；Judge 证据裁决与任务 Final Gate 裁决由不同 Stage 界定；Reporter 的唯一 report 返回完整科学报告正文和标题。

任务范围以 Research Plan 的 Goal / Research Contract / Final Gate 为准，Scientific Model 承载精简长期认识。SQLite 与正式裁决记录保存科学状态；Canvas 是浏览投影。阶段从这些实际要求决定适用的覆盖、比较及证据条件，不对开放科研统一强加代际序列或经验匹配要求。展示字段限制由 Contract 常量生成，失败回退属于 SDK。

Skill 白名单在开发者指令中提供名字与路径，Agent 按当前工作需要读取；完整方法说明和证据正文不重复灌入身份或每次调用。开发与科研恢复不重写既有科学裁决。
