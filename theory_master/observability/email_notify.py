"""TheoryMaster terminal failure and unrecoverable Attention notifications.

Normal completion, transient SDK-internal Attention, and operator-driven stops
are silent. Email is sent only after a terminal hold/failure is confirmed.
"""

from __future__ import annotations

import html
import os
import smtplib
import ssl
import tempfile
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any


def _notification_setting(key: str) -> str:
    """Get private notification settings from the environment or local env file."""
    value = (os.environ.get(key) or "").strip()
    if value:
        return value
    custom = os.environ.get("THEORYMASTER_ENV_FILE")
    path = Path(custom).expanduser() if custom else Path.home() / ".config" / "theory-master" / "env"
    if path.is_file():
        for entry in path.read_text(encoding="utf-8").splitlines():
            k, sep, v = entry.strip().partition("=")
            if sep and k.strip() == key:
                return v.strip().strip("'\"")
    return ""


def email_terminal_events_enabled(run_dir: Path | str | None = None) -> bool:
    """Return whether terminal email is enabled, honoring live task-level config.

    A detached Worker inherits its task flag in the process environment. That
    value becomes stale if the operator changes the task config while a long
    scientific Cycle is running, so when a run directory is known the current
    task .theory-master/env is authoritative and is re-read for each event.
    """
    if run_dir:
        candidate = Path(run_dir).expanduser()
        # A terminal alert from a disposable temp tree is almost always a test
        # fixture or diagnostic run. Never send real mail from it by inheriting
        # a task's copied opt-in; require an explicit override for intentional
        # temporary deployments.
        temp_root = Path(tempfile.gettempdir()).resolve()
        resolved_candidate = candidate.resolve()
        is_temp_path = resolved_candidate == temp_root or temp_root in resolved_candidate.parents
        allow_temp_email = (os.environ.get("THEORYMASTER_ALLOW_TEMP_TASK_EMAIL") or "").strip().lower()
        if is_temp_path and allow_temp_email not in {"1", "true", "yes", "on"}:
            return False
        for parent in (candidate, *candidate.parents):
            task_config = parent / ".theory-master"
            if not task_config.is_dir():
                continue
            task_env = task_config / "env"
            # Once a task root is identified, notification policy is task-scoped.
            # Do not inherit the host-wide SMTP opt-in for temporary/test tasks:
            # that can turn a test's synthetic terminal state into a real email.
            if not task_env.is_file():
                return False
            for line in task_env.read_text(encoding="utf-8").splitlines():
                name, sep, raw = line.strip().partition("=")
                if sep and name == "THEORY_MASTER_EMAIL_TERMINAL":
                    value = raw.strip().strip("'\"").lower()
                    return value not in {"", "0", "false", "no", "off"}
            # A task env file that omits the switch is also an explicit opt-out
            # from inheriting the machine-wide setting.
            return False
    value = (os.environ.get("THEORY_MASTER_EMAIL_TERMINAL") or "").strip()
    if not value:
        env_override = os.environ.get("THEORYMASTER_ENV_FILE")
        env_file = (Path(env_override).expanduser() if env_override else
                    Path.home() / ".config" / "theory-master" / "env")
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                name, sep, raw = line.strip().partition("=")
                if sep and name == "THEORY_MASTER_EMAIL_TERMINAL":
                    value = raw.strip().strip("'\"")
                    break
    if not value:
        return False
    return value.lower() not in {"0", "false", "no", "off"}


