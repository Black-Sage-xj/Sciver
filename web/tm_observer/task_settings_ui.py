"""Task-scoped Web settings page; uses the canonical Run operator API."""
from __future__ import annotations

import html
from typing import Any, Sequence

from . import run_monitor
from .run_controls_ui import _launch_id, settings_panel


def _task_selector(runs: Sequence[dict[str, Any]], selected_id: str = "") -> str:
    """Make task ownership explicit for Workspace-level Settings."""
    options = ['<option value="">选择要配置的任务</option>']
    for entry in runs:
        ident = str(entry.get("observer_id") or "")
        if len(ident) != 16 or not all(c in "0123456789abcdef" for c in ident):
            continue
        label = str(entry.get("task_name") or "未命名任务")
        options.append('<option value="' + html.escape(ident, quote=True) + '"'
                       + (' selected' if selected_id == ident else '') + '>'
                       + html.escape(label) + ' · ' + html.escape(ident[:8]) + '</option>')
    return (
        '<form class="tm-settings-task-picker" action="/settings/" method="get">'
        '<label for="tm-settings-task-choice">配置对象</label>'
        '<select id="tm-settings-task-choice" name="observer_id" required>'
        + ''.join(options) + '</select>'
        '<button type="submit">选择任务</button></form>'
    )


def _appearance_settings_panel() -> str:
    """Browser-local appearance applies across all workspace pages, not to a task."""
    return (
        '<section class="tm-task-settings-panel tm-appearance-panel" aria-label="外观设置">'
        '<h2>外观</h2>'
        '<p class="tm-task-settings-hint">在黑色与深蓝色主题之间切换，立即应用于整个 Web UI。'
        '偏好仅保存在当前浏览器，不改变科研任务或其他用户的设置。</p>'
        '<div class="tm-appearance-row">'
        '<label for="sciver-theme-picker">颜色主题</label>'
        '<select id="sciver-theme-picker" data-sciver-theme-picker aria-label="颜色主题">'
        '<option value="black">黑色 · 经典</option>'
        '<option value="blue">蓝色 · 深海</option>'
        '</select></div></section>'
    )


def workspace_settings_body(runs: Sequence[dict[str, Any]], selected: dict[str, Any] | None) -> str:
    """Workspace root landing without silently choosing a different task."""
    if selected is not None:
        return task_settings_body(selected, task_selector=_task_selector(
            runs, str(selected.get("observer_id") or "")))
    return (
        '<main class="tm-task-settings-page" aria-label="Workspace Settings">'
        '<header class="tm-task-settings-head"><div>'
        '<span class="tm-tui-eyebrow">WORKSPACE SETTINGS</span>'
        '<h1>Settings</h1>'
        '<p>在 Workspace 中管理设置。自动审计是任务级配置，请先选择任务。</p>'
        '</div></header>'
        + _appearance_settings_panel() +
        '<section class="tm-task-settings-panel">'
        + _task_selector(runs) +
        '</section></main>'
    )


def task_settings_body(run: dict[str, Any], *, task_selector: str = "") -> str:
    """Workspace Settings page with an explicit task-scoped editor."""
    state = run_monitor.data(run)
    ident = html.escape(str(run.get("observer_id") or ""), quote=True)
    title = html.escape(str(run.get("task_name") or "当前任务"), quote=True)
    cycle = html.escape(str(state.get("cycle_id") or run.get("cycle") or ""), quote=True)
    status = html.escape(str(state.get("status") or ""), quote=True)
    launch = html.escape(_launch_id(run), quote=True)
    return f'''<main class="tm-task-settings-page" aria-label="Workspace Settings">
      <header class="tm-task-settings-head">
        <div><span class="tm-tui-eyebrow">WORKSPACE SETTINGS</span>
          <h1>Settings</h1><p>当前配置对象：{title}</p></div>
        <a class="tm-task-settings-back" href="/run/{ident}/">← 返回 Run</a>
      </header>
      {_appearance_settings_panel()}
      {task_selector}
      <section class="tm-task-settings-panel" data-settings-task="{ident}"
        data-cycle="{cycle}" data-status="{status}" data-launch="{launch}">
        <h2>自动审计</h2>
        <p class="tm-task-settings-hint">这项功能不常用，仅在此处配置。设置会保存到当前任务，
          开关在下一次 Run/Resume 后由新 Runner 采用；不会打断正在执行的 Cycle。</p>
        {settings_panel(run, audit_only=True)}
      </section>
    </main>
    <script>(function(){{
      const page=document.querySelector('.tm-task-settings-panel');
      const settings=page?.querySelector('#tm-web-settings');
      if(!page||!settings)return;
      const feedback=settings.querySelector('.tm-setting-feedback');
      const show=message=>{{if(feedback)feedback.textContent=message;}};
      async function save(action,value,control,rollback){{
        if(control?.disabled)return;
        if(control)control.disabled=true;
        show('正在保存设置…');
        try{{
          const response=await fetch('/api/run/{ident}/action',{{
            method:'POST',headers:{{'Content-Type':'application/json',
              'X-TM-Operator-Intent':'1'}},
            body:JSON.stringify({{action,value,
              request_id:crypto.randomUUID().replace(/-/g,''),
              expected_cycle:page.dataset.cycle,
              expected_status:page.dataset.status,
              expected_launch_id:page.dataset.launch}})
          }});
          const data=await response.json();
          if(!response.ok||!data.ok)throw new Error(data.error||'保存失败');
          if(action==='set_operator_preference' && value.kind==='flag'){{
            const caption=settings.querySelector('.tm-setting-gear-status');
            if(caption)caption.textContent='已配置'+(value.value?'开启':'关闭')+' · 下次 Run/Resume';
          }}
          show('已保存。自动审计开关在下一次 Run/Resume 生效。');
        }}catch(error){{
          if(control&&rollback!==undefined)control.checked=rollback;
          show('保存失败：'+String(error.message||error)+'。请刷新页面确认状态。');
        }}finally{{
          if(control)control.disabled=false;
        }}
      }}
      settings.addEventListener('change',event=>{{
        const input=event.target.closest('[data-setting-key="cycle_audit"]');
        if(!input)return;
        save('set_operator_preference',{{kind:'flag',key:'cycle_audit',value:input.checked}},
             input,!input.checked);
      }});
      settings.addEventListener('click',event=>{{
        const button=event.target.closest('[data-setting-save-model="cycle_audit"]');
        if(!button)return;
        const selected=settings.querySelector('[data-setting-model="cycle_audit"]');
        if(!selected)return;
        save('set_operator_preference',{{kind:'model',key:'cycle_audit',value:selected.value}},
             button);
      }});
    }})();</script>'''
