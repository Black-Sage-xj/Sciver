---
name: compute-dispatch
description: >-
  TheoryMaster Investigator 在当前冻结 investigation 内有 10 个以上独立计算单元、预计串行超过 10 分钟、需要并行或 Slurm/HPC
  投递或运行状态核验时使用。负责计算资源与执行机制，不负责 workflow routing、Agent 接力、
  Canvas/笔记维护或 Cycle lifecycle。
---

# Compute Dispatch

用于 Investigator 当前 frozen objective 内已经科学授权的计算工作。这个 skill 只回答“怎样可靠、高效、可复现地执行这项计算”；是否需要该科学工作、是否继续 Investigation Round、后续交给谁以及 Cycle 如何推进，均由当前 Investigator contract 与 TheoryMaster SDK 决定。

## 何时加载

满足任一情况时使用：

- 预计串行耗时较长、CPU/内存/IO 开销明显，或需要先做成本评估；
- 独立计算单元达到 10 个，或小样本估计串行超过 10 分钟；批量、参数扫描或多个独立 run 存在实际并行收益；
- 需要 Slurm/HPC、MPI/OpenMP、进程池或其它明确的资源调度；
- 已提交计算需要核验队列/进程状态、日志、失败原因、可恢复性或产物完整性。

秒级轻量计算、纯文本处理或无需资源决策的小检查不必加载。
达到规模条件必须先完成一次可复核的成本与并行收益估算；**第一次估算不是永久的串行许可证**。当实际/预计独立单元数、计算总量、单位耗时、可用硬件或任务结构发生显著变化时（例如从几十个计算单元增加到数百个计算单元），**在下一批大计算开始前重新评估**，可以复用尚有效的 smoke 数据，不必重复昂贵测试。若单元很轻、总批量很小或并行开销确实更大，记录实测或保守估计理由后可以保持串行；无需新增审批或新计算预算流程。

## 先估算，再选择执行方式

成本不明时先对代表性最小样本做 smoke / timing。至少明确：

- 独立计算单元数量；
- 单元耗时、峰值内存和主要瓶颈（CPU / IO / MPI / accelerator）；
- 可并行维度与依赖关系；
- 可用 CPU / memory / scheduler allocation；
- 预期串行与并行墙时；
- 本地 / Slurm / HPC 的选择理由；
- 可恢复/重跑粒度。

### 科学收益与计算可行性门槛

- **成本估算先于提交**：区分科学上真正必须的样本/精度/证明强度，与某个保守不等式、低效算法或粗糙上界所造成的巨大表观需求。除了单元/并行成本，还应判断整项任务在给定资源下能否形成对冻结科学命题有价值的结果；不要把优化并发当作批准天文数量计算的理由。
- **不经济路径及时停止**：如估算已显示所选方法无法在合理资源下形成有信息量的证据，停止该方法的大规模执行，优先复用已有数据、必要的小规模烟雾测试、可复核的分析近似或在冻结合同内的其它方法。仅在科学问题确实必要、资源路径可验证且有合理预期时，才考虑大规模/HPC 扩容；不设一刀切的网络数、墙时或硬件阈值。
- **权限与后续**：本 Skill 不批准放宽冻结估计对象、误差标准、评价隔离或 decisive_test。若可行方法仍未找到，在研究记录中说明已量化的资源与科学前提缺口，继续在冻结范围内研究其他合法方法；不得自行触发 Native Goal `blocked` / SDK Attention 或申请科学停机。SDK 只在实际达到 Investigator Token 预算后安排独立投入审查，真实 Provider 额度或运行故障由 SDK 管理；不能把此工程结论包装成科学上的 `open`、反证或测试已完成。

### 默认充分利用可用资源（在测量确实有收益时）

