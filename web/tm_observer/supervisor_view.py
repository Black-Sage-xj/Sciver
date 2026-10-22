"""Read-only Supervisor takeover projection: switch state, home banner and session.

Observer never imports TheoryMaster. The switch rule mirrors
``theory_master.supervisor_intervention.supervisor_enabled``: the new task key
wins; otherwise any legacy explicit OFF keeps the task closed.
"""
from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .run_monitor import CYCLE_NAME, redact
from .supervisor_incident_ui import CHAT_MODULE, DIALOGUE_SCRIPT, dialogue_html

KEY = "THEORY_MASTER_SUPERVISOR"
LEGACY_ATTENTION_KEY = "THEORY_MASTER_ATTENTION_TAKEOVER"
_TRUE = {"1", "true", "yes", "on", "enabled"}


def _task_env(root: Path) -> dict[str, str]:
    path = root / ".theory-master" / "env"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64_000:
            return {}
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if sep:
            values[name.strip()] = value.strip().strip("'\"")
    return values


def _legacy_error_flag(root: Path) -> bool | None:
    path = root / ".theory-master" / "web-operator-preferences.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_000:
            return None
        flags = json.loads(path.read_text(encoding="utf-8")).get("flags") or {}
    except (OSError, ValueError, UnicodeError, AttributeError):
        return None
    value = flags.get("error_repair") if isinstance(flags, dict) else None
    return value if type(value) is bool else None


def supervisor_enabled_for(run: dict[str, Any], state: dict[str, Any] | None = None) -> bool:
    root = Path(str(run.get("research_root") or ""))
    env = _task_env(root) if str(root) else {}
    if KEY in env:
        return env[KEY].strip().lower() in _TRUE
    legacy_attention = env.get(LEGACY_ATTENTION_KEY)
    if legacy_attention is not None and legacy_attention.strip().lower() not in _TRUE:
        return False
    if _legacy_error_flag(root) is False:
        return False
    for key in ("supervisor_enabled", "attention_takeover_enabled"):
        if state and key in state:
            return bool(state[key])
    return True


MAX_SESSION_BYTES = 8_000_000
MAX_ROWS = 4000
MAX_CONTENT = 120_000
PREVIEW = 1200
STOP_STATUSES = {"NEEDS_ATTENTION", "FAILED", "FAILED_RECOVERY"}
LIVE_STATUSES = {"RUNNING", "ACTIVE", "STARTING", "RECOVERING", "LIVE"}
PROFILE_LABEL = {"runtime": "Runtime · 运行故障", "attention": "Attention · NEEDS_ATTENTION"}
PHASE_LABEL = {"active": "Supervisor 接管中", "hold": "Supervisor 已停下",
               "returned": "Supervisor 已交回", "stopped": "已停下 · 未接管"}
LEGACY_RECEIPT = re.compile(r"sdk_internal_review_[0-9]{2,4}(?:_[0-9a-f]{32})?\.json\Z")
SESSION_ID = re.compile(r"SUP-[0-9a-f]{16}\Z")


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _clean(value: Any, limit: int = MAX_CONTENT) -> tuple[str, bool]:
    text = redact(str(value if value is not None else ""))
    return text[:limit], len(text) > limit


