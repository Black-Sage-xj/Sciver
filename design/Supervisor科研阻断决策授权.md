# Supervisor 科研阻断决策授权与兼容策略

## 唯一决策权

科研 Investigator 的 Native Goal 进入 `blocked` 时，只能报告当前冻结检验无法继续；
不得自身对 Hypothesis 给出 Judge 判定，不得直接批准 `preserve_research`。

Supervisor 在 **`THEORY_MASTER_SUPERVISOR`** 开启且检查点、冻结 Hypothesis、
`blocked` 来源与授权边界均通过 SDK guard 后，以独立只读模型审查决定：

- `continue`：只允许在现有冻结命题及 `decisive_test` 内有一个具体可行的新行动时，
  有界继续相同 Cycle（最多一次自动科研继续）；不改变假说及评分口径。
- `preserve`：当真实科研阻断且无合格继续动作时，经 Supervisor 记录批准，
  再调用 Investigator `preserve_research` Stage 保存已经完成的研究。SDK 最终检查
  对应 `SUP-*` 会话和 `supervisor_authorization.json`，关闭为 `OVER` /
  `investigator:preserved`，不伪造 Judge / Reporter 或科学 verdict。
- `hold`：条件、来源或权限不充分时保留 `NEEDS_ATTENTION` 等待人工处理。

**Supervisor 关闭时不得自动作出科研继续或保全。** 普通资格候选
`no_qualified_candidate` 可以在 Supervisor 开启的情况下有界重提一次；
运行故障由 Supervisor 的 `runtime` 审查 profile 处理，二者与科学冻结裁决严格分权。

## 单一开关与兼容

- 系统唯一真实开关是 `supervisor_enabled()` / `set_supervisor()`，对应
  `THEORY_MASTER_SUPERVISOR`。当前并不存在可独立启停的另一套 Attention Takeover。
- `attention_takeover_enabled()`、`set_attention_takeover()` 和旧环境键只是历史兼容
  名称，统一委托到 Supervisor。若新旧开关有冲突，新开关优先。
- 触发科学审查的 Workflow 只使用 `supervisor_enabled()`；
  新实现位于 `supervisor_attention_review.py`，旧 `internal_attention_review.py`
  保留导入别名但不承担第二份实现。
- Native Goal 自身的 `blocked` 不被视为 Judge `inconclusive`，
  统计精度不足只限制对应冻结定量命题，不自动代表整个机制研究被反驳。

## 审计与历史

## 异常接管会话

Runtime failure、`NEEDS_ATTENTION`、hold、`protocol_health=violation` 在既有 Supervisor 审查之后，额外打开短生命周期 incident session（`SIS-*`）。已链接的会话嵌在 `/run/<id>/supervisor/#SUP-...`，独立页仍是 `/supervisor-session/<session_id>`。对话框使用 vendored Loquix（`message-list` / `chat-composer` / `attachment-panel`），只服务这一次 incident。人工消息走 `POST /api/supervisor-session/<id>/message`，可带 png/jpeg/gif/webp；文件落在该 SIS 的 images 目录，追问只收到文件名。`resolved` / `closed` 保留全部 supervisor、human、tool、system 消息供审计。

该会话不改变 resume / hold / preserve 决策函数，不建立全局聊天，也不在无异常时分配存储。人工追问不是只读解说：操作员要求的运行配置修改，由 SDK 应用到 `theory-master.config.json`、`config.toml` 或 `.theory-master/web-role-routes.json`。科学结论、研究状态和其它路径不在这个修改范围内。

## 防回归

`tests/test_supervisor_scientific_authority.py` 覆盖 Supervisor/旧开关冲突组合、
关闭时不调用审查、开启时无需旧别名、保全时必须有一致的会话授权，以及
旧模块只作为导入别名。现有 preservation guard 的独立测试继续保留。
