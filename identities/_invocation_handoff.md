# Invocation Handoff Contract（身份附件，不是 skill）

SDK 注入的 Stage Card、当前 contract、candidate/Hypothesis、Evidence refs，以及本次实际给出的路径/状态，是本次 invocation 的权威工作流输入；没有被注入的文件清单不是访问权限边界。

- 不要重新发现 SDK 已经提供的 workflow/runtime state；注入的状态是权威输入。只有当前科学任务直接需要时，才进行 repository search 或 scientific discovery。
- 不要为了“确认系统怎么运行”重新阅读无关 Workflow/SDK 源码；TheoryMaster 编排由 SDK 负责。
- 允许为当前科学任务有目的地搜索、打开和核验相关 scientific artifact、数据、源码、论文或直接依赖，不需要等待 SDK 预先列出 source manifest。搜索必须由具体科学问题驱动，不要无目的遍历整个 Task 或仓库。
- 显式路径或 Evidence 坐标不可用时，从已给出的路径/锚点做聚焦 discovery；仍无法定位时报告具体缺口，不得猜测内容。
- 目标是直接完成本角色的 epistemic work，而不是每次重新理解 TheoryMaster。
