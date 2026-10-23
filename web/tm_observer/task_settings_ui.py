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


def access_settings_panel(settings: dict[str, Any] | None) -> str:
    settings = settings or {"enabled": False, "username": ""}
    enabled = bool(settings.get("enabled"))
    external = bool(settings.get("externally_managed"))
    username = html.escape(str(settings.get("username") or ""), quote=True)
    checked = " checked" if enabled else ""
    disabled = " disabled" if external else ""
    external_note = '<p>登录由启动命令的认证文件管理，请修改该文件并重启服务。</p>' if external else ''
    return f'''<section class="tm-task-settings-panel tm-access-panel" aria-label="网页登录设置">
      <h2>网页登录</h2><p>本机使用默认无需登录。启用后，访问当前 Workspace 需要用户名和密码；所有登录账号都拥有运行控制权限。</p>
      {external_note}
      <form data-access-settings data-was-enabled="{str(enabled).lower()}">
        <label><input type="checkbox" name="enabled"{checked}{disabled}> 启用用户名和密码登录</label>
        <p><label>用户名 <input name="username" value="{username}" maxlength="80" autocomplete="username"{disabled}></label></p>
        <p><label>新密码 <input type="password" name="password" autocomplete="new-password" minlength="8" maxlength="1024"{disabled}></label></p>
        <p>密码至少 8 个字符。已设置密码时，留空表示保留；修改用户名时需要新密码。</p>
        <p><label>当前密码 <input type="password" name="current_password" autocomplete="current-password"{disabled}></label>（已启用登录时，修改或关闭均需验证）</p>
        <button type="submit"{disabled}>保存登录设置</button><p data-access-feedback role="status"></p>
      </form>
      <p>忘记密码：停止此服务，在本机运行 <code>sciver web --reset-login</code>；自定义工作区需带上同一 <code>--workspace</code>。默认本地登录只保护当前服务，不会修改模型账号。</p>
    </section><script>(function(){{
      const form=document.querySelector('[data-access-settings]');if(!form)return;
      const feedback=form.querySelector('[data-access-feedback]');
      form.addEventListener('submit',async event=>{{event.preventDefault();const button=form.querySelector('[type=submit]');button.disabled=true;feedback.textContent='正在保存…';
        try{{const response=await fetch('/api/settings/access',{{method:'POST',credentials:'same-origin',headers:{{'Content-Type':'application/json','X-TM-Operator-Intent':'1'}},body:JSON.stringify({{enabled:form.elements.enabled.checked,username:form.elements.username.value,password:form.elements.password.value,current_password:form.elements.current_password.value}})}});
          const data=await response.json();if(!response.ok)throw new Error(data.error||'保存失败');
          form.elements.password.value='';form.elements.current_password.value='';
          location.assign(data.enabled?'/login':'/settings/');
        }}catch(error){{feedback.textContent=error.message;button.disabled=false;}}
      }});
    }})();</script>'''


def workspace_settings_body(runs: Sequence[dict[str, Any]], selected: dict[str, Any] | None, *, access: dict[str, Any] | None = None) -> str:
    """Workspace root landing without silently choosing a different task."""
    if selected is not None:
        return task_settings_body(selected, task_selector=_task_selector(
            runs, str(selected.get("observer_id") or "")), access=access)
    return (
        '<main class="tm-task-settings-page" aria-label="Workspace Settings">'
        '<header class="tm-task-settings-head"><div>'
        '<span class="tm-tui-eyebrow">WORKSPACE SETTINGS</span>'
        '<h1>Settings</h1>'
        '<p>在 Workspace 中管理设置。自动审计是任务级配置，请先选择任务。</p>'
        '</div></header>'
        + _appearance_settings_panel() + access_settings_panel(access) +
        '<section class="tm-task-settings-panel">'
        + _task_selector(runs) +
        '</section></main>'
    )


def task_settings_body(run: dict[str, Any], *, task_selector: str = "", access: dict[str, Any] | None = None) -> str:
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
      {access_settings_panel(access)}
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
      const page=document.querySelector('.tm-task-settings-panel[data-settings-task]');
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
