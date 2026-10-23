<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/Sciver_Lockup_Horizontal_Blue_White.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/brand/Sciver_Lockup_Horizontal_Primary.svg">
    <img src="docs/assets/brand/Sciver_Lockup_Horizontal_Primary.svg" alt="Sciver 官方标志" width="420">
  </picture>
</p>

<h1 align="center">Sciver</h1>
<p align="center"><strong>自主科学发现系统</strong></p>
<p align="center"><em>Sci(ence)+(Disco)ver 从提出猜想到获得发现。</em></p>
<p align="center"><a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a></p>

Sciver 是一个持久化、多 Agent 协作的科学研究工作流引擎，并配有用于查看运行记录和审查科研进度的 **Web Observatory（网页观测台）**。观测台展示已持久化的产物和进程元数据，并通过 SDK 提供明确的操作入口，包括启动、恢复、暂停和终止科研运行。Sciver 旨在帮助研究团队将研究从猜想推进到结构化调查、证据审查和可追溯的结论记录。

本仓库包含引擎、Agent 工作流和 Web UI 的维护版本源码。实验数据、研究者账号、本地部署配置和个人科研工作区应与本仓库分开管理。

## 项目包含什么

- **科研工作流引擎**：编排多阶段、多 Agent 的科学调查过程。
- **Agent 角色与技能**：可复用的身份定义、指令和研究流程。
- **Web Observatory**：查看已持久化的运行记录、审查进度，并通过 SDK 显式控制科研运行。
- **测试与设计资料**：回归测试和技术设计文档。

## 仓库结构

- `theory_master/` — 核心 SDK、工作流编排、Agent 契约和运行时。
- `web/tm_observer/` — Web Observatory；Web 包从 `web/` 安装。
- `tests/`、`web/tests/` — 回归测试和集成测试。
- `design/`、`web/design/` — 技术设计文档。
- `theory_master/resources/skills/`、`identities/`、`scripts/` — 项目支持的技能、Agent 身份定义和开发工具。
- `config/.env.example` — 不包含凭据的配置模板。

部分包名和 CLI 命令仍保留历史上的 `TheoryMaster` / `tm` 命名。这些路径属于实现细节，不影响产品当前名称 Sciver。

## 快速开始

需要 Python 3.10 或更高版本。在仓库根目录执行：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[sdk]'
python -m pip install -e ./web
```

请根据实际使用的功能配置受支持的 SDK 模型提供方和外部工具。数值计算所用的 Python 环境，以及文献检索相关依赖，可能需要单独配置。

仓库中包含 `research-lit` 技能，但不捆绑外部文献检索适配器。如需启用外部检索，请安装一个你有权使用的适配器。SDK 默认在 `~/.local/share/theory-master/scholarly-scripts` 中查找 `lit_retrieve.py`；可通过 `THEORYMASTER_SCHOLARLY_SCRIPTS` 覆盖该目录，并在需要时用 `THEORYMASTER_SCHOLARLY_PYTHON` 指定已安装适配器依赖的 Python 解释器。即使没有外部检索适配器，Literature 仍可检查直接提供给它的研究资料。

## 运行测试

安装测试依赖并查看可用的 unittest 测试分类：

```bash
python -m pip install -e '.[test]' -e ./web
python scripts/run_tests.py --list
```

运行 CI 使用的完整核心 pytest 门禁：`python scripts/test_profiles.py default`。运行已登记的 unittest 分类测试集：`python scripts/run_tests.py`；只运行不变量测试门禁：`python scripts/run_tests.py --category invariant`。部分集成测试可能需要本地服务或额外依赖。

## 启动 Web Observatory

```bash
tm-observer serve --search-root /path/to/your/research/workspace --host 127.0.0.1 --port 8787
```

在本机打开 `http://127.0.0.1:8787/`。如需远程访问，请自行配置经过身份验证的反向代理或 SSH 隧道。**不要在没有身份验证的情况下直接暴露服务。**

网页包含会改变状态的操作。查看任务状态、日志及 Canvas/Markdown 文档属于只读操作；`run`、`resume`、`pause`、`kill` 会通过 SDK 改变执行状态。目标草稿、研究计划、Worker 设置和服务/账号控制也可能写入本地状态。启动或恢复任务可能发起模型请求并消耗模型提供方额度。网页登录账号不等于只读角色。请求保护和部署说明见 [Web 操作边界](web/README.md#operation-boundaries)。

`tm` CLI 仍可用于程序化工作流和诊断，但使用网页界面不要求先使用 CLI。

旧版交互式 TUI 暂时保留以维持兼容性，但不是推荐的主要界面。若要退役，将通过单独变更处理。

## 科研数据与安全

本仓库**不包含**个人 Canvas 图谱、实验运行数据、模型凭据、本地模型提供方账号、操作日志、历史变更登记或私有基准测试数据。这些内容应保存在独立的用户目录中。

请参考 `config/.env.example` 并在本地设置机密信息。仓库中的 `.gitignore` 不能替代安全审查。不要提交账号凭据、私有科研数据或未经授权再分发的第三方材料。

## 参与贡献与许可证

开发和 Pull Request 流程请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)；安全问题报告方式请参阅 [SECURITY.md](SECURITY.md)。开发期间请避免直接修改正式科研任务。

本仓库的软件采用 MIT 许可证，详见 [LICENSE](LICENSE)。Sciver 品牌图稿、商标及第三方材料可能另有权利或声明要求。MIT 软件许可证不代表自动授予商标使用权；复用或再分发这些材料前，请先核对适用条款。