SUPERVISOR_STYLE = """
.tm-supervisor-pane:empty { display:none; }
.tm-supervisor-pane { margin:0 0 12px; }
.tm-budget-denial-banner {padding:12px 16px;border:1px solid #947334;border-radius:10px;background:#2d2719;color:#f8e0ad;display:flex;flex-direction:column;gap:5px;font-size:12px;}\n.tm-budget-denial-banner strong {font-size:14px;}\n.tm-budget-denial-banner a {color:#facc75;text-decoration:underline;}\n.tm-budget-denial-chip {background:#4a3414;color:#fcd9a1;border-color:#92400e;}\n.tm-quota-wait-banner {padding:12px 16px;border:1px solid #947334;border-radius:10px;background:#29261b;color:#f5deb0;display:flex;flex-direction:column;gap:5px;font-size:12px;}
.tm-quota-wait-banner strong {font-size:14px;}
.tm-quota-wait-resets {font-weight:550;}
.tm-quota-wait-banner small {opacity:.76;}
.tm-supervisor-banner { display:flex; align-items:center; gap:14px; padding:12px 16px; border-radius:10px;
  border:1px solid #5b4a2a; background:#2a2216; color:#f5e6c8; text-decoration:none; }
.tm-supervisor-banner:hover, .tm-supervisor-banner:focus-visible { filter:brightness(1.12); outline:none; }
.tm-supervisor-banner.tm-supervisor-active { border-color:#b45309; background:#3a2410; animation:tm-supervisor-pulse 2.4s ease-in-out infinite; }
.tm-supervisor-banner.tm-supervisor-hold, .tm-supervisor-banner.tm-supervisor-stopped { border-color:#9f1239; background:#2f1520; color:#fde2e8; }
.tm-supervisor-banner.tm-supervisor-returned { border-color:#2f6f4f; background:#14261d; color:#d7f5e5; }
@keyframes tm-supervisor-pulse { 0%,100% { box-shadow:0 0 0 0 rgba(245,158,11,.0); } 50% { box-shadow:0 0 0 4px rgba(245,158,11,.22); } }
.tm-supervisor-pill, .tm-supervisor-chip { display:inline-flex; align-items:center; white-space:nowrap; padding:3px 9px; border-radius:999px;
  font-size:11px; font-weight:650; background:#4a3414; color:#fcd9a1; border:1px solid #92400e; }
.tm-supervisor-hold .tm-supervisor-pill, .tm-supervisor-stopped .tm-supervisor-pill, .tm-supervisor-chip.tm-supervisor-hold, .tm-supervisor-chip.tm-supervisor-stopped
  { background:#4c1d2b; color:#fecdd3; border-color:#9f1239; }
.tm-supervisor-returned .tm-supervisor-pill, .tm-supervisor-chip.tm-supervisor-returned { background:#173a2a; color:#bbf7d0; border-color:#2f6f4f; }
.tm-supervisor-chip.tm-supervisor-unfinished { background:#2b2b31; color:#d4d4d8; border-color:#52525b; }
.tm-supervisor-chip { margin-left:6px; font-size:10px; padding:2px 7px; }
.tm-supervisor-body { display:flex; flex-direction:column; gap:2px; min-width:0; flex:1; }
.tm-supervisor-body strong { font-size:13px; }
.tm-supervisor-body span { font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tm-supervisor-body small { font-size:11px; opacity:.75; }
.tm-supervisor-open { font-size:12px; white-space:nowrap; opacity:.9; }
.tm-supervisor-cycles { display:flex; flex-wrap:nowrap; gap:6px; margin:0 0 8px; overflow-x:auto; }
.tm-supervisor-cycles a { padding:4px 10px; border:1px solid #3f3f46; border-radius:7px; color:#d4d4d8; text-decoration:none; font-size:11px; flex:none; }
.tm-supervisor-cycles a[aria-current=page] { background:#334f76; border-color:#567dac; color:#fff; }
.tm-supervisor-session { margin:14px 0; border:1px solid var(--tm-border,#2f2f36); border-radius:12px; overflow:hidden; background:#18181c; }
.tm-supervisor-session-head { display:flex; flex-wrap:wrap; align-items:center; gap:8px 12px; padding:11px 15px; font-size:12px; color:#d4d4d8; }
.tm-supervisor-session-head .tm-supervisor-chip { margin-left:0; }
.tm-supervisor-session-head time { margin-left:auto; color:#a1a1aa; font-size:11px; }
.tm-supervisor-profile { font-weight:650; color:#e4e4e7; }
.tm-supervisor-empty { color:#a1a1aa; font-size:13px; padding:18px 4px; }
.tm-incident-bar { display:flex; flex-wrap:wrap; align-items:center; gap:8px 10px; padding:8px 12px; font-size:12px; color:#d4d4d8; border-bottom:1px solid #2f2f36; }
.tm-incident-bar a { color:#d4d4d8; text-decoration:none; }
.tm-incident-bar strong { color:#f4f4f5; }
.tm-incident-bar time { margin-left:auto; color:#a1a1aa; font-size:11px; }
body:has(.tm-supervisor-page) .tm-page-head { display:none; }
body:has(.tm-supervisor-page) .tm-main { height:100vh; display:flex; flex-direction:column; overflow:hidden; }
body:has(.tm-supervisor-page) .tm-content { flex:1; min-height:0; max-width:none; padding:8px 14px 10px; display:flex; flex-direction:column; overflow:hidden; }
.tm-supervisor-page { flex:1; min-height:0; display:flex; flex-direction:column; overflow:hidden; }
.tm-supervisor-page .tm-supervisor-session { flex:1; min-height:0; margin:0; display:flex; flex-direction:column; overflow:hidden; }
.tm-incident-chat {display:flex; flex-direction:column; flex:1; min-height:0; height:auto; margin:0; border:0; border-radius:0; overflow:hidden; background:#18181c; color:#e4e4e7;
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
@media (max-width:700px) { .tm-supervisor-banner { flex-wrap:wrap; } .tm-supervisor-open { width:100%; } }
"""


def _cycle_dir(run: dict[str, Any], cycle: str = "") -> Path | None:
    try:
        research = Path(str(run.get("research_root") or "")).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    name = cycle or str(run.get("cycle") or "")
    if not name:
        try:
            name = Path(str(run.get("cycle_dir") or "")).name
        except (TypeError, ValueError):
            return None
    if not CYCLE_NAME.fullmatch(name):
        return None
    try:
        folder = (research / name).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return folder if folder.parent == research and folder.is_dir() else None


