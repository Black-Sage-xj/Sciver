# TheoryMaster Authority Matrix

本文件是设计与一致性测试的权威角色边界，不作为每个 thread 的重复 prompt。

| Authority | Research Lead | Challenger | Investigator | Literature | Judge | Reporter | SDK |
|---|---|---|---|---|---|---|---|
| 产生/维护 scientific candidates | **Yes** | No | No | No | No | No | 持久化/派生 |
| qualification review | No | **Yes** | No | 可提供文献证据 | No | No | 调度/校验 |
| 锁定 Main Hypothesis / criteria | No | No | No | No | No | No | **Yes** |
| 理论 Evidence | 方向输入 | No | **Yes** | No | No | No | 注册 |
| 数值 / local-source Evidence | 方向输入 | No | **Yes** | No | No | No | 注册 |
| 文献 Evidence | 可请求 | 可请求 | 可请求 | **Yes** | 可请求 | No | 注册 |
| Evidence adjudication / rework objection | No | No | No | No | **Yes** | No | 执行 routing |
| Scientific Model 科学 patch / evolution frontier | **Yes** | No | No | No | 不覆盖 | No | validate/commit |
| 人类可读权威叙事 | 综合输入 | No | No | No | 决议输入 | **Yes** | 落盘 |
| Canvas / runtime state / process control | No | No | No | No | No | No | **Yes** |

原则：Research-Lead-centered、Investigator-unified、Judge-independent、SDK-owned workflow、Reporter narrative-only。理论与数值是 Investigator 的方法能力，不是独立 workflow 角色。
