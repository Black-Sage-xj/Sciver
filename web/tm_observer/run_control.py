"""Allowlisted Web TUI commands delegated to the *existing* Workflow SDK.

Observer cannot invent a Cycle verdict. Never accept a path or shell command
from the browser. The authenticated HTTP caller supplies only an observer_id,
expected task generation and a bounded semantic action.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .run_monitor import data

ACTIONS = {"set_worker_mode", "add_worker", "cancel_pending_worker", "start_worker_scheduler", "stop_worker_scheduler", "set_worker_route", "set_worker_profile", "set_worker_account", "save_model_tier_template", "inspect_codex_models", "worker_control", "pause", "kill", "abandon_cycle", "max_cycles", "attention_guidance", "attention_takeover", "supervisor", "quota_auto_adjust", "run", "resume", "set_role_route", "set_operator_preference"}
HIGH_RISK = {"set_worker_mode", "add_worker", "cancel_pending_worker", "start_worker_scheduler", "stop_worker_scheduler", "set_worker_route", "set_worker_profile", "set_worker_account", "save_model_tier_template", "worker_control", "pause", "kill", "abandon_cycle", "attention_guidance"}
VALID_REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z")


class OperatorError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status



class VerifiedPreflightRefusal(OperatorError):
    """SDK verified that it rejected the request without applying a mutation."""


def _sdk_apply(run: dict[str, Any], action: str, value: Any) -> dict[str, Any]:
    """Execute the SDK controller through a fixed local, non-shell worker.

    The Observer deliberately never loads the TheoryMaster runner package.
    The worker entrypoint `theory_master.operator_control` owns SDK imports.
    """
    source_root = Path(__file__).resolve().parents[2]  # integrated TheoryMaster checkout
    env = dict(os.environ)
    env["PYTHONPATH"] = str(source_root) + os.pathsep + env.get("PYTHONPATH", "")
    message = json.dumps({
        "action": action, "value": value,
        "task_root": str(run["task_root"]),
        "research_root": str(run["research_root"]),
        "run_id": run.get("run_id"),
    }, ensure_ascii=False)
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "theory_master.operator_control"],
            input=message, text=True, capture_output=True, shell=False,
            timeout=90, check=False, env=env,
        )
        # No raw exception/log/path details are disclosed in the HTTP response.
        response = json.loads((completed.stdout or "").splitlines()[-1])
        if completed.returncode != 0 or not response.get("ok"):
            if (action == "worker_control" and isinstance(value, dict)
                    and value.get("operation") == "preserve_release"
                    and response.get("confirmed_no_effect") is True
                    and response.get("error") == "preservation_preflight_rejected"):
                raise VerifiedPreflightRefusal(
                    "保全前置核验未通过，SDK 已确认未修改 Cycle 或 Worker；请刷新状态后检查", 409)
            raise OperatorError("SDK 操作未确认完成，请先刷新任务状态", 409)
        return response["outcome"]
    except OperatorError:
        raise
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, KeyError) as exc:
        raise OperatorError("SDK 控制服务状态不确定，禁止自动重试", 409) from exc


def _validated_value(action: str, value: Any) -> Any:
    if action == "set_worker_mode":
        if (not isinstance(value, dict) or set(value) != {"enabled", "desired_agents"}
                or type(value["enabled"]) is not bool
                or type(value["desired_agents"]) is not int
                or not 1 <= value["desired_agents"] <= 1024):
            raise OperatorError("多 Agent 数量必须为 1–1024，启用状态必须为布尔值")
        return value
    if action in {"add_worker", "cancel_pending_worker", "start_worker_scheduler", "stop_worker_scheduler"}:
        if value is not None:
            raise OperatorError("调度器操作不接受额外参数")
        return None
    if action == "save_model_tier_template":
        from theory_master.user_model_presets import CODEX_TIERS, MODEL_NAME
        from theory_master.backend_config import ROUTE_ROLE_ORDER, REASONING_EFFORTS
        if not isinstance(value, dict) or set(value) != {"profile", "roles"}:
            raise OperatorError("无效的模型档位设置")
        if value["profile"] not in CODEX_TIERS or not isinstance(value["roles"], dict) or set(value["roles"]) != set(ROUTE_ROLE_ORDER):
            raise OperatorError("档位必须包含全部六个科研阶段")
        for row in value["roles"].values():
            if not isinstance(row, dict) or set(row) != {"model", "reasoning_effort"}:
                raise OperatorError("档位字段不完整")
            if (not isinstance(row["model"], str) or not MODEL_NAME.fullmatch(row["model"])
                    or not isinstance(row["reasoning_effort"], str)
                    or row["reasoning_effort"] not in REASONING_EFFORTS):
                raise OperatorError("无效的模型或推理强度")
        return value
    if action == "inspect_codex_models":
        if (not isinstance(value, dict) or set(value) != {"worker_id"}
                or value["worker_id"] is not None and
                (not isinstance(value["worker_id"], str)
                 or not re.fullmatch(r"worker-[1-9][0-9]*", value["worker_id"]))):
            raise OperatorError("无效的模型检测请求")
        return value
    if action == "set_worker_account":
        if (not isinstance(value, dict) or set(value) != {"worker_id", "account"}
                or not isinstance(value.get("worker_id"), str)
                or not re.fullmatch(r"worker-[1-9][0-9]*", value["worker_id"])
                or value.get("account") not in {"1", "2", "keep"}):
            raise OperatorError("无效 Worker 账号分配")
        return value
    if action == "set_worker_profile":
        from theory_master.backend_config import MODEL_PROFILES
        if (not isinstance(value, dict) or set(value) != {"worker_id", "profile"}
                or not isinstance(value.get("worker_id"), str)
                or not re.fullmatch(r"worker-[1-9][0-9]*", value["worker_id"])
                or not isinstance(value.get("profile"), str)
                or value["profile"] not in MODEL_PROFILES):
            raise OperatorError("无效六阶段模型档位")
        return value
    if action == "set_worker_route":
        if (not isinstance(value, dict) or set(value) != {
                "worker_id", "role", "model", "provider", "reasoning_effort", "activation"}
                or not isinstance(value.get("worker_id"), str)
                or not re.fullmatch(r"worker-[1-9][0-9]*", value["worker_id"])):
            raise OperatorError("无效 Worker 模型配置")
        if any(not isinstance(v, str) or len(v) > 90 for v in value.values()):
            raise OperatorError("无效 Worker 模型字段")
        if value["role"] not in {"research_lead", "challenger", "investigator", "literature", "judge", "reporter"}:
            raise OperatorError("无效科研角色")
        if (value["reasoning_effort"] not in {"low", "medium", "high", "xhigh"}
                or value["activation"] not in {"next_cycle", "next_invocation"}):
            raise OperatorError("无效 Worker 模型生效方式")
        return value
    if action == "worker_control":
        if (not isinstance(value, dict) or set(value) not in (
                {"worker_id", "operation"}, {"worker_id", "operation", "account"})
                or not isinstance(value.get("worker_id"), str)
                or not re.fullmatch(r"worker-[1-9][0-9]*", value["worker_id"])
                or value.get("operation") not in {"pause", "kill", "resume", "release", "preserve_release", "dismiss"}
                or ("account" in value and
                    (value["operation"] != "resume" or value["account"] not in {"1", "2"}))):
            raise OperatorError("无效 Worker 操作或账号迁移")
        return value
    if action == "set_operator_preference":
        if not isinstance(value, dict) or set(value) != {"kind","key","value"}:
            raise OperatorError("无效运行偏好")
        kind, key, selected = value["kind"], value["key"], value["value"]
        if kind == "flag":
            if key not in {"error_repair", "cycle_audit"} or type(selected) is not bool:
                raise OperatorError("无效运行开关")
        elif kind == "model":
            if key not in {"supervisor", "attention_review", "cycle_audit"} or not isinstance(selected, str) or selected not in {"","gpt-6-luna","gpt-6-sol","gpt-6.1-sol"}:
                raise OperatorError("无效内部服务模型")
        else:
            raise OperatorError("不支持的偏好")
        return value
    if action == "set_role_route":
        if not isinstance(value, dict) or set(value) not in (
                {"role", "model", "provider", "reasoning_effort"},
                {"role", "model", "provider", "reasoning_effort", "activation"}):
            raise OperatorError("模型配置字段不完整")
        if any(not isinstance(v, str) or len(v) > 90 for v in value.values()):
            raise OperatorError("模型配置类型不正确")
        if value["role"] not in {"research_lead", "challenger", "investigator", "literature", "judge", "reporter"}:
            raise OperatorError("未知 Agent")
        if value["reasoning_effort"] not in {"low", "medium", "high", "xhigh"}:
            raise OperatorError("无效 effort")
        if value.get("activation", "next_invocation") not in {"next_invocation", "next_cycle"}:
            raise OperatorError("无效模型生效时间")
        return value
    if action in ("run", "resume"):
        if value is None:
            return {}
        if not isinstance(value, dict) or not set(value).issubset({"cycle_audit", "error_repair", "attention_takeover"}):
            raise OperatorError("不支持的启动选项")
        if any(type(entry) is not bool for entry in value.values()):
            raise OperatorError("启动开关必须为 true 或 false")
        return value
    if action == "max_cycles":
        if type(value) is not int or value < 1 or value > 1_000_000:
            raise OperatorError("max_cycles 应为 1 至 1000000 的整数")
        return value
    if action == "attention_guidance":
        if not isinstance(value, str) or not value.strip() or len(value) > 3000:
            raise OperatorError("一次性指导必须为 1 至 3000 字符")
        return value.strip()
    if action in ("attention_takeover", "supervisor", "quota_auto_adjust"):
        if type(value) is not bool:
            raise OperatorError("开关值必须为 true 或 false")
        return value
    if value is not None:
        raise OperatorError("该操作不接受额外参数")
    return None


def execute(run: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Execute one checked task-local command, serialized + idempotent on disk.

    Caller MUST first authenticate and check Origin/CSRF. Discovered `run`
    must come from the server's own task index, never a client-supplied path.
    """
    action = payload.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        raise OperatorError("不支持的任务操作")
    req_id = payload.get("request_id")
    if not isinstance(req_id, str) or not VALID_REQUEST_ID.fullmatch(req_id):
        raise OperatorError("request_id 必须是随机生成的 32 位十六进制值")
    expected_cycle = payload.get("expected_cycle")
    expected_status = payload.get("expected_status")
    expected_launch = payload.get("expected_launch_id")
    if not isinstance(expected_cycle, str) or not isinstance(expected_status, str):
        raise OperatorError("缺少操作目标状态，请刷新页面")
    if expected_launch is not None and not isinstance(expected_launch, str):
        raise OperatorError("无效 launch ID")
    value = _validated_value(action, payload.get("value"))
    root = Path(str(run["research_root"])).resolve(strict=True)
    if not root.is_dir():
        raise OperatorError("科研任务目录不可访问", 404)
    guard = root / ".theory-master"
    if not guard.is_dir() or guard.is_symlink():
        raise OperatorError("任务控制注册表不存在", 409)
    lock = guard / "web-operator.lock"
    # Deliberately avoid shell process operations. This lock serializes Web
    # writes; SDK/managed-runner locking remains authoritative for execution.
    with lock.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            receipts = guard / "web-operator-receipts"
            receipt = receipts / f"{req_id}.json"
            if receipt.is_file():
                saved = json.loads(receipt.read_text(encoding="utf-8"))
                if saved.get("action") != action or (action in {"worker_control", "set_worker_route", "set_worker_profile", "set_worker_account"} and saved.get("value") != value):
                    raise OperatorError("request_id 不可复用于不同操作", 409)
                if saved.get("phase") == "rejected_no_effect":
                    raise OperatorError("先前保全请求已确认未执行；请刷新后重新发起新请求", 409)
                if saved.get("phase") != "applied":
                    raise OperatorError("此请求已提交但结果尚不确定，请检查任务状态，勿重复执行", 409)
                return {"ok": True, "duplicate": True, "outcome": saved.get("outcome")}
            if action in {"worker_control", "set_worker_route", "set_worker_profile", "set_worker_account"}:
                from .worker_console import registered_workers
                target = next((w for w in registered_workers(run)
                               if w["worker_id"] == value["worker_id"]), None)
                if target is None:
                    raise OperatorError("Worker 已不存在，请刷新", 409)
                if (expected_cycle != str(target.get("current_cycle") or "") or
                        expected_status.upper() != str(target.get("status") or "").upper() or
                        expected_launch not in {None, ""}):
                    raise OperatorError("Worker Cycle 或状态已变化，请刷新", 409)
                pending = {"action": action, "value": value, "phase": "pending",
                           "at": datetime.now(timezone.utc).isoformat()}
                receipts.mkdir(mode=0o700, exist_ok=True)
                fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as saved_file:
                    json.dump(pending, saved_file)
                    saved_file.flush()
                    os.fsync(saved_file.fileno())
                try:
                    outcome = _sdk_apply(run, action, value)
                except VerifiedPreflightRefusal:
                    completed = {**pending, "phase": "rejected_no_effect",
                                 "outcome": {"reason_code": "preservation_preflight_rejected"}}
                    temp = receipt.with_name(f".{req_id}.{os.getpid()}.tmp")
                    with temp.open("w", encoding="utf-8") as saved:
                        json.dump(completed, saved, ensure_ascii=False)
                        saved.flush()
                        os.fsync(saved.fileno())
                    os.replace(temp, receipt)
                    raise
                completed = {**pending, "phase": "applied", "outcome": outcome}
                temp = receipt.with_name(f".{req_id}.{os.getpid()}.tmp")
                with temp.open("w", encoding="utf-8") as handle2:
                    json.dump(completed, handle2)
                    handle2.flush()
                    os.fsync(handle2.fileno())
                os.replace(temp, receipt)
                return {"ok": True, "outcome": outcome}
            from .run_monitor import data as snapshot_data
            state = snapshot_data(run)
            actual_cycle = str(state.get("cycle_id") or run.get("cycle") or "")
            actual_status = str(state.get("status") or "").upper()
            registry_path = guard / "runtime.json"
            try:
                runtime = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() and not registry_path.is_symlink() else {}
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
                runtime = {}
            actual_launch = str(runtime.get("launch_id") or "")
            if expected_cycle != actual_cycle or expected_status.upper() != actual_status:
                raise OperatorError("Cycle 或状态已变化，请刷新页面重试", 409)
            if (expected_launch or "") != actual_launch:
                raise OperatorError("Runner 已切换，请刷新页面重试", 409)
            running = actual_status in {"RUNNING", "ACTIVE", "STARTING", "RECOVERING"}
            paused_kill = action == "kill" and actual_status == "PAUSED" and not state.get("execution_active")
            if action in ("pause", "kill", "abandon_cycle") and not (running or paused_kill):
                raise OperatorError("当前 Cycle 不处于可控制的运行状态", 409)
            if (action in {"kill", "abandon_cycle"} and running and
                    (state.get("sdk_runtime_capabilities") or {}).get("abandon_cycle_v1") is not True):
                raise OperatorError("当前 Runner 尚未加载本回合放弃协议，请在安全暂停后 Resume 更新，再使用此操作", 409)
            if action == "attention_guidance" and actual_status != "NEEDS_ATTENTION":
                raise OperatorError("只有 NEEDS_ATTENTION 可以提交指导", 409)
            if action in {"start_worker_scheduler", "add_worker"} and actual_status in {"RUNNING", "ACTIVE", "STARTING", "RECOVERING"}:
                from .worker_console import registered_workers
                if not any(str(w.get("status") or "").upper() in {"RUNNING", "STARTING"}
                           for w in registered_workers(run)):
                    raise OperatorError("当前旧版单 Agent Runner 仍在运行，完成后才能安全启动多 Agent", 409)
            if action == "run" and actual_status not in {"", "UNKNOWN", "STOPPED", "OVER", "IDLE", "NEW", "CANCELLED"}:
                raise OperatorError("当前任务状态不允许启动新 Cycle", 409)
            if action == "resume" and actual_status not in {"PAUSED", "INTERRUPTED", "NEEDS_ATTENTION", "FAILED", "FAILED_RECOVERY", "ORPHANED", "CHECKPOINT_REACHED", "RECOVERABLE", "UNRESPONSIVE"}:
                raise OperatorError("当前任务状态不允许 Resume；如前一 Cycle 为 OVER，请使用 Run", 409)
            receipts.mkdir(mode=0o700, exist_ok=True)
            # Reserve the request ID BEFORE performing the SDK write. A crash
            # leaves a pending marker and never silently retries an action.
            pending = {"action": action, "phase": "pending", "at": datetime.now(timezone.utc).isoformat()}
            fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as result_file:
                json.dump(pending, result_file, ensure_ascii=False)
                result_file.flush()
                os.fsync(result_file.fileno())
            try:
                outcome = _sdk_apply(run, action, value)
            except OperatorError:
                raise
            except (ValueError, OSError, RuntimeError) as exc:
                raise OperatorError(f"任务操作结果不确定，请核查状态：{type(exc).__name__}", 409) from exc
            safe_outcome = {"action": action, "phase": "applied", "outcome": outcome, "at": pending["at"]}
            temporary = receipts / f".{req_id}.{os.getpid()}.tmp"
            with temporary.open("x", encoding="utf-8") as result_file:
                json.dump(safe_outcome, result_file, ensure_ascii=False)
                result_file.flush()
                os.fsync(result_file.fileno())
            os.replace(temporary, receipt)
            with (guard / "web-operator-audit.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps({"request_id": req_id, "action": action, "cycle": actual_cycle,
                                      "status_before": actual_status, "at": safe_outcome["at"]}, ensure_ascii=False) + "\n")
            return {"ok": True, "duplicate": False, "outcome": outcome}
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
