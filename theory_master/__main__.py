from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import tempfile
from datetime import datetime
from pathlib import Path

from .core import CanonicalFakeBackend, CodexBackend, DEFAULT_ROUTES, FakeBackend, MODEL_PROFILES, MODEL_PROFILE_DIR, MODEL_PROVIDER_MAP, TaskSpec, Workflow, direction_text, latest_cycle_dir, migration_preview, read_model_profile, read_model_provider, resolve_routes, write_json, write_model_profile, write_model_provider
from .backends import MODEL_PROFILE_LEVELS
from .scientific_model import load as load_scientific_model
from .identity import ROLES, identity_block
from .skills import role_skills
from .observability.pricing import load_pricing_cache, pricing_cache_path, refresh_pricing
from .observability.costs import backfill_research_costs
from .load_snapshots import create_load_snapshot
from .codex_accounts import account_status, ensure_layout, login_account, use_account
from .task import TaskBinding, bootstrap, load_discovered, summary as task_summary
from .regression_cases import RegressionCaseError, list_cases, materialize_case
from .cli_discovery import (
    default_search_root, enter_task, print_runs, resumable_runs,
    spec_from_resume_identifier, spec_from_task, spec_from_tui_identifier,
)
from .cli_runtime import (
    _collector, _env_flag, _schedule_close_current_tmux_window, attach_tui,
    literature_startup_check, print_inspect, print_status, project_control,
    run_with_tui, smoke, validate_roles,
)






































def print_active_model_config() -> None:
    profile = read_model_profile()
    provider = read_model_provider()
    routes = resolve_routes(profile=profile, env={"THEORYMASTER_MODEL_PROVIDER": provider})
    profile_path = MODEL_PROFILE_DIR / f"{profile}.toml"
    level = MODEL_PROFILE_LEVELS.get(profile)
    print(f"active tier:    {level} ({profile})" if level is not None else f"active profile: {profile}")
    print(f"profile file:   {profile_path}")
    print(f"provider override: {provider}")
    if profile == "codex" and provider == "codex":
        from .account_model_routing import routes_for_account
        from .codex_accounts import selected_account
        account = selected_account()
        print(f"selected Codex account: {account or '(unknown)'} (next-Cycle effective tier)")
        routes = routes_for_account(routes, account)
    for role, route in routes.items():
        effort = route.reasoning_effort or "-"
        print(f"{role:<14} {route.model:<16} {route.provider:<11} effort={effort}")


def model_profile_command(action: str) -> int:
    if action != "status":
        path = write_model_profile(action)
        profile = read_model_profile()
        write_model_provider("codex")
        level = MODEL_PROFILE_LEVELS.get(profile)
        label = f"{level} ({profile})" if level is not None else profile
        print(f"model profile: {label} ({path})")
    print_active_model_config()
    return 0


def model_provider_command(action: str) -> int:
    if action != "status":
        path = write_model_provider(action)
        print(f"model provider: {action} ({path})")
    print_active_model_config()
    return 0


def codex_account_command(action: str) -> int:
    if action == "init":
        ensure_layout()
    elif action in {"1", "2"}:
        # Global CLI selection must not restart a daemon that might be
        # hosting unrelated Desktop/Worker traffic. Web explicitly manages
        # verified Desktop proxy disconnection when requested.
        path = use_account(action, restart_app=False)
        print(f"Codex default account: {action} ({path}); shared selector only, Desktop reconnect if needed")
    elif action in {"login-1", "login-2"}:
        number = action[-1]
        path = ensure_layout()[number]
        print(f"Starting device-code login for Codex account {number}: {path}")
        return login_account(number)

    status = account_status()
    print(f"selected account: {status['selected'] or 'unknown'}")
    print(f"selector:         {status['selector']}")
    print(f"TM runtime:       {status['runtime_home']}")
    override = status.get("theorymaster_codex_home_override")
    if override:
        print(f"TM home override: {override}")
    ambient = status.get("ambient_codex_home")
    if ambient:
        print(f"ambient CODEX_HOME ignored by TM: {ambient}")
    for number, info in status["accounts"].items():
        marker = "*" if number == status["selected"] else " "
        auth = "logged in" if info["auth_present"] else "not logged in"
        print(f"{marker} account {number}: {auth}  {info['path']}")
    return 0


