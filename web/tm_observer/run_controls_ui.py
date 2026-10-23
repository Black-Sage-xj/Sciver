"""Run control widgets, separate from the externally owned canvas-md module."""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from . import run_monitor
from .quota_auto_ui import _help_tooltip
from .supervisor_view import supervisor_enabled_for


def _e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _launch_id(run: dict[str, Any]) -> str:
    root = Path(str(run.get("research_root") or ""))
    registry = root / ".theory-master" / "runtime.json"
    try:
        if registry.is_symlink() or registry.stat().st_size > 100_000:
            return ""
        document = json.loads(registry.read_text(encoding="utf-8"))
        return str(document.get("launch_id") or "") if isinstance(document, dict) else ""
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        return ""


def can_resume_state(state: dict[str, Any]) -> bool:
    """Use the same state rules for the original operator and quick State."""
    status = str(state.get("status") or "").upper()
    return (status in {"PAUSED", "INTERRUPTED", "NEEDS_ATTENTION", "FAILED",
                       "FAILED_RECOVERY", "ORPHANED", "CHECKPOINT_REACHED",
                       "RECOVERABLE", "UNRESPONSIVE"}
            and str(state.get("phase") or "").lower() not in {"abandon_requested", "control_requested"})


def control_panel(run: dict[str, Any]) -> str:
    state = run_monitor.data(run)
    status = str(state.get("status") or "").upper()
    cycle = str(state.get("cycle_id") or run.get("cycle") or "")
    running = status in {"RUNNING", "ACTIVE", "STARTING", "RECOVERING"}
    attention = status == "NEEDS_ATTENTION"
    can_run = status in {"", "OVER", "STOPPED", "UNKNOWN", "IDLE", "NEW", "CANCELLED"}
    can_resume = can_resume_state(state)
    paused_resume = status == "PAUSED" and can_resume
    # Kill replaces the former duplicate "abandon current Cycle" user control.
    # PAUSED has no live Runner to consume a checkpoint command: SDK finalizes
    # the abandonment immediately, while RUNNING waits for a safe boundary.
    paused_kill = (paused_resume and not bool(state.get("execution_active"))
                   and str(state.get("phase") or "").lower() != "control_requested")
    kill_supported = paused_kill or (
        running and (state.get("sdk_runtime_capabilities") or {}).get("abandon_cycle_v1") is True)
    quick_options = ((
        '<button type="button" data-tui-quick-action="resume">Resume · 继续当前 Cycle</button>'
        '<button type="button" data-tui-quick-action="kill"'
        + ('' if paused_kill else ' disabled title="等待暂停操作到达安全检查点"')
        + '>Kill · 结束当前 Cycle（不可恢复）</button>'
    ) if paused_resume else (
        '<button type="button" data-tui-quick-action="pause">暂停 · 之后继续</button>'
        '<button type="button" data-tui-quick-action="kill"'
        + ('' if kill_supported else ' disabled title="当前 Runner 尚未加载安全结束协议"')
        + '>Kill · 结束当前 Cycle（不可恢复）</button>'
    ) if running else (
        '<button type="button" data-tui-quick-action="run">Run · 启动新 Cycle</button>'
    ) if can_run else (
        '<button type="button" data-tui-quick-action="resume">Resume · 继续当前 Cycle</button>'
    ) if can_resume else '')
    disabled = '' if running else ' disabled title="当前没有可控制的运行 Cycle"'
    kill_disabled = ('' if kill_supported else
        ' disabled title="当前 Runner 尚未加载安全结束协议；先 Pause 到安全点后再 Kill"')
    guidance = ('''<label>一次性人工指导<textarea id="tm-operator-guidance" rows="3" maxlength="3000" placeholder="指导将在下一次恢复后交给 Agent"></textarea></label>
        <button type="button" data-operator-action="attention_guidance">保存指导</button>''' if attention else '')
    return f'''<style>
    .tm-operator {{display:none !important;}}
    .tm-operator-title {{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;}}
    .tm-operator-title h3 {{margin:0;font-size:15px;}}
    .tm-operator-title > span {{font-size:11px;color:#a1a1aa;}}
    .tm-operator-actions {{display:flex;gap:9px;flex-wrap:wrap;align-items:center;}}
    .tm-operator-actions > label {{display:inline-flex;align-items:center;gap:7px;color:#d4d4d8;font-size:12px;}}
    .tm-operator-actions input[type=number] {{min-width:78px;width:88px;background:#18181b;color:#fafafa;border:1px solid #3f3f46;border-radius:7px;padding:7px 9px;}}
    .tm-operator-actions button {{padding:8px 12px;}}
    .tm-operator-actions button:disabled {{opacity:.35;cursor:not-allowed;}}
    .tm-operator-launch {{border-bottom:1px solid #27272a;padding-bottom:13px;}}
    .tm-operator-launch button:not(:disabled) {{background:#f4f4f5;color:#18181b;border-color:#f4f4f5;font-weight:650;}}
    .tm-operator-danger:not(:disabled) {{color:#fda4af;border-color:#963647;background:#32141c;}}
    .tm-operator-dialog {{border:1px solid #495164;border-radius:16px;background:#1d2029;color:#f4f4f5;
      max-width:min(440px,calc(100vw - 28px));width:100%;padding:25px;box-shadow:0 24px 68px #000b;}}
    .tm-operator-dialog::backdrop {{background:rgba(0,0,0,.73);backdrop-filter:blur(4px);}}
    .tm-operator-dialog h3 {{margin:0 0 10px;font-size:18px;}}
    .tm-operator-dialog p {{font-size:13px;line-height:1.8;color:#b4bdcc;}}
    .tm-operator-dialog-buttons {{display:flex;gap:9px;justify-content:flex-end;flex-wrap:wrap;margin-top:16px;}}
    .tm-operator-dialog-buttons button {{padding:9px 14px;border-radius:8px;}}
    .tm-operator-dialog-buttons .tm-operator-danger {{background:#581b2a;color:#fecdd3;}}
    .tm-operator-dialog-buttons button:disabled {{opacity:.38;cursor:not-allowed;}}
    .tm-operator-quick-options {{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:18px 0 9px;}}
    .tm-operator-resume-options {{grid-template-columns:minmax(0,1fr);}}
    .tm-operator-quick-options button {{border:1px solid #52627a;border-radius:9px;padding:13px 9px;background:#292f3c;color:#edf2fa;cursor:pointer;}}
    .tm-operator-quick-options button:hover:not(:disabled) {{background:#3a4458;}}
    .tm-operator-quick-options button:disabled {{opacity:.36;cursor:not-allowed;}}
    .tm-operator-compat-note {{color:#fbbf24 !important;font-size:11px !important;}}
    .tm-operator-quick-options [data-tui-quick-action=kill] {{color:#fecaca;border-color:#854254;background:#351e29;}}
    .tm-operator-confirm-warning {{border-left:3px solid #f59e0b;background:rgba(180,83,9,.13);
      border-radius:5px;padding:11px 13px;color:#fcdcb2 !important;}}
    .tm-operator-confirm-ack {{display:flex;align-items:flex-start;gap:10px;color:#e6e7ec;font-size:12px;line-height:1.7;cursor:pointer;}}
    .tm-operator-confirm-ack[hidden] {{display:none;}}
    @media(max-width:540px) {{.tm-operator-dialog{{padding:18px;}}}}
    .tm-operator-guidance textarea {{display:block;width:100%;min-height:80px;resize:vertical;background:#18181b;color:#fafafa;border:1px solid #3f3f46;border-radius:8px;margin:8px 0;padding:10px;}}
    #tm-operator-feedback {{display:block;margin:9px 0;color:#fcd34d;font-size:12px;}}
    #tm-operator-feedback:empty {{display:none;}}
    </style><section class="tm-operator" id="tm-operator" hidden data-id="{_e(run.get('observer_id'))}"
          data-cycle="{_e(cycle)}" data-status="{_e(status)}" data-launch="{_e(_launch_id(run))}">
      <!-- Only the action adapter is hidden; State and Cycle cards own the UI. -->
      <button type="button" data-operator-action="run"{' disabled' if not can_run else ''}></button>
      <button type="button" data-operator-action="resume"{' disabled' if not can_resume else ''}></button>
      <button type="button" data-operator-action="pause"{disabled}></button>
      <button type="button" data-operator-action="kill"{kill_disabled}></button>
      {f'<div class="tm-operator-guidance">{guidance}</div>' if guidance else ''}
      <span id="tm-operator-feedback" role="status" aria-live="polite"></span>
      <dialog id="tm-operator-quick-dialog" class="tm-operator-dialog" aria-labelledby="tm-operator-quick-title">
        <h3 id="tm-operator-quick-title">{'恢复当前 Cycle' if can_resume else '启动新 Cycle' if can_run else 'Workflow 控制'}</h3>
        <p>{'继续之前暂停的科研任务，沿用现有检查点和 Agent 线程；点击 Resume 后仍需确认。' if paused_resume else '已结束的 Cycle 不会恢复，Run 将创建新的 Cycle。' if can_run else '针对当前运行中的 Cycle。选择操作后仍需单独确认；当前模型 Turn 不会被强制结束。'}</p>
        <div class="tm-operator-quick-options{' tm-operator-resume-options' if paused_resume else ''}">
          {quick_options}
        </div>
        <div class="tm-operator-dialog-buttons"><button type="button" data-operator-quick-close>取消</button></div>
      </dialog>
      <dialog id="tm-operator-confirm-dialog" class="tm-operator-dialog" aria-labelledby="tm-operator-confirm-title">
        <h3 id="tm-operator-confirm-title">确认任务操作</h3>
        <p id="tm-operator-confirm-description"></p>
        <p id="tm-operator-confirm-warning" class="tm-operator-confirm-warning" hidden></p>
        <label id="tm-operator-confirm-ack-label" class="tm-operator-confirm-ack" hidden>
          <input id="tm-operator-confirm-ack" type="checkbox"/>
          我理解该操作不能撤销；已执行的计算、日志将保留，但不会自动生成 Judge 的科学结论。
        </label>
        <div class="tm-operator-dialog-buttons">
          <button type="button" id="tm-operator-confirm-cancel">取消</button>
          <button type="button" id="tm-operator-confirm-submit" class="tm-operator-danger">确认操作</button>
        </div>
      </dialog>
    </section>
    <script>
    (function() {{
      const box=document.getElementById('tm-operator'); if(!box)return;
      const feedback=document.getElementById('tm-operator-feedback');
      const quickDialog=document.getElementById('tm-operator-quick-dialog');
      const confirmDialog=document.getElementById('tm-operator-confirm-dialog');
      // Dialogs must leave the hidden adapter before showModal().
      if(quickDialog&&confirmDialog)box.after(quickDialog,confirmDialog);
      // Errors/success must remain visible even though the controller is hidden.
      const header=document.querySelector('.tm-tui-header');
      if(header&&feedback)header.append(feedback);
      quickDialog?.querySelector('[data-operator-quick-close]')?.addEventListener('click',()=>{{
        quickDialog.close();
      }});
      async function confirmAction(action,label,step,steps) {{
        const dialog=confirmDialog;
        const title=document.getElementById('tm-operator-confirm-title');
        const description=document.getElementById('tm-operator-confirm-description');
        const warning=document.getElementById('tm-operator-confirm-warning');
        const ackWrap=document.getElementById('tm-operator-confirm-ack-label');
        const ack=document.getElementById('tm-operator-confirm-ack');
        const submit=document.getElementById('tm-operator-confirm-submit');
        const cancel=document.getElementById('tm-operator-confirm-cancel');
        if(!dialog)return false;
        const kill=action==='kill';
        const destructive=kill||action==='abandon_cycle';
        const paused=box.dataset.status==='PAUSED';
        title.textContent=kill?'危险操作 · Kill 当前 Cycle ('+step+'/'+steps+')':label+' · 操作确认'+(steps>1?' ('+step+'/'+steps+')':'');
        description.textContent=kill?
          (paused
            ? 'PAUSED 状态会直接作废当前 Cycle，不需要等待 Runner 检查点。下次必须主动 Run 一个新 Cycle。'
            : 'RUNNING 状态会请求 SDK 在下一安全检查点作废当前 Cycle，不会异步强杀正在运行的模型 Turn；不自动启动下一 Cycle。'):
          (action==='resume'
            ? '从之前的检查点恢复当前科研 Cycle，保留已有 Agent 对话、计算和研究结果。'
            : label+'：请确认操作目标是当前 Cycle。');
        warning.hidden=!destructive;
        warning.textContent=kill?
          '永久作废当前 Cycle：不可撤销、不可 Resume；未完成的研究不会获得 Judge 结论，也不会升级为 Canvas 的科学进展。已有日志和科研文件保留以便追溯；未完成写入可能不完整。请确认之后需要手动 Run 新 Cycle。':
          '当前 Cycle 结束后无法恢复。请再次确认。';
        ackWrap.hidden=!destructive;ack.checked=false;
        submit.disabled=destructive;
        ack.onchange=()=>{{submit.disabled=destructive&&!ack.checked;}};
        return await new Promise(resolve=>{{
          let accepted=false;
          const onSubmit=()=>{{if(submit.disabled)return;accepted=true;dialog.close();}};
          const onCancel=()=>dialog.close();
          submit.addEventListener('click',onSubmit);
          cancel.addEventListener('click',onCancel);
          dialog.addEventListener('close',()=>{{
            submit.removeEventListener('click',onSubmit);
            cancel.removeEventListener('click',onCancel);
            resolve(accepted);
          }},{{once:true}});
          dialog.showModal();
        }});
      }}
      let startActionPending=false;
      let startProgress=null;
      async function submit(action,value) {{
        const startAction=action==='run'||action==='resume';
        if(startAction&&startActionPending){{feedback.textContent='启动请求仍在处理，请勿重复提交。';return false;}}
        const confirmations=action==='kill'?2:1;
        const label={{run:'启动新Cycle',resume:'恢复任务',pause:'暂停',kill:'Kill · 结束当前 Cycle',
          abandon_cycle:'结束本回合',max_cycles:'修改Cycle上限',
          attention_guidance:'提交一次性指导',attention_takeover:'切换Supervisor',supervisor:'切换Supervisor',quota_auto_adjust:'切换额度自调'}}[action]||action;
        if(action!=='max_cycles'){{
          for(let i=1;i<=confirmations;i++) {{
            if(!await confirmAction(action,label,i,confirmations))return false;
          }}
        }}
        if(startAction){{
          startProgress=window.tmStartProgress?.start({{action,label,scope:'task'}});
          if(!startProgress){{feedback.textContent='已有启动请求正在处理，请勿重复提交。';return false;}}
          startActionPending=true;
          box.querySelectorAll('[data-operator-action="run"],[data-operator-action="resume"]').forEach(b=>b.disabled=true);
        }}
        const request_id=crypto.randomUUID().replace(/-/g,'');
        feedback.textContent=startAction?'正在提交启动请求…':'正在提交请求…';
        try {{
          const response=await fetch('/api/run/'+box.dataset.id+'/action',{{method:'POST',
            headers:{{'Content-Type':'application/json','X-TM-Operator-Intent':'1'}},
            body:JSON.stringify({{action,value,request_id,expected_cycle:box.dataset.cycle,
              expected_status:box.dataset.status,expected_launch_id:box.dataset.launch}})}});
          const reply=await response.json();
          if(!response.ok||!reply.ok)throw new Error(reply.error||'任务控制失败');
          if(action!=='max_cycles')box.querySelectorAll('button').forEach(b=>b.disabled=true);
          if(startAction){{
            feedback.textContent=label+'：请求已受理，正在核验运行状态…';
            startProgress.accepted();
          }}else feedback.textContent=label+'：请求已登记。';
          return true;
        }}catch(error){{
          const message=String(error.message||error);
          if(startAction){{
            feedback.textContent=label+'：请求结果未确认，请先检查状态，不要直接重复提交。';
            startProgress?.failed(message);
          }}else feedback.textContent=label+'：'+message+'。可刷新后重试。';
          return false;
        }}
      }}
      box.addEventListener('click',event=>{{const button=event.target.closest('[data-operator-action]');if(!button)return;
        const action=button.dataset.operatorAction;
        let value=null;
        if(action==='attention_guidance')value=box.querySelector('#tm-operator-guidance').value;
        if(action==='run'||action==='resume'){{value={{}};box.querySelectorAll('[data-launch-setting]').forEach(c=>value[c.dataset.launchSetting]=c.checked);}}
        submit(action,value);
      }});
      // Inline Cycle editing reuses the same authenticated API and guard.
      document.addEventListener('tm-cycle-max-save',async event=>{{
        const okay=await submit('max_cycles',event.detail.value);
        event.detail.complete(okay,feedback.textContent);
      }});
      box.querySelectorAll('[data-operator-toggle]').forEach(input=>input.addEventListener('change',event=>{{
        const checked=event.target.checked;
        event.target.checked=!checked;
        submit(event.target.dataset.operatorToggle,checked);
      }}));
    }})();
    </script>'''


