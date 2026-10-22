"""Page and actions for one Supervisor incident session."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

_STYLE = """
.tm-incident {max-width: 860px}
.tm-incident header p {margin: .2rem 0 1rem; color: #444}
.tm-incident-meta {font-family: ui-monospace, monospace; font-size: .92rem}
.tm-incident-notification-context {margin:10px 0 14px;padding:12px 14px;border:1px solid #52525b;border-radius:10px;background:#27272a;color:#e4e4e7}
.tm-incident-notification-context pre {white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;color:#d4d4d8;margin:8px 0 0}
.tm-incident .closed {color: #666}
.tm-incident-chat {display:flex; flex-direction:column; height:calc(100vh - 9rem); min-height:24rem; border:1px solid #3f3f46; border-radius:16px; overflow:hidden; background:#18181c; color:#e4e4e7;
  --loquix-surface-color:#18181c; --loquix-border-color:#3f3f46; --loquix-text-color:#e4e4e7;
  --loquix-composer-bg:#18181c; --loquix-composer-border-color:#3f3f46;
  --loquix-message-user-bg:#173a2a; --loquix-message-assistant-bg:#111114; --loquix-message-list-bg:#18181c;
  --loquix-input-bg:#0c0c0f; --loquix-input-color:#f4f4f5;}
.tm-incident-chat loquix-message-list {flex:1; min-height:0; display:block}
.tm-incident-chat loquix-chat-composer {display:block}
.tm-incident-chat img {max-width:240px; max-height:180px; border-radius:8px}
.tm-incident-text {white-space:pre-wrap}
.tm-incident-reads {margin:4px 16px; color:#a1a1aa; font-size:12px}
.tm-incident-reads summary {cursor:pointer}
.tm-incident-reads ul {margin:6px 0 0; padding-left:1.2rem}
.tm-incident-reads code {font-size:11px; word-break:break-all}
.tm-incident-error {min-height:1.2rem; margin:8px 14px 0; color:#fecdd3}
.tm-incident-actions {margin:8px 14px 14px}
"""
_SENDER = {"human": "user", "supervisor": "assistant", "tool": "tool", "system": "system"}
CHAT_MODULE = '<script type="module" src="/vendor/loquix-incident.js"></script>'
DIALOGUE_SCRIPT = """
<script>
function tmIncidentRoot(control){
  return control && (control.closest('section') || control.parentElement);
}
async function tmIncidentPost(path, body, control){
  const root = tmIncidentRoot(control);
  const error = root && root.querySelector('.tm-incident-error');
  const composer = root && root.querySelector('loquix-chat-composer');
  if(control && control.tagName === 'BUTTON') control.disabled = true;
  if(composer && path.endsWith('/message')) composer.disabled = true;
  try {
    const res = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},body:JSON.stringify(body)});
    const detail = await res.text();
    if(!res.ok){
      if(error) error.textContent = detail || '发送失败';
      if(control && control.tagName === 'BUTTON') control.disabled = false;
      if(composer) composer.disabled = false;
      return;
    }
    location.reload();
  } catch (err) {
    if(error) error.textContent = '发送失败：' + err;
    if(control && control.tagName === 'BUTTON') control.disabled = false;
    if(composer) composer.disabled = false;
  }
}
function tmReadFiles(panel){
  const items = panel && Array.isArray(panel.attachments) ? panel.attachments : [];
  return Promise.all(items.filter(item => item && item.file).map(item => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const raw = String(reader.result || '');
      resolve({name:item.file.name, media_type:item.file.type || '', data: raw.includes(',') ? raw.split(',').pop() : raw});
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(item.file);
  })));
}
function tmBindComposer(form){
  if(form.dataset.bound) return;
  form.dataset.bound = '1';
  const send = async (content) => {
    const panel = form.querySelector('loquix-attachment-panel');
    let images = [];
    try { images = await tmReadFiles(panel); }
    catch (err) {
      const error = tmIncidentRoot(form).querySelector('.tm-incident-error');
      if(error) error.textContent = '图片读取失败';
      return;
    }
    tmIncidentPost('/api/supervisor-session/'+form.dataset.session+'/message',{content:content,images:images},form);
  };
  form.addEventListener('loquix-submit', ev => send((ev.detail && ev.detail.content) || ''));
  customElements.whenDefined('loquix-chat-composer').then(() => {
    const shadow = form.shadowRoot;
    if(!shadow) return;
    shadow.addEventListener('click', ev => {
      const button = ev.target.closest && ev.target.closest('[part="send-button"]');
      if(!button) return;
      const input = shadow.querySelector('loquix-prompt-input');
      const text = input && input.value ? input.value.trim() : '';
      const panel = form.querySelector('loquix-attachment-panel');
      if(text || !panel || !panel.attachments || !panel.attachments.length) return;
      send('');
    });
  });
}
document.querySelectorAll('loquix-chat-composer.tm-incident-form').forEach(tmBindComposer);
document.querySelectorAll('[data-incident-resolve]').forEach(btn=>{
  btn.onclick=()=>tmIncidentPost('/api/supervisor-session/'+btn.dataset.incidentResolve+'/resolve',{note:'resolved by operator'},btn);
});
document.querySelectorAll('[data-incident-close]').forEach(btn=>{
  btn.onclick=()=>tmIncidentPost('/api/supervisor-session/'+btn.dataset.incidentClose+'/close',{note:'closed by operator'},btn);
});
</script>
"""


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _visible(content: str, role: str) -> str:
    if role != "supervisor":
        return content
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    if not text or text[:1] not in "{[":
        return content
    try:
        value = json.loads(text)
    except ValueError:
        return content
    if not isinstance(value, dict):
        return content
    parts = [str(value.get(key) or "").strip() for key in (
        "answer", "explanation", "diagnosis", "reason", "guidance", "repair_suggestion")]
    prose = "\n\n".join(part for part in parts if part)
    return prose or content


def _file_read(role: str, text: str) -> bool:
    if role != "tool":
        return False
    line = text.strip()
    if not line or "\n" in line or len(line) > 240:
        return False
    return line.startswith("read ") or line.startswith("!")


def _read_group(rows: list[dict[str, Any]]) -> str:
    lines = "".join(f"<li><code>{_e(str(row.get('content') or '').strip())}</code></li>" for row in rows)
    label = f"读取了 {len(rows)} 个文件"
    return (
        f'<details class="tm-incident-reads" data-role="tool"><summary>{label}</summary>'
        f"<ul>{lines}</ul></details>"
    )


def _message_item(session_id: str, row: dict[str, Any]) -> str:
    role = str(row.get("role") or "")
    sender = _SENDER.get(role, "system")
    text = _visible(str(row.get("content") or ""), role)
    images = ""
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if session_id:
        for image in meta.get("images") or []:
            if not isinstance(image, dict):
                continue
            image_id = str(image.get("id") or "")
            if not re.fullmatch(r"[0-9a-f]{16}", image_id):
                continue
            src = f"/api/supervisor-session/{_e(session_id)}/image/{image_id}"
            images += f'<img slot="below-bubble" alt="{_e(image.get("name") or "image")}" src="{src}">'
    return (
        f'<loquix-message-item data-role="{_e(role)}" sender="{sender}" '
        f'message-id="{_e(row.get("id") or "")}">'
        f'<div class="tm-incident-text">{_e(text)}</div>{images}</loquix-message-item>'
    )


def dialogue_html(session_id: str, rows: list[dict[str, Any]], *, active: bool, waiting: bool) -> str:
    """Loquix thread. Layout follows a native assistant composer; the session stays the incident store."""
    items: list[str] = []
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        if pending:
            items.append(_read_group(pending))
            pending.clear()

    for row in rows:
        role = str(row.get("role") or "")
        text = str(row.get("content") or "")
        if _file_read(role, text):
            pending.append(row)
            continue
        flush()
        items.append(_message_item(session_id, row))
    flush()
    if waiting:
        items.append(
            '<loquix-message-item data-role="system" sender="assistant" status="pending">'
            'Supervisor 正在回复…</loquix-message-item>'
        )
    composer = ""
    if active and session_id:
        disabled = " disabled" if waiting else ""
        composer = (
            f'<loquix-chat-composer class="tm-incident-form" data-session="{_e(session_id)}" '
            f'variant="contained" placeholder="输入消息" max-length="4000"{disabled}>'
            '<loquix-attachment-panel slot="actions-left" '
            'accepted-types="image/png,image/jpeg,image/gif,image/webp" '
            'max-files="4" max-size="1500000"></loquix-attachment-panel>'
            '</loquix-chat-composer>'
            '<p class="tm-incident-error"></p>'
            f'<p class="tm-incident-actions">'
            f'<button type="button" data-incident-resolve="{_e(session_id)}">标记已解决</button> '
            f'<button type="button" data-incident-close="{_e(session_id)}">关闭归档</button></p>'
        )
    return (
        '<div class="tm-incident-chat"><loquix-message-list auto-scroll show-scroll-anchor>'
        + "".join(items) + "</loquix-message-list>" + composer + "</div>"
    )


def research_roots(runs: list[dict[str, Any]]) -> list[Path]:
    roots: list[Path] = []
    for run in runs:
        raw = run.get("research_root") or run.get("task_root")
        if raw:
            roots.append(Path(str(raw)))
    return roots


def page_body(record: dict[str, Any], messages: list[dict[str, Any]], *, task_id: str = "") -> str:
    sid = str(record.get("id") or "")
    active = record.get("status") == "active"
    context_rows = [row for row in messages if (row.get("metadata") or {}).get("source") == "notification_context"]
    dialogue_rows = [row for row in messages if (row.get("metadata") or {}).get("source") != "notification_context"]
    waiting = bool(active and dialogue_rows and dialogue_rows[-1].get("role") == "human")
    context_html = "".join(
        '<aside class="tm-incident-notification-context"><strong>通知上下文 · 只读来源记录</strong><pre>'
        + _e(row.get("content") or "") + '</pre></aside>' for row in context_rows
    )
    closed = "" if active else f'<p class="closed">会话已 {_e(record.get("status"))}，对话保留供审计。</p>'
    from .navigation_model import task_console_url, task_explorer_url, task_supervisor_url
    task_url = task_console_url(task_id)
    context_links = (
        '<nav class="tm-incident-context" aria-label="返回科研任务">'
        f'<a href="{task_url}">Task Console</a> · '
        f'<a href="{task_supervisor_url(task_id)}">Supervisor</a> · '
        f'<a href="{task_explorer_url(task_id)}">Explore map</a></nav>'
        if task_url else '<p class="tm-incident-meta">所属任务暂不可定位 · <a href="/run/">Task List</a></p>'
    )
    script = CHAT_MODULE + (DIALOGUE_SCRIPT if active else "")
    if waiting:
        script += '<script>setTimeout(()=>location.reload(),3000);</script>'
    return (
        f'<style>{_STYLE}</style><section class="tm-incident" aria-label="Supervisor Incident">'
        '<header><strong>Supervisor Incident</strong>'
        f'<p class="tm-incident-meta">Cycle {_e(record.get("cycle_id"))}<br>Trigger:<br>'
        f'{_e(record.get("trigger_event"))}</p></header>'
        + context_links + context_html + dialogue_html(sid, dialogue_rows, active=active, waiting=waiting)
        + closed + script + "</section>"
    )


def json_session(value: Any) -> str:
    return json.dumps(str(value or ""))


def submit_human(runs: list[dict[str, Any]], session_id: str, content: str,
                 images: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    from theory_master.supervisor_incident_session import locate_incident, post_human_message
    root, _record, _messages = locate_incident(research_roots(runs), session_id)
    return post_human_message(root, session_id, content, images=images)


def read_image(runs: list[dict[str, Any]], session_id: str, image_id: str) -> tuple[bytes, str]:
    from theory_master.supervisor_incident_session import image_bytes, locate_incident
    root, _record, _messages = locate_incident(research_roots(runs), session_id)
    return image_bytes(root, session_id, image_id)


def finish(runs: list[dict[str, Any]], session_id: str, action: str, note: str) -> dict[str, Any]:
    from theory_master.supervisor_incident_session import (
        close_incident, locate_incident, resolve_incident,
    )
    root, _record, _messages = locate_incident(research_roots(runs), session_id)
    if action == "resolve":
        return resolve_incident(root, session_id, note)
    if action == "close":
        return close_incident(root, session_id, note)
    raise ValueError("unknown incident action")
