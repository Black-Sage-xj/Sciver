"""One audited completed Goal whose earlier failed Setup named another path.

The adapter references the actual authored file. It never copies a delivery,
executes a recorded command, changes a scientific field, or starts a model.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import shlex
from pathlib import Path
from types import SimpleNamespace

VALIDATOR = "native-complete-delivery-alias/v1"
MISSING = "explicit handoff file does not exist or is a symlink"
_OLD_CONTEXT_HOME = b"/home/baixj/gel_network/gs_study/11-18-18:51-test-plant-invasion"
_NEW_CONTEXT_HOME = b"/home/baixj/gel_network/gs_study/11-19-progression-recheck-plant-invasion"
_OLD_CONTEXT_SHA = "66f1eb3d7dd69f2e52e5fda832a4268b0890b10669d1520dafb6d00fcad831de"
_OLD_INVESTIGATOR_SCHEMA = "8bab514f335469918d584b17ec696c04a4c643947c211a1ee20b405a8323f69c"
_CURRENT_INVESTIGATOR_SCHEMA = "75805b53f9e5751770a39760f579ce5f0878765499323ebb409660de353f9d25"


def _read(path: Path, inputs: dict, limit: int = 16 * 1024 * 1024) -> bytes:
    from .native_goal import _safe_workspace_bytes
    path = path.absolute()
    data = _safe_workspace_bytes(Path("/"), Path(*path.parts[1:]), max_bytes=limit)
    inputs[str(path)] = hashlib.sha256(data).hexdigest()
    return data


def _json(path: Path, inputs: dict, limit: int = 4 * 1024 * 1024) -> dict:
    value = json.loads(_read(path, inputs, limit))
    if not isinstance(value, dict):
        raise ValueError("Delivery-alias input must be an object")
    return value


def _rows(path: Path, inputs: dict) -> list[dict]:
    rows = [json.loads(line) for line in _read(path, inputs).splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Delivery-alias audit is not object JSONL")
    return rows


def _control(cycle: Path, inputs: dict) -> None:
    path = cycle / "recovery_control.json"
    if path.exists() or path.is_symlink():
        _json(path, inputs)
        raise ValueError("Pending operator control forbids completed delivery recovery")


def _goal(cycle: Path, origin: dict, previous: dict) -> dict:
    from .supervisor_native_goal_recovery import read_existing_goal
    from .native_goal_recovery import verify_existing_goal
    identity = verify_existing_goal(read_existing_goal(cycle, origin), origin, previous=previous)
    if identity["status"] != "complete":
        raise ValueError("Original native Goal is not actually complete")
    return identity


def _authored_python_write(detail: str, relative: str, raw: bytes) -> bool:
    """Recognize a fixed literal-write shape without evaluating any code."""
    try:
        shell = shlex.split(detail)
        if len(shell) != 3 or shell[0] not in {"/usr/bin/bash", "/bin/bash"} or shell[1] != "-lc":
            return False
        command = shell[2]
        marker = "<<'PY'\n"
        start, end = command.rfind(marker), command.rfind("\nPY")
        if start < 0 or end <= start or command[end + 3:].strip():
            return False
        statements = ast.parse(command[start + len(marker):end]).body
        if len(statements) != 6:
            return False
        template = ast.parse(
            "import json\nfrom pathlib import Path\np=Path('PLACEHOLDER')\n"
            "obj={}\np.write_text(json.dumps(obj,ensure_ascii=False,indent=2))\nprint(p)"
        ).body
        expected = copy.deepcopy(template)
        expected[2].value.args[0] = ast.Constant(relative)
        expected[3].value = copy.deepcopy(statements[3].value)
        if any(ast.dump(actual) != ast.dump(want) for actual, want in zip(statements, expected)):
            return False
        authored = ast.literal_eval(statements[3].value)
        return (isinstance(authored, dict) and set(authored) == {"findings"}
                and json.dumps(authored, ensure_ascii=False, indent=2).encode() == raw)
    except (ValueError, TypeError, SyntaxError, AttributeError, IndexError):
        return False


def _task(cycle: Path, inputs: dict):
    from .specification import TaskSpec
    pointer = _json(cycle.parent / ".theory-master/task.json", inputs)
    config = Path(str(pointer.get("config_path") or ""))
    if pointer.get("schema") != "theory-master/task/v2" or not config.is_absolute():
        raise ValueError("Missing canonical task configuration")
    _json(config, inputs)
    spec = TaskSpec.from_config(config)
    if spec.research_root.resolve() != cycle.parent or not spec.task_mode:
        raise ValueError("Configuration does not own this Cycle")
    _read(spec.document, inputs)
    # Only the task's explicit public input manifest may expand this list.
    home = Path(spec.task_home)
    manifest = home / "input_manifest.json"
    if manifest.exists() or manifest.is_symlink():
        public = _json(manifest, inputs)
        for row in [*public.get("data_files", []), *public.get("derived_context_files", [])]:
            path = Path(str(row.get("path") or ""))
            path = path if path.is_absolute() else home / path
            relative = path.relative_to(home)
            if (".." in relative.parts or not relative.parts
                    or any(re.search(r"(?i)(grader|expected|evaluator|answer)", part) for part in relative.parts)):
                raise ValueError("Input manifest is not an authorized public source")
            data = _read(path, inputs)
            if hashlib.sha256(data).hexdigest() != row.get("sha256"):
                # This fixture changed two task-home links when copying the
                # neutral public context, but retained its source digest.
                # Reversing only those links must reproduce that exact digest.
                relocated = (path == home / "public_context.md"
                    and row.get("role") == "neutral_factual_context"
                    and row.get("sha256") == _OLD_CONTEXT_SHA
                    and str(home).encode() == _NEW_CONTEXT_HOME
                    and data.count(_NEW_CONTEXT_HOME) == 2
                    and _OLD_CONTEXT_HOME not in data
                    and hashlib.sha256(data.replace(_NEW_CONTEXT_HOME, _OLD_CONTEXT_HOME)).hexdigest()
                        == _OLD_CONTEXT_SHA)
                if not relocated:
                    raise ValueError("Frozen public input changed")
    return spec


def _evidence(cycle: Path, state: dict) -> tuple[dict, dict, dict]:
    from .contract_stages import role_contract
    from .invocations import InvocationLedger, stable_hash
    from .native_goal import (NativeGoalResult, execution_limit_setup_binding,
                              read_goal_findings, verify_native_goal_context_reference)
    from .native_goal_recovery import find_native_goal_origin
    from .runtime_supervisor_review import resume_blocker
    from .scientific_objective import frozen_science_digest

    inputs: dict[str, str] = {}
    _control(cycle, inputs)
    failure = state.get("last_invocation_failure") or {}
    native = state.get("native_goal") or {}
    attention = state.get("attention") or {}
    invocation = str(state.get("last_invocation_id") or "")
    thread = str((state.get("threads") or {}).get("investigator") or "")
    prior_reentry = "The original native Goal reattachment opportunity was already used; another reentry requires review."
    blocker = resume_blocker(state, runtime_revalidated=True)
    contract = role_contract("investigator", phase="investigate")
    historical_contract_ref = {"contract_id": "theory-master/role/investigator/investigate",
                               "contract_version": 10, "schema_hash": _OLD_INVESTIGATOR_SCHEMA}
    current_contract_ref = {**historical_contract_ref, "schema_hash": _CURRENT_INVESTIGATOR_SCHEMA}
    if (cycle.name != "5" or cycle.parent.parent.name != "17-plant-invasion-2b9d7e76c636"
            or (state.get("runtime_source_provenance") or {}).get("sdk_git_sha")
                != "db4ad9044bd32cce5f1043ccb601d6e3753e2550"
            or state.get("status") != "needs_attention" or state.get("phase") != "investigator:handoff_invalid"
            or state.get("execution_active") or state.get("active_invocation_id") or state.get("worker_id")
            or state.get("protocol_health") != "valid" or state.get("scientific_verdict") is not None
            or (state.get("goal_completion") or {}).get("verdict") == "passed"
            or (state.get("hypothesis_lock") or {}).get("status") != "locked"
            or attention != {"code": "investigator_handoff_invalid", "reason": MISSING, "thread_id": thread}
            or not re.fullmatch(r"inv-[A-Za-z0-9_-]+", invocation)
            or failure != {"invocation_id": invocation, "role": "investigator", "stage": "investigate",
                           "failure_phase": "invoke", "error_type": "RecoveryStop"}
            or native.get("status") != "complete" or native.get("thread_id") != thread
            or native.get("provider_failure") or native.get("runtime_failure_kind")
            or blocker != prior_reentry
            or resume_blocker(state, runtime_revalidated=True, for_repair=True) is not None
            or int(state.get("supervisor_runtime_resume_count") or 0) != 1
            or contract.descriptor() != current_contract_ref):
        raise ValueError("Not an unclosed completed native Goal delivery-path failure")
    spec = _task(cycle, inputs)
    if state.get("goal_sha256") != spec.goal_sha256:
        raise ValueError("Original Goal plan changed")
    ledger_rows = _rows(cycle / "invocations.jsonl", inputs)
    events = _rows(cycle / "events.jsonl", inputs)
    runtime = _rows(cycle / "runtime_events.jsonl", inputs)
    ledger = InvocationLedger(cycle / "invocations.jsonl")
    record = ledger.snapshot(invocation) or {}
    if (not ledger_rows or ledger_rows[-1].get("invocation_id") != invocation
            or record.get("state") != "interrupted" or record.get("commit_state") != "not_committed"
            or record.get("failure_phase") != "invoke" or record.get("closure_hash")
            or record.get("reason") != "SDK handoff invalid: " + MISSING
            or record.get("thread_id") != thread or record.get("contract_ref") != historical_contract_ref
            or record.get("role") != "investigator" or record.get("stage") != "investigate"
            or not record.get("native_goal_identity")
            or (cycle / "semantic_closure" / (invocation + ".json")).exists()):
        raise ValueError("Original monitor invocation is not an interrupted delivery")
    resumed = copy.deepcopy(state)
    resumed["recovery"] = {**resumed.get("recovery", {}), "action": "resume"}
    origin = find_native_goal_origin(SimpleNamespace(
        state=resumed, owner=SimpleNamespace(cycle_dir=cycle), invocation_ledger=ledger,
        invocation_id="inv-delivery-alias-proof", stage="investigate", logical_key=record["logical_key"],
        identifier=thread, contract=SimpleNamespace(descriptor=lambda: historical_contract_ref),
        handoff_path=cycle / "workspace/.tm-handoff/inv-delivery-alias-proof.json"))
    declared = Path(origin["handoff_path"])
    if declared.exists() or declared.is_symlink():
        raise ValueError("Canonical original handoff already exists")
    clean = lambda value: {k: v for k, v in value.items() if k != "persistent_goal"}
    observed_origin = native.get("origin_binding") or {}
    if (clean(record.get("native_goal_origin") or {}) != clean(origin)
            or any(observed_origin.get(k) != v for k, v in clean(origin).items()
                   if k not in {"objective", "setup_context"})):
        raise ValueError("Completed Goal changed its original delivery binding")
    previous = native.get("persistent_goal") or {}
    identity = _goal(cycle, origin, previous)
    starts = {row.get("payload", {}).get("invocation_id"): row for row in events if row.get("type") == "role.started"}
    monitor_start = (starts.get(invocation) or {}).get("at")
    if not monitor_start:
        raise ValueError("Monitor has no recorded start")
    window = [row for row in runtime if str(row.get("ts") or "") >= monitor_start]
    if any(row.get("error") or row.get("event") in {"LLM_FAILED", "LLM_TURN_FAILED"} for row in window):
        raise ValueError("Another runtime/provider error occurred during delivery")
    candidates = []
    for failed in ledger.snapshots():
        alias_id = str(failed.get("invocation_id") or "")
        if (failed.get("state") != "failed" or failed.get("commit_state") != "not_committed"
                or failed.get("error_type") != "InvalidRequestError" or failed.get("failure_phase") != "invoke"
                or failed.get("error") != "JSON-RPC error -32600: thread must be idle before starting a goal: " + thread
                or failed.get("thread_id") != thread or failed.get("logical_key") != record["logical_key"]
                or failed.get("contract_ref") != historical_contract_ref
                or not re.fullmatch(r"inv-[A-Za-z0-9_-]+", alias_id)):
            continue
        begin = (starts.get(alias_id) or {}).get("at")
        if not begin or begin >= monitor_start:
            continue
        following = [row["at"] for row in starts.values() if row.get("at", "") > begin]
        end = min(following) if following else monitor_start
        audit = [row for row in runtime if begin <= str(row.get("ts") or "") < end
                 and row.get("event") == "AGENT_INPUT" and row.get("role") == "investigator"
                 and row.get("stage") == "investigate"]
        setups = [row for row in audit if row.get("input_kind") == "native_goal_setup_turn"]
        objectives = [row for row in audit if row.get("input_kind") == "native_goal_objective"]
        path = cycle / "workspace/.tm-handoff" / (alias_id + ".json")
        if len(setups) != 1 or len(objectives) != 1 or not path.is_file():
            continue
        context = setups[0]["content"].partition("\n")[2]
        binding = execution_limit_setup_binding(context, path)
        verify_native_goal_context_reference(objectives[0]["content"], context)
        if (binding is None or binding["invocation_id"] != alias_id
                or binding["frozen_objective_sha256"] != origin["frozen_objective_sha256"]):
            continue
        raw = _read(path, inputs, 1024 * 1024)
        relative = path.relative_to(cycle / "workspace").as_posix()
        writes = [row for row in window if row.get("event") == "TOOL_ACTIVITY" and row.get("tool") == "command"
                  and row.get("thread_id") == thread and row.get("turn_id")
                  and _authored_python_write(str(row.get("detail") or ""), relative, raw)]
        if len(writes) != 1:
            continue
        write = writes[0]
        completions = [row for row in window if row.get("event") == "LLM_TURN_COMPLETED"
                       and row.get("thread_id") == thread and row.get("turn_id") == write["turn_id"]
                       and row.get("ts", "") >= write["ts"] and not row.get("error")]
        if len(completions) != 1:
            continue
        mapped = json.loads(read_goal_findings(NativeGoalResult("complete", thread, 0, 0), path))
        candidates.append((path, mapped, alias_id, write, setups[0], objectives[0]))
    if len(candidates) != 1:
        raise ValueError("No unique authored alias under the original completed Goal")
    path, mapped, alias_id, write, setup, objective = candidates[0]
    manifest_path = cycle / "invocation_artifacts" / (invocation + ".json")
    inventory = _json(manifest_path, inputs)
    if (record.get("invocation_artifact_manifest") != str(manifest_path)
            or inventory.get("invocation_id") != invocation or inventory.get("status") != "interrupted"
            or inventory.get("role") != "investigator" or inventory.get("stage") != "investigate"
            or inventory.get("workspace") != str(cycle / "workspace") or inventory.get("deleted")):
        raise ValueError("Original scientific inventory is not preserved")
    total = 0
    for row in [*inventory.get("created", []), *inventory.get("modified", [])]:
        relative = Path(str(row.get("relative_path") or ""))
        scientific = cycle / "workspace" / relative
        if relative.is_absolute() or ".." in relative.parts or str(scientific) != row.get("path"):
            raise ValueError("Unsafe scientific inventory source")
        data = _read(scientific, inputs)
        total += len(data)
        if total > 64 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != row.get("sha256"):
            raise ValueError("Original scientific source changed")
    if state.get("last_safe_checkpoint"):
        _read(Path(state["last_safe_checkpoint"]), inputs)
    binding = {
        "authority": "sdk_complete_with_audited_delivery_alias",
        "resume_preparation": "saved_delivery_semantic_closure", "invocation_id": invocation,
        "thread_id": thread, "role": "investigator", "stage": "investigate", "logical_key": record["logical_key"],
        "contract_ref": historical_contract_ref, "current_contract_ref": current_contract_ref,
        "original_source_git_sha": "db4ad9044bd32cce5f1043ccb601d6e3753e2550",
        "handoff_path": str(path), "handoff_sha256": inputs[str(path)],
        "raw_findings_sha256": stable_hash(mapped), "frozen_objective_sha256": origin["frozen_objective_sha256"],
        "artifact_manifest_path": str(manifest_path), "artifact_manifest_sha256": inputs[str(manifest_path)],
        "config_path": str(spec.config_path), "benchmark_root": str(spec.benchmark_root),
        "research_root": str(spec.research_root), "origin": origin, "goal_identity": identity,
        "declared_handoff_path": str(declared), "polluted_invocation_id": alias_id,
        "polluted_setup_sha256": hashlib.sha256(setup["content"].encode()).hexdigest(),
        "polluted_objective_sha256": hashlib.sha256(objective["content"].encode()).hexdigest(),
        "authored_write_event_sha256": stable_hash(write), "authored_write_turn_id": write["turn_id"],
    }
    return binding, inputs, record


def validate_completed_delivery_alias(cycle: Path, state: dict, *, engine: Path) -> dict:
    from .supervisor_repair_receipts import _record_validation, source_manifest
    binding, inputs, _ = _evidence(cycle, state)
    _read(engine / "native_delivery_reprocessing.py", inputs)
    _read(engine / "supervisor_delivery_alias_recovery.py", inputs)
    _read(engine.parent / "tests/test_supervisor_delivery_alias_recovery_20261119.py", inputs)
    return _record_validation(cycle, state, engine, validator=VALIDATOR,
        module="supervisor_delivery_alias_recovery.py", change_id="completed-native-delivery-alias",
        tests=json.dumps(binding, ensure_ascii=False), manifest=source_manifest(engine),
        disposition="already_fixed", extra_inputs=inputs)


def recheck_receipt_inputs(receipt: dict, state: dict) -> None:
    expected = json.loads(receipt["tests"])
    binding, _, _ = _evidence(Path(receipt["cycle"]), state)
    if binding != expected:
        raise ValueError("Completed delivery alias changed after validation")


def bound_alias_delivery(owner, state: dict, receipt: dict) -> tuple:
    from .invocations import InvocationLedger
    from .contract_stages import role_contract
    from .native_goal import NativeGoalResult, read_goal_findings
    binding, _, original = _evidence(owner.cycle_dir, state)
    if (binding != json.loads(receipt["tests"]) or binding["config_path"] != str(owner.spec.config_path)
            or binding["research_root"] != str(owner.spec.research_root)
            or binding["benchmark_root"] != str(owner.spec.benchmark_root)):
        raise ValueError("Completed delivery receipt belongs to another task")
    return (receipt, binding, original, InvocationLedger(owner.cycle_dir / "invocations.jsonl"),
            role_contract("investigator", phase="investigate"),
            read_goal_findings(NativeGoalResult("complete", binding["thread_id"], 0, 0), Path(binding["handoff_path"])),
            Path(binding["artifact_manifest_path"]))


def prepare_completed_delivery_alias(config_path: Path) -> dict:
    """Install only a standard SDK receipt under stopped-task admission locks."""
    from .specification import TaskSpec, latest_cycle_dir
    from .native_delivery_reprocessing import _stopped_task
    from .runtime import write_json
    spec = TaskSpec.from_config(config_path)
    with _stopped_task(spec.research_root.resolve()):
        cycle = latest_cycle_dir(spec.research_root, task_mode=True)
        if cycle is None:
            raise ValueError("No original Cycle")
        path = cycle / "research_state.json"
        state = _json(path, {})
        receipt = validate_completed_delivery_alias(cycle, state, engine=Path(__file__).resolve().parent)
        write_json(path, state)
        return receipt


def reprocess_completed_delivery_alias(config_path: Path) -> dict:
    """Use canonical normalization/closure/commit, with no model transport."""
    from .specification import TaskSpec, latest_cycle_dir
    from .workflow import Workflow
    from .native_delivery_reprocessing import reprocess_failed_native_delivery

    class OfflineDeliveryBackend:
        name = "offline-completed-native-delivery"
        def invoke(self, *args, **kwargs):
            raise AssertionError("Completed delivery recovery cannot call a model")
        invoke_goal = ensure_thread = invoke

    spec = TaskSpec.from_config(config_path)
    owner = Workflow(spec, OfflineDeliveryBackend())
    cycle = latest_cycle_dir(spec.research_root, task_mode=True)
    if cycle is None:
        raise ValueError("No original Cycle")
    owner._bind_cycle(cycle, create=False)
    return reprocess_failed_native_delivery(owner)


def project_reprocessed_delivery_to_judge(config_path: Path, *, dry_run: bool = False) -> dict:
    """Finish the SDK routing projection after the historical offline replay.

    The first Resume after the model-free commit exposed a missing investigation
    round projection and failed during Setup, before another model Turn. This
    adapter consumes only the already committed Investigator output.
    """
    from .invocations import InvocationLedger, stable_hash
    from .native_delivery_reprocessing import _stopped_task
    from .runtime import now, write_json
    from .specification import TaskSpec
    from .supervisor_repair_receipts import _digest
    from .workflow import Workflow

    class OfflineBackend:
        name = "offline-delivery-projection"
        def invoke(self, *args, **kwargs):
            raise AssertionError("Delivery projection cannot call a model")
        invoke_goal = ensure_thread = invoke

    spec = TaskSpec.from_config(config_path)
    root = spec.research_root.resolve()
    cycle = root / "5"
    if cycle.parent.parent.name != "17-plant-invasion-2b9d7e76c636":
        raise ValueError("Delivery projection belongs to another task")
    with _stopped_task(root):
        state_path = cycle / "research_state.json"
        state = _json(state_path, {})
        failed = state.get("last_invocation_failure") or {}
        recovery = state.get("recovery") or {}
        committed_id = "inv-5-investigator-d2ff31815a7e"
        setup_id = "inv-5-investigator-e710e2ed4eea"
        audit = state.get("supervisor_delivery_reprocessing") or {}
        attempt_key = "attempt-0001"
        record = (state.get("investigation_attempts") or {}).get(attempt_key) or {}
        if (state.get("status") != "failed" or state.get("phase") != "failed"
                or state.get("execution_active") or state.get("scientific_verdict") is not None
                or recovery.get("kind") != "NativeGoalRecoveryConflict"
                or recovery.get("message") != "unfinished native Goal changed thread or contract"
                or failed != {"invocation_id": setup_id, "role": "investigator",
                              "stage": "investigate", "failure_phase": "setup",
                              "error_type": "NativeGoalRecoveryConflict"}
                or state.get("last_invocation_id") != setup_id
                or state.get("investigation_attempt") != 1
                or state.get("investigation_round_count") != 0
                or state.get("total_investigation_round_count") != 0
                or state.get("next_executor") != "investigator"
                or record.get("outputs") != []
                or audit.get("status") != "committed" or audit.get("invocation_id") != committed_id
                or audit.get("extra_model_requests") != 0):
            raise ValueError("Not the unprojected, already committed delivery")
        owner = Workflow(spec, OfflineBackend())
        owner._bind_cycle(cycle, create=False)
        ledger = InvocationLedger(cycle / "invocations.jsonl")
        committed, setup = ledger.snapshot(committed_id) or {}, ledger.snapshot(setup_id) or {}
        closure_path = cycle / "semantic_closure" / (committed_id + ".json")
        closure = _json(closure_path, {})
        persisted_audit = _json(cycle / "delivery_reprocessing" / (committed_id + ".json"), {})
        receipt_path = Path(str(audit.get("receipt_path") or ""))
        receipt = _json(receipt_path, {})
        if (persisted_audit != audit or _digest(receipt) != audit.get("receipt_sha256")
                or receipt.get("validator") != VALIDATOR
                or receipt.get("cycle") != str(cycle)
                or committed.get("state") != "committed"
                or committed.get("commit_state") != "committed"
                or committed.get("closure_artifact") != str(closure_path)
                or committed.get("closure_hash") != audit.get("closure_hash")
                or stable_hash(closure) != audit.get("closure_hash")
                or setup.get("state") != "failed" or setup.get("commit_state") != "not_committed"
                or setup.get("failure_phase") != "setup"
                or setup.get("error_type") != "NativeGoalRecoveryConflict"
                or (cycle / "semantic_closure" / (setup_id + ".json")).exists()
                or closure.get("agent_output") != (state.get("agent_outputs") or {}).get("investigator")
                or closure.get("closed_output") != (state.get("role_outputs") or {}).get("investigator")):
            raise ValueError("Committed Investigator delivery is not intact")
        adjudicable = owner._adjudicable_judge_records(owner._evidence_context())
        if not adjudicable:
            raise ValueError("Committed delivery has no adjudicable Evidence")
        if dry_run:
            return {"next_executor": "judge", "committed_invocation_id": committed_id,
                    "evidence_ids": [row["evidence_id"] for row in adjudicable],
                    "dry_run": True}
        output = state["role_outputs"]["investigator"]
        record["outputs"].append({
            "executor_role": "investigator", "round": 1, "round_completed": True,
            "invocation_id": committed_id, "output": output,
            "evidence_refs": list(state.get("evidence_refs") or []),
        })
        state["investigation_round_count"] = 1
        state["total_investigation_round_count"] = 1
        record["readiness"] = {"decision": "send_to_judge",
                               "reason": "investigator_submission", "ready": True}
        state["previous_executor"] = "investigator"
        state["next_executor"] = "judge"
        state.update(status="paused", phase="engineering_delivery_projected", resumable=True,
                     execution_active=False, active_invocation_id=None, attention={},
                     recovery={"action": "engineering_delivery_projected", "kind": "saved_native_delivery",
                               "invocation_id": committed_id, "at": now()})
        owner._emit(state, "investigation.round_completed", {
            "role": "investigator", "investigation_attempt": 1, "round": 1,
            "thread_id": (state.get("threads") or {}).get("investigator"),
            "source": "receipt_bound_offline_delivery_projection",
        })
        owner._emit(state, "investigation.routing", {
            "investigation_attempt": 1, **record["readiness"], "selected_executor": "judge",
            "source": "receipt_bound_offline_delivery_projection",
        })
        owner._save_attempt(state)
        owner._checkpoint(state, "attempt-0001-r1")
        write_json(cycle / "delivery_reprocessing" / (committed_id + "-routing.json"), {
            "schema": "theory-master/delivery-routing-projection/v1", "at": now(),
            "committed_invocation_id": committed_id, "failed_setup_invocation_id": setup_id,
            "closure_hash": audit["closure_hash"],
            "adjudicable_evidence_ids": [row["evidence_id"] for row in adjudicable],
            "next_executor": "judge", "extra_model_requests": 0,
        })
        owner._persist(state)
        return {"next_executor": "judge", "committed_invocation_id": committed_id,
                "evidence_ids": [row["evidence_id"] for row in adjudicable]}
