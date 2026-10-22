"""Allowlisted SDK operator command entrypoint for local-first Web TUI.

Runs in a short-lived, fixed-argv worker process, NOT inside Observer's
read-only scientific projection process. Actual control semantics remain the
same canonical Workflow methods used by `tm tui`.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from .core import FakeBackend, TaskSpec, Workflow
from .task import TaskBinding


class ControlRejected(ValueError):
    pass


from .worker_runtime import PreservationPreflightRejected


def dispatch(specified: dict[str, Any]) -> dict[str, Any]:
    action = specified.get("action")
    if action not in {"set_worker_mode", "add_worker", "cancel_pending_worker", "start_worker_scheduler", "stop_worker_scheduler", "set_worker_route", "set_worker_profile", "set_worker_account", "save_model_tier_template", "inspect_codex_models", "worker_control", "pause", "kill", "abandon_cycle", "max_cycles", "attention_guidance", "attention_takeover", "supervisor", "quota_auto_adjust", "run", "resume", "set_role_route", "set_operator_preference"}:
        raise ControlRejected("unknown operator action")
    root = Path(str(specified["task_root"])).resolve(strict=True)
    binding = TaskBinding.load(root)
    spec = TaskSpec.from_config(binding.config_path)
    research = Path(str(specified["research_root"])).resolve(strict=True)
    if spec.research_root.resolve() != research:
        raise ControlRejected("task research root changed")
    if specified.get("run_id") is not None and int(spec.master_id) != int(specified["run_id"]):
        raise ControlRejected("task ID changed")
    value = specified.get("value")
    if action == "set_worker_mode":
        from .worker_mode import configure_mode
        from .worker_runtime import stop_scheduler, list_workers
        if (not isinstance(value, dict) or not {"enabled", "desired_agents"}.issubset(value)
                or not set(value).issubset({"enabled", "desired_agents", "share_accounts"})):
            raise ControlRejected("invalid task Worker mode")
        updated = configure_mode(spec.research_root, **value)
        stopped = stop_scheduler(spec) if not updated["enabled"] else None
        active_workers = sum(bool(w.get("process_alive")) for w in list_workers(spec.research_root))
        return {"mode": updated, "scheduler": stopped, "active_workers_unaffected": active_workers,
                "effective_at": "next_scheduler_tick_or_explicit_start"}
    if action == "add_worker":
        if value is not None:
            raise ControlRejected("add_worker accepts no parameters")
        from .worker_mode import request_one_worker
        from .worker_runtime import runtime_health, list_workers, start_scheduler
        if runtime_health(spec.research_root).get("active"):
            raise ControlRejected("旧版单 Agent Runner 正在运行，不能安全新增 Worker；请等待或先迁移")
        updated = request_one_worker(spec.research_root,
                                     registered_count=sum(not w.get("dismissed") for w in list_workers(spec.research_root)))
        # Do not bypass scheduler_daemon's frozen Cycle budget or claim guards.
        # It checks the request on its next tick (at most about eight seconds).
        try:
            scheduler = start_scheduler(spec, updated["desired_agents"])
            return {"status": "requested", "requested_agents": updated["requested_agents"],
                    "scheduler": scheduler.get("status"),
                    "actual_workers": len(list_workers(spec.research_root))}
        except (ValueError, OSError, RuntimeError) as exc:
            # A recorded request remains visible; never claim a Worker started.
            return {"status": "waiting", "requested_agents": updated["requested_agents"],
                    "error": str(exc), "actual_workers": len(list_workers(spec.research_root))}
    if action == "cancel_pending_worker":
        if value is not None:
            raise ControlRejected("cancel_pending_worker accepts no parameters")
        from .worker_mode import reduce_one_worker_request
        from .worker_runtime import list_workers
        import fcntl
        scheduler_lock = spec.research_root / ".theory-master" / "scheduler.lock"
        scheduler_lock.parent.mkdir(parents=True, exist_ok=True)
        with scheduler_lock.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            visible = sum(not worker.get("dismissed") for worker in list_workers(spec.research_root))
            return reduce_one_worker_request(spec.research_root, registered_count=visible)
    if action == "start_worker_scheduler":
        from .worker_mode import read_mode
        from .worker_runtime import start_scheduler
        configured = read_mode(spec.research_root)
        if not configured["enabled"]:
            raise ControlRejected("enable multi-Agent mode before starting")
        return start_scheduler(spec, configured["desired_agents"])
    if action == "stop_worker_scheduler":
        from .worker_runtime import stop_scheduler
        return stop_scheduler(spec)
    if action == "save_model_tier_template":
        from .user_model_presets import save
        if not isinstance(value, dict) or set(value) != {"profile", "roles"}:
            raise ControlRejected("invalid custom model tier")
        return {"configured": save(value["profile"], value["roles"]),
                "applies_to_current_turn": False}
    if action == "inspect_codex_models":
        from .codex_model_catalog import model_catalog
        if (not isinstance(value, dict) or set(value) != {"worker_id"}
                or value["worker_id"] is not None and
                (not isinstance(value["worker_id"], str)
                 or not re.fullmatch(r"worker-[1-9][0-9]*", value["worker_id"]))):
            raise ControlRejected("invalid model catalog request")
        return model_catalog(spec.research_root, value["worker_id"])
    if action == "set_worker_account":
        from .worker_runtime import set_worker_account_preference
        if (not isinstance(value, dict) or set(value) != {"worker_id", "account"}
                or not isinstance(value["worker_id"], str)
                or not isinstance(value["account"], str)):
            raise ControlRejected("invalid Worker account preference")
        return set_worker_account_preference(spec.research_root,
                                             value["worker_id"], value["account"])
    if action == "set_worker_profile":
        from .worker_runtime import save_worker_profile
        from .backend_config import MODEL_PROFILES
        if (not isinstance(value, dict) or set(value) != {"worker_id", "profile"}
                or not isinstance(value["worker_id"], str)
                or value["profile"] not in MODEL_PROFILES):
            raise ControlRejected("invalid six-Agent model tier selection")
        updated = save_worker_profile(spec.research_root, value["worker_id"], value["profile"])
        return {"configured": updated, "effective_at": "next_safe_role_invocation",
                "applies_to_current_turn": False}
    if action == "set_worker_route":
        from .worker_runtime import save_worker_route
        if not isinstance(value, dict) or set(value) != {
                "worker_id", "role", "model", "provider", "reasoning_effort", "activation"}:
            raise ControlRejected("invalid Worker route")
        if value["activation"] not in {"next_cycle", "next_invocation"}:
            raise ControlRejected("invalid Worker route activation")
        return save_worker_route(spec.research_root, str(value["worker_id"]),
                                 role=str(value["role"]), model=str(value["model"]),
                                 provider=str(value["provider"]),
                                 effort=str(value["reasoning_effort"]),
                                 activation=str(value["activation"]))
    if action == "worker_control":
        from .worker_runtime import worker_action
        if (not isinstance(value, dict) or not {"worker_id", "operation"}.issubset(value)
                or not set(value).issubset({"worker_id", "operation", "account"})):
            raise ControlRejected("invalid Worker control")
        if "account" in value and value["operation"] != "resume":
            raise ControlRejected("account migration requires paused Worker resume")
        return worker_action(spec, str(value["worker_id"]), str(value["operation"]),
                             account=value.get("account"))
    if action == "set_operator_preference":
        from .operator_preferences import write
        if not isinstance(value, dict) or set(value) != {"kind", "key", "value"}:
            raise ControlRejected("invalid operator preference")
        saved = write(spec.research_root, **value)
        return {"saved": saved, "effective_at": ("next_run_or_resume" if value["key"] in {"error_repair","cycle_audit"} and value["kind"] == "flag" else "next_use")}
    if action == "set_role_route":
        from .backend_config import save_task_role_override
        if not isinstance(value, dict):
            raise ControlRejected("model/effort selection must be an object")
        updated = save_task_role_override(spec.research_root,
            role=value["role"], model=value["model"], provider=value["provider"],
            effort=value["reasoning_effort"],
            activation=value.get("activation", "next_invocation"))
        return {"configured": updated,
                "effective_at": ("next_cycle_boundary" if updated["activation"] == "next_cycle"
                                 else "next_safe_agent_invocation_if_supported_else_next_run_or_resume"),
                "applies_to_current_turn": False}
    if action in {"run", "resume", "set_role_route", "set_operator_preference"}:
        from .runtime_manager import runtime_health, launch_managed_runtime
        if runtime_health(spec.research_root).get("active"):
            raise ControlRejected("task already has an active runner")
        from .operator_preferences import read as operator_prefs
        prefs = operator_prefs(spec.research_root)["flags"]
        options = value or {}
        output = launch_managed_runtime(
            spec, operation=action, identifier=str(spec.master_id),
            cycle_audit=prefs.get("cycle_audit", options.get("cycle_audit")), error_repair=options.get("error_repair"),
            attention_takeover=options.get("attention_takeover"))
        if output.get("status") != "started":
            raise ControlRejected("the managed launch did not start")
        return {"launch_status": "started", "launch_id": output.get("launch_id"), "effective_at": "managed_supervisor"}
    workflow = Workflow(spec, FakeBackend())
    if action in {"pause", "kill", "abandon_cycle"}:
        outcome = workflow.request_control(action, reason="web_operator")
        return {"requested": action, "status": outcome.get("status"), "effective_at": "next_sdk_safe_boundary"}
    if action == "max_cycles":
        return {"max_cycles": workflow.set_max_cycles(value), "effective_at": "next_cycle_boundary"}
    if action == "attention_guidance":
        payload = workflow.submit_attention_guidance(value, source="human")
        return {"guidance_status": "saved", "source": payload.get("source"), "effective_at": "next_resume"}
    if action in {"attention_takeover", "supervisor"}:
        result = workflow.set_supervisor(value)
        return {"enabled": bool(result.get("enabled")), "effective_at": result.get("handoff") or "sdk"}
    result = workflow.set_quota_auto_adjust(value)
    return {"enabled": bool(result.get("enabled")), "effective_at": "supervisor_controller"}


def main() -> int:
    try:
        command = json.loads(sys.stdin.read(8192))
        if not isinstance(command, dict):
            raise ControlRejected("invalid request")
        outcome = dispatch(command)
        print(json.dumps({"ok": True, "outcome": outcome}, ensure_ascii=False))
        return 0
    except PreservationPreflightRejected:
        # A narrow, typed SDK guard known to occur before any state mutation.
        print(json.dumps({"ok": False, "error": "preservation_preflight_rejected",
                          "confirmed_no_effect": True}, ensure_ascii=False))
        return 1
    except (ControlRejected, ValueError, OSError, RuntimeError) as error:
        print(json.dumps({"ok": False, "error": type(error).__name__}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
