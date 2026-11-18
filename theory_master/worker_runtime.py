"""Public Worker control API and CLI.

Implementation owners are separated into storage, routing, execution, quota,
operator actions, preservation and scheduling. Dependencies are explicit in
those modules; this facade has no mutable override state.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .worker_store import (
    worker_dir, read_worker, save_worker, list_workers, reconcile_released_worker_cycle, available_worker_name, _graph_path, _live_cycle_claims, _worker_live_claim, _claim_owns_cycle, _checkpoint_matches_claim, _recover_failed_cycle_pointer, _ensure_legacy_claims_migrated, _living_workers, _master_complete,
)
from .worker_launch import (
    _launch_detached_service, _task_scoped_process_env,
)
from .worker_routes import (
    _pinned_codex_home, worker_route_overrides, worker_route_activations, save_worker_route, read_worker_profile, save_worker_profile, effective_worker_profile, resolve_worker_routes, _share_enabled, _shared_account_candidate, worker_account_preferences, set_worker_account_preference, _rebind_idle_worker_home, _route_capacity,
)
from .worker_direction import (
    research_direction_fingerprint, _direction_values, _retry_direction_path, _pending_retry_direction, _waiting_local_replan_reason, _partition_waiting_workers, _notify_all_waiting, detect_critical_knowledge_change, _restore_cycle_operator_guidance,
)
from .worker_execution import (
    _update_cycle_memory_index, _wake_fingerprint_for, run_once,
)
from .worker_quota import (
    start_worker_quota_watch, _repair_missing_quota_watchers, _technical_review_quota_watch_eligible, _budget_review_technical_failures, _budget_reviewer_last_technical_error, _dispatch_exhausted_budget_reviewer_transport, watch_worker_quota, _migrate_paused_worker_account, _verified_codex_quota_wait, _verified_pinned_codex_quota_wait, _persist_verified_quota_wait,
)
from .worker_preservation import (
    _flag_orphaned_preservation_jobs, PreservationPreflightRejected, request_preserve_release, execute_preserve_release_job,
)
from .worker_actions import (
    worker_action,
)
from .worker_scheduler import (
    _recover_proposal_gate, schedule_once, scheduler_status, _validate_scheduler_skills, _record_scheduler_start_failure, start_scheduler, scheduler_worker_ids, scheduler_daemon, stop_scheduler,
)
from .worker_state import (
    DIRECTION_FINGERPRINT_FIELDS, DIRECTION_FINGERPRINT_SCHEMA, MAX_REPEATED_TECHNICAL_REVIEW_FAILURES, MAX_SHARED_ACCOUNT_WORKERS, NON_INCIDENT_STOPS, RESUMABLE, RETRY_DIRECTION_SCHEMA, RUNNING, SCHEDULER_NO_PROGRESS_GRACE_SECONDS, SCHEMA, STOPPED_GOAL_COMPLETED, WAITING_RESEARCH_DIRECTION, WORKER_ACCOUNT_PREFERENCES_SCHEMA,
)
from .goal_terminal import GoalSealedError
from .multi_worker import CycleClaims, ProposalGate, _read_json, _save_json, attach_owner, finish_owned_claim, utcnow
from .runtime_manager import _runtime_env, _service_command, _service_environment, process_identity_alive, process_start_ticks, runtime_health, runtime_source_provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run-one", "tick", "status", "daemon", "start", "stop", "pause", "resume", "release", "retry_direction", "preserve-job", "quota-watch", "quota-watch-start"])
    parser.add_argument("--research-root", required=True, type=Path)
    parser.add_argument("--worker")
    parser.add_argument("--account", choices=["1", "2"],
                        help="optional new login for safe paused-Cycle account migration")
    parser.add_argument("--claim-id")
    parser.add_argument("--epoch", type=int)
    parser.add_argument("--resource-slots", type=int, default=0)
    parser.add_argument("--preferred-hypothesis",
                        help="deprecated and ignored: Preferred no longer influences scheduling")
    parser.add_argument("--resume-cycle")
    args = parser.parse_args(argv)
    from .task import TaskBinding
    from .specification import TaskSpec
    binding = TaskBinding.load(args.research_root)
    spec = TaskSpec.from_task_root(binding.task_root)
    if args.action == "preserve-job":
        if not args.worker:
            parser.error("preserve-job requires an authorized Worker")
        return execute_preserve_release_job(spec, args.worker)
    if args.action in {"quota-watch", "quota-watch-start"}:
        if not args.worker:
            parser.error("quota watcher requires --worker")
        if args.action == "quota-watch":
            return watch_worker_quota(spec, args.worker)
        print(json.dumps(start_worker_quota_watch(spec, args.worker), ensure_ascii=False))
        return 0
    if args.action == "run-one":
        if not args.worker or not args.claim_id or args.epoch is None:
            parser.error("run-one requires worker, claim-id and epoch")
        return run_once(spec, args.worker, args.claim_id, args.epoch,
                        resume_cycle=args.resume_cycle)
    if args.action == "daemon":
        return scheduler_daemon(spec, args.resource_slots, args.preferred_hypothesis)
    if args.action == "start":
        response = start_scheduler(spec, args.resource_slots, preferred_hypothesis=args.preferred_hypothesis)
    elif args.action == "stop":
        response = stop_scheduler(spec)
    elif args.action in {"pause", "resume", "release", "retry_direction"}:
        if not args.worker:
            parser.error(f"{args.action} requires --worker worker-N")
        response = worker_action(spec, args.worker, args.action, account=args.account)
    elif args.action == "tick":
        response = schedule_once(spec, resource_slots=args.resource_slots,
                                 preferred_hypothesis=args.preferred_hypothesis)
    else:
        response = {"workers": list_workers(spec.research_root),
                    "cycle_claims": CycleClaims(spec.research_root, _graph_path(spec)).list_claims(),
                    "proposal_gate": ProposalGate(spec.research_root).status(),
                    "scheduler": scheduler_status(spec.research_root)}
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