def _clean(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return "" if text in {"-", "None"} else text


def _clip(text: str, limit: int = 28) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _run_dir(state: dict[str, Any]) -> str:
    for key in ("run_dir", "cycle_dir"):
        value = _clean(state.get(key))
        if value:
            return value
    return ""


# code -> (标题, 发生了什么, 原因, 不能恢复时的下一步, 可以恢复时的下一步)
_ATTENTION_COPY = {
    "runtime_no_recoverable_checkpoint": (
        "没有可恢复检查点，不能自动重放",
        "运行停在写出检查点之前。当前存档不能安全自动重放。这不是科学结论错了，也不是目标已经做完。",
        "检查点还没写出来就停了，现存档不能自动重放。",
        "先查这次运行停在哪一步。不要直接重开；原因没变时，重复启动还会停在同一处。",
        "先确认已经有可恢复的存档，再在该任务里执行 tm resume。没有新检查点时不要重开。",
    ),
    "runtime_retry_exhausted": (
        "内部重试已用完，仍无法继续",
        "允许的自动检修和重试都做完了，任务还是停着。这不是科学结论错了，也不是目标已经做完。",
        "自动恢复次数已经用完，需要人看这次运行。",
        "先看停在哪一步。原因没处理前不要重复启动。",
        "原因处理完后，再在该任务里执行 tm resume。原因没变时不要重复启动。",
    ),
    "no_qualified_candidate": (
        "前沿候选都被否决，流程停住",
        "独立前沿候选没有通过资格审查，系统不会自己再开一轮。这不是运行崩溃，也不是目标已经做完。",
        "",
        "先看候选为什么被否决。不要原样重开同一轮。",
        "看完否决原因，决定改方向或补证据后，再在该任务里执行 tm resume。",
    ),
    "needs_input": (
        "缺你的输入，流程在等",
        "当前步骤需要你给方向或材料，系统不会自己猜。这不是运行崩溃。",
        "",
        "补上缺的输入后再继续。不要空着手重复启动。",
        "补上缺的输入后，再在该任务里执行 tm resume。",
    ),
}


def _failure_title(detail: str) -> str:
    low = detail.lower()
    if "without assistant final response" in low or "no assistant" in low:
        return "模型没有给出最终回复，运行中断"
    if "usage limit" in low or "quota" in low:
        return "额度不足，运行中断"
    text = detail
    for prefix in ("Codex backend delivery error:", "RuntimeError:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    short = _clip(text, 28)
    return short or "运行异常终止"


def _render_mail(subject: str, what: str, why: str, nxt: str, where: str) -> tuple[str, str]:
    """Plain text plus a short HTML layout. Both say only what happened and what to do."""
    lines = ["您好，", "", "发生了什么", what]
    if why:
        lines += ["", "原因", why]
    lines += ["", "接下来", nxt]
    if where:
        lines += ["", f"运行目录：{where}"]
    lines += ["", "—— Sciver", ""]
    plain = "\n".join(lines)

    def block(text: str) -> str:
        return "<p style=\"margin:0\">" + "<br>".join(html.escape(part) for part in text.split("\n")) + "</p>"

    parts = [
        "<div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "color:#1c1c1c;line-height:1.65;max-width:640px\">",
        "<p style=\"margin:0 0 18px;font-size:15px\">您好，</p>",
        "<h2 style=\"margin:0 0 8px;font-size:18px\">发生了什么</h2>",
        block(what),
    ]
    if why:
        parts += [
            "<h2 style=\"margin:18px 0 8px;font-size:18px\">原因</h2>",
            "<div style=\"background:#f4f4f5;border-left:4px solid #b45309;padding:10px 12px\">",
            block(why),
            "</div>",
        ]
    parts += [
        "<h2 style=\"margin:18px 0 8px;font-size:18px\">接下来</h2>",
        "<div style=\"background:#eff6ff;border-left:4px solid #1d4ed8;padding:10px 12px\">",
        block(nxt),
        "</div>",
    ]
    if where:
        parts.append(
            f"<p style=\"margin:16px 0 0;color:#52525b;font-size:13px\">运行目录：{html.escape(where)}</p>"
        )
    parts.append("<p style=\"margin:22px 0 0;color:#71717a;font-size:12px\">—— Sciver</p></div>")
    return plain, "".join(parts)


def _quota_copy(payload: dict[str, Any], recovery: dict[str, Any]) -> tuple[str, str, str, str]:
    reset_at = payload.get("next_reset_at")
    reset_line = "重置时间未知"
    if isinstance(reset_at, (int, float)) and not isinstance(reset_at, bool) and 0 < reset_at < 10**11:
        from datetime import datetime
        from time import time
        timestamp = datetime.fromtimestamp(reset_at).astimezone()
        hours = max(0.0, (reset_at - time()) / 3600)
        reset_line = f"约 {hours:.1f} 小时后（{timestamp:%Y-%m-%d %H:%M %Z}）"
    poll_seconds = payload.get("next_check_in_s")
    interval = (f"每 {int(poll_seconds)} 秒" if isinstance(poll_seconds, (int, float))
                and not isinstance(poll_seconds, bool) and 0 < poll_seconds <= 3600
                else "定期")
    detail = _clean(payload.get("error") or recovery.get("message"))
    why = "两个 Codex 账号都已确认没有可用额度。"
    if detail:
        why = f"{why}\n{detail}"
    why = f"{why}\n最早可能恢复：{reset_line}。到点不保证立刻可用。"
    nxt = (
        f"不用动手。系统会{interval}检查一次，任一账号恢复后自动继续。"
        "期间不会做 JSON repair、普通 reconnect 或 compaction 重试。"
        "只有自动恢复服务本身停了，才需要在该任务里执行 tm resume。"
    )
    return "【暂停】两个账号额度都用完，正在等恢复", (
        "两个 Codex 账号都没有可用额度。任务已安全暂停，正在等额度恢复。"
    ), why, nxt


def _attention_copy(
    state: dict[str, Any], payload: dict[str, Any], attention: dict[str, Any],
) -> tuple[str, str, str, str]:
    code = _clean(payload.get("attention_code") or attention.get("code") or state.get("closure_reason"))
    reason = _clean(payload.get("reason") or attention.get("reason") or state.get("human_attention"))
    known = _ATTENTION_COPY.get(code)
    resumable = state.get("resumable") is True
    if known:
        title, what, why, blocked, ready = known
        if not why:
            why = reason or "系统没有给出更具体的说明。"
        nxt = ready if resumable else blocked
    else:
        title = "需要人工处理，无法自动继续"
        # A generic NEEDS_ATTENTION code does NOT prove that an automatic
        # repair attempt occurred, let alone that it succeeded or exhausted.
        what = (
            "本次任务停在需要处理的检查点；被冻结的科研检验尚未完成，"
            "此状态本身不是 Judge 的科学结论，也不表示已经自动检修或重试。"
        )
        why = reason or "系统没有给出更具体的停机说明。"
        nxt = ("原因处理完后，再在该任务里执行 tm resume。原因没变时不要重复启动。"
               if resumable else
               "先查停机原因。不能自动恢复，不要直接重开。")
    return f"【需处理】{title}", what, why, nxt


def _attach_mail(message: EmailMessage, subject: str, plain: str, html_body: str) -> None:
    message["Subject"] = subject
    message.set_content(plain, subtype="plain", charset="utf-8")
    message.add_alternative(html_body, subtype="html", charset="utf-8")


def should_send_terminal_email(event: str, payload: dict[str, Any] | None = None) -> bool:
    """Raw quota pauses are internal; only verified two-account waiting is emailed."""
    kind = str(event or "").strip().lower()
    if kind in {"run.failed", "run.needs_attention"}:
        return True
    return (
        kind == "run.paused"
        and str((payload or {}).get("pause_kind") or "").strip().lower() == "provider_quota_exhausted"
        and (payload or {}).get("quota_wait_confirmed") is True
    )


def send_terminal_email(event: str, state: dict[str, Any], payload: dict[str, Any]) -> None:
    """Send one terminal-event email using the repository's 163 SMTP sender.

    The import is deliberately lazy: observing a run must remain usable when
    email support is not on ``PYTHONPATH`` or when notifications are disabled.
    """
    if not should_send_terminal_email(event, payload) or not email_terminal_events_enabled(_run_dir(state) or None):
        return

    # Keep terminal notification independent of auto_submit's optional HPC
    # dependencies (notably Paramiko).  Importing batch_watch used to make a
    # local TheoryMaster failure unable to send its own failure email.
    notify_to = _notification_setting("THEORYMASTER_NOTIFY_TO")
    user = _notification_setting("AUTO_SUBMIT_SMTP_USER")
    password = _notification_setting("AUTO_SUBMIT_SMTP_PASSWORD")
    host = (os.environ.get("AUTO_SUBMIT_SMTP_HOST") or "smtp.163.com").strip()
    port = int((os.environ.get("AUTO_SUBMIT_SMTP_PORT") or "465").strip())

    kind = str(event or "").strip().lower()
    recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else {}
    attention = state.get("attention") if isinstance(state.get("attention"), dict) else {}
    where = _run_dir(state)
    if kind == "run.paused":
        subject, what, why, nxt = _quota_copy(payload, recovery)
    elif kind == "run.needs_attention":
        subject, what, why, nxt = _attention_copy(state, payload, attention)
    else:
        detail = _clean(payload.get("error") or recovery.get("message") or payload.get("reason"))
        if not detail:
            verdict = _clean(payload.get("verdict") or state.get("last_verdict"))
            detail = f"裁决：{verdict}" if verdict else "运行异常终止，没有更具体的失败说明。"
        subject = f"【失败】{_failure_title(detail)}"
        what = "这次运行异常终止，没有正常做完。"
        why = detail
        nxt = "先看上面的原因。不要原样重开；原因还在时，再启动会再失败。"
    plain, html_body = _render_mail(subject, what, why, nxt, where)
    if not user or not password or not notify_to:
        raise RuntimeError("SMTP 账号或口令为空, 无法发信")
    domain = user.split("@", 1)[1] if "@" in user else "163.com"
    message = EmailMessage()
    message["Message-ID"] = make_msgid(domain=domain)
    message["Date"] = formatdate(localtime=True)
    message["From"] = formataddr(("Sciver", user))
    message["To"] = notify_to
    message["Reply-To"] = user
    _attach_mail(message, subject, plain, html_body)
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as smtp:
            smtp.login(user, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=60) as smtp:
            smtp.starttls(context=context)
            smtp.login(user, password)
            smtp.send_message(message)


def notify_cycle_terminal_once(
    cycle_dir: Path, *, event: str, state: dict[str, Any], payload: dict[str, Any],
) -> bool:
    """Emit a durable once-per-incident terminal alert after SDK has stopped.

    Collector and Supervisor share this entrypoint so a late supervisor
    projection cannot send a second alert about the same stop.
    """
    if not should_send_terminal_email(event, payload) or not email_terminal_events_enabled(cycle_dir):
        return False
    from .incident_registry import register_cycle_incident, deliver_incident_notification

    status = str(event or "").strip().lower()
    is_attention = status == "run.needs_attention"
    attention = state.get("attention") if isinstance(state.get("attention"), dict) else {}
    phase = str(payload.get("stage") or state.get("phase") or "")
    if is_attention:
        code = str(payload.get("attention_code") or attention.get("code")
                   or state.get("closure_reason") or "needs_attention")
        reason = str(payload.get("reason") or attention.get("reason")
                     or state.get("human_attention") or "NEEDS_ATTENTION")
        severity, kind = "attention", "needs_attention"
    else:
        code = "run_failed" if status == "run.failed" else "provider_quota_exhausted"
        reason = str(payload.get("error") or payload.get("reason")
                     or state.get("human_attention") or status)
        severity, kind = ("error", "system_failure") if status == "run.failed" else ("attention", "quota_pause")
    # Use one stable identity per Cycle and stop reason regardless of which
    # component projected FAILED or which stage was present in the snapshot.
    # This prevents Collector vs Supervisor status skew from sending duplicates.
    stable = (f"{phase}:{code}:verified_dual_account_wait" if kind == "quota_pause"
              else f"{phase}:{code}")
    incident, _ = register_cycle_incident(
        Path(cycle_dir), severity=severity, kind=kind,
        source_event="sdk.terminal_notification",
        summary=reason, code=code, discriminator=stable,
        fields={"phase": phase, "notification_channel": "email"},
    )
    return deliver_incident_notification(
        Path(cycle_dir), incident=incident,
        send=lambda: send_terminal_email(event, state, payload),
    )

def send_completion_email(
    cycle_id: str,
    goal_id: str,
    cycle_dir: str,
    *,
    status: str = "OVER",
) -> None:
    """Send an explicit Cycle-end notice without changing automatic failure-email policy."""
    detail = f"goal={goal_id}; terminal_status={status}"
    send_restart_email(cycle_id, detail, cycle_dir, status_override=status)


def send_restart_email(
    cycle_id: str,
    error: str,
    cycle_dir: str,
    *,
    failed: bool = False,
    status_override: str | None = None,
) -> None:
    """Send a repair/restart result, or an explicit operator-requested terminal notice."""
    detail = _clean(error)
    where = _clean(cycle_dir)
    if status_override:
        subject = "【结束】本次运行已结束"
        what = "这次运行已经结束。"
        why = ""
        nxt = "不用处理停机。"
    elif failed:
        subject = "【未接上】自动修复没有恢复运行"
        what = "自动修复没有把运行接回去。"
        why = detail
        nxt = "先看原因，再决定要不要重开。不要原样再修一次。"
    else:
        subject = "【已接力】自动修复已重新接上"
        what = "自动修复已经把运行接回去。"
        why = detail
        nxt = "不用额外操作。若再次停下，会另发一封。"
    plain, html_body = _render_mail(subject, what, why, nxt, where)
    # Restart notification must remain available when auto_submit's optional
    # HPC stack (for example Paramiko) is not installed in the TheoryMaster
    # runtime.  Keep it on the same dependency-free SMTP path as terminal mail.
    notify_to = _notification_setting("THEORYMASTER_NOTIFY_TO")
    user = _notification_setting("AUTO_SUBMIT_SMTP_USER")
    password = _notification_setting("AUTO_SUBMIT_SMTP_PASSWORD")
    host = (os.environ.get("AUTO_SUBMIT_SMTP_HOST") or "smtp.163.com").strip()
    port = int((os.environ.get("AUTO_SUBMIT_SMTP_PORT") or "465").strip())
    if not user or not password or not notify_to:
        raise RuntimeError("SMTP 账号或口令为空, 无法发信")
    domain = user.split("@", 1)[1] if "@" in user else "163.com"
    message = EmailMessage()
    message["Message-ID"] = make_msgid(domain=domain)
    message["Date"] = formatdate(localtime=True)
    message["From"] = formataddr(("Sciver", user))
    message["To"] = notify_to
    message["Reply-To"] = user
    _attach_mail(message, subject, plain, html_body)
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as smtp:
            smtp.login(user, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=60) as smtp:
            smtp.starttls(context=context)
            smtp.login(user, password)
            smtp.send_message(message)