- **先确认独立性**：不同数据样本、试验组或参数点若仅共享只读冻结输入且输出独立，先列出真正可以并行的维度及共享文件写入/内存冲突风险。严格依赖步骤（例如 事前冻结输入 → 各独立单元执行 → 验收 → 汇总）只允许在依赖满足后并行内部单元。
- **有明显并行收益时默认优先并行**，而不是因为现有 Python 脚本只有 `for` 循环就接受串行。对于 CPU-bound 的多独立单元，优先多个进程；不要误把 `OPENBLAS_NUM_THREADS=1` 当作禁止多个 worker，这个设置是为了避免每个 worker 内部超额订阅。
- **利用率与预算**：测量 CPU affinity / cpuset / cgroup quota、空闲内存、磁盘剩余空间、已有活跃任务与每 worker 的峰值内存及输出量；计算计划 `workers × threads_per_worker ≤ 可用 CPU`，并限制合计内存和输出峰值，留出宿主机/其它任务的余量。资源足够时优先使用更多核心，不机械固定 1 个或填满 48 个逻辑线程。
- **校准与复核**：条件允许时使用同一代码/同等工作负载的**不同独立 smoke 单元**测 1、8、16 workers 的吞吐、总墙时、CPU 占用、IO 压力与结果一致性，再选实际更快且安全的数量。资源较少或任务少时对候选自动裁剪。严禁对同一已缓存结果重复运行后声称代表吞吐测试。
- **持续更新**：新增独立计算单元后，重新评估剩余未完成任务数量和缓存命中率；若原计划从 <2 分钟的小批量变成几十分钟的大批量，必须重新选择串行/并行策略。若因并行初始化、每进程重复加载大矩阵、存储吞吐、结果冲突而继续串行，记录测量或至少带数字的工程理由。
- **不牺牲科学正确性**：不可在盲测开启前读取响应或改变 frozen Hypothesis/参数/随机种子；科学对象的独立性（统计样本数）与计算单元可并行性是两回事。不能把同一原始样本上的大量派生计算当作相同数量的独立统计样本。输出、认证和最终科学判断保持原权威链。

并行策略遵循计算结构：

- 多个独立且较重的 run/参数点：优先 array / job-level parallelism，并限制并发；
- 单任务内部独立 CPU 子计算：使用与配额匹配的进程池或库内并行；
- IO-bound 工作：按存储/网络吞吐限流，避免盲目增加 worker；
- 已有 MPI/OpenMP/BLAS 等内部并行：先复用并计入 CPU 总预算，不叠加造成 oversubscription；
- 并行开销大于收益或存在严格依赖：保持串行并记录理由。

外层 worker × 每 worker 内部线程/进程不得超过实际资源配额。需要容量估计时可以使用当前仓库已有、已授权的资源探测工具，但本 skill 不假定固定宿主机 CPU 数、解释器路径或队列配置。

## 通用独立任务执行器（随 Skill 按需使用）

内置 `scripts/parallel_units.py` 可作为**普通计算工具**由 Investigator 在当前 invocation 内使用，不改变 TM 的 Agent/Goal/Stage 层级。当前 Skill 的 `SKILL.md` 与 `scripts/` 位于同一目录；按实际白名单路径定位脚本，不预载可执行脚本的全部源码进上下文。

- 使用范围：相互独立、可通过固定命令行参数重复计算、**有各自独立工作目录和结果文件**的单元（例如每个样本、每个参数组）。如果原科研程序只支持所有计算单元写一个共用 `summary.json`，先在当前 Cycle 科学工作目录把单元执行与末尾聚合分开，并用串行 smoke 验证一致性；**不要直接并行启动多个会写相同文件的全量脚本**。
- 清单 JSON 格式：`{"schema":"theory-master/independent-compute-units/v1","units":[{"id":"unit_001","cwd":"jobs/unit_001","argv":["<scientific-python-executable>","<absolute-existing-worker-script>","--item","001"],"inputs":[{"path":"<frozen-input-file-or-worker-script>","sha256":"<64-char-file-SHA256>"}],"outputs":["result.json"]}]}`。`cwd` 必须是 Investigator 当前 `cycle_workspace` 的不同独立子目录；`outputs` 是单元 cwd 下相对路径。每个科学输入、执行脚本、冻结参数与版本必须由 `inputs` 的 SHA256 或等价只读冻结来源覆盖。只有清单中经过科学批准的任务才允许执行。
- 先执行 `python <skill>/scripts/parallel_units.py --workspace <cycle_workspace> --manifest <workspace里的manifest.json> --dry-run`，核验参数、输入哈希、worker 上限、剩余资源。随后可用相同命令去掉 `--dry-run` 正式执行；默认最多 16 个 worker 且按 CPU/memory/disk 限制自动收缩；可显式 `--workers 1|8|16` 和 `--memory-mib-per-worker`、`--disk-mib-per-worker` 调整。
- 该工具运行的是 `argv` **无 shell 解释** 的独立子进程；把每个单元的 stdout/stderr、实际退出码、持续时间、输入与输出校验和保存到 `<manifest-stem>.dispatch/`，同时汇总 `summary.json`。已验收的成功单元在相同清单再次执行时跳过；原任务失败时需显式 `--retry-failed`，只重跑失败单元（前提是输入/程序哈希未变化）；结果被篡改、清单变更或磁盘/内存不足则拒绝。
- **执行器不是科研沙箱或证据 Judge**：它只校验声明的路径和输入哈希，不能证明子程序没有访问其他数据；仍依赖 TM 当前 Cycle sandbox、正确的输入清单、输出身份隔离与 Investigator 的科学验证。进程 `returncode=0` 和 `summary.status=complete` 只代表工程执行完成，绝不等于 frozen decisive_test 获得支持。
- 需要并行 smoke 对比时，**每种 worker 数使用不同的代表性、但等价的独立任务批次/清单目录**；不要直接针对已经缓存的结果比较 worker 数，否则缓存跳过会造成虚假的提速。
- 大规模计算进程在**同一个当前 TM Investigator 调用内**等待/恢复；不要自行建立另一个持续科研 Agent、隐式修改 ResearchPlan/Canvas，或用定时后台代理绕开 TM 的 Token 额度管理。

