"""Evidence-bound reentry for the historical over-capacity Lead repair deadlock.

This owner certifies preparation for a new Agent proposal. It never publishes
one of its offline probe subsets as a scientific proposal or changes a source.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

VALIDATOR = "lead-lineage-structure-reentry/v1"
_PHASE = "research_lead:frontier_proposal:contract_repair_failed"
_OLD_LINEAGE_SHA256 = "de13b9c3547c29f5a528a1c5e834c6b6e33b59be771077bf549807337106589d"
_OLD_PROMPT = ("Protected source decisions from the original parsed proposal; preserve these IDs, "
               "main/other roles and valid contributions without reordering candidates:\n")
_MAX_FILE = 16_000_000


def is_untouched_lineage_failure(cycle: Path, state: dict) -> bool:
    """Allow identity hashing without a checkpoint, never authorize a resume."""
    if not isinstance(state, dict):
        return False
    recovery, lock = state.get("recovery") or {}, state.get("hypothesis_lock") or {}
    frontier = state.get("frontier_review") or {}
    attention = state.get("attention") or {}
    return bool(isinstance(recovery, dict) and isinstance(lock, dict) and isinstance(frontier, dict)
        and isinstance(attention, dict)
        and state.get("status") == "needs_attention" and state.get("phase") == _PHASE
        and state.get("protocol_health") == "violation" and state.get("resumable") is True
        and not state.get("execution_active") and state.get("scientific_verdict") is None
        and not state.get("last_safe_checkpoint") and not state.get("role_outputs")
        and not state.get("agent_outputs") and not state.get("hypothesis_locked")
        and not state.get("native_goal")
        and not state.get("candidates") and not state.get("branches") and not state.get("active_branch_id")
        and lock == {"status": "unlocked"} and not state.get("hypothesis_id")
        and state.get("qualification_status", "pending") == "pending"
        and not frontier.get("generated_candidates") and not frontier.get("reviews")
        and not frontier.get("selected_candidate_id")
        and not state.get("active_invocation_id") and not state.get("last_invocation_id")
        and attention.get("code") == "contract_repair_failed"
        and recovery.get("action") == "contract_repair_failed"
        and not any(recovery.get(key) for key in ("category", "kind", "message"))
        and recovery.get("role") == "research_lead" and recovery.get("stage") == "frontier_proposal"
        and str(state.get("cycle_id")) == cycle.name)


def _read(path: Path, inputs: dict[str, str]) -> bytes:
    if (any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file()
            or path.stat().st_size > _MAX_FILE):
        raise ValueError("Unsafe lineage recovery input: " + str(path))
    data = path.read_bytes()
    if len(data) > _MAX_FILE:
        raise ValueError("Lineage recovery input exceeded its read bound")
    inputs[str(path.absolute())] = hashlib.sha256(data).hexdigest()
    return data


def _git_bytes(engine: Path, head: str, name: str) -> bytes:
    proc = subprocess.run(["git", "show", head + ":theory_master/" + name],
                          cwd=engine.parent, capture_output=True, timeout=10, check=False)
    if proc.returncode or len(proc.stdout) > _MAX_FILE:
        raise ValueError("Original SDK source is unavailable")
    return proc.stdout


def _historical_source_proof(engine: Path, state: dict) -> dict:
    source = state.get("runtime_source_provenance") or {}
    head = source.get("sdk_git_sha")
    if (source.get("source_coherent") is not True or source.get("source_coherence_errors")
            or source.get("source_root") != str(engine.parent)
            or source.get("sdk_git_root") != str(engine.parent)
            or not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head)):
        raise ValueError("Original coherent source identity is missing")
    hashes, paths = source.get("module_sha256"), source.get("module_paths")
    if not isinstance(hashes, dict) or "workflow.py" not in hashes or not isinstance(paths, dict):
        raise ValueError("Original source hashes are missing")
    for name, expected in hashes.items():
        if (not isinstance(name, str) or Path(name).name != name or not name.endswith(".py")
                or paths.get(name) != str(engine / name)
                or hashlib.sha256(_git_bytes(engine, head, name)).hexdigest() != expected):
            raise ValueError("Original source hash does not match its recorded commit")
    old_lineage = _git_bytes(engine, head, "hypothesis_lineage.py")
    if hashlib.sha256(old_lineage).hexdigest() != _OLD_LINEAGE_SHA256:
        raise ValueError("Original SDK did not contain the recognized lineage deadlock")
    return {"git_head": head, "module_sha256": dict(hashes),
            "hypothesis_lineage_sha256": _OLD_LINEAGE_SHA256}


def _failure_inputs(cycle: Path, state: dict, engine: Path) -> tuple[dict, dict[str, str]]:
    from .contracts import role_contract
    from .invocations import InvocationLedger
    if not is_untouched_lineage_failure(cycle, state):
        raise ValueError("Not an untouched Lead lineage failure")
    recovery = state["recovery"]
    invocation, thread = recovery.get("invocation_id"), recovery.get("thread_id")
    if (not isinstance(invocation, str) or not invocation or Path(invocation).name != invocation
            or not isinstance(thread, str) or not thread
            or (state.get("threads") or {}).get("research_lead") != thread):
        raise ValueError("Original Lead invocation/thread identity is missing")
    artifact = cycle / "contract_repairs" / (invocation + ".json")
    if recovery.get("artifact") != str(artifact):
        raise ValueError("Foreign contract repair artifact")
    inputs: dict[str, str] = {}
    saved = json.loads(_read(artifact, inputs))
    if any(saved.get(key) != recovery.get(key) for key in
           ("invocation_id", "role", "stage", "thread_id", "repair_prompt", "at")):
        raise ValueError("Original failure and artifact differ")
    ledger_path, events = cycle / "invocations.jsonl", cycle / "runtime_events.jsonl"
    ledger_rows = [json.loads(line) for line in _read(ledger_path, inputs).splitlines() if line.strip()]
    if {row.get("invocation_id") for row in ledger_rows} != {invocation}:
        raise ValueError("Another invocation or committed role boundary exists")
    folded = InvocationLedger(ledger_path).snapshot(invocation) or {}
    contract = role_contract("research_lead", phase="frontier_proposal")
    if (folded.get("state") != "failed" or folded.get("error_type") != "ContractRepairFailed"
            or folded.get("commit_state") != "not_committed"
            or folded.get("role") != "research_lead" or folded.get("stage") != "frontier_proposal"
            or folded.get("thread_id") != thread or folded.get("contract_ref") != contract.descriptor()
            or any(not re.fullmatch(r"[0-9a-f]{64}", str(folded.get(key) or ""))
                   for key in ("logical_key", "input_hash"))):
        raise ValueError("Original uncommitted contract invocation is not intact")
    rows = [json.loads(line) for line in _read(events, inputs).splitlines() if line.strip()]
    tasks = [r for r in rows if r.get("event") == "AGENT_INPUT" and r.get("role") == "research_lead"
             and r.get("stage") == "frontier_proposal" and r.get("input_kind") == "task"]
    repairs = [r for r in rows if r.get("event") == "AGENT_INPUT" and r.get("role") == "research_lead"
               and r.get("stage") == "frontier_proposal" and r.get("input_kind") == "contract_repair"]
    if len(tasks) != 1 or len(repairs) != 3 or repairs[-1].get("content") != saved.get("repair_prompt"):
        raise ValueError("Original bounded repair history is incomplete")
    prompt = tasks[0].get("content", "")
    context = json.loads(prompt.partition("Context:\n")[2])
    from .invocations import stable_hash
    if stable_hash(context) != folded["input_hash"]:
        raise ValueError("Captured task context differs from the original invocation input hash")
    graph = context.get("agent_graph") or {}
    columns, hypotheses = graph.get("hypothesis_columns"), graph.get("hypotheses")
    if not isinstance(columns, list) or "id" not in columns or not isinstance(hypotheses, list):
        raise ValueError("Original SQLite H identity index is missing")
    index = columns.index("id")
    known = {row[index] for row in hypotheses if isinstance(row, list) and len(row) > index
             and isinstance(row[index], str) and row[index]}
    raw = saved.get("raw_outputs")
    if not isinstance(raw, list) or len(raw) != 4 or any(not isinstance(item, str) for item in raw):
        raise ValueError("Original bounded outputs are incomplete")
    delivered = [r.get("content") for r in rows if r.get("event") == "LLM_MESSAGE"
                 and r.get("role") == "research_lead" and r.get("message_role") == "assistant"
                 and r.get("thread_id") == thread and r.get("content") in raw]
    if delivered != raw:
        raise ValueError("Raw outputs do not match the original thread deliveries")
    outputs = [json.loads(item) for item in raw]
    original = outputs[0]
    if (not isinstance(original, dict) or set(original) - {"candidates", "needs_literature"}
            or original.get("needs_literature") not in (None, False)
            or not isinstance(original.get("candidates"), list) or len(original["candidates"]) != 1):
        raise ValueError("Only the single-candidate over-capacity fault is supported")
    candidate = original["candidates"][0]
    main, others = candidate.get("lineage_main_node_id"), candidate.get("lineage_other_node_ids")
    if (main not in known or not isinstance(others, list) or len(others) != 3
            or any(not isinstance(source, str) or source not in known or source == main for source in others)
            or len(set(others)) != 3):
        raise ValueError("Original source declaration is not the recognized three-other fault")
    notes = (candidate.get("transition") or {}).get("source_contributions")
    if not isinstance(notes, dict) or set(notes) != {main, *others}:
        raise ValueError("Original source contributions are not complete")
    scientific = copy.deepcopy(original)
    scientific["candidates"][0].pop("lineage_other_node_ids")
    for answer in outputs:
        value = copy.deepcopy(answer)
        current = value["candidates"][0].pop("lineage_other_node_ids")
        if (value != scientific or not isinstance(current, list) or len(current) not in (2, 3)
                or not set(current).issubset(others) or len(set(current)) != len(current)):
            raise ValueError("A prior repair changed other scientific or contract content")
    roles = {main: "main", **{source: "other" for source in others}}
    protected = [{"index": 0, "hypothesis": candidate.get("hypothesis"), "roles": roles,
                  "exact": False, "contributions": notes}]
    for row in repairs:
        if json.loads(row.get("content", "").partition(_OLD_PROMPT)[2]) != protected:
            raise ValueError("Historical repair did not freeze the recognized invalid structure")
    expected = [
        {"path": "/", "code": "contract_violation", "expected": contract.ref, "actual": "object",
         "message": "transition.source_contributions keys must exactly match main and other source H IDs"},
        {"path": "/candidates", "code": "lineage_content_loss", "expected": "preserved source decisions",
         "actual": "changed lineage", "message":
         "candidates[0] repair must preserve valid lineage IDs and main/other roles: " + str(roles)},
    ]
    if saved.get("validation_errors") != expected:
        raise ValueError("Failure includes a different contract violation")
    # Bind the canonical public contract/config and graph bytes; neither is
    # changed by validation. A later scientific/source update stales receipt.
    pointer = cycle.parent / ".theory-master" / "task.json"
    pointer_value = json.loads(_read(pointer, inputs))
    config_path = Path(str(pointer_value.get("config_path") or ""))
    if pointer_value.get("schema") != "theory-master/task/v2" or not config_path.is_absolute():
        raise ValueError("Canonical task configuration is missing")
    config = json.loads(_read(config_path, inputs))
    from .task import effective_path_model
    _, research, _ = effective_path_model(config)
    if research != cycle.parent:
        raise ValueError("Configuration names a different research root")
    for name in ("science_graph.sqlite3", "science_graph.sqlite3-wal"):
        path = cycle.parent / name
        if path.exists() or path.is_symlink():
            _read(path, inputs)
    plan = Path(str(config.get("plan_path") or ""))
    if not plan.is_absolute():
        raise ValueError("Canonical research plan is missing")
    _read(plan, inputs)
    old_source = _historical_source_proof(engine, state)
    return {"invocation_id": invocation, "thread_id": thread,
            "logical_key": folded["logical_key"], "contract_ref": contract.descriptor(),
            "source_pool": others, "main_source": main, "raw_outputs_sha256": hashlib.sha256(
                json.dumps(raw, ensure_ascii=False).encode()).hexdigest(),
            "original_source": old_source, "probe_input": {"output": original, "known_ids": sorted(known)}}, inputs


def _probe(engine: Path, payload: dict) -> dict:
    # Fresh imports also protect a long-lived Supervisor holding old modules.
    script = '''import copy,itertools,json,sys
def offline(event,args):
    if event in {'socket.connect','socket.getaddrinfo','subprocess.Popen','os.system','os.posix_spawn','ctypes.dlopen'} or event.startswith('os.exec'):
        raise RuntimeError('Lineage probe forbids network/process execution')
sys.addaudithook(offline)
sys.path.insert(0,sys.argv[1])
from theory_master.hypothesis_lineage import lineage_repair_snapshot,assert_repair_lineage_preserved
from theory_master.contracts import role_contract
p=json.load(sys.stdin); original=p['output']; known=set(p['known_ids'])
c=role_contract('research_lead',phase='frontier_proposal'); frozen=lineage_repair_snapshot(original,known)
assert len(frozen)==1 and len(frozen[0]['repairable_other_sources'])==3
main=original['candidates'][0]['lineage_main_node_id']; pool=frozen[0]['repairable_other_sources']; checked=0
for n in range(3):
    for subset in itertools.combinations(pool,n):
        value=copy.deepcopy(original); candidate=value['candidates'][0]; candidate['lineage_other_node_ids']=list(subset)
        candidate['transition']['source_contributions']={k:v for k,v in candidate['transition']['source_contributions'].items() if k in {main,*subset}}
        c.validate(value,{'_known_hypothesis_ids':list(known),'_require_explicit_lineage':True})
        assert_repair_lineage_preserved(frozen,value); checked+=1
        changed=copy.deepcopy(value); changed['candidates'][0]['transition']['source_contributions'][main]='Changed scientific explanation.'
        try: assert_repair_lineage_preserved(frozen,changed)
        except ValueError: pass
        else: raise AssertionError('Protected explanation was mutable')
print(json.dumps({'fresh_lineage_probe':'passed','legal_subsets_checked':checked,'current_contract':c.descriptor()}))
'''
    with tempfile.TemporaryDirectory(prefix="tm-lineage-reentry-") as td:
        proc = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(engine.parent)],
            input=json.dumps(payload), text=True, cwd=td, capture_output=True, timeout=30,
            env={"PATH": os.defpath, "HOME": td, "LANG": "C.UTF-8"}, check=False)
    if proc.returncode:
        raise ValueError("Fresh lineage repair probe failed")
    evidence = json.loads(proc.stdout)
    if evidence.get("fresh_lineage_probe") != "passed" or evidence.get("legal_subsets_checked") != 7:
        raise ValueError("Incomplete lineage repair probe")
    return evidence


def validate_existing_lineage_structure(cycle: Path, state: dict, *, engine: Path) -> dict | None:
    if not is_untouched_lineage_failure(cycle, state):
        return None
    from .supervisor_repair_receipts import _digest, _record_validation, failure_binding, source_manifest
    manifest, binding = source_manifest(engine), failure_binding(cycle, state)
    try:
        evidence, inputs = _failure_inputs(cycle, state, engine)
        evidence.update(_probe(engine, evidence.pop("probe_input")))
    except (OSError, ValueError, TypeError, KeyError, IndexError, subprocess.SubprocessError):
        return None
    if (source_manifest(engine) != manifest or failure_binding(cycle, state) != binding
            or any(hashlib.sha256(_read(Path(p), {})).hexdigest() != digest for p, digest in inputs.items())):
        raise ValueError("Lineage recovery inputs changed during validation")
    evidence["resume_preparation"] = "agent_selects_legal_source_subset"
    return _record_validation(cycle, state, engine, validator=VALIDATOR,
        module="hypothesis_lineage.py", change_id="existing-source:" + _digest(manifest),
        tests=json.dumps(evidence, sort_keys=True), manifest=manifest,
        disposition="already_fixed", extra_inputs=inputs)


def recheck_receipt_inputs(receipt: dict) -> None:
    evidence = json.loads(receipt.get("tests") or "{}")
    if (receipt.get("validator") != VALIDATOR or evidence.get("fresh_lineage_probe") != "passed"
            or evidence.get("legal_subsets_checked") != 7
            or evidence.get("resume_preparation") != "agent_selects_legal_source_subset"
            or evidence.get("original_source", {}).get("hypothesis_lineage_sha256") != _OLD_LINEAGE_SHA256
            or not evidence.get("invocation_id") or not evidence.get("thread_id")):
        raise ValueError("Invalid lineage recovery evidence")


def has_verified_lineage_reentry(state: dict, *, cycle: Path | None = None) -> bool:
    from .supervisor_repair_receipts import inspect_repair_receipt
    ref = state.get("supervisor_repair_receipt") or {}
    try:
        if cycle is None:
            cycle = Path(str(ref["path"])).parents[2]
        if not is_untouched_lineage_failure(cycle, state):
            return False
        receipt = inspect_repair_receipt(cycle, state)
        key = str(receipt.get("repair_identity") or "")
        return bool(receipt.get("validator") == VALIDATOR and key
                    and key not in (state.get("supervisor_lineage_reentries") or []))
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return False


def consume_lineage_reentry(cycle: Path, state: dict) -> bool:
    """Transition only the verified preparation fault, before resume mutates it."""
    if not has_verified_lineage_reentry(state, cycle=cycle):
        if is_untouched_lineage_failure(cycle, state):
            raise ValueError("Untouched Lead contract failure requires verified lineage repair evidence")
        return False
    from .runtime_supervisor_review import resume_blocker
    from .supervisor_repair_receipts import inspect_repair_receipt
    from .runtime import now
    receipt = inspect_repair_receipt(cycle, state)
    # A Runtime review already books its resume count and repair credit before
    # Workflow entry. This separate single-use receipt consumption rechecks all
    # operational guards without charging that same authorized resume twice.
    # The exact failure family has no runtime/budget category or native Goal.
    blocker = resume_blocker(state, for_repair=True, runtime_revalidated=True,
                             contract_projection_repair=receipt)
    if blocker:
        raise ValueError("Lineage reentry guard: " + blocker)
    control = cycle / "recovery_control.json"
    if control.exists() or control.is_symlink():
        raise ValueError("Pending operator control forbids lineage reentry")
    key = receipt["repair_identity"]
    state.setdefault("supervisor_lineage_reentries", []).append(key)
    if key not in state.setdefault("supervisor_consumed_repairs", []):
        state["supervisor_consumed_repairs"].append(key)
    state.setdefault("supervisor_lineage_reentry_audit", []).append({
        "at": now(), "repair_receipt": copy.deepcopy(state["supervisor_repair_receipt"]),
        "original_failure": copy.deepcopy(state["recovery"]),
        "original_protocol_health": state["protocol_health"],
        "original_attention": copy.deepcopy(state.get("attention")),
        "authority": "verified_lineage_preparation_reentry", "scientific_outputs_committed": False,
    })
    # The old scientific and failed delivery records remain untouched. The
    # corrected runtime may now ask the Lead, which still owns source choice.
    state["protocol_health"] = "valid"
    state.pop("attention", None)
    state.pop("human_attention", None)
    return True


def prepare_lineage_reentry(research_root: Path) -> dict:
    """Install only receipt bookkeeping on a stopped task, never launch it."""
    from .native_delivery_reprocessing import _stopped_task
    from .runtime import write_json
    from .specification import latest_cycle_dir
    from .supervisor_repair_receipts import failure_binding
    root = Path(research_root).absolute()
    with _stopped_task(root):
        cycle = latest_cycle_dir(root, task_mode=True)
        if cycle is None:
            raise ValueError("No existing Cycle")
        state_path = cycle / "research_state.json"
        state = json.loads(state_path.read_text())
        if (cycle / "recovery_control.json").exists():
            raise ValueError("Pending operator control forbids lineage preparation")
        receipt = validate_existing_lineage_structure(cycle, state, engine=Path(__file__).resolve().parent)
        if receipt is None:
            raise ValueError("Historical lineage recovery did not validate")
        current = json.loads(state_path.read_text())
        if failure_binding(cycle, current) != receipt["failure_binding"]:
            raise ValueError("Failure changed before lineage receipt installation")
        write_json(state_path, state)
        return receipt