def _read_small_json(path: Path, limit: int = 200_000) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_status(folder: Path | None) -> dict[str, Any] | None:
    if folder is None:
        return None
    value = _read_small_json(folder / "supervisor" / "status.json", 64_000)
    if not value or value.get("schema") != "theory-master/supervisor-status/v1":
        return None
    if value.get("status") not in {"active", "hold", "returned"}:
        return None
    return value


def home_status(run: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Supervisor phase for the Run home screen; None when nothing needs showing."""
    state = state or {}
    if _quota_pause(run, state) or _verified_budget_denial(run):
        return None  # Resource waits/denials are not Runtime fault banners.
    status_key = str(state.get("status") or run.get("raw_status") or run.get("state") or "").upper()
    enabled = supervisor_enabled_for(run, state)
    current = _read_status(_cycle_dir(run))
    # The immutable Supervisor status may be from an older, already superseded
    # incident. The current Cycle attention is authoritative for whether that
    # incident still describes the present stop. Keep the old session in history.
    attention = state.get("attention")
    current_code = str(attention.get("code") or "") if isinstance(attention, dict) else ""
    if (current is not None and current.get("status") == "hold" and current_code
            and str(current.get("code") or "") != current_code):
        return None
    if current is not None:
        phase = current["status"]
        # Returned sessions stay in the header bell. The home banner is only
        # for a takeover that is still in progress or still needs a person.
        if phase == "returned":
            return None
        if (phase == "hold" and status_key in LIVE_STATUSES) or status_key in {"OVER", "COMPLETED", "CANCELLED"}:
            current = None
        else:
            summary, _ = _clean(current.get("summary"), 400)
            return {"phase": phase, "enabled": enabled, "profile": current.get("profile"),
                    "code": _clean(current.get("code"), 120)[0], "summary": summary,
                    "session_id": current.get("session_id"), "at": current.get("at"),
                    "action": current.get("action")}
    if status_key in STOP_STATUSES:
        attention = state.get("attention") if isinstance(state.get("attention"), dict) else {}
        code = attention.get("code") or ("run_failed" if status_key.startswith("FAILED") else "needs_attention")
        reason = attention.get("reason") or state.get("human_attention") or ""
        return {"phase": "stopped", "enabled": enabled,
                "profile": "runtime" if status_key.startswith("FAILED") else "attention",
                "code": _clean(code, 120)[0], "summary": _clean(reason, 400)[0],
                "session_id": None, "at": None, "action": None}
    return None


def _banner_target(run: dict[str, Any], supervisor_id: str) -> str:
    """Bind the current SUP event to its own SIS chat, never the default chat."""
    ident = str(run.get("observer_id") or "")
    base = f"/run/{ident}/supervisor/"
    folder = _cycle_dir(run)
    fallback = base + (f"?cycle={folder.name}" if folder else "")
    if folder is None or not SESSION_ID.fullmatch(supervisor_id):
        return fallback
    fallback += f"#{supervisor_id}"
    link = _read_small_json(folder / "supervisor" / "incident_link.json")
    if (not link or link.get("schema") != "theory-master/supervisor-incident-link/v1"
            or str(link.get("cycle_id") or "") != folder.name):
        return fallback
    sis_id = str(link.get("session_id") or "")
    if not re.fullmatch(r"SIS-[0-9a-f]{16}", sis_id):
        return fallback
    record = _read_small_json(folder.parent / ".theory-master" / "supervisor_incidents"
                              / "sessions" / f"{sis_id}.json")
    audit = record.get("audit") if isinstance(record, dict) else None
    if (not record or record.get("schema") != "theory-master/supervisor-incident-session/v1"
            or record.get("id") != sis_id or str(record.get("cycle_id") or "") != folder.name
            or not isinstance(audit, dict) or audit.get("supervisor_session_id") != supervisor_id):
        return fallback
    return base + f"?session={sis_id}"


def _quota_pause(run: dict[str, Any], state: dict[str, Any] | None) -> bool:
    folder = _cycle_dir(run)
    record = _read_small_json(folder / "research_state.json") if folder else None
    value = record if isinstance(record, dict) else (state or {})
    recovery = value.get("recovery")
    return (str(value.get("status") or "").lower() == "paused"
            and value.get("phase") == "provider_quota_exhausted"
            and value.get("resumable") is True
            and isinstance(recovery, dict)
            and recovery.get("code") == "provider_quota_exhausted")


def quota_wait_banner(run: dict[str, Any], state: dict[str, Any] | None = None,
                      quota_snapshot: dict[str, Any] | None = None,
                      worker_account: str | None = None) -> str:
    """Show real reset timestamps only when recent source records prove them."""
    if not _quota_pause(run, state):
        return ""
    now_ts = time.time()
    samples = quota_snapshot.get("accounts") if isinstance(quota_snapshot, dict) else None
    accounts = samples if isinstance(samples, list) else []
    facts = []
    for number in ("1", "2"):
        row = next((x for x in accounts if isinstance(x, dict)
                    and x.get("account_home") == "account-" + number), None)
        stamp = row.get("queried_at") if isinstance(row, dict) else None
        fresh = (row is not None and row.get("ok") is True
                 and type(stamp) in (int, float) and now_ts - 900 <= stamp <= now_ts + 60)
        windows = (row.get("primary"), row.get("secondary")) if fresh else ()
        resets = [w["resets_at"] for w in windows if isinstance(w, dict)
                  and type(w.get("remaining_percent")) in (int, float)
                  and w["remaining_percent"] <= 0
                  and type(w.get("resets_at")) in (int, float)
                  and now_ts < w["resets_at"] < now_ts + 8*86400]
        if resets:
            predicted = datetime.fromtimestamp(max(resets), timezone(timedelta(hours=8)))
            label = "预计 " + predicted.strftime("%m月%d日 %H:%M") + "（UTC+8）"
        else:
            label = "重置时间待服务端确认"
        facts.append("账号 " + number + "：" + html.escape(label))
    pinned = worker_account if worker_account in {"1", "2"} else "未知"
    return ('<section class="tm-supervisor-pane" data-tui-pane="supervisor">'
            '<div class="tm-quota-wait-banner" role="status" data-quota-wait="true">'
            '<strong>额度等待中</strong><span>当前账号 ' + html.escape(pinned)
            + '，已保留当前 Cycle；额度自调开启时，恢复可用额度后自动续跑。</span>'
            '<div class="tm-quota-wait-resets">' + " · ".join(facts) + '</div>'
            '<small>额度不足属于资源等待；独立技术故障仍由 Supervisor 处理。</small>'
            '</div></section>')


def _verified_budget_denial(run: dict[str, Any]) -> bool:
    """Legacy or 3+3 denial is a resource decision, not a Runtime fault."""
    folder = _cycle_dir(run)
    state = _read_small_json(folder / "research_state.json") if folder else None
    # Rich run_state snapshots can exceed the generic 200 KB incident cap.
    # Keep this dedicated read bounded, but do not mistake an oversized valid
    # legacy Budget Reviewer decision for an absent record.
    current = _read_small_json(folder / "run_state.json", 1_000_000) if folder else None
    return bool(
        isinstance(state, dict) and isinstance(current, dict)
        and state.get("status") == "needs_attention"
        and current.get("status") == "NEEDS_ATTENTION"
        and state.get("phase") == "investigator:budget_denied"
        and current.get("phase") == state.get("phase")
        and state.get("execution_active") is False
        and (state.get("investigator_budget") or {}).get("rejected") is True
        and (state.get("attention") or {}).get("code") == "investigator_budget_review_rejected"
        and (current.get("attention") or {}).get("code") == "investigator_budget_review_rejected"
    )


def _budget_denial_banner(run: dict[str, Any]) -> str:
    url = _banner_target(run, "")
    return (
        '<section class="tm-supervisor-pane" data-tui-pane="supervisor">'
        '<div class="tm-budget-denial-banner" role="status" data-budget-denial="true">'
        '<strong>预算审核拒绝 · 待保全研究成果</strong>'
        '<span>Budget Reviewer 已拒绝追加科研预算；这不是 Codex 额度故障。'
        '系统在安全检查点按规则尝试一次成果保全，成功后释放前沿并继续调度。</span>'
        '<a href="' + _e(url) + '">查看原始审核决定 →</a>'
        '</div></section>'
    )


def _engineering_recovery_banner(run: dict[str, Any]) -> str:
    """Ground current Worker recovery state in bounded durable receipts."""
    folder = _cycle_dir(run)
    if folder is None:
        return ""
    scientific = _read_small_json(folder / "research_state.json")
    if not isinstance(scientific, dict):
        return ""
    wid = str(scientific.get("worker_id") or "")
    if not re.fullmatch(r"worker-[1-9][0-9]*", wid):
        return ""
    workers = folder.parent / ".theory-master" / "workers" / wid
    current_worker = _read_small_json(workers / "runtime.json", 64_000)
    if (isinstance(current_worker, dict)
            and str(current_worker.get("current_cycle") or "") == folder.name
            and str(current_worker.get("status") or "").upper() in LIVE_STATUSES):
        return ""  # a recovering Worker takes precedence over old frozen fault projections
    phase = str(scientific.get("phase") or "")
    status = str(scientific.get("status") or "").lower()
    label, detail = "", ""
    if status == "failed":
        receipt = _read_small_json(workers / "supervisor_dispatch.json")
        if (receipt and receipt.get("cycle") == folder.name
                and receipt.get("status") == "deferred_peers_active"):
            label = "Supervisor 工程恢复等待安全窗口"
            detail = ("另一科研 Worker 正在运行。已保留当前 Cycle 和科研 Claim；"
                      "仅允许经核验的原线程容量恢复，禁止运行中热替换共享代码。"
                      "无需重复手动 Resume。")
    elif status == "needs_attention" and phase == "investigator:budget_review":
        attention = scientific.get("attention") or {}
        if isinstance(attention, dict) and attention.get("code") == "investigator_budget_review_failed":
            label = "Budget Reviewer 技术故障 · 不等于预算拒绝"
            budget = scientific.get("investigator_budget") or {}
            idx = budget.get("review_index") if isinstance(budget, dict) else None
            try:
                idx = int(idx)
            except (ValueError, TypeError, OverflowError):
                idx = None
            attempt = (_read_small_json(workers / f"budget_reviewer_transport_recovery_{folder.name}_{idx}.json")
                       if idx is not None else None)
            detail = ("仅在原账号重新可用、且确认属于传输错误后，"
                      "允许一次受控恢复；反复出现无效 JSON 或账本错误时"
                      "必须先修复协议。不会自动批准科研预算。")
            if isinstance(attempt, dict) and attempt.get("status") == "resume_blocked":
                detail = "恢复尝试被原 Cycle 安全检查阻止；未增加预算，需查看工程事件。"
    elif status == "over":
        return ""
    if not label:
        orphan = _read_small_json(workers / f"preservation_orphaned_{folder.name}.json")
        if orphan and orphan.get("status") == "manual_reconciliation_required":
            label = "科研成果保全进程失联"
            detail = ("系统没有获得可验证的完成回执；原 Cycle 和科研 Claim 保留，"
                      "不会擅自重做模型调用。可查看重要事件核验。")
    if not label:
        return ""
    link = _banner_target(run, "")
    return ('<section class="tm-supervisor-pane" data-tui-pane="supervisor">'
            '<div class="tm-quota-wait-banner" role="status" data-recovery-gate="true">'
            f'<strong>{_e(label)}</strong><span>{_e(detail)}</span>'
            f'<small><a href="{_e(link)}">查看对应 Cycle 的 Supervisor 记录 →</a></small>'
            '</div></section>')


def banner_html(run: dict[str, Any], state: dict[str, Any] | None = None) -> str:
    """Always-present polled pane; empty when there is nothing to surface."""
    if _quota_pause(run, state):
        return quota_wait_banner(run, state)
    if _verified_budget_denial(run):
        return _budget_denial_banner(run)
    recovery = _engineering_recovery_banner(run)
    if recovery:
        return recovery
    info = home_status(run, state)
    if info is None:
        return '<section class="tm-supervisor-pane" data-tui-pane="supervisor"></section>'
    href = _banner_target(run, str(info.get("session_id") or ""))
    phase = info["phase"]
    note = ("Supervisor 已关闭，需人工处理" if phase == "stopped" and not info["enabled"]
            else "没有接管会话，需人工处理" if phase == "stopped"
            else "模型/SDK 正在处理，科学流程暂停在安全边界" if phase == "active"
            else "已交回科学流程" if phase == "returned"
            else "Supervisor 未能自动恢复，需人工处理")
    profile = PROFILE_LABEL.get(str(info.get("profile") or ""), "")
    return (
        '<section class="tm-supervisor-pane" data-tui-pane="supervisor">'
        f'<a class="tm-supervisor-banner tm-supervisor-{_e(phase)}" href="{_e(href)}" '
        f'data-supervisor-phase="{_e(phase)}">'
        f'<span class="tm-supervisor-pill">{_e(PHASE_LABEL[phase])}</span>'
        f'<span class="tm-supervisor-body"><strong>{_e(profile)}'
        + (f' · {_e(info["code"])}' if info.get("code") else '') + '</strong>'
        f'<span>{_e(info.get("summary") or note)}</span>'
        f'<small>{_e(note)}</small></span>'
        '<span class="tm-supervisor-open">查看 Supervisor 对话 →</span></a>'
        '</section>'
    )


def index_chip(run: dict[str, Any]) -> str:
    if _verified_budget_denial(run):
        return '<span class="tm-supervisor-chip tm-budget-denial-chip">预算审核拒绝 · 待保全</span>'
    info = home_status(run, {"status": run.get("raw_status") or run.get("state")})
    if info is None:
        return ""
    return (f'<span class="tm-supervisor-chip tm-supervisor-{_e(info["phase"])}" '
            f'data-supervisor-phase="{_e(info["phase"])}">{_e(PHASE_LABEL[info["phase"]])}</span>')


def _session_rows(folder: Path) -> tuple[list[dict[str, Any]], list[str]]:
    path = folder / "supervisor" / "session.jsonl"
    warnings: list[str] = []
    if not path.exists():
        return [], warnings
    if path.is_symlink() or not path.is_file():
        return [], ["session.jsonl rejected"]
    rows: list[dict[str, Any]] = []
    read = 0
    try:
        with path.open("rb") as handle:
            for line in handle:
                read += len(line)
                if read > MAX_SESSION_BYTES or len(rows) >= MAX_ROWS:
                    warnings.append("Supervisor 会话超过读取上限，只显示前段。")
                    break
                try:
                    row = json.loads(line.decode("utf-8"))
                except (UnicodeError, ValueError):
                    continue
                if isinstance(row, dict) and row.get("schema") == "theory-master/supervisor-session/v1":
                    rows.append(row)
    except OSError:
        warnings.append("session.jsonl unreadable")
    return rows, warnings


def _message(row: dict[str, Any]) -> dict[str, Any]:
    content, cut = _clean(row.get("content"))
    return {"seq": row.get("seq"), "at": _clean(row.get("at"), 64)[0],
            "kind": _clean(row.get("kind"), 32)[0], "content": content, "truncated": cut,
            "action": _clean(row.get("action"), 48)[0] if row.get("action") else None,
            "status": _clean(row.get("status"), 32)[0] if row.get("status") else None,
            "role": _clean(row.get("role"), 48)[0] if row.get("role") else None,
            "thread_id": _clean(row.get("thread_id"), 128)[0] if row.get("thread_id") else None}


def _legacy_sessions(folder: Path) -> list[dict[str, Any]]:
    directory = folder / "attention"
    try:
        if directory.is_symlink() or not directory.is_dir():
            return []
        names = sorted(p.name for p in directory.iterdir() if LEGACY_RECEIPT.fullmatch(p.name))[:100]
    except OSError:
        return []
    found = []
    for name in names:
        record = _read_small_json(directory / name)
        if (not record or record.get("schema") != "theory-master/internal-attention-review/v1"
                or record.get("supervisor_session")):
            continue
        exchange = record.get("exchange") if isinstance(record.get("exchange"), dict) else {}
        at = record.get("at")
        code = record.get("attention_code") or "needs_attention"
        rows = [{"kind": "opened", "content": f"{code}（历史 Attention 审查回放）", "at": at}]
        if exchange.get("prompt"):
            rows.append({"kind": "prompt", "content": exchange.get("prompt"), "at": at,
                         "role": "attention_reviewer"})
        if exchange.get("model_output"):
            rows.append({"kind": "model_output", "content": exchange.get("model_output"), "at": at,
                         "thread_id": exchange.get("thread_id")})
        if not exchange:
            rows.append({"kind": "system", "at": at,
                         "content": "该次历史审查没有保存 Prompt 与模型原文，只保留决策。"})
        retry = record.get("action") == "retry_qualification"
        reason = str(record.get("reason") or "")
        if retry and record.get("guidance"):
            reason += "\n\nGuidance → Research Lead:\n" + str(record.get("guidance"))
        rows.append({"kind": "decision", "content": reason, "at": at,
                     "action": record.get("action"), "status": "returned" if retry else "hold"})
        found.append({"session_id": name, "profile": "attention", "legacy": True,
                      "code": _clean(code, 120)[0], "incident_id": record.get("incident_id"),
                      "model": record.get("model"), "status": "returned" if retry else "hold",
                      "opened_at": _clean(at, 64)[0], "messages": [_message(r) for r in rows]})
    return found


def _has_data(folder: Path) -> bool:
    if ((folder / "supervisor" / "session.jsonl").is_file()
            or (folder / "supervisor" / "recovery.json").is_file()):
        return True
    directory = folder / "attention"
    try:
        return directory.is_dir() and any(LEGACY_RECEIPT.fullmatch(p.name) for p in directory.iterdir())
    except OSError:
        return False


def _cycle_names(run: dict[str, Any]) -> list[str]:
    try:
        research = Path(str(run.get("research_root") or "")).resolve(strict=True)
        names = [p.name for p in research.iterdir()
                 if p.is_dir() and not p.is_symlink() and CYCLE_NAME.fullmatch(p.name)]
    except (OSError, RuntimeError):
        return []
    return sorted(names, key=lambda v: (int(v.removeprefix("cycle-")), v))


def sessions(run: dict[str, Any], cycle: str = "") -> dict[str, Any]:
    names = _cycle_names(run)[-60:]
    with_data = [name for name in names if (folder := _cycle_dir(run, name)) is not None and _has_data(folder)]
    current = str(run.get("cycle") or "")
    selected = cycle or (current if current in with_data else (with_data[-1] if with_data else current))
    folder = _cycle_dir(run, selected) if selected else None
    if folder is None:
        if cycle:
            raise ValueError("unknown cycle")
        return {"cycle": "", "cycles": with_data, "current_cycle": current, "sessions": [], "warnings": []}
    rows, warnings = _session_rows(folder)
    status = _read_status(folder) or {}
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        sid = str(row.get("session_id") or "")
        if not SESSION_ID.fullmatch(sid):
            continue
        entry = grouped.setdefault(sid, {
            "session_id": sid, "profile": _clean(row.get("profile"), 32)[0], "legacy": False,
            "code": _clean(row.get("code"), 120)[0], "incident_id": _clean(row.get("incident_id"), 64)[0],
            "model": None, "status": "unfinished", "opened_at": _clean(row.get("at"), 64)[0], "messages": [],
        })
        entry["messages"].append(_message(row))
        if row.get("kind") == "decision" and row.get("status") in {"hold", "returned"}:
            entry["status"] = row["status"]
    if status.get("session_id") in grouped:
        entry = grouped[status["session_id"]]
        entry["model"] = _clean(status.get("model"), 80)[0] or None
        if entry["status"] == "unfinished" and status.get("status") == "active":
            entry["status"] = "active"
        elif status.get("status") == "returned":
            entry["status"] = "returned"
    found = list(grouped.values()) + _legacy_sessions(folder)
    found.sort(key=lambda s: str(s.get("opened_at") or ""), reverse=True)
    return {"cycle": folder.name, "cycles": with_data, "current_cycle": current,
            "sessions": found, "warnings": warnings}


_ROLE_BY_KIND = {
    "opened": "supervisor", "model_output": "supervisor", "decision": "supervisor",
    "system": "system", "verification": "system", "human": "human",
    "tool": "tool", "repair_agent": "tool", "test": "tool", "repair_promoted": "tool",
}
_ROLE_LABEL = {"supervisor": "Supervisor", "human": "Human", "tool": "Tool", "system": "System"}


def _incident_record(folder: Path, sup_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Linked SIS dialogue for this SUP section. Missing link means project the SUP rows."""
    link = _read_small_json(folder / "supervisor" / "incident_link.json")
    if not link or link.get("schema") != "theory-master/supervisor-incident-link/v1":
        return None
    session_id = str(link.get("session_id") or "")
    if not session_id.startswith("SIS-") or "/" in session_id:
        return None
    root = folder.parent
    path = root / ".theory-master" / "supervisor_incidents" / "sessions" / f"{session_id}.json"
    record = _read_small_json(path)
    if not record or record.get("schema") != "theory-master/supervisor-incident-session/v1":
        return None
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    bound = str(audit.get("supervisor_session_id") or "")
    if bound and bound != sup_id:
        return None
    messages_path = path.with_name(f"{session_id}.messages.jsonl")
    rows: list[dict[str, Any]] = []
    try:
        if messages_path.is_symlink() or not messages_path.is_file():
            return record, rows
        read = 0
        with messages_path.open("rb") as handle:
            for line in handle:
                read += len(line)
                if read > MAX_SESSION_BYTES or len(rows) >= MAX_ROWS:
                    break
                try:
                    row = json.loads(line.decode("utf-8"))
                except (UnicodeError, ValueError):
                    continue
                if isinstance(row, dict) and row.get("role") in _ROLE_LABEL:
                    content, _cut = _clean(row.get("content"))
                    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                    rows.append({
                        "role": row["role"], "content": content,
                        "id": str(row.get("id") or ""), "metadata": metadata,
                    })
    except OSError:
        return record, rows
    return record, rows


def _projected_roles(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Map the old SUP transcript onto dialogue roles. Hide the internal prompt and evidence JSON."""
    rows: list[dict[str, str]] = []
    for message in messages:
        role = _ROLE_BY_KIND.get(str(message.get("kind") or ""))
        if role is None:
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if message.get("kind") == "model_output" and content[:1] in "{[":
            try:
                parsed = json.loads(content)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                parts = [str(parsed.get(key) or "").strip() for key in ("reason", "diagnosis", "repair_suggestion", "guidance")]
                content = "\n\n".join(part for part in parts if part) or content
        rows.append({"role": role, "content": content})
    return rows




_STATUS_TITLE = {"active": "接管中", "hold": "已停下", "returned": "已交回", "unfinished": "未完成"}


def _refresh_script(*, active: bool, waiting: bool) -> str:
    """Refresh live Supervisor sessions without destroying an in-progress draft."""
    if not active and not waiting:
        return ""
    delay = 3000 if waiting else 8000
    return (
        '<script>(()=>{'
        f'const delay={delay};'
        'const composers=()=>Array.from(document.querySelectorAll("loquix-chat-composer"));'
        'const hasDraft=()=>composers().some(c=>{const p=c.shadowRoot?.querySelector("loquix-prompt-input");return Boolean(p&&String(p.value||"").trim())});'
        'const editing=()=>{const a=document.activeElement;return Boolean((a&&a.matches&&a.matches("input,textarea,select,[contenteditable=true],loquix-chat-composer"))||composers().some(c=>Boolean(c.shadowRoot?.activeElement)))};'
        'function poll(){setTimeout(()=>{if(document.visibilityState!=="hidden"&&!document.querySelector("details[open]")&&!editing()&&!hasDraft())location.reload();poll()},delay)}poll();'
        '})();</script>'
    )


def page_body(run: dict[str, Any], cycle: str = "", state: dict[str, Any] | None = None) -> str:
    data = sessions(run, cycle)
    ident = _e(run.get("observer_id"))
    enabled = supervisor_enabled_for(run, state)
    cycle_links = "".join(
        f'<a href="/run/{ident}/supervisor/?cycle={_e(name)}"'
        + (' aria-current="page"' if name == data["cycle"] else '') + f'>Cycle {_e(name)}</a>'
        for name in data["cycles"][-24:]
    ) or '<span>暂无接管记录</span>'
    articles = []
    waiting = False
    live_status = str((state or {}).get("status") or run.get("raw_status")
                      or run.get("state") or "").upper() in LIVE_STATUSES
    for session in data["sessions"]:
        status = session["status"]
        historical_hold = status == "hold" and (
            data["cycle"] != data["current_cycle"] or live_status)
        status_label = "历史曾停下 · 当前已恢复运行" if historical_hold and live_status and data["cycle"] == data["current_cycle"] else (
            "历史曾停下" if historical_hold else _STATUS_TITLE.get(status, status))
        chips = (f'<span class="tm-supervisor-chip tm-supervisor-{_e(status)}">{_e(status_label)}</span>'
                 f'<span class="tm-supervisor-profile">{_e(PROFILE_LABEL.get(session["profile"], session["profile"]))}</span>'
                 f'<code>{_e(session["code"])}</code>'
                 + (f'<span>模型 {_e(session["model"])}</span>' if session.get("model") else '')
                 + ('<span>历史回放</span>' if session.get("legacy") else ''))
        folder = _cycle_dir(run, data["cycle"])
        linked = _incident_record(folder, session["session_id"]) if folder is not None else None
        if linked is not None:
            record, rows = linked
            trigger = record.get("trigger_event") or session.get("code")
            cycle_label = record.get("cycle_id") or data["cycle"]
            session_waiting = bool(rows and rows[-1]["role"] == "human" and record.get("status") == "active")
            waiting = waiting or session_waiting
            active = record.get("status") == "active"
            thread = dialogue_html(str(record["id"]), rows, active=active, waiting=session_waiting)
            if not active:
                thread += f'<p class="tm-incident-actions">会话已 {_e(record.get("status"))}，对话保留供审计。</p>'
        else:
            rows = _projected_roles(session["messages"])
            trigger = session.get("code")
            cycle_label = data["cycle"]
            thread = dialogue_html("", rows, active=False, waiting=False)
        trigger_bit = (f'<code>{_e(trigger)}</code>'
                       if str(trigger or "") and str(trigger) != str(session.get("code") or "") else "")
        articles.append(
            f'<section class="tm-supervisor-session tm-incident" id="{_e(session["session_id"])}" '
            f'aria-label="Supervisor Incident">'
            f'<div class="tm-incident-bar"><a href="/run/{ident}/">← 返回</a>'
            f'<strong>Cycle {_e(cycle_label)}</strong>{trigger_bit}{chips}'
            f'<time>{_e(session["opened_at"])}</time></div>'
            + thread + '</section>')
    empty = ('<p class="tm-supervisor-empty">该 Cycle 没有 Supervisor 接管记录。'
             + ('' if enabled else ' Supervisor 当前关闭。') + '</p>')
    recovery_panel = ''
    folder = _cycle_dir(run, data['cycle'])
    if folder is not None:
        from theory_master.supervisor_recovery import projection
        try:
            job = projection(folder)
        except (OSError, ValueError, TypeError):
            job = {'status': 'unreadable'}
        if job:
            current = data['cycle'] == data['current_cycle']
            label = '执行中' if enabled and current and job.get('executing') else (
                '历史恢复记录' if not current else '调度关闭' if not enabled else str(job.get('status') or '等待'))
            recovery_panel = ('<section class="tm-supervisor-recovery"><h3>恢复任务 · ' + _e(label) + '</h3>'
                + '<p>负责人：' + _e(job.get('recovery_owner')) + ' · 下一动作：' + _e(job.get('current_action'))
                + ' · 下次检查（Unix 秒）：' + _e(job.get('next_wakeup_epoch')) + '</p>'
                + '<p>等待条件：' + _e(job.get('blocking_dependency') or '无') + '</p>'
                + '<p>最后验证进展：' + _e(str(job.get('last_verified_progress') or '尚无')) + '</p></section>')
    warnings = "".join(f'<p class="tm-stage-warning">{_e(w)}</p>' for w in data["warnings"])
    refresh = _refresh_script(
        active=any(s["status"] == "active" for s in data["sessions"]), waiting=waiting,
    )
    incident_script = (CHAT_MODULE + DIALOGUE_SCRIPT) if any("tm-incident-form" in item for item in articles) else (
        CHAT_MODULE if any("loquix-message-item" in item for item in articles) else '')
    return (
        '<style>' + SUPERVISOR_STYLE + '</style>'
        '<section class="tm-run-workspace tm-supervisor-page" aria-label="Supervisor 接管对话">'
        + f'<nav class="tm-supervisor-cycles" aria-label="选择 Cycle">{cycle_links}</nav>'
        + warnings + recovery_panel + ("".join(articles) if articles else empty) + incident_script + refresh + '</section>'
    )