def _read_operator_preferences(run: dict[str, Any]) -> dict[str, Any]:
    """Bounded read-only view of task-specific flag/model choices."""
    root = Path(str(run.get("research_root") or ""))
    path = root / ".theory-master" / "web-operator-preferences.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16000:
            return {"flags": {}, "models": {}}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != "theory-master/web-operator-preferences/v1":
            return {"flags": {}, "models": {}}
        return {"flags": payload.get("flags") or {}, "models": payload.get("models") or {}}
    except (OSError, ValueError, UnicodeError, TypeError):
        return {"flags": {}, "models": {}}


def _settings_sources(run: dict[str, Any]) -> tuple[tuple[Any, ...], dict[str, Any]]:
    state = run_monitor.data(run)
    prefs = _read_operator_preferences(run)
    flags = prefs.get("flags") or {}
    models = prefs.get("models") or {}
    sources = (
        ("supervisor", "Supervisor", "任务跑不下去时由 SDK 内部 Supervisor 接管：运行故障（FAILED）走 Runtime 诊断，可在安全 checkpoint 上自动 Resume 一次；NEEDS_ATTENTION 走 Attention 审查，可交 Research Lead 重提一次资格。两者都不改科学结论，硬预算/运行 guard 只登记并停下。开关保存后在下一次安全判定生效，不打断当前 Agent Turn。接管过程可在 Supervisor 页面查看。",
         supervisor_enabled_for(run, state), "hot", "supervisor"),
        ("cycle_audit", "自动审计", "在 Cycle 结束（OVER、COMPLETE）或 NEEDS_ATTENTION 后，可另启独立 Codex 检查科研过程和系统表现，会额外消耗模型额度。审计不替代 Judge 的科学判定。此项在下一次 Run/Resume 采用。",
         bool(flags.get("cycle_audit", state.get("cycle_audit_actions_enabled"))), "flag", "cycle_audit"),
        ("quota_auto_adjust", "额度自调", "Supervisor 在后台监测 Codex 账号额度，并在阈值条件满足或账号真正耗尽时尝试安全切换与恢复。关闭后停止新的自动切换检查。开关保存后当前任务的下一次额度检查生效，不会使用重置卡，也不打断当前模型 Turn。",
         bool(state.get("quota_auto_adjust_enabled")), "hot", None),
    )
    return sources, models, flags


