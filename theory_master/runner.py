"""One headless TheoryMaster Workflow attempt for the supervisor or public --foreground CLI."""
from __future__ import annotations
import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from .backends import CodexBackend, parse_role_spec, resolve_routes
from .route_preflight import model_route_preflight_lines
from .observability.collector import Collector
from .specification import TaskSpec
from .task import TaskBinding
from .workflow import Workflow


def _flag(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _require_input_support_takeover(spec: TaskSpec, expected_cycle: str) -> None:
    from .runtime_manager import load_registry, process_identity_alive
    from .supervisor_activation import input_support_target, latest_recovery
    from .supervisor_intervention import supervisor_enabled

    root = spec.research_root
    registry = load_registry(root)
    receipt = registry.get("input_support_attempt")
    supervisor_pid = registry.get("supervisor_pid")
    target = latest_recovery(root) if supervisor_enabled(root) else None
    if (not target or target[0].name != expected_cycle or target[2] != "input_support"
            or registry.get("requested_operation") != "takeover"
            or registry.get("takeover_cycle") != expected_cycle
            or registry.get("current_cycle") != expected_cycle
            or not registry.get("launch_id")
            or supervisor_pid != os.getppid()
            or not process_identity_alive(supervisor_pid, registry.get("supervisor_start_ticks"))
            or not isinstance(receipt, dict)
            or receipt.get("status") != "launching"
            or receipt.get("launch_id") != registry["launch_id"]
            or receipt.get("stopped_cycle") != expected_cycle):
        raise ValueError("Input-support recovery has no matching managed Supervisor")
    plan = input_support_target(target[0], target[1], registry=registry)
    if (not plan or plan["route"] != "input_support"
            or not all(plan.get(key) and plan[key] == receipt.get(key)
                       for key in ("dependency_identity", "wake_identity"))):
        raise ValueError("Verified input support changed before the new research Cycle")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--operation", choices=["run", "resume", "preserve", "close-preserved", "reassess"], required=True)
    parser.add_argument("--identifier", default="0")
    parser.add_argument("--approve-preservation", action="store_true", help="explicit Operator authorization; preserve uses one Investigator model Stage, close-preserved never calls a model")
    parser.add_argument("--model"); parser.add_argument("--provider"); parser.add_argument("--route", action="append", default=[])
    parser.add_argument("--cycle-audit", dest="cycle_audit", action="store_true"); parser.add_argument("--no-cycle-audit", dest="cycle_audit", action="store_false")
    parser.add_argument("--error-repair", dest="error_repair", action="store_true"); parser.add_argument("--no-error-repair", dest="error_repair", action="store_false")
    parser.add_argument("--attention-takeover", dest="attention_takeover", action="store_true"); parser.add_argument("--no-attention-takeover", dest="attention_takeover", action="store_false")
    parser.add_argument("--check-literature", action="store_true"); parser.set_defaults(cycle_audit=None, error_repair=None, attention_takeover=None)
    parser.add_argument("--stop-before", choices=["hypothesis_lock", "investigator", "judge"])
    parser.add_argument("--managed-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--supervisor-takeover-cycle", help=argparse.SUPPRESS)
    parser.add_argument("--retained-quota", help=argparse.SUPPRESS)
    parser.add_argument("--boot-resume-launch", help=argparse.SUPPRESS)
    parser.add_argument("--goal-retry-launch", help=argparse.SUPPRESS)
    parser.add_argument("--runtime-repair-resume-launch", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    boot_operation = (args.operation == "resume" and args.supervisor_takeover_cycle is None
                      or args.operation == "reassess" and args.supervisor_takeover_cycle is not None)
    if args.boot_resume_launch and (not args.managed_child or not boot_operation or args.retained_quota is not None
            or args.stop_before):
        parser.error("A boot Resume requires an exact managed Supervisor launch")
    if args.goal_retry_launch and (not args.managed_child or args.operation != "reassess"
            or args.boot_resume_launch or args.retained_quota is not None or args.stop_before):
        parser.error("A Goal retry requires an exact managed reassessment launch")
    if args.runtime_repair_resume_launch is not None and (not args.runtime_repair_resume_launch
            or not args.managed_child or args.operation != "resume" or not args.supervisor_takeover_cycle
            or args.boot_resume_launch is not None or args.goal_retry_launch is not None
            or args.retained_quota is not None or args.stop_before):
        parser.error("A technical repair Resume requires an exact managed Supervisor launch")
    if args.supervisor_takeover_cycle is not None and not args.managed_child:
        parser.error("Supervisor takeover is a managed SDK launch only")
    if args.operation == "reassess" and args.supervisor_takeover_cycle is None:
        parser.error("Delivery-only Goal reassessment requires managed Supervisor takeover")
    budget_preservation = args.operation == "preserve" and args.supervisor_takeover_cycle is not None
    if args.operation in {"preserve", "close-preserved"} and not (budget_preservation or args.approve_preservation):
        parser.error("preservation/closure requires --approve-preservation; Native blocked is not permission to abandon research")
    binding = TaskBinding.load(args.task_root)
    spec = TaskSpec.from_task_root(binding.task_root)
    if args.runtime_repair_resume_launch or args.boot_resume_launch:
        from .supervisor_runtime_resume import child_resume_lock, launch_options

        options = launch_options(model=args.model, provider=args.provider, routes=args.route,
                                 cycle_audit=args.cycle_audit, error_repair=args.error_repair,
                                 attention_takeover=args.attention_takeover, check_literature=args.check_literature)
    if args.runtime_repair_resume_launch:
        from .supervisor_runtime_resume import verify_child

        with child_resume_lock(spec.research_root):
            repair = verify_child(spec.research_root, args.runtime_repair_resume_launch, args.supervisor_takeover_cycle)
            if repair is None or repair["authorization"].get("options") != options:
                parser.error("The technical repair Resume authorization or launch options changed")
    retained = None
    boot_native_goal = None
    boot_delivery_pin = None
    boot_runtime_repair = False
    if args.boot_resume_launch:
        from .supervisor_boot_recovery import RUNTIME_REPAIR_ACTIVE_RESUME, verify_boot_resume

        with child_resume_lock(spec.research_root):
            ticket = verify_boot_resume(spec.research_root, args.boot_resume_launch, managed_child=True)
        expected_cycle = args.supervisor_takeover_cycle if args.operation == "reassess" else args.identifier
        expected_kinds = {"goal_reassessment"} if args.operation == "reassess" else {None, RUNTIME_REPAIR_ACTIVE_RESUME}
        if ticket is None or ticket["cycle"] != expected_cycle or ticket.get("kind") not in expected_kinds:
            parser.error("The interrupted Cycle changed before boot Resume")
        boot_runtime_repair = ticket.get("kind") == RUNTIME_REPAIR_ACTIVE_RESUME
        if boot_runtime_repair:
            if options != ticket["origin"].get("options"):
                parser.error("The technical repair continuation launch options changed")
        elif args.check_literature or args.model or args.provider or args.route:
            parser.error("A boot Resume requires an exact managed Supervisor launch")
        if args.operation == "reassess":
            boot_delivery_pin = ticket["origin"].get("recovery_pin")
            if boot_delivery_pin is None:
                parser.error("The interrupted Goal reassessment has no safe Judge continuation")
        boot_native_goal = ticket["origin"].get("native_goal")
        if boot_native_goal is not None:
            from .retained_goal_quota import prepare_boot_goal_home

            try:
                retained = prepare_boot_goal_home(spec.research_root, boot_native_goal["account_binding"])
            except (OSError, ValueError, RuntimeError) as error:
                parser.error(f"Boot Resume cannot restore the original Native Goal account ({type(error).__name__})")
    if args.retained_quota is not None:
        if not args.managed_child or args.operation != "resume":
            parser.error("Retained quota recovery requires a managed Resume")
        from .retained_goal_quota import verify_retained_quota_launch
        try:
            approval = json.loads(args.retained_quota)
            if not isinstance(approval, dict):
                raise ValueError("Malformed retained quota approval")
            retained = verify_retained_quota_launch(spec.research_root, approval, managed_child=True)
        except (OSError, ValueError, TypeError) as error:
            parser.error(f"Retained quota recovery could not verify its original Goal and credential ({type(error).__name__})")
        args.identifier = approval["cycle"]
    task_env_file = spec.research_root / ".theory-master" / "env"
    routes = resolve_routes(model=args.model, provider=args.provider, role_specs=args.route, task_env_file=task_env_file if task_env_file.is_file() else None, task_route_override_root=spec.research_root)
    for line in model_route_preflight_lines(routes, task_env_file=task_env_file):
        print(line, file=sys.stderr)
    cycle_audit = args.cycle_audit if args.cycle_audit is not None else _flag(os.environ.get("THEORY_MASTER_CYCLE_AUDIT"), False)
    from .supervisor_intervention import supervisor_enabled
    supervisor_on = supervisor_enabled(spec.research_root)
    error_repair = args.error_repair if args.error_repair is not None else supervisor_on
    attention_takeover = supervisor_on if args.attention_takeover is None else bool(args.attention_takeover)
    collector = Collector(
        spec,
        system_failure_actions=bool(error_repair),
        email_notifications=True,
        cycle_audit_actions=bool(cycle_audit),
        attention_takeover_actions=attention_takeover,
    )
    if args.check_literature and args.operation != "reassess":
        from .__main__ import literature_startup_check
        literature_startup_check(routes)
    def routes_at_cycle_boundary():
        return resolve_routes(
            model=args.model, provider=args.provider, role_specs=args.route,
            task_env_file=task_env_file if task_env_file.is_file() else None,
            task_route_override_root=spec.research_root,
        )

    workflow = Workflow(
        spec, CodexBackend(spec.research_root, routes=routes,
                           **({"retained_goal_home": retained} if retained is not None else {}),
                           **({"boot_native_goal": boot_native_goal} if boot_native_goal is not None else {})), routes,
        collector=collector, stop_before=args.stop_before,
        route_resolver=routes_at_cycle_boundary,
        resume_route_overrides=(
            dict(parse_role_spec(item) for item in args.route)
            if args.operation == "resume" else {}
        ),
        explicit_cli_route_roles=(set(routes) if args.model else
                                  {parse_role_spec(item)[0] for item in args.route}),
    )
    try:
        # This must happen before Workflow allocates or resumes a Cycle.  The
        # backend normally imports the provider lazily at ensure_thread(), but
        # managed tm run/resume must fail before persisting an active Cycle when
        # its SDK or configured provider route cannot start.
        takeover_target = None
        if args.supervisor_takeover_cycle is not None and not args.runtime_repair_resume_launch:
            from .supervisor_activation import latest_recovery, takeover_runner_operation
            takeover_target = latest_recovery(spec.research_root) if supervisor_enabled(spec.research_root) else None
            if (takeover_target is None or takeover_target[0].name != args.supervisor_takeover_cycle
                    or args.operation != takeover_runner_operation(takeover_target[2])):
                raise ValueError("The stopped Cycle changed before Supervisor takeover")
        if boot_native_goal is not None:
            # The original thread is the first protected provider operation.
            # Normal preflight would create unrelated disposable threads.
            workflow.backend._verify_provider_runtime()
        # Technical repair Resume first claims its existing authorization in Workflow.
        elif (not (args.runtime_repair_resume_launch or boot_runtime_repair)
              and args.operation not in {"close-preserved", "reassess"} and not budget_preservation):
            workflow.backend.preflight_runtime()
        def execute():
            if budget_preservation:
                from .budget_auto_preservation import run_single_budget_preservation
                from .runtime_manager import load_registry

                registry = load_registry(spec.research_root)
                result = run_single_budget_preservation(
                    workflow, takeover_target[0], launch_id=registry.get("launch_id"))
                print("sdk_budget_preservation:", result)
            elif args.operation == "preserve":
                result = workflow.preserve_interrupted_cycle(args.identifier, authorization="operator_approved")
                print("preserve_research:", result)
            elif args.operation == "close-preserved":
                result = workflow.close_preserved_cycle(args.identifier, authorization="operator_approved")
                print("preservation_closure:", result)
            elif args.operation == "reassess":
                from .runtime_manager import load_registry, process_identity_alive
                from .supervisor_goal_reassessment import (delivery_reassessment_plan, reassessment_launch_identity,
                                                           run_goal_reassessment)

                registry = load_registry(spec.research_root)
                target_cycle = args.supervisor_takeover_cycle
                supervisor_pid = registry.get("supervisor_pid")
                if (registry.get("requested_operation") != "takeover"
                        or registry.get("takeover_cycle") != target_cycle
                        or registry.get("current_cycle") != target_cycle
                        or supervisor_pid != os.getppid()
                        or not process_identity_alive(supervisor_pid, registry.get("supervisor_start_ticks"))):
                    raise ValueError("Delivery-only reassessment has no matching managed Supervisor")
                plan = delivery_reassessment_plan(spec)
                launch_id = registry.get("launch_id")
                if (not plan or plan["stopped_cycle"] != target_cycle or not launch_id
                        or registry.get("delivery_reassessment_attempt") != {
                            **reassessment_launch_identity(plan, launch_id), "status": "launching",
                        }):
                    raise ValueError("Verified delivery changed before Goal reassessment")
                recovery_pin = boot_delivery_pin
                if args.goal_retry_launch:
                    from .supervisor_reassessment_retry import verify_goal_retry

                    retry = verify_goal_retry(spec.research_root, args.goal_retry_launch, managed_child=True)
                    if retry is None:
                        raise ValueError("The interrupted Goal Judge checkpoint changed before retry")
                    recovery_pin = retry["recovery_pin"]
                else:
                    retry = registry.get("goal_reassessment_retry")
                    if isinstance(retry, dict) and retry.get("attempt") == registry["delivery_reassessment_attempt"]:
                        raise ValueError("The interrupted Goal Judge requires its managed retry checkpoint")
                result = run_goal_reassessment(workflow, plan, watch_stopped=True, recovery_pin=recovery_pin)
                if result is None:
                    raise ValueError("Goal reassessment lost its verified delivery authority")
                print("sdk_goal_reassessment:", result)
            elif args.operation == "resume":
                from .supervisor_qualification_recovery import finish_planned_delivery

                if args.runtime_repair_resume_launch:
                    result = workflow.resume(args.supervisor_takeover_cycle,
                                             runtime_repair_resume_launch=args.runtime_repair_resume_launch)
                elif args.boot_resume_launch:
                    result = workflow.resume(args.identifier, boot_resume_launch=args.boot_resume_launch)
                elif args.supervisor_takeover_cycle is not None or retained is not None:
                    result = workflow.resume(args.identifier, supervisor_takeover=True)
                else:
                    result = workflow.resume(args.identifier)
                finish_planned_delivery(workflow, result)
            else:
                from .supervisor_qualification_recovery import finish_planned_delivery

                if takeover_target and takeover_target[2] == "input_support":
                    _require_input_support_takeover(spec, args.supervisor_takeover_cycle)
                elif args.supervisor_takeover_cycle is not None:
                    from .runtime_manager import load_registry

                    registry = load_registry(spec.research_root)
                    if (registry.get("requested_operation") == "takeover"
                            and registry.get("takeover_cycle") == args.supervisor_takeover_cycle
                            and registry.get("current_cycle") == args.supervisor_takeover_cycle):
                        from .supervisor_activation import latest_recovery
                        from .supervisor_goal_reassessment import goal_reassessment_plan, run_goal_reassessment

                        target = latest_recovery(spec.research_root)
                        if target and finish_planned_delivery(workflow, target[1]):
                            return
                        plan = goal_reassessment_plan(workflow)
                        if plan and (not spec.submission_contract_ref or plan["committed_outcomes"]):
                            result = run_goal_reassessment(workflow, plan)
                            if result and result["assessment"].get("verdict") != "not_passed":
                                return
                finish_planned_delivery(workflow, workflow.run())
        if args.managed_child:
            execute()
        else:
            from .runtime_manager import foreground_runtime
            with foreground_runtime(spec.research_root, master_id=int(getattr(spec, "master_id", 0) or 0), operation=("resume" if args.operation in {"preserve", "close-preserved"} else args.operation)):
                execute()
    except BaseException:
        traceback.print_exc()
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
