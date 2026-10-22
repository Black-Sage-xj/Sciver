---
name: theory-investigation
description: >-
  Investigator 需要定义、解析推导、closure、scaling、极限、机制或理论预言时加载。
  用于理论方法选择与证据边界，不负责 workflow routing 或 Judge 裁决。
---

# Theory Investigation

用于当前冻结 Hypothesis 内的理论推进。

## 方法

- 先冻结 target object、变量、定义域、已知条件、assumptions 与 approximation regime，再开始推导。
- 识别贯穿推导的 invariant theoretical object。若使用 proxy、局部切片、surrogate 或近似量，必须说明它与目标对象的关系，不得让方便计算的替代量静默取代真正 target。
- 对非平凡推导先建立最小 dependency path：target ← intermediate claims ← definitions / assumptions / identities，并标出 approximation 或额外 assumption 从哪一步进入。依赖结构应保持 acyclic；任何中间结论都不能偷用它本应支持的最终 target。
- 对关键推导步骤明确其 epistemic type：definition / identity、derived claim under stated conditions、controlled approximation / closure、interpretation / mechanism、conjecture、numerical indication。不得隐式把较弱类别升级为较强结论。
- 对每个重要理论 finding 给出适用 scope、关键 assumptions、可失败条件与 limitations，并指出最近的一个更强但当前结果并未建立的 claim。
- 主动做与问题相关的 consistency / limiting-case / dimensional / hidden-assumption 检查；若存在多个合理 formulation，应说明结论是否依赖其中某一种。
- 如果推导得到可检验判据，明确写出可区分预言、所需输入与失败条件；需要数值执行时使用 `numerical-investigation` 作为 Investigator 的方法能力，并在同一 Investigator round 内直接完成可执行的数值验证；不要把“需要计算”本身当作必须结束本 turn 的理由。
- 得到一个有用 theoretical finding 也不是 round 结束条件；若当前 Lead-derived frozen investigation objective 仍有可执行的推导、数值验证或 task-local source check，就继续同一 round。
- 不强行 closure。若当前推导无法可靠闭合，定位最小 unresolved theoretical obligation，并说明缺少的是 definition、assumption、lemma、derivation step 还是 evidence。
- 若发现原表述需要 reframing、corrected assumption 或 narrower target，可以提出该方案，但只能作为 Investigator finding 返回；不得自行替换当前 active Hypothesis、改变 Cycle scope 或修改任何 upstream scientific state。
- 是否足以支持/反对 Hypothesis 由 Judge 裁决；本 skill 只负责理论方法与理论 Evidence 的质量。
- 需要正式符号推导时加载 `symbolic`；遇到 theorem / lemma / proposition / 严格 proof obligation 时加载 `proof-writer`；涉及当前任务专用的科学对象、数据协议或历史产物时，只按 developer whitelist 中 **Task-local Master skills** 的声明加载对应领域 Skill；纯抽象理论/符号推导不必因此加载，未列出时不得假定其它任务的领域口径。

## 结论表达

标明理论对象、推导依赖、已证与未证部分、适用假设。数值结果和源码核查必须注明实际使用的方法；正式交付的字段以 SDK Setup 为准。修正版命题只是研究建议，不能替换冻结目标。

## 边界

不改 Main Hypothesis、criterion、Cycle scope、Scientific Model、Canvas 或其他 upstream scientific state；不把有限搜索当一般证明；不自行发起外部文献检索；不把本 skill 扩展成独立 workflow role 或裁决器。
