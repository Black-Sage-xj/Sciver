"""First-use instructions shared by empty Observatory pages.

The Observatory discovers persisted tasks; creation and model authentication use
existing CLI entry points so this panel does not grant new runtime permissions.
"""
from __future__ import annotations

import html


WORKSPACE_SETUP = '''export SCIVER_WORKSPACE="$HOME/sciver-workspace"
mkdir -p "$SCIVER_WORKSPACE"
export THEORY_MASTER_SEARCH_ROOT="$SCIVER_WORKSPACE"
export THEORY_MASTER_DATA_ROOT="$SCIVER_WORKSPACE/.theory-master-tasks"'''


def first_use_panel() -> str:
    """Render copyable commands with offline preparation and live run separated."""
    def code(commands: str) -> str:
        return '<pre><code>' + html.escape(commands) + '</code></pre>'

    return '''<style>
.tm-onboarding{margin:18px 0;padding:20px;border:1px solid var(--line,#41414b);border-radius:12px;max-width:100%;color:var(--text,#e4e4e7);line-height:1.7}
.tm-onboarding h2{font-size:19px;margin:0 0 10px}.tm-onboarding h3{font-size:15px;margin:19px 0 6px}
.tm-onboarding p{margin:7px 0}.tm-onboarding pre{max-width:100%;overflow:auto;padding:12px;border:1px solid var(--line,#41414b);border-radius:8px;background:var(--panel,#15151a);font:12px/1.65 ui-monospace,monospace;white-space:pre}
.tm-onboarding a{color:var(--accent,#85b8ff)}.tm-onboarding details{margin-top:15px}.tm-onboarding summary{cursor:pointer}
</style><section class="tm-onboarding" aria-label="首次使用引导">
<h2>开始第一个科研任务</h2>
<p>自己的课题可从 <a href="/research-chat/?new=1">创建科研项目</a> 开始，先讨论问题、查阅资料，再审阅计划并确认立项。</p>
<details><summary>使用终端准备官方教学示例</summary>
<p>在运行 Sciver 的机器上打开终端，使用 Python 3.12 或更高版本及安装 Sciver 时的虚拟环境。网页从工作目录发现任务。</p>
<h3>1. 创建工作目录并完成离线检查</h3>
<p>任务和运行数据放在源码目录之外。以下命令只运行本地模拟检查和准备官方 Test0，不会调用模型。</p>''' + code(WORKSPACE_SETUP + '\nsciver smoke\nsciver test Test0 --root "$SCIVER_WORKSPACE" --prepare-only') + '''
<p>记录输出中的 Task ID 和 Console 路径。每次准备 Test0 都创建全新任务，从 Cycle 1 开始，使用空白图谱、空白 Scientific Model 和新 Agent 会话。</p>
<h3>2. 打开任务页</h3>
<p>如果当前网页的搜索目录不同，停止当前网页服务，在上述终端用相同工作目录重新启动：</p>''' + code('tm-observer serve --search-root "$SCIVER_WORKSPACE" --host 127.0.0.1 --port 8787') + '''
<p>打开 <a href="/run/">Task List</a> 并刷新，进入任务查看 Research Plan、Canvas 和 Cycle 结果。准备完成尚未启动科研。</p>
<details><summary>3. 配置模型并启动真实科研（消耗模型额度）</summary>
<p>先安装受支持的 Codex CLI，使 <code>codex --version</code> 可执行；再完成账号登录与模型检查。登录和本地状态检查不会启动科研。</p>''' + code('codex --version\nsciver codex-account login-1\nsciver codex-account 1\nsciver codex-account status\nsciver model-profile status') + '''
<p>确认当前模型与账号后，使用上面输出的实际 Task ID 替换 0。以下操作会调用模型，消耗账号额度；费用取决于模型、Token 与运行时长：</p>''' + code('sciver run 0') + '''
<p>在 <a href="/services/">Services</a> 检查账号状态，在 <a href="/run/">Task List</a> 进入任务查看运行和结果。自己的课题需先按 README 模板编写 <code>research_plan.md</code>，再执行 <code>sciver new /绝对路径/research_plan.md</code>。</p>
</details></details></section>'''
