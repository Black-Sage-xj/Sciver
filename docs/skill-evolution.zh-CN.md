# Skill 自提升优化

在 Settings 选择任务，开启 **Skill 自提升优化**。默认关闭；保存后在后续安全科研调用采用。
开启不会启动任务、独立学习 Agent、效果评测、回放或额外模型请求。它会在正常研究调用中增加少量可选的经验归纳文本，并在后续按需加载相关技能。

关闭后停止接纳新技能和修订；该任务已经采用的版本保留。当前调用和同一逻辑调用的重试始终使用原来的技能绑定。开启前已经存在的旧 Agent 会话仍保留原白名单，新技能从兼容的新会话开始使用。

## 运行流程

1. Investigator / Literature 在正常工作中遇到可复用方法、纠正或技能使用结果时，可以写一个小旁路 JSON。没有有价值经验时不写。
2. SDK 在科学调用完成提交后读取旁路，记录来源文件 hash，执行格式、路径、版本和容量检查。
3. 新增、修订、合并、归档和回滚均保存完整提请及前后版本。变更自动进入重要事件，可展开查看差异；无需逐次批准。
4. 后续调用获得相关技能的名称、描述和不可变路径，按需加载正文。方法的后续实际使用结果可作为正常调用的反馈，继续修订技能。

技能标注为 **源自轨迹**。加载次数、作者报告的成功或失败分别记录，不把科学命题被否定当作方法失败，不宣称独立验证了效果提升。科学交付 JSON、Judge 裁决和 Scientific Model 的权威边界不变。

首个兼容会话收到旁路格式说明，后续调用只收到简短提醒和变化的技能清单。格式说明丢失时可按提示读取随包附带的 protocol.md，不追加模型请求。

## 存储与复用

个人技能库默认位于 `~/.local/share/sciver/skill-evolution/`，可用 `SCIVER_SKILL_LIBRARY` 指定其它本机目录。
SQLite 保存技能索引、提请、来源和使用观察；不可变版本目录保存 `SKILL.md` 和最多三个已使用的便携辅助脚本。
本机多个 Worker 使用事务和精确 base_version 协调修改；冲突候选保留为待修订，不覆盖新版本。

技能默认为当前任务作用域。明确归纳为通用流程并附相关标签的技能，可在其它开启学习的任务中按相关性提供；其它任务对共享版本只读，需要调整时创建自己的派生技能。
官方 Test0 不加载其它任务的习得技能，其产生的技能也限制在本次任务，保留每次全新实例的隔离约定。

运行记录位于 Cycle 的 `skill_bindings/` 和 `skill_maintenance/`。旁路原稿留在当前工作目录，维护失败不会触发科学重试或 Supervisor 修复。
提请副本位于研究根目录 `.theory-master/skill-evolution/proposals/`；重要事件引用确切提请 ID。

## CLI

以下命令仅改变设置或管理方法记录，不启动科研：

```bash
sciver skills on 12
sciver skills off 12
sciver skills status 12
sciver skills list 12 --role investigator
sciver skills proposals 12
sciver skills show 12 --proposal SKP-...
sciver skills archive 12 --name residual-check --reason "已由其它方法替代"
sciver skills rollback 12 --name residual-check --version <历史版本> --reason "恢复原方法"
```

技能体积、候选数和目录规模有硬限制。日常维护不安装依赖、不扫描整个历史库、不运行测试或外部服务。
此模块是 SciVer 原生实现，没有嵌入非 MIT 框架或其技能库。
