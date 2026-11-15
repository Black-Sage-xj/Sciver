"""Minimal, task-scoped add-Agent control for Task Console.

The large mode/configuration toolbar has been removed. The visible + button
uses the same authenticated, allowlisted SDK operator action endpoint.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from . import run_monitor
from .run_controls_ui import _launch_id


def mode_snapshot(run: dict[str, Any]) -> dict[str, Any]:
    root = Path(str(run.get("research_root") or "")).resolve()
    path = root / ".theory-master" / "worker_mode.json"
    default = {"enabled": False, "desired_agents": 2}
    try:
        if path.is_symlink() or (path.exists() and path.stat().st_size > 1048576):
            return default
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if (raw.get("schema") == "theory-master/worker-mode/v1"
                and type(raw.get("enabled")) is bool
                and type(raw.get("desired_agents")) is int
                and 1 <= raw["desired_agents"] <= 1024):
            return {"enabled": raw["enabled"], "desired_agents": raw["desired_agents"]}
    except (OSError, ValueError, TypeError):
        pass
    return default


def top_bar(run: dict[str, Any]) -> str:
    """Keep only invisible optimistic-concurrency metadata; no toolbar."""
    state = run_monitor.data(run)
    cycle = html.escape(str(state.get("cycle_id") or run.get("cycle") or ""), quote=True)
    status = html.escape(str(state.get("status") or ""), quote=True)
    launch = html.escape(_launch_id(run), quote=True)
    return (f'<span data-worker-request-context data-cycle="{cycle}" '
            f'data-status="{status}" data-launch="{launch}" hidden></span>')


STYLE = """
[data-worker-request-context][hidden]{display:none!important}
"""


def script(ident: str) -> str:
    """One delegated handler survives live-console pane replacement."""
    js = r'''(() => {
      if (window.__tmAddAgentConsole) return;
      window.__tmAddAgentConsole = true;
      const ident = __TASK_ID__;
      document.addEventListener('click', async event => {
        const button = event.target.closest?.('[data-worker-add],[data-worker-cancel],[data-worker-close]');
        if (!button || button.disabled) return;
        event.preventDefault();
        const nav = button.closest('.tm-worker-tabs');
        const info = nav?.querySelector('[data-worker-feedback]');
        const isCancel = button.hasAttribute('data-worker-cancel');
        const isClose = button.hasAttribute('data-worker-close');
        const ctx = isClose ? button : document.querySelector('[data-worker-request-context]');
        if (!ctx) {
          if (info) { info.hidden = false; info.textContent = '无法读取任务状态，请刷新页面。'; }
          return;
        }
        const workerId = button.dataset.workerClose;
        if (isClose && !confirm('关闭 Agent ' + workerId.split('-')[1] + '？\n此 Agent 将退出调度；运行中的工作会先在安全边界暂停。\n当前 Cycle 不再继续，已有科研记录和日志保留。')) return;
        const action = isClose ? 'worker_control' : isCancel ? 'cancel_pending_worker' : 'add_worker';
        const value = isClose ? {worker_id: workerId, operation: 'close'} : null;
        const label = isClose ? '关闭' : isCancel ? '撤销待分配请求' : '申请新增';
        button.disabled = true;
        if (nav) nav.dataset.workerMutating = '1';
        if (info) { info.hidden = false; info.textContent = '正在' + label + ' Agent…'; }
        try {
          const response = await fetch('/api/run/' + ident + '/action', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-TM-Operator-Intent': '1'},
            body: JSON.stringify({action, value,
              request_id: crypto.randomUUID().replace(/-/g, ''),
              expected_cycle: ctx.dataset.cycle || '',
              expected_status: ctx.dataset.status || '',
              expected_launch_id: isClose ? '' : ctx.dataset.launch})
          });
          const result = await response.json();
          if (!response.ok || !result.ok) throw new Error(result.error || '操作未完成');
          const outcome = result.outcome || {};
          if (isClose && outcome.status === 'closing') {
            // Keep the card until the worker process has actually exited.
            // The regular console poll will display durable CLOSING/errors.
            button.setAttribute('aria-busy', 'true');
            if (info) info.textContent = '正在安全暂停并关闭此 Agent，科研记录会保留。';
          } else if (isClose && new URL(location.href).searchParams.get('worker') === workerId) {
            location.assign('/run/' + ident + '/?worker=worker-1');
          } else {
            location.reload();
          }
        } catch (error) {
          button.disabled = false;
          if (info) {
            info.hidden = false;
            info.textContent = label + '失败：' + String(error.message || error)
              + '。请刷新任务状态后再试。';
          }
        } finally {
          if (nav) delete nav.dataset.workerMutating;
        }
      });
    })();'''.replace('__TASK_ID__', json.dumps(ident))
    return '<script>' + js + '</script>'
