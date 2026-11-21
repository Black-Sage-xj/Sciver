---
name: research-literature
description: Use the shared Literature service or interpret a study receipt.
---

# 文献调研

本轮已有 Literature 作业回执时，直接按回执状态、报告和来源回答该请求。需要新检索时，使用本轮共享 Literature 服务绑定的 `state_dir`、`search_root`、`session_id`，以用户的实际问题和授权范围填写参数。命令模板中的大写名称均须替换为实际值。

```sh
python -m tm_observer.research_chat_literature --state-dir "STATE_DIR" --search-root "SEARCH_ROOT" --session "SESSION_ID" --query "QUESTION" --scope "SCOPE"
```

服务使用会话指定的模型、推理强度和预算。扩展调研范围或预算须获得用户授权。复用已有检索清单用 `--retrieval-manifest`；恢复已有作业时保留原问题、范围和清单，并传入原 `--job-id`。同步调用以 `yield_time_ms=30000` 启动；命令仍在执行时，使用 `write_stdin` 在同一终端会话每 30–60 秒等待一次，直到收到 final JSON。作业运行期间无需轮询文件或另开同一作业。暂停、取消或失败时说明实际状态、已有材料和相关诊断。

以实际报告和原始来源回答，提供回执中的 `report_href`。区分元数据、摘要、可读全文和已经对照原文核实的主张，说明影响结论的访问限制和仍待解决的问题。检索空结果不足以认定新颖性。PDF 转换限于 Settings 已启用的 Marker 或 MinerU，遵守页数及资源上限；转换关闭时按实际可读的资料回答。
