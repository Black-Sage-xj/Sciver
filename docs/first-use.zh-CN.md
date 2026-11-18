# 首次使用

需要 Python 3.12 或更高版本。按 [README](../README.zh-CN.md) 从源码安装，或按[分发说明](distribution.md)安装 wheel；每个终端均需激活相同虚拟环境。科研任务和运行数据放在源码目录之外。

## 离线准备

```bash
export SCIVER_WORKSPACE="$HOME/sciver-workspace"
mkdir -p "$SCIVER_WORKSPACE"
export THEORY_MASTER_SEARCH_ROOT="$SCIVER_WORKSPACE"
export THEORY_MASTER_DATA_ROOT="$SCIVER_WORKSPACE/.theory-master-tasks"
sciver smoke
sciver test --list
sciver test Test0 --root "$SCIVER_WORKSPACE" --prepare-only
```

`smoke` 使用模拟后端，输出临时检查目录。省略 `--prepare-only` 会启动真实模型调用并消耗额度。`--prepare-only` 验证并复制官方 Test0 输入，初始化新任务，不会调用模型。记录输出中的 Task ID、Task Home、Research Root 和 Console 路径。每次 Test0 都创建全新任务、空白图谱、空白 Scientific Model 和新的 Agent 会话，从 Cycle 1 开始。历史任务保留；只有明确要继续已有任务时才使用 `resume`。

## 查看任务

在另一个终端激活相同虚拟环境，设置相同工作目录变量，然后启动网页服务：

```bash
tm-observer serve --search-root "$SCIVER_WORKSPACE" --host 127.0.0.1 --port 8787
```

打开 `http://127.0.0.1:8787/run/`，刷新 Task List，进入已准备的任务，查看 Research Plan。`--search-root` 应包含任务目录。运行数据可以单独存放，网页会校验任务的规范 SDK 绑定后再打开计划或图谱。上面的配置将两者放在同一工作目录中。只完成准备时还没有科研结果。

## 配置模型并启动科研

先安装受支持的 Codex CLI，确保 `codex --version` 可执行，再使用 Sciver 的账号登录步骤。桌面端登录不代表 Sciver 已配置完成。

```bash
python -m pip install 'sciver[sdk]==0.1.1'
export THEORYMASTER_COMPUTE_PYTHON="$(command -v python)"
codex --version
sciver codex-account login-1
sciver codex-account 1
sciver codex-account status
sciver model-profile status
```

SDK extra 安装模型客户端依赖，可能需要访问包索引；完全离线时须提供这些依赖的 wheel，并添加 `--no-index --find-links /absolute/path/to/reviewed-wheels`。上述 export 将当前虚拟环境选为计算环境；按实际研究需要在该环境安装数值库。按照终端提示完成设备码登录。`codex-account status` 应显示所选账号为已登录；这检查的是本地凭据，不代表已经成功发起在线模型请求。核对 `model-profile status` 打印的实际模型路由。Services 可查看账号与额度信息。其他 Provider 和工具需配置各自的本地环境，可参考 `config/.env.example`。

**下面的操作会启动真实模型调用，消耗账号额度，并可能产生费用。** 成本取决于 Provider、模型、Token 和运行时长；一次任务可能包含多个 Agent 和 Cycle。Research Chat 也会消耗模型额度。将 `0` 替换成刚才输出的实际 Task ID，并在配置好相同工作目录变量的终端执行：

```bash
sciver run 0
```

在 Task List → Task Console 查看运行状态、异常、Cycle 和报告；在 Canvas 查看积累的科学图谱。完成裁决后才会形成科学结论，停止或失败不代表已得出科学结果。Research Root 保存运行产物，备份工作目录时应连同任务目录一起保留。

## 创建自己的课题

网页用户可直接打开 `/research-chat/?new=1`，在“创建新项目”选择“已有明确问题”或“从兴趣或现象开始”。先与 Sciver 讨论、查阅资料，按需要生成并修改计划，再点击“审阅计划并创建项目”。可以先保存未完成的草稿；确认创建后会获得正式项目链接，科研尚未启动。发送筹备聊天消息使用已配置的模型账号。问题需要文献时，Chat 可在你的请求范围内调用现有 Literature Agent，复用其身份、技能和输出合同；独立模型会话完成发现、选读、比较及按缺口补查。默认最多 2 轮、6 个核心来源获取尝试；问题未解决时保留资料并暂停，同一作业恢复会复用已完成工作。交互详情见[科研协同指南](research-chat-collaboration.zh-CN.md)。

也可以通过终端创建：

创建独立任务目录并编写计划。下面四个一级标题必须按此顺序出现，`sciver new` 会添加由 SDK 维护的 Scientific Model。开始真实科研前，将示例段落替换为具体目标、研究范围、允许的资源、完成标准及可用输入。

```bash
mkdir -p "$SCIVER_WORKSPACE/my-topic"
cat > "$SCIVER_WORKSPACE/my-topic/research_plan.md" <<'PLAN'
# Goal

判断一个具体科学命题在明确适用范围内是否成立。

# Research Contract

明确研究范围、证据标准、允许的工具、资源限制和交付物。

# Final Gate

给出完成整体科研目标的可检验标准。

# Required Context

列出输入资料及其绝对路径、背景假设和约束。
PLAN
sciver new "$SCIVER_WORKSPACE/my-topic/research_plan.md"
```

`new` 只校验和初始化计划，不调用模型。记录新 Task ID，在 Research Plan 审阅计划后，使用 `sciver run <Task ID>` 启动。示例是格式模板，需要填入真实问题与输入。

## 排错

- 找不到命令：激活安装时的虚拟环境，检查 `sciver --help` 和 `tm-observer --help`。
- 任务列表为空：确认输出的 Task Home 在网页服务的 `--search-root` 之下；按上述工作目录重启服务并刷新。
- 缺少登录：安装 Codex 可执行程序，完成 `login-1`，选择账号 `1`，再次检查 `status`。不要把凭据粘贴到聊天或提交 Git。
- 计划格式错误：按顺序使用 `# Goal`、`# Research Contract`、`# Final Gate`、`# Required Context`，每段内容不能为空。初始化后保留生成的 `# Scientific Model` 作为最后一段。
- Test0 准备失败：检查错误信息、目录权限和 `sciver test --list`。显式指定的目标目录必须为空；使用 `--root` 会自动分配全新实例目录。


## 可选 PDF 转换

新安装默认**关闭** PDF 转换。在 **Settings（`/settings`）→ PDF 转换** 中，为运行 Sciver 的主机选择能力：

- **本地 Marker**：明确配置可执行文件路径，或将 `marker_single` 放入服务进程的 `PATH`。Sciver 不假定个人环境路径、不自动安装 Marker；本机启用不会改变产品默认值。
- **在线 MinerU**：填写 API Token，或给服务进程设置 `MINERU_API_KEY`。转换时会上传 PDF。Token 保存在服务器私有配置（0600）或环境变量中，读取设置只显示是否已配置。

设置默认保存在 `$XDG_CONFIG_HOME/sciver/document-conversion.json`，未设置 XDG 时使用 `~/.config/sciver/document-conversion.json`；可用 `SCIVER_DOCUMENT_CONVERSION_CONFIG` 指定位置。修改对后续转换生效。关闭转换仍可检索题录、摘要及可访问的 HTML 正文。默认处理前 20 页，填 0 表示所有可处理页；前 N 页不代表整篇论文已核验。原 PDF、转换文本、图片与版面文件保留用于核查，转换成功不会自动变成已验证的科学主张。确定性测试与模拟后端验收工程链路，真实模型调研质量及在线转换尚未验证。
