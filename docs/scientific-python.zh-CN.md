# 科学计算 Python 的任务配置

在任务的 `theory-master.config.json` 中，可显式指定科学脚本和依赖检查使用的解释器：

```json
{
  "compute_python": "/opt/research/.venv/bin/python"
}
```

该字段必须是非空绝对路径，目标文件必须可执行。SDK 按以下顺序选择：任务 `compute_python`、环境变量 `THEORYMASTER_COMPUTE_PYTHON`、既有默认 `~/miniconda3/bin/python`。任务字段无效时直接报错，不回退到另一环境。没有配置该字段的任务保持既有行为；SDK 不从 `research_plan.md` 正文推断解释器路径。

venv 的 `bin/python` 路径会保留，不将其符号链接解析为基础解释器。所选路径同时写入 Codex app-server 的 `PYTHON`、`THEORYMASTER_COMPUTE_PYTHON` 和 PATH 首项，以及新科研线程的 developer 指令。backend 在首次使用或 preflight 时固定本次选择，防止环境注入与线程身份取到不同配置。此设置不替换运行 SDK/Worker 本身的 Python。

运行前的 preflight 先用所选解释器执行有超时限制的 `-I` 标准库探针，不调用模型。成功后在任务研究根目录的 `runtime_preflight/compute-python/<id>.json` 中新增一份收据，记录选择来源、配置路径（适用时）、所选路径、解释器实际报告的 executable、version、prefix 和 base_prefix。收据只证明该计算解释器探针成功，不证明全部科研依赖已安装，也不代表随后 SDK 路由检查或科学任务成功；这不是科学证据或裁决。

修改配置会作用于之后创建的 backend。已经持久化的科研线程仍保存原有 developer 身份；SDK 不因配置变化自动改写旧身份、历史科学记录或迁移正在执行的任务。解释器环境迁移应在另行授权的运行维护中核对既有线程，不应仅改 PATH 后声称已同步。