def _switch_control(key: str, label: str, enabled: bool, mode: str) -> str:
    """Only the physical switch track is a label; card surfaces cannot toggle."""
    return (
        '<label class="tm-setting-switch-control" title="切换' + _e(label) + '">'
        '<input type="checkbox" role="switch" aria-label="' + _e(label) + '"'
        ' data-setting-key="' + key + '" data-setting-mode="' + mode + '"'
        + (' checked' if enabled else '') + '/>'
        '<span class="tm-setting-switch-track" aria-hidden="true"></span></label>'
    )


def quota_auto_switch(run: dict[str, Any]) -> str:
    """Render the sole quota toggle, attached to the controller status card."""
    sources, _models, _flags = _settings_sources(run)
    for key, label, _description, enabled, mode, _aux in sources:
        if key == "quota_auto_adjust":
            return _switch_control(key, label, enabled, mode)
    return ""


def settings_panel(run: dict[str, Any], *, audit_only: bool = False) -> str:
    sources, models, flags = _settings_sources(run)
    model_options = (("gpt-6-luna", "GPT-6 Luna"), ("gpt-6-sol", "GPT-6 Sol"),
                     ("gpt-6.1-sol", "GPT-6.1 Sol"))
    toggles = []
    details = []
    audit_menu = ""
    for key, label, description, enabled, mode, aux in sources:
        if key not in {"quota_auto_adjust", "cycle_audit"}:
            caption = " · 默认" if mode == "flag" and key not in flags else ""
            status = "已配置开启" if enabled else "已配置关闭" if mode == "flag" else "关闭"
            if mode == "hot":
                status = "开启" if enabled else "关闭"
            effect = "下次 Run/Resume" if mode == "flag" else "当前任务"
            toggles.append(
                '<div class="tm-setting-compact tm-setting-card-surface" data-setting-card="' + key + '">'
                '<span class="tm-setting-compact-copy">'
                '<span class="tm-setting-compact-title">'
                '<button type="button" class="tm-setting-title-action" data-setting-open="' + key
                + '" aria-expanded="false" aria-controls="tm-setting-detail-box">' + _e(label) + '</button>'
                + _help_tooltip(key, description) + '</span>'
                '<small>' + status + ' · ' + effect + (caption if mode == "flag" else '') + '</small></span>'
                + _switch_control(key, label, enabled, mode)
                + '</div>'
            )
        item = ('<section class="tm-setting-detail-item" data-setting-detail="' + key + '" hidden>'
                '<strong>' + _e(label) + '</strong>'
                '<p>' + _e(description) + '</p>')
        if key == "supervisor" and run.get("observer_id"):
            item += ('<p><a class="tm-setting-supervisor-link" href="/run/' + _e(run.get("observer_id"))
                     + '/supervisor/">打开 Supervisor 接管对话 →</a></p>')
        if aux is not None:
            default = "gpt-6-luna" if aux == "supervisor" else ""
            selected = str(models.get(aux) or (models.get("attention_review") if aux == "supervisor" else "") or default)
            options = (('<option value=""' + (' selected' if not selected else '') +
                        '>Codex CLI 当前默认</option>') if aux == "cycle_audit" else "")
            for model, name in model_options:
                options += (f'<option value="{model}"' + (' selected' if selected == model else '')
                            + f'>{name}{"（默认）" if model == default else ""}</option>')
            default_label = ('默认：GPT-6 Luna · CodexHTTPS' if aux == 'supervisor'
                             else '默认：继承 Codex CLI 设置')
            item += (
                '<div class="tm-setting-model"><label>审查模型 '
                '<select data-setting-model="' + aux + '">' + options + '</select></label>'
                '<span>' + default_label + '</span>'
                '<button type="button" data-setting-save-model="' + aux + '">保存模型</button></div>'
                '<p class="tm-setting-advanced-note">只有点击“保存模型”才写入任务覆盖配置。</p>'
            )
        if key == "cycle_audit":
            # A standalone settings page exposes the actual Audit model
            # editor, unlike a hidden item in Run's contextual detail rail.
            if audit_only:
                item = item.replace('data-setting-detail="cycle_audit" hidden>',
                                    'data-setting-detail="cycle_audit">')
            audit_status = ("已配置开启" if enabled else "已配置关闭")
            audit_menu = (
                '<details class="tm-setting-gear-submenu"' + (' open' if audit_only else '') + '>'
                '<summary><span class="tm-setting-gear-menu-title">自动审计'
                + _help_tooltip(key, description) + '</span>'
                '<span class="tm-setting-gear-status">'
                + audit_status + ' · 下次 Run/Resume</span></summary>'
                '<div class="tm-setting-gear-audit-controls">'
                '<span>允许审计</span>' + _switch_control(key, label, enabled, mode) + '</div>'
                + item + '</section></details>'
            )
        else:
            details.append(item + '</section>')
    if audit_only:
        return ('<section class="tm-settings-audit" id="tm-web-settings"'
                ' aria-label="自动审计设置">' + audit_menu
                + '<p class="tm-setting-feedback" role="status" aria-live="polite"></p>'
                '</section>')
    return (
        '<section class="tm-tui-settings" id="tm-web-settings" aria-label="任务级运行开关">'
        '<div class="tm-setting-compact-row">' + ''.join(toggles) + '</div>'
        '<div class="tm-setting-detail-panel" id="tm-setting-detail-box" '
        'aria-label="选中开关的详细设置" aria-live="polite" hidden>'
        + ''.join(details) + '</div>'
        '<p class="tm-setting-feedback" role="status" aria-live="polite"></p>'
        '</section>'
    )