## 本地 / Slurm / HPC

- **本地**：短 smoke、轻量验证以及资源可控的计算。
- **Slurm**：较长、批量、资源占用显著或需要 scheduler 管理的计算；根据任务结构选择单 job、array 或 job 内并行，不机械强制某一种形式。
- **HPC**：当前项目规则、运行环境或正式生产计算明确要求远端 HPC 时使用；遵循当前 task/runtime 已提供的 host、queue、module、submission 与文件同步约束。

不要因为某个执行端不可用就静默改变科学参数、样本、数据集或 frozen criterion。若执行基础设施不足，精确报告工程 blocker。

## 执行与复现

- 使用当前 workflow/runtime 提供的解释器、工具链和环境；需要 provenance 时记录实际 `sys.executable`、关键版本和 scheduler allocation，不硬编码其它主机路径。
- 扩大规模前先做最小 smoke；并行改造后用小样本核对串行/并行结果的一致性。
- 分钟级前台命令先保存进度日志，再按预计墙时选择工具允许的较长单次等待；避免约每 30 秒反复轮询同一进程，让增长的对话上下文重复进入模型。Gateway `exec` 的默认超时为 120 秒，预计更长时显式传入有界的 `timeout_s`（当前上限 1800 秒）；超过上限时使用可恢复的作业方式。等待结束统一核对退出码、日志与产物。
- 输入、参数、seed/task index、命令、输出和状态必须可追踪；每个独立计算目录按 Investigator 规范保留 README。
- 输出隔离，失败保留非零退出、stderr/log 和失败单元信息；可恢复任务只重跑工程上失败且科学输入未改变的单元。
- scheduler 的 `COMPLETED`、进程退出码 0 或文件存在都不等于科学验收通过；必须核对预期 artifact、输入/参数一致性、完整性与当前 criterion 需要的最小结果。

## 监控、恢复与重试

本 skill 可以在**当前 Investigator invocation** 中：

- 查询已提交 job/process 的实际状态；
- 检查 scheduler/log/artifact 是否健康；
- 识别 OOM、timeout、环境缺失、节点失败、IO/路径错误等工程故障；
- 在科学输入完全不变时，给出或执行最小工程性恢复，例如重提失败单元、恢复 checkpoint 或调整等价执行资源。

不得：

- 为持续监控自行安装 cron/watch/relay 或启动 Agent 接力；
- 创建 tmux/后台“未来继续”的 Agent workflow；
- 通过 `owner_agent`、`next_action` 等字段决定下一角色或下一 Stage；
- 因工程失败自动改变 scientific parameter、dataset、criterion 或 Hypothesis；
- 无限重试。

如果计算在当前 invocation 内无法完成，返回真实 job/run 状态、可复现检查方式和未完成的 engineering/scientific boundary，由 TheoryMaster SDK 决定后续 continuation。

## 产物与权限边界

- 计算脚本、README、运行日志、派生数据和结果只写当前 `cycle_workspace` / 当前计算目录允许的位置。
- 不写 `notes_dir`、Canvas、Scientific Model、Frontier State 或其它角色 workspace。
- 不调用 `canvas-console`、Reporter、Literature、其它 Agent 或隐藏 reviewer。
- 不创建/关闭 Cycle，不启动新 Investigation Round，不选择下一 Stage，不执行 workflow routing。
- 不把资源选择、运行成功或调度状态解释成科学 verdict。

本 skill 是 Investigator 的 execution capability；TheoryMaster SDK 始终拥有 Agent 调度、continuation、routing、状态持久化和 Cycle lifecycle。
