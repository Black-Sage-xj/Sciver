"""Single read-only lifecycle projection for operator-registered executors.

The registry and raw process observations live outside research workspaces.
HTTP requests never invoke Docker, initialize accounts or mutate task state.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REGISTRY_ENV = "SCIVER_EXECUTION_REGISTRY"
REGISTRY_SCHEMA = "sciver/execution-registry/v1"
EVIDENCE_SCHEMA = "sciver/execution-observations/v1"


def read_document(path: Path) -> dict:
    try:
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def registry() -> dict:
    value = os.environ.get(REGISTRY_ENV, "").strip()
    data = read_document(Path(value).expanduser()) if value else {}
    return data if data.get("schema") == REGISTRY_SCHEMA else {}


def process_identity(pid: int) -> dict:
    try:
        path = Path(f"/proc/{int(pid)}")
        raw = (path / "stat").read_text()
        fields = raw[raw.rfind(")") + 2:].split()
        namespace_pids = next(line.split()[1:] for line in (path / "status").read_text().splitlines()
                              if line.startswith("NSpid:"))
        return {"host_pid": int(pid), "namespace_pids": [int(v) for v in namespace_pids],
                "start_ticks": int(fields[19]), "state": fields[0]}
    except (OSError, ValueError, IndexError, StopIteration):
        return {}


def alive(pid: Any, ticks: Any) -> bool:
    if type(pid) is not int or type(ticks) is not int:
        return False
    observed = process_identity(pid)
    return observed.get("start_ticks") == ticks and observed.get("state") not in {None, "Z", "X", "x"}


def _age(stamp: Any) -> float:
    try:
        instant = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - instant.astimezone(timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def _mounted(path: str, mounts: list) -> bool:
    target = Path(path)
    for mount in mounts:
        source = mount.get("Source")
        if source and mount.get("Destination") == source and target.is_relative_to(Path(source)):
            return True
    return False


def project(research_root: Path, task_root: Path, config: dict, runtime: dict,
            scientific: dict, observed: dict) -> dict | None:
    settings = registry()
    record = settings.get("tasks", {}).get(str(research_root.resolve()))
    if not isinstance(record, dict):
        return None
    result = {"managed": True, "state": "UNKNOWN", "status": "UNKNOWN",
              "execution_active": False, "pid_alive": False, "pid": None,
              "phase": "unknown", "active_agent": None,
              "message": "执行状态暂不可核验", "source": os.environ.get(REGISTRY_ENV),
              "container_id": record.get("container_id"), "queue_position": None,
              "display_name": record.get("display_name")}
    if (record.get("task_id") != config.get("task_id")
            or record.get("task_home") != str(task_root.resolve())
            or record.get("research_root") != str(research_root.resolve())):
        result["message"] = "执行登记与任务身份不匹配"
        return result
    evidence = read_document(Path(str(settings.get("observations_file", ""))))
    result["observed_at"] = evidence.get("observed_at")
    if evidence.get("schema") != EVIDENCE_SCHEMA or not -5 <= _age(evidence.get("observed_at")) <= 20:
        result.update(state="OBSERVATION_STALE", status="OBSERVATION_STALE", message="运行状态采集暂未更新")
        return result
    controller = read_document(Path(str(record.get("controller_file", ""))))
    controller_live = alive(record.get("controller_pid"), record.get("controller_start_ticks"))
    pending = controller.get("pending") or []
    assigned = record.get("selector") in (controller.get("running") or [])
    queued = record.get("selector") in pending
    result["controller_alive"] = controller_live
    if queued:
        result["queue_position"] = pending.index(record["selector"]) + 1
    outer = read_document(Path(str(record.get("status_file", ""))))
    container = evidence.get("containers", {}).get(record.get("container_id")) or {}
    if (container.get("id") != record.get("container_id")
            or container.get("image_id") != record.get("image_id")
            or not _mounted(str(task_root.resolve()), container.get("mounts") or [])
            or not _mounted(str(research_root.resolve()), container.get("mounts") or [])):
        result["message"] = "容器身份或任务挂载不可核验"
        return result
    host_owner = None
    if container.get("running") and type(runtime.get("runner_pid")) is int:
        for process in container.get("processes", []):
            pids = process.get("namespace_pids") or []
            if (pids and pids[-1] == runtime["runner_pid"]
                    and process.get("start_ticks") == runtime.get("runner_start_ticks")
                    and alive(process.get("host_pid"), process.get("start_ticks"))):
                host_owner = process["host_pid"]
                break
    scientific_status = str(scientific.get("status") or observed.get("status") or "").upper()
    outer_status = outer.get("status")
    result.update(scientific_status=scientific_status, execution_status=outer_status,
                  container_running=container.get("running") is True,
                  container_runner_pid=runtime.get("runner_pid"),
                  updated_at=outer.get("updated_at"), status_source=record.get("status_file"))
    if host_owner and scientific_status not in {"NEEDS_ATTENTION", "PAUSED", "FAILED", "CANCELLED"}:
        result.update(state="LIVE", status="RUNNING", execution_active=True, pid_alive=True,
                      pid=host_owner, phase=scientific.get("phase") or observed.get("phase"),
                      active_agent=observed.get("current_agent") or observed.get("current_turn_role"),
                      message="运行中" + (" · 已恢复" if outer_status == "resuming_sciver" else ""))
    elif outer_status == "time_limit" or outer.get("runtime_outcome") in {"official_agent_time_limit", "existing_time_budget_exhausted"}:
        result.update(state="TIME_LIMIT", status="TIME_LIMIT", phase="stopped",
                      message="已达到时间上限" + (" · 评分排队中" if queued and controller_live else ""))
    elif queued and controller_live:
        result.update(state="QUEUED", status="QUEUED", phase="queued",
                      message=f"排队第 {result['queue_position']} 位 · 等待运行资源")
    elif assigned and controller_live and outer_status not in {"complete", "runtime_failed", "environment_failed"}:
        result.update(state="RECOVERING", status="RECOVERING", phase="recovering", message="正在恢复运行")
    elif scientific_status in {"NEEDS_ATTENTION", "PAUSED", "NO_QUALIFIED_CANDIDATE"}:
        state = "PAUSED" if scientific_status == "PAUSED" else "NEEDS_ATTENTION"
        result.update(state=state, status=state, phase="waiting", message="需要处理" if state == "NEEDS_ATTENTION" else "已暂停")
    elif outer_status in {"runtime_failed", "environment_failed", "evaluation_failed"}:
        result.update(state="FAILED", status="FAILED", phase="stopped", message="运行已停止 · 存在待处理故障")
    elif outer_status == "complete":
        result.update(state="STOPPED", status="STOPPED", phase="finished", message="执行已结束")
    elif queued:
        result.update(state="PAUSED", status="PAUSED", phase="queued", message="队列控制器已停止，等待恢复调度")
    else:
        result.update(state="ORPHANED", status="ORPHANED", phase="stopped", message="未核验到任务运行进程")
    result["snapshot"] = {key: result[key] for key in ("status", "phase", "execution_active")}
    result["snapshot"]["current_agent"] = result["active_agent"]
    return result
