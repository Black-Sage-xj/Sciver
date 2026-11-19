"""Narrow proof for an interrupted Goal's failed start-again recovery.

This authorizes one monitor reattachment to the original Goal. It does not
complete, clear, recreate or adjudicate that Goal, and never supplies findings.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

VALIDATOR = "native-goal-origin-reattachment/v1"
ERROR_PREFIX = "JSON-RPC error -32600: thread must be idle before starting a goal: "


def is_native_goal_start_conflict(state: dict) -> bool:
    recovery = state.get("recovery") or {}
    return (
        isinstance(recovery, dict)
        and recovery.get("kind") == "InvalidRequestError"
        and isinstance(recovery.get("message"), str)
        and recovery["message"].startswith(ERROR_PREFIX)
    )


def reentry_key(state: dict, *, thread_id: str | None = None) -> str:
    from .scientific_objective import frozen_science_digest
    from .supervisor_repair_receipts import _digest

    threads = state.get("threads") or {}
    thread = str(
        thread_id
        or (threads.get("investigator") if isinstance(threads, dict) else "")
        or ""
    )
    return _digest([thread, frozen_science_digest(state.get("hypothesis_lock") or {})])


def _read(path: Path, inputs: dict[str, str], limit: int = 16 * 1024 * 1024) -> bytes:
    from .native_goal import _safe_workspace_bytes

    path = path.absolute()
    data = _safe_workspace_bytes(Path("/"), Path(*path.parts[1:]), max_bytes=limit)
    inputs[str(path)] = hashlib.sha256(data).hexdigest()
    return data


def _rows(path: Path, inputs: dict[str, str]) -> list[dict]:
    rows = [
        json.loads(line) for line in _read(path, inputs).splitlines() if line.strip()
    ]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("native recovery audit contains a non-object")
    return rows


def _control(cycle: Path, inputs: dict[str, str]) -> None:
    path = cycle / "recovery_control.json"
    if path.exists() or path.is_symlink():
        control = json.loads(_read(path, inputs, 1024 * 1024))
        if not isinstance(control, dict) or control.get("action") in {
            "pause",
            "interrupt",
            "kill",
            "abandon_cycle",
        }:
            raise ValueError("operator control remains pending")


def _capability(engine: Path, inputs: dict[str, str]) -> None:
    """Inspect the actual phase/transport owners without running a model."""
    from .native_goal_recovery import find_native_goal_origin, attach_existing_goal

    if not callable(find_native_goal_origin) or not callable(attach_existing_goal):
        raise ValueError("native Goal reattachment capability is unavailable")
    requirements = {
        "invocation_transport.py": {"find_native_goal_origin", "bind_origin_context"},
        "codex_goal_transport.py": {"origin_from_context", "attach_existing_goal"},
    }
    for name, expected in requirements.items():
        tree = ast.parse(_read(engine / name, inputs).decode())
        calls = {
            node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
        }
        if not expected <= calls:
            raise ValueError(
                "current invocation does not route through original Goal reattachment"
            )
    for name in ("native_goal_recovery.py", "codex_sdk_compat.py"):
        _read(engine / name, inputs)
    _read(
        engine.parent / "tests/test_supervisor_native_goal_recovery_20261119.py", inputs
    )


def _origin(cycle: Path, state: dict, inputs: dict[str, str]) -> tuple[dict, dict]:
    from .contract_stages import role_contract
    from .invocations import InvocationLedger
    from .native_goal_recovery import find_native_goal_origin

    invocation = state.get("last_invocation_id")
    if (
        not isinstance(invocation, str)
        or not re.fullmatch(r"inv-[A-Za-z0-9_-]+", invocation)
        or state.get("active_invocation_id") is not None
    ):
        raise ValueError("missing terminal failed invocation")
    failure = state.get("last_invocation_failure") or {}
    stage = failure.get("stage")
    if stage not in {
        "investigate",
        "judge_rework",
        "integrate_literature",
    } or failure != {
        "invocation_id": invocation,
        "role": "investigator",
        "stage": stage,
        "failure_phase": "invoke",
        "error_type": "InvalidRequestError",
    }:
        raise ValueError("not the native Goal invoke failure")
    ledger_rows = _rows(cycle / "invocations.jsonl", inputs)
    ledger = InvocationLedger(cycle / "invocations.jsonl")
    record = ledger.snapshot(invocation) or {}
    thread = record.get("thread_id")
    contract = role_contract("investigator", phase=stage)
    if (
        not ledger_rows
        or ledger_rows[-1].get("invocation_id") != invocation
        or record.get("state") != "failed"
        or record.get("commit_state") != "not_committed"
        or record.get("error_type") != "InvalidRequestError"
        or record.get("error") != state["recovery"]["message"]
        or record.get("failure_phase") != "invoke"
        or record.get("role") != "investigator"
        or record.get("stage") != stage
        or record.get("contract_ref") != contract.descriptor()
        or not record.get("logical_key")
        or record.get("closure_hash")
        or not isinstance(thread, str)
        or not thread
        or state["recovery"]["message"] != ERROR_PREFIX + thread
        or (state.get("threads") or {}).get("investigator") != thread
    ):
        raise ValueError(
            "failed invocation does not match its original thread/contract"
        )
    if (cycle / "semantic_closure" / f"{invocation}.json").exists():
        raise ValueError("failed invocation already has a semantic closure")
    events = _rows(cycle / "events.jsonl", inputs)
    starts = [
        row
        for row in events
        if row.get("type") == "role.started"
        and (row.get("payload") or {}).get("invocation_id") == invocation
    ]
    if (
        len(starts) != 1
        or starts[0]["payload"].get("role") != "investigator"
        or starts[0]["payload"].get("stage") != stage
        or starts[0]["payload"].get("thread_id") != thread
    ):
        raise ValueError("failed invocation has no unique thread-bound start")
    begin, finish = starts[0].get("at"), state["recovery"].get("at")
    if not isinstance(begin, str) or not isinstance(finish, str) or begin > finish:
        raise ValueError("invalid failure audit window")
    runtime = _rows(cycle / "runtime_events.jsonl", inputs)
    window = [row for row in runtime if begin <= str(row.get("ts") or "") <= finish]
    projection = [row for row in window if row.get("event") == "RUN_FAILED"]
    if (
        len(projection) != 1
        or projection[0].get("ts") != finish
        or projection[0].get("error") != state["recovery"]["message"]
        or projection[0].get("error_kind") != "InvalidRequestError"
        or projection[0].get("failure_category") != "runtime"
        or projection[0].get("resumable") is not True
    ):
        raise ValueError("failure projection is not this exact startup error")
    if any(
        (row.get("error") or row.get("event") in {"LLM_FAILED", "LLM_TURN_FAILED"})
        and row is not projection[0]
        for row in window
    ):
        raise ValueError("another provider failure is present in the failed invocation")
    resumed = copy.deepcopy(state)
    resumed["recovery"]["action"] = "resume"
    session = SimpleNamespace(
        state=resumed,
        owner=SimpleNamespace(cycle_dir=cycle),
        invocation_ledger=ledger,
        invocation_id=invocation,
        stage=stage,
        logical_key=record["logical_key"],
        identifier=thread,
        contract=contract,
        handoff_path=cycle / "workspace/.tm-handoff" / f"{invocation}.json",
    )
    origin = find_native_goal_origin(session)
    if origin is None:
        raise ValueError(
            "failed recovery has no unique unfinished predecessor Goal origin"
        )
    snapshots = ledger.snapshots()
    original_index = next(
        index
        for index, row in enumerate(snapshots)
        if row.get("invocation_id") == origin["invocation_id"]
    )
    monitors = snapshots[original_index + 1 :]
    prior_ids = {origin["invocation_id"]}
    for monitor in monitors:
        if (
            monitor.get("state") != "failed"
            or monitor.get("commit_state") != "not_committed"
            or monitor.get("role") != "investigator"
            or monitor.get("stage") != stage
            or monitor.get("logical_key") != record["logical_key"]
            or monitor.get("contract_ref") != contract.descriptor()
            or monitor.get("thread_id") != thread
            or monitor.get("closure_hash")
            or monitor.get("failure_phase") != "invoke"
            or monitor.get("error_type") != "InvalidRequestError"
            or monitor.get("error") != ERROR_PREFIX + thread
            or monitor.get("predecessor_invocation_id") not in (None, *prior_ids)
        ):
            raise ValueError(
                "intervening invocation breaks the original Goal reentry chain"
            )
        prior_ids.add(monitor["invocation_id"])
    if (
        not monitors
        or snapshots[original_index].get("successor_invocation_id")
        != monitors[0]["invocation_id"]
        or monitors[0].get("predecessor_invocation_id") != origin["invocation_id"]
    ):
        raise ValueError(
            "first failed monitor does not point to the interrupted original Goal"
        )
    original_starts = [
        row
        for row in events
        if row.get("type") == "role.started"
        and (row.get("payload") or {}).get("invocation_id") == origin["invocation_id"]
    ]
    if len(original_starts) != 1:
        raise ValueError("original Goal start window is ambiguous")
    original_begin = int(datetime.fromisoformat(original_starts[0]["at"]).timestamp())
    later_starts = [
        int(datetime.fromisoformat(row["at"]).timestamp())
        for row in events
        if row.get("type") == "role.started"
        and int(datetime.fromisoformat(row["at"]).timestamp()) > original_begin
    ]
    if not later_starts:
        raise ValueError("original Goal has no later failed recovery window")
    record["goal_created_window"] = [original_begin, min(later_starts)]
    return origin, record


def read_existing_goal(cycle: Path, origin: dict):
    """GET only: never load/resume a thread or start/activate/cancel its Goal."""
    try:
        from openai_codex import Codex, CodexConfig
        from openai_codex.errors import CodexError
    except ImportError as exc:
        raise ValueError("official native Goal reader is unavailable") from exc
    from .codex_accounts import runtime_home
    from .codex_sdk_compat import read_goal

    home = runtime_home().absolute()
    if home.is_symlink() or not (home / "config.toml").is_file():
        raise ValueError("the existing TheoryMaster Codex runtime home is unavailable")
    try:
        with Codex(
            CodexConfig(env={"CODEX_HOME": str(home)}, cwd=str(cycle / "workspace"))
        ) as client:
            return read_goal(client, origin["thread_id"])
    except CodexError as exc:
        raise ValueError("original native Goal GET is unavailable") from exc


def _goal(
    cycle: Path,
    state: dict,
    origin: dict,
    *,
    previous: dict | None = None,
    created_window: list[int] | None = None,
) -> dict:
    from .native_goal_recovery import verify_existing_goal

    identity = verify_existing_goal(
        read_existing_goal(cycle, origin), origin, previous=previous
    )
    budget = state["investigator_budget"]
    allowance, observed = budget.get("allowance_tokens"), budget.get("observed_tokens")
    usage, native_limit = identity.get("tokens_used"), identity.get("token_budget")
    created = identity["created_at"]
    in_original_window = created_window is None or any(
        created_window[0] <= stamp < created_window[1]
        for stamp in (created, created / 1000)
    )
    if (
        identity.get("status") != "active"
        or budget.get("hard_cap_reached")
        or not all(
            type(value) is int and value >= 0
            for value in (allowance, observed, usage, identity.get("time_used_seconds"))
        )
        or max(usage, observed) >= allowance
        or not in_original_window
        or (
            native_limit is not None
            and (type(native_limit) is not int or usage >= native_limit)
        )
    ):
        raise ValueError(
            "the original Goal is not active within its existing allowance"
        )
    return identity


def recheck_receipt_inputs(receipt: dict, state: dict) -> None:
    """Recheck live identity/monotonic usage as well as immutable audit bytes."""
    evidence = json.loads(receipt["tests"])
    cycle = Path(receipt["cycle"])
    inputs: dict[str, str] = {}
    _control(cycle, inputs)
    for path, expected in receipt.get("extra_inputs", {}).items():
        if hashlib.sha256(_read(Path(path), inputs)).hexdigest() != expected:
            raise ValueError("native recovery proof changed")
    _goal(
        cycle,
        state,
        evidence["origin"],
        previous=evidence["goal_identity"],
        created_window=evidence["goal_created_window"],
    )


def receipt_goal_identity(state: dict, cycle: Path, origin: dict) -> dict | None:
    """Carry the consumed receipt's identity to the final attach-time GET.

    Resume has appended operational audit rows and changed recovery.action by
    this point. The immutable receipt and freshly reconstructed original Setup
    remain authoritative; a mutable status field is never accepted as proof.
    """
    from .supervisor_repair_receipts import _digest, source_manifest, repair_credit_key

    ref = state.get("supervisor_repair_receipt") or {}
    if not isinstance(ref, dict) or not ref.get("path"):
        return None
    path = Path(str(ref["path"]))
    if path.parent != cycle / "supervisor/repairs":
        raise ValueError("native Goal receipt is outside its Cycle")
    receipt = json.loads(_read(path, {}, 4 * 1024 * 1024))
    if not isinstance(receipt, dict) or _digest(receipt) != ref.get("sha256"):
        raise ValueError("native Goal receipt digest changed")
    if receipt.get("validator") != VALIDATOR:
        return None
    if (
        receipt.get("schema") != "theory-master/supervisor-repair/v1"
        or receipt.get("cycle") != str(cycle.resolve())
        or receipt.get("phase") != "repair_validated"
        or receipt.get("source_root") != str(Path(__file__).resolve().parent)
        or receipt.get("source_manifest")
        != source_manifest(Path(__file__).resolve().parent)
        or receipt.get("repair_identity") != ref.get("repair_identity")
        or receipt.get("repair_identity")
        != _digest(
            [
                VALIDATOR,
                receipt.get("source_manifest"),
                receipt.get("extra_inputs") or {},
            ]
        )
        or repair_credit_key(state)
        not in (state.get("supervisor_consumed_repairs") or [])
        or reentry_key(state, thread_id=origin["thread_id"])
        not in (state.get("supervisor_native_goal_reentries") or [])
    ):
        raise ValueError(
            "native Goal receipt was not consumed for this bounded reentry"
        )
    evidence = json.loads(receipt["tests"])
    clean_origin = {
        key: value for key, value in origin.items() if key != "persistent_goal"
    }
    expected_origin = {
        key: value
        for key, value in evidence["origin"].items()
        if key != "persistent_goal"
    }
    if clean_origin != expected_origin or evidence.get("reentry_key") != reentry_key(
        state, thread_id=origin["thread_id"]
    ):
        raise ValueError(
            "consumed native Goal receipt belongs to another frozen origin"
        )
    return dict(evidence["goal_identity"])


def validate_existing_native_goal_reattachment(
    cycle: Path, state: dict, *, engine: Path
) -> dict | None:
    from .native_goal_recovery import NativeGoalRecoveryConflict
    from .runtime_supervisor_review import resume_blocker
    from .supervisor_repair_receipts import (
        _record_validation,
        failure_binding,
        source_manifest,
        _file_hash,
    )

    if any(
        state.get(key) is not None and not isinstance(state[key], dict)
        for key in (
            "recovery",
            "investigator_budget",
            "hypothesis_lock",
            "threads",
            "last_invocation_failure",
        )
    ):
        return None
    recovery = state.get("recovery") or {}
    if (
        not is_native_goal_start_conflict(state)
        or state.get("status") != "failed"
        or state.get("phase") != "failed"
        or state.get("execution_active")
        or state.get("worker_id")
        or state.get("protocol_health") != "valid"
        or state.get("scientific_verdict") is not None
        or state.get("native_goal")
        or recovery.get("action") != "error"
        or recovery.get("category") != "runtime"
        or not isinstance(state.get("investigator_budget"), dict)
        or (state.get("hypothesis_lock") or {}).get("status") != "locked"
        or reentry_key(state) in (state.get("supervisor_native_goal_reentries") or [])
        or resume_blocker(state, for_repair=True) is not None
    ):
        return None
    try:
        manifest, binding = source_manifest(engine), failure_binding(cycle, state)
        inputs: dict[str, str] = {}
        _control(cycle, inputs)
        _capability(engine, inputs)
        origin, record = _origin(cycle, state, inputs)
        identity = _goal(
            cycle, state, origin, created_window=record["goal_created_window"]
        )
        evidence = {
            "authority": "original_active_goal_get",
            "origin": origin,
            "goal_identity": identity,
            "failed_invocation_id": record["invocation_id"],
            "goal_created_window": record["goal_created_window"],
            "resume_preparation": "monitor_original_native_goal_without_setup_or_start",
            "evidence_paths": sorted(inputs),
            "reentry_key": reentry_key(state),
        }
        if (
            source_manifest(engine) != manifest
            or failure_binding(cycle, state) != binding
            or any(_file_hash(Path(path)) != digest for path, digest in inputs.items())
        ):
            raise ValueError("native Goal recovery proof changed during validation")
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        NativeGoalRecoveryConflict,
    ):
        return None
    return _record_validation(
        cycle,
        state,
        engine,
        validator=VALIDATOR,
        module="native_goal_recovery.py",
        change_id="existing-source:"
        + hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
        tests=json.dumps(evidence, sort_keys=True),
        manifest=manifest,
        disposition="already_fixed",
        extra_inputs=inputs,
    )


def prepare_native_goal_reentry(research_root: Path) -> dict:
    """Install SDK proof under the normal admission/scheduler locks; never resume."""
    import fcntl
    from .runtime import write_json
    from .specification import latest_cycle_dir
    from .runtime_admission import (
        admission_lock,
        assert_no_single_owner,
        assert_no_worker_claims,
    )
    from .runtime_manager import runtime_health
    from .supervisor_repair_receipts import failure_binding

    root = Path(research_root).absolute()
    gate = root / ".theory-master/scheduler.lock"
    with admission_lock(root), gate.open("a+") as handle:
        assert_no_single_owner(root)
        assert_no_worker_claims(root)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if runtime_health(root).get("active"):
            raise ValueError("runtime ownership changed")
        cycle = latest_cycle_dir(root, task_mode=True)
        if cycle is None:
            raise ValueError("no existing Cycle")
        path = cycle / "research_state.json"
        state = json.loads(path.read_text())
        receipt = validate_existing_native_goal_reattachment(
            cycle, state, engine=Path(__file__).resolve().parent
        )
        if receipt is None:
            raise ValueError("original native Goal reattachment proof did not validate")
        if (
            failure_binding(cycle, json.loads(path.read_text()))
            != receipt["failure_binding"]
        ):
            raise ValueError("persisted failure changed before receipt installation")
        _control(cycle, {})
        write_json(path, state)
        return receipt