def pricing_command(action: str, research_root: Path | None = None) -> int:
    if action == "backfill":
        snapshot, _ = refresh_pricing(force=False)
        if research_root is None:
            research_root = spec_from_task().research_root
        result = backfill_research_costs(research_root)
        print(f"research root:   {result['research_root']}")
        print(f"changed records: {result['changed_records']}")
        for item in result["cycles"]:
            if int(item.get("changed_records") or 0) <= 0:
                continue
            cost = item.get("cost") or {}
            amount = cost.get("amount")
            rendered = f"~${float(amount):.4f}" if amount is not None else "—"
            print(f"{Path(item['path']).parent.name:<12} {int(item['changed_records']):>3} records  {rendered}")
        return 0
    if action == "refresh":
        snapshot, changed = refresh_pricing(force=True)
    else:
        snapshot, changed = refresh_pricing(force=False)
    print(f"pricing cache: {pricing_cache_path()}")
    print(f"source:        {snapshot.get('source_url')}")
    print(f"fetched at:    {snapshot.get('fetched_at')}")
    print(f"tier:          {snapshot.get('pricing_tier')} / {snapshot.get('currency')} per 1M tokens")
    print(f"refreshed:     {'yes' if changed else 'no (cache still fresh)'}")
    for model in sorted((snapshot.get("models") or {})):
        entry = snapshot["models"][model]
        short = entry.get("short_context") or {}
        long = entry.get("long_context") or {}
        line = f"{model:<16} short input={short.get('input')} cached={short.get('cached_input')} write={short.get('cache_write')} output={short.get('output')}"
        if long:
            line += f" | long input={long.get('input')} cached={long.get('cached_input')} write={long.get('cache_write')} output={long.get('output')}"
        print(line)
    return 0


def _refresh_pricing_daily() -> None:
    try:
        _, changed = refresh_pricing(force=False)
        if changed:
            print(f"TheoryMaster pricing refreshed: {pricing_cache_path()}")
    except Exception as exc:
        # Pricing is observability, not a scientific execution dependency. A
        # stale/missing rate card must never prevent the workflow from running.
        print(f"TheoryMaster pricing refresh warning: {exc}", file=sys.stderr)


