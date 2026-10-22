"""Read-only per-Worker Web console projection.

Observer never imports or launches a research SDK. Only registered Worker
records may select a Cycle. Master legacy controls are never reused for a
Worker because they target the newest Cycle of the whole Task.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .collector import _ACTIVE_WORKER_STATES, _verified_active_worker

WORKER_ID = re.compile(r"worker-([1-9][0-9]*)\Z")
CYCLE_ID = re.compile(r"[1-9][0-9]*\Z")


def registered_workers(run: dict[str, Any]) -> list[dict[str, Any]]:
    root = Path(str(run.get("research_root") or "")).resolve()
    folder = root / ".theory-master" / "workers"
    if not folder.is_dir() or folder.is_symlink():
        return []
    rows = []
    for path in folder.glob("worker-*/runtime.json"):
        if not WORKER_ID.fullmatch(path.parent.name) or path.is_symlink() or path.parent.is_symlink():
            continue
        try:
            if path.stat().st_size > 65536:
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
        if (not isinstance(value, dict) or value.get("schema") != "theory-master/worker-runtime/v1"
                or value.get("worker_id") != path.parent.name or value.get("dismissed") is True):
            continue
        # A stale RUNNING runtime record must not keep a dead Worker green.
        # Preserve its raw lifecycle for diagnosis; the displayed status is
        # identical to Task List's PID+start-ticks+Cycle verification.
        raw_status = str(value.get("status") or "UNKNOWN").upper()
        effective = ("ORPHANED" if raw_status in _ACTIVE_WORKER_STATES
                     and not _verified_active_worker(root, value, path.parent.name)
                     else raw_status)
        value = {key: value.get(key) for key in ("worker_id", "target_h_id", "current_cycle", "account")}
        value["raw_status"] = raw_status
        value["status"] = effective
        rows.append(value)
    return sorted(rows, key=lambda row: int(row["worker_id"].split("-")[1]))


def worker_batch_cycle_progress(run: dict[str, Any], worker_id: str | None) -> tuple[int, int] | None:
    """Read the current scheduler batch counter; never confuse it with Cycle ID.

    Each Worker gets its own max_cycles_per_worker allocation. Per-Cycle
    run_state.cycle_index is only the invocation ordinal (often always 1).
    """
    if not worker_id or not WORKER_ID.fullmatch(worker_id) or not run.get("research_root"):
        return None
    try:
        root = Path(str(run["research_root"])).resolve(strict=True)
        directory = root / ".theory-master"
        snapshot = directory / "worker_scheduler.json"
        if (directory.is_symlink() or snapshot.is_symlink() or not snapshot.is_file()
                or snapshot.stat().st_size > 16384):
            return None
        state = json.loads(snapshot.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("quota_mode") != "per_worker":
            return None
        counts = state.get("cycles_started_by_worker")
        worker_ids = state.get("worker_ids")
        maximum = state.get("max_cycles_per_worker")
        if (not isinstance(counts, dict) or not isinstance(worker_ids, list)
                or worker_id not in worker_ids or type(maximum) is not int
                or not 1 <= maximum <= 1000000):
            return None
        count = counts.get(worker_id)
        if type(count) is not int or not 0 <= count <= maximum:
            return None
        return count, maximum
    except (OSError, ValueError, TypeError, UnicodeError):
        return None


def live_task_runner(run: dict[str, Any], workers: list[dict[str, Any]]) -> bool:
    """An independently verified Task Runner is not a released Worker process.

    Task List already verifies the PID/start-ticks; a live registered Worker
    retains priority in Worker mode. No guessed RUNNING from Cycle files.
    """
    return (str(run.get("state") or "").upper() == "LIVE"
            and run.get("pid_alive") is True
            and not any(str(w.get("status") or "").upper() in _ACTIVE_WORKER_STATES
                        for w in workers))


def select_worker(run: dict[str, Any], requested: str | None) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    from .worker_settings_ui import mode_snapshot
    if not mode_snapshot(run)["enabled"]:
        return run, [], None
    workers = registered_workers(run)
    if not workers:
        return run, [], None
    if not requested and live_task_runner(run, workers):
        # Managed tm resume can run after worker-1 was explicitly RELEASED.
        # Default to the current Task, leaving old Workers selectable as history.
        return run, workers, None
    chosen = next((w for w in workers if w["worker_id"] == requested), workers[0])
    selected = chosen["worker_id"]
    projected = dict(run)
    projected["worker_id"] = selected
    projected["raw_status"] = chosen.get("raw_status") or "UNKNOWN"
    projected["state"] = chosen.get("status") or "UNKNOWN"
    projected["active_agent"] = selected
    cycle_id = str(chosen.get("current_cycle") or "")
    root = Path(str(run["research_root"])).resolve()
    if CYCLE_ID.fullmatch(cycle_id) and (root / cycle_id / ".theory-master-cycle").is_file():
        projected["cycle_dir"] = str(root / cycle_id)
        projected["cycle"] = cycle_id
    else:
        projected.pop("cycle_dir", None)
        projected["cycle"] = ""
    return projected, workers, selected


def requested_worker_slots(run: dict[str, Any], worker_count: int) -> int:
    """Only explicit + requests create waiting cards, not old capacity limits."""
    path = Path(str(run.get("research_root") or "")).resolve() / ".theory-master" / "worker_mode.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            return worker_count
        mode = json.loads(path.read_text(encoding="utf-8"))
        requested = mode.get("requested_agents")
        if (mode.get("schema") == "theory-master/worker-mode/v1"
                and mode.get("enabled") is True
                and type(requested) is int and 1 <= requested <= 1024):
            return max(worker_count, requested)
    except (OSError, ValueError, TypeError):
        pass
    return worker_count


def tab_bar(run: dict[str, Any], workers: list[dict[str, Any]], selected: str | None) -> str:
    """Worker tabs with a distinct global add action and safe close controls."""
    ident = quote(str(run.get("observer_id") or ""), safe="")
    links = []
    plus = ('<button type="button" class="tm-worker-add" data-worker-add '
            'aria-label="新增 Agent" title="新增独立科研 Agent">'
            '<span aria-hidden="true">+</span> 新增 Agent</button>')
    def dismiss(worker_id: str, number: str, status: str, cycle: str) -> str:
        if len(workers) < 2 or status not in {"OVER", "COMPLETE", "RELEASED", "STOPPED", "CANCELLED", "FAILED_START"}:
            return ""
        return (f'<button type="button" class="tm-worker-dismiss" data-worker-dismiss="{worker_id}" '
                f'data-cycle="{html.escape(cycle, quote=True)}" data-status="{html.escape(status, quote=True)}" '
                f'aria-label="收起 Agent {number}" title="收起已结束 Agent；保留科研数据">×</button>')
    runner_link = ""
    if not workers:
        # Legacy single-Agent consoles still have one real Task console, not
        # an invented parallel Worker process.
        status = html.escape(str(run.get("state") or run.get("raw_status") or "IDLE").upper())
        links.append('<span class="tm-worker-card">'
                     f'<a class="tm-worker-tab is-selected" href="/run/{ident}/">'
                     f'<span class="tm-worker-dot" data-status="{status}"></span>'
                     f'Agent 1<small>{status}</small></a></span>')
    else:
        if live_task_runner(run, workers):
            # The independently running Task is not a third Agent. Keep an
            # accessible return path without counting it as a Worker card.
            runner_link = ('<div class="tm-task-runner-link">'
                           '<span class="tm-worker-dot" data-status="RUNNING" aria-hidden="true"></span>'
                           '<span>独立 Task Runner · RUNNING（非 Agent）</span>'
                           + ('<span>当前查看</span>' if selected is None
                              else f'<a href="/run/{ident}/">查看当前运行 ↗</a>')
                           + '</div>')
        for index, worker in enumerate(workers):
            worker_id = worker["worker_id"]
            number = worker_id.split("-")[1]
            status = html.escape(str(worker.get("status") or "UNKNOWN").upper())
            cls = "is-selected" if worker_id == selected else ""
            href = f"/run/{ident}/?worker={quote(worker_id)}"
            links.append(f'<span class="tm-worker-card{" tm-worker-first" if index == 0 else ""}">'
                         f'<a class="tm-worker-tab {cls}" href="{href}" '
                         f'aria-current="{"page" if worker_id == selected else "false"}">'
                         f'<span class="tm-worker-dot" data-status="{status}"></span>'
                         f'Agent {number}<small>{status}</small></a>'
                         f'{dismiss(worker_id, number, status, str(worker.get("current_cycle") or ""))}</span>')
    count = len(workers)
    waiting = max(0, requested_worker_slots(run, count) - count)
    existing_numbers = {int(w["worker_id"].split("-")[1]) for w in workers}
    next_number = max(existing_numbers | {1}) + 1
    for offset in range(min(waiting, 24)):
        number = next_number + offset
        links.append(f'<span class="tm-worker-card tm-worker-queued">'
                     f'<span class="tm-worker-tab tm-worker-pending" '
                     f'aria-label="Agent {number} 等待分配" '
                     'title="等待独立科研前沿和可用资源">'
                     '<span class="tm-worker-dot" data-status="WAITING"></span>'
                     f'Agent {number}<small>待分配</small></span>'
                     + (f'<button type="button" data-worker-cancel class="tm-worker-cancel" '
                        f'aria-label="撤销待分配 Agent {number}" title="撤销最近一个待分配 Agent">×</button>'
                        if offset == min(waiting, 24)-1 else '') + '</span>')
    if waiting > 24:
        links.append(f'<small class="tm-worker-more">另有 {waiting - 24} 个新增请求等待分配</small>')
    feedback = ('<span class="tm-worker-feedback" data-worker-feedback hidden '
                'role="status" aria-live="polite"></span>')
    return ('<nav class="tm-worker-tabs" aria-label="Parallel Research Workers">'
            '<div class="tm-worker-list" role="group" aria-label="Agent 列表">'
            + "".join(links) + '</div>' + plus + runner_link + feedback + '</nav>')


STYLE = '''
.tm-worker-tabs{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;
 gap:8px;padding:8px 0 12px}
.tm-worker-list{display:flex;align-items:center;gap:8px;min-width:0;overflow-x:auto;
 scrollbar-width:thin;padding:0 0 3px;scroll-snap-type:x proximity}
.tm-task-runner-link{grid-column:1/-1;display:flex;align-items:center;gap:7px;
 font-size:12px;color:#adbaca;padding:1px 0}
.tm-task-runner-link a{color:#bfdbfe;text-decoration:underline;text-underline-offset:3px}
.tm-worker-card{display:inline-flex;position:relative;flex:0 0 auto;scroll-snap-align:start}
.tm-worker-tab{display:flex;align-items:center;gap:7px;white-space:nowrap;flex:0 0 auto;
 border-radius:9px;padding:9px 13px;background:#1e2836;border:1px solid #435269;
 color:#e6edf7;text-decoration:none;font-size:13px}
.tm-worker-tab.is-selected{background:#344861;border-color:#8baacf;font-weight:650}
.tm-worker-tab small{font-size:10px;color:#aebaca;font-weight:400}
.tm-worker-dot{width:7px;height:7px;border-radius:50%;background:#97a2b0}
.tm-worker-dot[data-status=RUNNING]{background:#38bc74}
.tm-worker-dot[data-status=PAUSED],.tm-worker-dot[data-status=NEEDS_ATTENTION]{background:#e2b04a}
.tm-worker-dot[data-status=FAILED],.tm-worker-dot[data-status=ORPHANED]{background:#f47676}
.tm-worker-dot[data-status=WAITING]{background:#98a3b5}
.tm-worker-add{display:inline-flex;align-items:center;gap:6px;padding:9px 12px;
 border:1px dashed #6482a2;background:#233144;color:#dce7f5;
 border-radius:9px;font-size:12px;font-weight:650;cursor:pointer;white-space:nowrap}
.tm-worker-add span{font-size:20px;line-height:12px;color:#64d69d}
.tm-worker-add:hover{background:#2e445b;border-color:#93b8dd}
.tm-worker-card:has(.tm-worker-dismiss)>.tm-worker-tab,
.tm-worker-card:has(.tm-worker-cancel)>.tm-worker-tab{padding-right:34px}
.tm-worker-dismiss,.tm-worker-cancel{position:absolute;right:6px;top:7px;width:24px;height:24px;
 display:grid;place-items:center;background:transparent;border:0;color:#b5c0d0;
 font-size:20px;border-radius:6px;cursor:pointer;line-height:1}
.tm-worker-dismiss:hover,.tm-worker-cancel:hover{background:#ef444426;color:#ff8888}
.tm-worker-dismiss:focus-visible,.tm-worker-cancel:focus-visible{outline:2px solid #ff8888;outline-offset:1px}
.tm-worker-dismiss:disabled,.tm-worker-cancel:disabled{opacity:.45;cursor:wait}
.tm-worker-add:focus-visible{outline:2px solid #93c5fd;outline-offset:2px}
.tm-worker-add:disabled{opacity:.45;cursor:wait}
.tm-worker-pending{border-style:dashed;color:#bac5d3;background:#1b2532}
.tm-worker-more{align-self:center;color:#b8c4d5}
.tm-worker-feedback{grid-column:1/-1;color:#f5ba8f;font-size:12px}
.tm-worker-feedback[hidden]{display:none}
.tm-worker-dialog{border:1px solid #52627a;border-radius:14px;background:#1d2029;color:#f4f4f5;
 width:min(420px,calc(100vw - 28px));max-width:420px;padding:22px;box-shadow:0 24px 68px #000b}
.tm-worker-dialog::backdrop{background:rgba(0,0,0,.73)}
.tm-worker-dialog h3{font-size:17px;margin:0 0 8px}
.tm-worker-dialog p{font-size:12px;color:#bac3d0;line-height:1.7}
.tm-worker-dialog .tm-worker-controls{display:grid;grid-template-columns:1fr 1fr;gap:9px}
.tm-worker-dialog .tm-worker-controls button{padding:12px 9px;border-radius:9px}
.tm-worker-dialog [data-worker-action=kill]{color:#fecaca;border-color:#854254;background:#351e29}
.tm-worker-dialog form{display:flex;justify-content:flex-end;margin-top:15px}
.tm-worker-dialog form button{padding:8px 13px;border:1px solid #52627a;border-radius:8px;background:#303849;color:#edf2fa}
.tm-worker-controls{display:flex;align-items:center;gap:9px;flex-wrap:wrap;padding:10px 0}
.tm-worker-controls button{padding:7px 13px;border-radius:8px;border:1px solid #63748b;
 background:#2a374a;color:#e6edf7;cursor:pointer}
.tm-worker-controls button:disabled{opacity:.5;cursor:default}
'''
