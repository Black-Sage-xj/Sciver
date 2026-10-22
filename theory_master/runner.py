"""One headless TheoryMaster Workflow attempt for the supervisor or public --foreground CLI."""
from __future__ import annotations
import argparse
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--operation", choices=["run", "resume", "preserve", "close-preserved"], required=True)
    parser.add_argument("--identifier", default="0")
    parser.add_argument("--approve-preservation", action="store_true", help="explicit Operator authorization; preserve uses one Investigator model Stage, close-preserved never calls a model")
    parser.add_argument("--model"); parser.add_argument("--provider"); parser.add_argument("--route", action="append", default=[])
    parser.add_argument("--cycle-audit", dest="cycle_audit", action="store_true"); parser.add_argument("--no-cycle-audit", dest="cycle_audit", action="store_false")
    parser.add_argument("--error-repair", dest="error_repair", action="store_true"); parser.add_argument("--no-error-repair", dest="error_repair", action="store_false")
    parser.add_argument("--attention-takeover", dest="attention_takeover", action="store_true"); parser.add_argument("--no-attention-takeover", dest="attention_takeover", action="store_false")
    parser.add_argument("--check-literature", action="store_true"); parser.set_defaults(cycle_audit=None, error_repair=None, attention_takeover=None)
    parser.add_argument("--stop-before", choices=["hypothesis_lock", "judge"])
    parser.add_argument("--managed-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    binding = TaskBinding.load(args.task_root)
    spec = TaskSpec.from_task_root(binding.task_root)
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
    if args.check_literature:
        from .__main__ import literature_startup_check
        literature_startup_check(routes)
    def routes_at_cycle_boundary():
        return resolve_routes(
            model=args.model, provider=args.provider, role_specs=args.route,
            task_env_file=task_env_file if task_env_file.is_file() else None,
            task_route_override_root=spec.research_root,
        )

    workflow = Workflow(
        spec, CodexBackend(spec.research_root, routes=routes), routes,
        collector=collector, stop_before=args.stop_before,
        route_resolver=routes_at_cycle_boundary,
        resume_route_overrides=(
            dict(parse_role_spec(item) for item in args.route)
            if args.operation == "resume" else {}
        ),
        explicit_cli_route_roles=(set(routes) if args.model else
                                  {parse_role_spec(item)[0] for item in args.route}),
    )
    if args.operation in {"preserve", "close-preserved"} and not args.approve_preservation:
        parser.error("preservation/closure requires --approve-preservation; Native blocked is not permission to abandon research")
    try:
        # This must happen before Workflow allocates or resumes a Cycle.  The
        # backend normally imports the provider lazily at ensure_thread(), but
        # managed tm run/resume must fail before persisting an active Cycle when
        # its SDK or configured provider route cannot start.
        if args.operation != "close-preserved":
            workflow.backend.preflight_runtime()
        def execute():
            if args.operation == "preserve":
                result = workflow.preserve_interrupted_cycle(args.identifier, authorization="operator_approved")
                print("preserve_research:", result)
            elif args.operation == "close-preserved":
                result = workflow.close_preserved_cycle(args.identifier, authorization="operator_approved")
                print("preservation_closure:", result)
            elif args.operation == "resume":
                workflow.resume(args.identifier)
            else:
                workflow.run()
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