def _interactive_terminal() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def cycle_command(args) -> int:
    """Inspect Cycle provenance or execute a verified checkpoint in isolation."""
    from .specification import list_cycle_dirs, cycle_number
    from .cli_discovery import spec_from_task
    try:
        spec = spec_from_task(master_id=args.master_id)
        cycles = [p for p in list_cycle_dirs(spec.research_root, task_mode=spec.task_mode)
                  if cycle_number(p.name) == int(args.cycle_number)]
        if len(cycles) != 1:
            raise ValueError(f"logical Cycle {args.cycle_number} resolves to {len(cycles)} attempts; refusing ambiguous selection")
        cycle = cycles[0]
        if args.cycle_action == "rerun":
            from .cycle_replay import replay_cycle
            outcome = replay_cycle(spec, cycle, args.from_stage, dry_run=bool(getattr(args, "dry_run", False)))
            print(json.dumps(outcome, ensure_ascii=False, indent=2))
            return 0
        if args.cycle_action == "checkpoints":
            root = cycle / "replay_checkpoints"
            records = sorted(root.glob("*.json")) if root.is_dir() else []
            if not records:
                print(f"Cycle {args.cycle_number}: no verified replay checkpoints (legacy snapshots are not reconstructed)")
            for path in records:
                payload = json.loads(path.read_text(encoding="utf-8"))
                print(f"Cycle {args.cycle_number} attempt={payload.get('attempt_id')} checkpoint={payload.get('name')} boundary={payload.get('boundary')} sha256={payload.get('state_sha256')}")
            return 0
        replay_root = spec.research_root / ".cycle-replays" / str(args.cycle_number)
        manifests = sorted(replay_root.glob("*/manifest.json")) if replay_root.is_dir() else []
        for manifest_path in manifests:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            print(f"Replay attempt={payload.get('attempt_id')} source_attempt={payload.get('source_attempt_id')} stage={payload.get('stage')} status={payload.get('status')} path={manifest_path.parent}")
        state_path = cycle / "research_state.json"
        if not state_path.is_file():
            raise ValueError(f"Cycle attempt has no research_state.json: {cycle}")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        attempts = state.get("investigation_attempts") or {}
        if not attempts:
            print(f"Cycle {args.cycle_number} attempt={state.get('attempt_id') or cycle.name}: no persisted Investigator attempts")
        for key, item in sorted(attempts.items()):
            print(f"Cycle {args.cycle_number} attempt={state.get('attempt_id') or cycle.name} investigation={key} executor={item.get('executor')} status={item.get('status')} outputs={len(item.get('outputs') or [])}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"TheoryMaster cycle: {exc}", file=sys.stderr)
        return 2


def _attach_managed_tui(spec, *, health_wait_s: float = 2.0) -> dict:
    from theory_master.runtime_manager import reconnect_tui, runtime_health

    if not _interactive_terminal():
        print("TheoryMaster TUI: skipped automatic attach in a non-interactive terminal; use `theory_master tui` to reconnect.")
        return {"status": "skipped", "reason": "non_interactive"}
    try:
        return reconnect_tui(spec, focus=True)
    except RuntimeError as exc:
        deadline = time.monotonic() + max(0.0, float(health_wait_s))
        health = {}
        while True:
            health = runtime_health(Path(spec.research_root))
            if health.get("active"):
                print(
                    f"TheoryMaster TUI warning: runtime is running but TUI attach failed: {exc}",
                    file=sys.stderr,
                )
                return {"status": "attach_failed", "runtime_status": "running", "error": str(exc)}
            if str(health.get("state") or "").upper() in {"FAILED", "ORPHANED"}:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        raise


def main() -> int:
    if len(sys.argv) == 1:
        try:
            return enter_task()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"TheoryMaster: {exc}", file=sys.stderr)
            return 2
    parser = argparse.ArgumentParser(prog="sciver"); sub = parser.add_subparsers(dest="command", required=True)
    from .onboarding import default_workspace
    for name in ("quickstart", "web"):
        command = sub.add_parser(name, help="prepare and verify Test0, then open Web UI" if name == "quickstart" else "start and open the local Web UI")
        command.add_argument("--workspace", type=Path, default=default_workspace())
        command.add_argument("--port", type=int, default=8787)
        command.add_argument("--no-open", action="store_true", help="print the URL without opening a browser")
        if name == "quickstart":
            command.add_argument("--no-web", action="store_true", help="only initialize and verify Test0, then exit")
    for name in ("validate", "initialize", "cancel", "project"):
        item = sub.add_parser(name); item.add_argument("master_id", nargs="?", default=None)
        if name == "cancel": item.add_argument("--reason", required=True)
    cycle_p = sub.add_parser("cycle", help="inspect verified Cycle checkpoints and isolated replay attempts")
    cycle_sub = cycle_p.add_subparsers(dest="cycle_action", required=True)
    for action in ("checkpoints", "attempts"):
        cp = cycle_sub.add_parser(action)
        cp.add_argument("master_id")
        cp.add_argument("cycle_number", type=int)
    rerun_p = cycle_sub.add_parser("rerun")
    rerun_p.add_argument("master_id")
    rerun_p.add_argument("cycle_number", type=int)
    rerun_p.add_argument("--from", dest="from_stage", choices=["start", "investigator", "judge"], required=True)
    rerun_p.add_argument("--dry-run", action="store_true", help="validate checkpoint and isolation preflight without creating an attempt")
    for name in ("pause", "interrupt"):
        item = sub.add_parser(name); item.add_argument("master_id", nargs="?", default=None); item.add_argument("--reason", default="operator request")
    kill_p = sub.add_parser("kill")
    kill_p.add_argument("master_id", nargs="?", default=None); kill_p.add_argument("--reason", default="operator request")
    kill_p.add_argument("--role"); kill_p.add_argument("--thread-id")
    resume_p = sub.add_parser("resume")
    resume_p.add_argument("identifier", nargs="?", default=None, help="Task ID or Cycle ID; defaults to the current task")
    resume_p.add_argument("--email-terminal", action="store_true", help="send a 163 email only if the resumed run fails unexpectedly")
    resume_p.add_argument("--check-literature", action="store_true", help="opt-in startup check: run a tiny live tm-scholar retrieval before resuming Sciver")
    resume_p.add_argument("--model", help="all-role model alias or MODEL@PROVIDER, e.g. gpt-5.6-luna")
    resume_p.add_argument("--provider", help="provider for --model when the model is not a known alias")
    resume_p.add_argument(
        "--route",
        action="append",
        default=[],
        help="per-role overlay ROLE=MODEL or ROLE=MODEL@PROVIDER; repeatable",
    )
    resume_p.add_argument("--foreground", action="store_true", help="run task-mode Workflow headlessly in the current process instead of the managed supervisor")
    resume_p.add_argument("--stop-before-hypothesis-lock", action="store_true", help="test checkpoint: stop after candidate selection and before Hypothesis Lock")
    resume_p.add_argument("--stop-before-judge", action="store_true", help="test checkpoint: stop after Investigation and before Judge")
    resume_audit = resume_p.add_mutually_exclusive_group()
    resume_audit.add_argument("--cycle-audit", dest="cycle_audit", action="store_true", help="enable the opt-in fresh Codex audit after each terminal Cycle")
    resume_audit.add_argument("--no-cycle-audit", dest="cycle_audit", action="store_false", help="disable post-Cycle Codex audit for this invocation")
    resume_p.set_defaults(cycle_audit=None)
    resume_repair = resume_p.add_mutually_exclusive_group()
    resume_repair.add_argument("--error-repair", dest="error_repair", action="store_true", help="enable automatic fresh-Codex repair handoff on system failure")
    resume_repair.add_argument("--no-error-repair", dest="error_repair", action="store_false", help="disable automatic Codex repair handoff for this invocation")
    resume_p.set_defaults(error_repair=None)
    resume_attention = resume_p.add_mutually_exclusive_group()
    resume_attention.add_argument("--attention-takeover", dest="attention_takeover", action="store_true", help="enable fresh-Codex takeover when the resumed Cycle stops at NEEDS_ATTENTION")
    resume_attention.add_argument("--no-attention-takeover", dest="attention_takeover", action="store_false", help="disable NEEDS_ATTENTION AI takeover for this invocation")
    resume_p.set_defaults(attention_takeover=None)
    run_p = sub.add_parser("run")
    run_p.add_argument("master_id", nargs="?", default=None, help="Task ID; defaults to the current task")
    run_p.add_argument(
        "--email-terminal",
        action="store_true",
        help="send a 163 email only if the run fails unexpectedly",
    )
    run_p.add_argument("--model", help="all-role model alias or MODEL@PROVIDER, e.g. gpt-5.6-luna")
    run_p.add_argument("--provider", help="provider for --model when the model is not a known alias")
    run_p.add_argument("--check-literature", action="store_true", help="opt-in startup check: run a tiny live tm-scholar retrieval before starting Sciver")
    run_p.add_argument("--foreground", action="store_true", help="run task-mode Workflow headlessly in the current process instead of the managed supervisor")
    run_p.add_argument("--stop-before-hypothesis-lock", action="store_true", help="test checkpoint: stop after candidate selection and before Hypothesis Lock")
    run_p.add_argument("--stop-before-judge", action="store_true", help="test checkpoint: stop after Investigation and before Judge")
    run_audit = run_p.add_mutually_exclusive_group()
    run_audit.add_argument("--cycle-audit", dest="cycle_audit", action="store_true", help="enable the opt-in fresh Codex audit after each terminal Cycle")
    run_audit.add_argument("--no-cycle-audit", dest="cycle_audit", action="store_false", help="disable post-Cycle Codex audit for this invocation")
    run_p.set_defaults(cycle_audit=None)
    run_repair = run_p.add_mutually_exclusive_group()
    run_repair.add_argument("--error-repair", dest="error_repair", action="store_true", help="enable automatic fresh-Codex repair handoff on system failure")
    run_repair.add_argument("--no-error-repair", dest="error_repair", action="store_false", help="disable automatic Codex repair handoff for this invocation")
    run_p.set_defaults(error_repair=None)
    run_attention = run_p.add_mutually_exclusive_group()
    run_attention.add_argument("--attention-takeover", dest="attention_takeover", action="store_true", help="enable fresh-Codex takeover when a Cycle stops at NEEDS_ATTENTION")
    run_attention.add_argument("--no-attention-takeover", dest="attention_takeover", action="store_false", help="disable NEEDS_ATTENTION AI takeover for this invocation")
    run_p.set_defaults(attention_takeover=None)
    run_p.add_argument(
        "--route",
        action="append",
        default=[],
        help="per-role overlay ROLE=MODEL or ROLE=MODEL@PROVIDER; repeatable",
    )
    tui_p = sub.add_parser("tui")
    tui_p.add_argument("master_id", nargs="?", default=None, help="Task ID or Cycle ID; defaults to the task in the current directory")
    tui_p.add_argument("--root", type=Path, help="repository/search root for the supplied Task ID or Cycle ID")
    tui_p.add_argument("--demo", action="store_true", help="offline fixture only; normal tui attaches to the real Workflow control plane")
    tui_p.add_argument("--direct", action="store_true", help=argparse.SUPPRESS)
    for name in ("status", "inspect"):
        item = sub.add_parser(name); item.add_argument("master_id", nargs="?", default=None)
    item = sub.add_parser("smoke"); item.add_argument("--live", action="store_true")
    item = sub.add_parser("new", help="initialize a task from one research_plan.md path")
    item.add_argument("research_plan", type=Path)
    test_p = sub.add_parser("test", help="instantiate a fresh canonical Sciver regression fixture")
    test_p.add_argument("case", nargs="?", help="fixture selector such as Test0")
    test_p.add_argument("destination", type=Path, nargs="?", help="empty/new directory for the runnable TM task")
    test_p.add_argument("--open", action="store_true", help="start and open the local Web UI after task creation")
    test_p.add_argument("--port", type=int, default=8787)
    test_p.add_argument("--list", action="store_true", help="list available canonical test fixtures")
    worker_p = sub.add_parser("workers", help="dynamic parallel research Workers; launch requires explicit start")
    worker_p.add_argument("master_id", nargs="?", default=None)
    worker_p.add_argument("action", nargs="?", choices=["status", "plan", "start", "stop", "tick", "pause", "resume", "release"], default="status")
    worker_p.add_argument("--resource-slots", type=int, default=None,
                          help="verified total independent Codex/compute slots; not a fixed Agent count")
    worker_p.add_argument("--preferred-hypothesis", help="ignored; historical Preferred no longer affects scheduling (I-12)")
    worker_p.add_argument("--worker", help="exact worker-N for pause/resume/release")
    worker_p.add_argument("--account", choices=["1", "2"],
                          help="resume a safely paused Cycle using a different Codex identity")
    worker_p.add_argument("--share-accounts", choices=["on", "off"],
                          help="explicitly enable/disable shared Codex account usage for future Workers")
    item = sub.add_parser("ls", help="list Sciver tasks with persisted run state, including completed tasks")
    item.add_argument("--root", type=Path)
    item = sub.add_parser("migrate"); item.add_argument("--state", required=True, type=Path); item.add_argument("--preview", action="store_true", required=True)
    item = sub.add_parser("reset-research-lead"); item.add_argument("master_id", nargs="?", default=None); item.add_argument("--reason", default="human_reset")
    pricing_p = sub.add_parser("pricing", help="inspect/refresh official prices or backfill historical Codex costs")
    pricing_p.add_argument("action", nargs="?", default="status", choices=["status", "refresh", "backfill"])
    pricing_p.add_argument("--research-root", type=Path, help="research root for pricing backfill; defaults to the discovered Sciver task")
    profile_p = sub.add_parser("model-profile", help="switch Sciver model tier: 1=test, 2=codex, 3=advanced")
    profile_p.add_argument("action", nargs="?", default="status", choices=["status", "1", "2", "3", *sorted(MODEL_PROFILES)])
    provider_p = sub.add_parser("model-provider", help="switch Sciver model provider")
    provider_p.add_argument("action", nargs="?", default="status", choices=["status", *sorted(MODEL_PROVIDER_MAP)])
    account_p = sub.add_parser("codex-account", help="switch Sciver ChatGPT/Codex login identity only")
    account_p.add_argument("action", nargs="?", default="status", choices=["status", "init", "1", "2", "login-1", "login-2"])
    args = parser.parse_args()
    if args.command == "cycle": return cycle_command(args)
    if args.command == "pricing": return pricing_command(args.action, args.research_root)
    if args.command == "model-profile": return model_profile_command(args.action)
    if args.command == "model-provider": return model_provider_command(args.action)
    if args.command == "codex-account": return codex_account_command(args.action)
    if args.command == "workers":
        from .multi_worker import BranchClaims
        from .worker_runtime import worker_action, list_workers, start_scheduler, stop_scheduler, schedule_once, scheduler_status
        try:
            spec = spec_from_task(master_id=args.master_id)
            if spec.branch_canvas is None:
                parser.error("task has no shared Canvas; cannot discover branch claims")
            coordinator = BranchClaims(spec.research_root, spec.branch_canvas.with_name("science_graph.sqlite3"))
            if args.share_accounts is not None:
                from .worker_mode import configure_mode, read_mode
                mode = read_mode(spec.research_root)
                configure_mode(spec.research_root, enabled=mode["enabled"],
                               desired_agents=mode["desired_agents"],
                               share_accounts=args.share_accounts == "on")
            if args.action == "plan":
                outcome = coordinator.plan(available_resource_slots=args.resource_slots)
            elif args.action == "status":
                outcome = {"workers": list_workers(spec.research_root), "claims": coordinator.status(),
                           "scheduler": scheduler_status(spec.research_root)}
            elif args.action in {"start", "tick"}:
                if args.resource_slots is None:
                    parser.error("workers start/tick requires --resource-slots (verified capacity)")
                outcome = (start_scheduler(spec, args.resource_slots, preferred_hypothesis=args.preferred_hypothesis)
                           if args.action == "start" else
                           schedule_once(spec, resource_slots=args.resource_slots,
                                         preferred_hypothesis=args.preferred_hypothesis))
            elif args.action == "stop":
                outcome = stop_scheduler(spec)
            else:
                if not args.worker:
                    parser.error("workers pause/resume/release requires --worker worker-N")
                outcome = worker_action(spec, args.worker, args.action, account=args.account)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0
    if args.command in {"quickstart", "web"}:
        from .onboarding import launch_web, prepare_test0
        try:
            if args.command == "quickstart":
                prepare_test0(args.workspace.expanduser().resolve())
            if not getattr(args, "no_web", False):
                launch_web(args.workspace, port=args.port, open_browser=not args.no_open)
        except KeyboardInterrupt:
            print("\nWeb UI stopped. Your task is saved; reopen with sciver web.")
        except (OSError, ValueError, RuntimeError) as exc:
            parser.error(str(exc))
        return 0
    if args.command == "ls": return print_runs(args.root)
    if args.command == "smoke": return smoke(args.live)
    if args.command == "new": print(task_summary(bootstrap(args.research_plan))); return 0
    if args.command == "test":
        if args.list:
            for case in list_cases():
                print(f"{case.case_id}\taliases={','.join(case.aliases)}\t{case.metadata.get('purpose', '')}")
            return 0
        if not args.case or args.destination is None:
            parser.error("test requires CASE and DESTINATION, e.g. 'sciver test Test0 /path/to/task'")
        try:
            root = materialize_case(args.case, args.destination)
            binding = bootstrap(root / "research_plan.md")
        except (RegressionCaseError, OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
        print(task_summary(binding))
        if args.open:
            from .onboarding import launch_web
            try:
                launch_web(root, port=args.port)
            except KeyboardInterrupt:
                pass
            except (OSError, ValueError) as exc:
                parser.error(str(exc))
        return 0
    if args.command == "migrate": print(json.dumps(migration_preview(args.state), ensure_ascii=False, indent=2)); return 0
    if args.command == "tui":
        if args.demo:
            from theory_master.tui.demo import launch_demo_tui
            return launch_demo_tui()
        try:
            tui_spec = spec_from_tui_identifier(args.master_id, args.root)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
        if args.direct:
            return attach_tui(tui_spec)
        from theory_master.runtime_manager import reconnect_tui
        reconnect_tui(tui_spec, focus=True)
        return 0
    try:
        if args.command == "resume":
            if args.identifier is None:
                spec = spec_from_task()
            else:
                try:
                    spec = spec_from_task(master_id=args.identifier)
                except (OSError, ValueError, json.JSONDecodeError):
                    spec = spec_from_resume_identifier(args.identifier)
        else:
            spec = spec_from_task(master_id=getattr(args, "master_id", None))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if args.command in {"run", "resume", "initialize", "cancel", "reset-research-lead"}:
        create_load_snapshot(spec, reason=args.command)
    if args.command in {"run", "resume"}:
        _refresh_pricing_daily()
        # CLI runs always notify terminal state; an explicit environment
        # override of 0 remains available for maintenance/offline runs.
        if os.environ.get("THEORY_MASTER_EMAIL_TERMINAL") is None:
            os.environ["THEORY_MASTER_EMAIL_TERMINAL"] = "1"
    task_env_file = spec.research_root / ".theory-master" / "env"
    if not task_env_file.is_file():
        task_env_file = None
    try:
        routes = resolve_routes(
            model=getattr(args, "model", None),
            provider=getattr(args, "provider", None),
            role_specs=getattr(args, "route", None),
            task_env_file=task_env_file,
            task_route_override_root=spec.research_root if getattr(spec, "task_mode", False) else None,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.command == "validate":
        roles = validate_roles(spec.config_path)
        print(f"valid: {spec.goal_id} (roles: {len(roles)}, optional skills: {sum(len(items) for items in roles.values())})")
        return 0
    if args.command == "status": return print_status(spec)
    if args.command == "inspect": return print_inspect(spec)
    if args.command == "initialize":
        Workflow(spec, CodexBackend(spec.research_root, routes=routes), routes).initialize(); print("initialized"); return 0
    if args.command == "project": return project_control(spec)
    if args.command == "reset-research-lead":
        result = Workflow(spec, CodexBackend(spec.research_root, routes=routes), routes).reset_research_lead(args.reason)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    if args.command == "cancel":
        Workflow(spec, CodexBackend(spec.research_root, routes=routes), routes).cancel(args.reason); print("cancelled"); return 0
    if args.command in {"pause", "interrupt", "kill"}:
        workflow = Workflow(spec, CodexBackend(spec.research_root, routes=routes), routes)
        state = workflow.request_control(args.command, reason=args.reason, role=getattr(args, "role", None), thread_id=getattr(args, "thread_id", None))
        print(f"{args.command} requested for {state.get('cycle_id') or state.get('cycle_dir')}")
        return 0
    if args.command in {"run", "resume"} and getattr(args, "foreground", False):
        if not getattr(spec, "task_mode", False):
            parser.error("--foreground is supported only for task-mode runs")
        from theory_master.runner import main as foreground_runner
        runner_args = [
            "--task-root", str(Path(spec.research_root).resolve()),
            "--operation", args.command,
            "--identifier", str((args.identifier if args.command == "resume" else args.master_id) or spec.master_id),
        ]
        model = getattr(args, "model", None)
        provider = getattr(args, "provider", None)
        if model:
            runner_args += ["--model", model]
        if provider:
            runner_args += ["--provider", provider]
        for route in getattr(args, "route", None) or []:
            runner_args += ["--route", route]
        if getattr(args, "cycle_audit", None) is True:
            runner_args.append("--cycle-audit")
        elif getattr(args, "cycle_audit", None) is False:
            runner_args.append("--no-cycle-audit")
        if getattr(args, "error_repair", None) is True:
            runner_args.append("--error-repair")
        elif getattr(args, "error_repair", None) is False:
            runner_args.append("--no-error-repair")
        if getattr(args, "attention_takeover", None) is True:
            runner_args.append("--attention-takeover")
        elif getattr(args, "attention_takeover", None) is False:
            runner_args.append("--no-attention-takeover")
        if getattr(args, "check_literature", False):
            runner_args.append("--check-literature")
        if getattr(args, "stop_before_hypothesis_lock", False):
            runner_args += ["--stop-before", "hypothesis_lock"]
        if getattr(args, "stop_before_judge", False):
            runner_args += ["--stop-before", "judge"]
        return foreground_runner(runner_args)

    if args.command in {"run", "resume"} and getattr(spec, "task_mode", False):
        from theory_master.runtime_manager import launch_managed_runtime
        operation = args.command
        identifier = str((args.identifier if args.command == "resume" else args.master_id) or spec.master_id)
        result = launch_managed_runtime(
            spec,
            operation=operation,
            identifier=identifier,
            model=getattr(args, "model", None),
            provider=getattr(args, "provider", None),
            routes=getattr(args, "route", None),
            cycle_audit=getattr(args, "cycle_audit", None),
            error_repair=getattr(args, "error_repair", None),
            attention_takeover=getattr(args, "attention_takeover", None),
            check_literature=bool(getattr(args, "check_literature", False)),
        )
        print(f"TheoryMaster runtime: {result.get('status')}")
        if result.get("detail"):
            print(result["detail"])
        _attach_managed_tui(spec)
        return 0
    if args.command in {"run", "resume"} and getattr(args, "check_literature", False):
        check = literature_startup_check(routes)
        print("Literature startup check: OK")
        print(json.dumps(check, ensure_ascii=False, indent=2))
    if args.command == "resume":
        collector = _collector(spec, cycle_audit=getattr(args, "cycle_audit", None), error_repair=getattr(args, "error_repair", None), attention_takeover=getattr(args, "attention_takeover", None))
        workflow = Workflow(spec, CodexBackend(spec.research_root, routes=routes), routes, collector=collector)
        return run_with_tui(workflow, collector, operation=lambda: workflow.resume(args.identifier))
    collector = _collector(spec, cycle_audit=getattr(args, "cycle_audit", None), error_repair=getattr(args, "error_repair", None), attention_takeover=getattr(args, "attention_takeover", None))
    workflow = Workflow(spec, CodexBackend(spec.research_root, routes=routes), routes, collector=collector)
    return run_with_tui(workflow, collector)


if __name__ == "__main__":
    raise SystemExit(main())
