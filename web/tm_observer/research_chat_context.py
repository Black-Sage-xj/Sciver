"""Bounded, read-only evidence projection for Workspace Research Chat.

The chat must answer from real TheoryMaster task artifacts, not only a plan and
Canvas headline. This module reads existing Cycle files; it never runs agents,
changes scientific state, or writes into the research workspace.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_MAX_JSON_BYTES = 2_000_000
_MAX_TEXT_BYTES = 80_000


def _json(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_JSON_BYTES:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def _text(path: Path, limit: int) -> str:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_TEXT_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[:limit].strip()
    except OSError:
        return ""


def _tail_events(path: Path, limit: int = 12) -> list[dict[str, Any]]:
    try:
        if path.is_symlink() or not path.is_file():
            return []
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 100_000))
            raw = handle.read().decode("utf-8", errors="replace")
        result = []
        for line in raw.splitlines()[-limit * 3:]:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                result.append(row)
        return result[-limit:]
    except OSError:
        return []


def _event_line(event: dict[str, Any]) -> str:
    kind = str(event.get("type") or event.get("event") or "event")
    at = str(event.get("at") or event.get("ts") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
    bits = []
    for key in ("role", "stage", "verdict", "branch_id", "title", "summary", "error", "status"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            bits.append(f"{key}={str(value).strip()[:220]}")
    suffix = "; ".join(bits)
    return f"- {at} {kind}" + (f": {suffix}" if suffix else "")


def _cycle_block(cycle: Path, task_root: Path) -> str:
    cycle_id = cycle.name
    run = _json(cycle / "run_state.json")
    research = _json(cycle / "research_state.json")
    synthesis = _json(cycle / "cycle_synthesis_out.json")
    report = _text(cycle / "run_report.md", 1_800)
    outcome_path = task_root / "notes" / "outcomes" / f"{cycle_id}.md"
    outcome = _text(outcome_path, 1_600)
    lock = research.get("hypothesis_lock") if isinstance(research.get("hypothesis_lock"), dict) else {}
    status = str(run.get("status") or research.get("status") or "unknown")
    phase = str(run.get("phase") or "")
    started = str(run.get("started_at") or run.get("cycle_started_at") or "")
    last_event = run.get("last_event")
    if isinstance(last_event, dict):
        last_at = str(last_event.get("at") or last_event.get("ts") or "")
    else:
        last_at = str(last_event or "")
    runtime_tail = _tail_events(cycle / "runtime_events.jsonl", limit=4)
    if runtime_tail:
        last_at = str(runtime_tail[-1].get("ts") or last_at)
    lines = [f"### Cycle {cycle_id}", f"- 实际状态：{status}" + (f"；phase={phase}" if phase else "")]
    if started:
        lines.append(f"- 开始时间：{started}")
    if last_at:
        lines.append(f"- 最近可见运行事件：{last_at}")
    if lock:
        hypothesis_id = str(lock.get("id") or research.get("claimed_hypothesis_id") or "")
        title = str(lock.get("title") or lock.get("hypothesis") or "")
        if hypothesis_id or title:
            lines.append(f"- 冻结/锁定假说：{hypothesis_id} {title[:650]}".strip())
        decisive = str(lock.get("decisive_test") or "")
        if decisive:
            lines.append(f"- 当前检验：{decisive[:900]}")
    if run.get("current_agent") or run.get("current_activity"):
        activity = run.get("current_activity")
        if isinstance(activity, dict):
            activity = "; ".join(f"{k}={v}" for k, v in list(activity.items())[:5])
        lines.append(f"- 中断/当前执行位置：agent={run.get('current_agent') or 'unknown'}; activity={str(activity or '')[:500]}")
    scientific = synthesis.get("scientific_synthesis")
    if report:
        report_facts = [line.strip().lstrip("- ") for line in report.splitlines()
                        if any(key in line.lower() for key in ("cycle_status:", "branch_verdict:", "run_id:"))]
        if report_facts:
            lines.append("- 运行报告状态/裁决字段：" + "; ".join(report_facts[:4]))
    if isinstance(scientific, dict):
        conclusions = scientific.get("conclusions") or []
        if conclusions:
            lines.append("- Cycle synthesis 的结论：")
            lines.extend(f"  - {str(value).strip()[:650]}" for value in conclusions[:3] if str(value).strip())
        questions = scientific.get("open_questions") or []
        if questions:
            lines.append("- 未解决问题：")
            lines.extend(f"  - {str(value).strip()[:420]}" for value in questions[:2] if str(value).strip())
    elif report:
        lines.append("- 运行报告摘录：")
        lines.append("  " + report.replace("\n", "\n  ")[:1_400])
    events = _tail_events(cycle / "events.jsonl", limit=8)
    if events:
        lines.append("- 运行事件（尾部记录；非完整日志）：")
        lines.extend(_event_line(event)[:650] for event in events[-6:])
    sources = [f"research/{cycle_id}/run_state.json", f"research/{cycle_id}/research_state.json"]
    if synthesis:
        sources.append(f"research/{cycle_id}/cycle_synthesis_out.json")
    if report:
        sources.append(f"research/{cycle_id}/run_report.md")
    if outcome:
        sources.append(f"notes/outcomes/{cycle_id}.md")
        if not isinstance(scientific, dict):
            lines.extend(["- 归档 Outcome 摘录：", "  " + outcome.replace("\n", "\n  ")[:1_200]])
    lines.append("- 来源锚点：" + "; ".join(sources))
    if status.lower() in {"interrupted", "running", "active", "in_progress"}:
        lines.append("- 证据边界：该 Cycle 未形成可视为完成的终局裁决；不得把它当成已完成实验结果。")
    return "\n".join(lines)


def recent_cycle_context(task_root: Path, research_root: Path, *, max_cycles: int = 5) -> str:
    """Return a bounded timeline from the latest real Cycle directories.

    The latest five numeric Cycle folders are included even when one is
    interrupted; their own timestamps/statuses, not directory mtimes, define
    what can be claimed about progress.
    """
    try:
        task = task_root.resolve(strict=True)
        root = research_root.resolve(strict=True)
    except OSError:
        return "真实 Cycle 轨迹不可读取：task_root/research_root 不存在。"
    if not task.is_dir() or not root.is_dir():
        return "真实 Cycle 轨迹不可读取：task_root/research_root 不是目录。"
    cycles = []
    try:
        for path in root.iterdir():
            if path.is_symlink() or not path.is_dir() or not path.name.isdigit():
                continue
            if not (path / ".theory-master-cycle").is_file() or (path / ".theory-master-cycle").is_symlink():
                continue
            if not ((path / "run_state.json").is_file() or (path / "research_state.json").is_file()):
                continue
            cycles.append(path)
    except OSError:
        return "真实 Cycle 轨迹不可读取：无法列出 research_root。"
    cycles.sort(key=lambda item: int(item.name))
    selected = cycles[-max(1, min(int(max_cycles), 8)):]
    if not selected:
        return "research_root 中未找到带 .theory-master-cycle 标记的实际 Cycle 目录。"
    lines = [f"以下为 research_root 中按 Cycle 编号排序的最近 {len(selected)} 个实际运行目录；不是仅依据 Canvas 或 Research Plan 推断。"]
    lines.extend(_cycle_block(cycle, task) for cycle in reversed(selected))
    return "\n\n".join(lines)[:24_000]


def current_worker_context(research_root: Path) -> str:
    """Fresh, bounded OS-identity-backed Worker snapshot for Research Chat.

    Never infer real liveness from the last frozen Cycle JSON or from the
    scheduler's terminal status. The scheduler is an allocator, not a Worker.
    """
    from datetime import datetime, timezone
    root = Path(research_root)
    observed = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        from theory_master.worker_runtime import list_workers, scheduler_status
        workers = list_workers(root)
        scheduler = scheduler_status(root)
    except (OSError, ValueError, TypeError) as exc:
        return f"实时 Worker 身份检查不可用（{type(exc).__name__}）；不可依据旧 Cycle 快照宣称进程仍在运行。"
    lines = [f"- 实时检查时间（UTC）：{observed}"]
    if not workers:
        lines.append("- 无已登记 Worker；需另外检查 Master Runner，不能据此推断任务总体已停止。")
    live = 0
    for worker in workers[:24]:
        alive = worker.get("process_alive") is True
        if alive:
            live += 1
        effective = worker.get("effective_status") or worker.get("status") or "UNKNOWN"
        name = str(worker.get("worker_id") or "unknown")[:40]
        cycle = str(worker.get("current_cycle") or "尚未创建")[:30]
        account = str(worker.get("account") or "未登记")[:20]
        lines.append(f"- {name}: Cycle {cycle}; 状态={effective}; "
                     f"OS进程身份验证={'存活' if alive else '未存活'}; account={account}")
    lines.append(f"- 当前存活 Worker 数：{live} / {len(workers)}（仅已登记的科研 Worker，不含子进程）。")
    if scheduler:
        status = str(scheduler.get("status") or "UNKNOWN")[:30]
        scheduler_alive = scheduler.get("process_alive") is True
        lines.append(f"- Worker 调度器：状态={status}，进程={'存活' if scheduler_alive else '未存活'}。"
                     "调度器停止分配不代表已启动的 Worker 停止运行。")
        counts = scheduler.get("cycles_started_by_worker")
        max_cycles = scheduler.get("max_cycles_per_worker")
        if isinstance(counts, dict) and len(counts) <= 24 and type(max_cycles) is int:
            allocations = ", ".join(f"{str(w)[:30]}={str(c)[:8]}/{max_cycles}"
                                    for w, c in sorted(counts.items()))
            lines.append(f"- 本批次每 Worker 新 Cycle 分配次数：{allocations}。"
                         "达到配额后停止的是后续分配，不是正在执行的 Cycle。")
    return "\n".join(lines)[:6_000]
