# 2026-10-17 ResearchGraph V2 迁移记录

> 本文记录旧 Canvas 切换到 SQLite 时的事实和保全边界。2026-10-18 起，**“每 H 唯一 Outcome”不再是现行科学语义**；现行规范见 [ResearchGraph设计文档.md](./ResearchGraph设计文档.md)。此前的唯一 H Outcome 行保留为兼容的全 H 状态，不得覆盖每轮独立的 Cycle Outcome。

## V2 切换时的事实

- 正式 `exploration.canvas` 已由 `science_graph.sqlite3` 原位生成。原始 Canvas 保留 `exploration.canvas.before-outcome-add9efdaebd1.bak` 和 `exploration.canvas.before-sqlite-add9efdaebd1.bak`；原始 SHA-256 为 `add9efdaebd1364f326df816b136826259647a64e03cb52c2c4be92ae98321af`。
- 备份含 Canvas、Research Plan、Worker runtime、branch claims、Cycle 状态与调度器状态，目录为任务 1 `.theory-master/migration-backups/researchgraph-v2-20261017-full-cutover/`。
- 原始 275 节点、326 边及全部位置已归档；迁移时为 95 H、95 旧式 H Outcome、112 条历史 Cycle evaluation、163 个投影 H/E 节点、214 条显示边。后续真实运行已增加 Cycle evaluation，迁移数量不可当作现值。
- 旧 Decision 的 supported/rejected 仅是该轮冻结检验的局部裁决，不自动成为宽泛历史 H 的全命题状态。旧 Finding/StageSummary 没有明确 Judge 授权时以 External 保存。
- 旧 Canvas 全字节、原始节点与边、Decision ID、报告及 Evidence 链接必须保持可追溯。`exploration.canvas` 是 SQLite 的只读投影，不是独立写入权威。

## 向每 Cycle 一个 Outcome 整理

现行读取从 `cycle_evaluations` 取每轮完整裁决，在 H 卡片下按 Cycle 展示，生成 `notes/outcomes/<Cycle ID>.md` 阅读文档与同名 `.json` 结构化记录。Markdown 不再重复整份 JSON；生成器升级只覆盖可验证的 SDK 文档，人工注释或未知格式保留并报告冲突。旧 H 可以有多条历史 Cycle Outcome；原 `outcomes` 表一 H 一行只作历史整体状态兼容。未完成或仅运维收尾的 Cycle 没有科学 Outcome。新 Cycle 锁定新的 H，并通过 `hypothesis_cycle_owners` 保证一个 Cycle 对应自己的 H；同 Cycle 恢复幂等。

正式历史回填应先在隔离副本预览并核对异常，保留正在运行的 Cycle；运行中的 Worker 不得与数据库/Canvas 写入者同时切换。任何历史 Decision 归属或科学来源不明时保留原资料并登记歧义，不能为了整齐自动生成 H 身份或演化边。

## 节点摘要/档案的后续维护契约（2026-10-20）

节点摘要不是自由文案，而是冻结科学命题的忠实压缩：必须分开表达具体主张、关键适用条件，以及由 SDK 维护的认识状态。展示模型只在 H 锁定后生成 `canvas_claim` 与 `canvas_conditions`；SDK 固定长度、状态标签和回退规则。失败不阻断科研 Cycle，也不允许完整原文回退进摘要。无法安全压缩时显示“摘要暂不可用”，并保留完整原文和可用详情链接。

历史 H 档案回填以 SQLite 和 `legacy_records` 为来源，逐字保留已有文字；缺失字段不补造。多父 H 的全部上游 ID 与每条边的演化类型必须写入档案。SQLite 为权威数据层，Canvas 为只读投影。文档模板迁移只更新可验证的 SDK 生成区块，不覆盖人工注释。
