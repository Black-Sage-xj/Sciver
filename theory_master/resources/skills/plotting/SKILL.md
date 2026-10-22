---
name: plotting
description: >-
  当本回合需要新建或修改科学图时使用。英文坐标/图例/panel、任务侧复现指针。
  不写笔记图注，不写 Canvas。不用于只读取现有图。
---

# plotting

偶发能力。Investigator / numerical-investigation 已规定落盘位置；本 skill 只在真正作图时加载。

优先改已有绘图脚本并复用已有结果目录。脚本放 `cycle_workspace/analysis/`，正式图放 `cycle_workspace/result/<analysis_name>/`，文件名反映指标和比较对象。沙箱外的任务输入与共享工具默认只读，除非当前角色的明确权限另行允许。

## 图像本体

- title、axis label、legend、colorbar、annotation、subplot label 用英文。
- 多曲线、多类别或多种标记必须渲染 legend 或等价直接标注；不能只有 `label=` 而未显示图例。
- 新生成的多 panel 正式图用 `(a)` `(b)` `(c)`，按阅读顺序且不重复。
- 单位、归一化和符号保持一致；颜色、线型、marker 不承担无法从图例识别的隐含语义。

## 最小复现

能从稳定 TSV/NPZ/JSON 重画则只记指针：figure path、source data path、列/筛选/参数、analysis script。不要另存一份等价 `original_data/figure.tsv`。

聚合、拟合、插值、抽样或变换无法从稳定输入恢复时，保存送入绘图层的数值：表用 TSV，矩阵/嵌套用 NPZ/JSON；误差棒、拟合线、阈值和标注一并保存。不要存 Matplotlib Figure pickle。

Smoke/调试图不强制完整 manifest；一旦进入正式结果就必须补齐追溯。

## 统计

误差棒或阴影必须标明 SD、SEM、分位、CI 或其它。样本不独立时，不要把普通误差区间写成显著性或因果证据。自定义指标在结果文档中定义，脚本至少保存参数和输入来源。

## 边界

- 不写 `notes_dir`、不写 Canvas、不写中文图注。
- 禁止 `result/report.md`。
- 临时诊断图不得冒充正式结果。
- 不直接将图同步到当前工作区之外的个人笔记库；图留在 `cycle_workspace/result/`，是否归档或发布由具备写入权限的后续流程决定。
