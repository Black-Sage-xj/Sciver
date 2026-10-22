---
name: pdf-reading
description: >-
  TheoryMaster Literature 阅读 PDF/原文：抽取可引用主张、公式、假设与反例，并保留页码定位。
  对应设计名 PDF-reading。不把全文写入 Canvas。
---

# pdf-reading

设计名：`PDF-reading`。

## 输入规范

- Literature 只把 SDK 已成功物化的可读全文作为全文阅读输入；PDF 二进制或 `pdftotext` 输出本身不构成已核全文。
- PDF 下载、转换与可读文本物化由 TheoryMaster SDK/runner 负责，Literature 不承担 converter/runtime 操作。
- 后续主张、公式、假设和 locator 的抽取以 SDK 提供的 Markdown / arXiv source 等可读文本为入口，并保留原始来源路径用于复核。
- 全文物化失败时，不得声称“已读全文”；标记为 `pdf-unread` / `unverified`。

## 抽取

每条引用主张必须带：

- 文献标识（题名/作者/年/DOI）
- 页码或章节定位
- 原文主张的短转述 vs 你的解释（分开写）
- 该主张对当前 Hypothesis 的关系：支持 / 冲突 / 不适用 / 未核原文

公式抄写保持符号，并注明原文定义是否与本任务合同一致。

## 禁止

- 未打开原文却写“论文证明了…”。
- 把 PDF 全文、大段扫描图贴进 Canvas 或 Cycle 摘要。
- 用二次综述转述冒充一作原文。
- 把未成功物化的 PDF、转换失败结果或 `pdftotext` fallback 标记为全文核验。
- 修改任务合同或关闭 Cycle。
