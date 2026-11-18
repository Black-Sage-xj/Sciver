"""Deterministic preserved-delivery proof; no model or real task is used."""
import copy
import hashlib
import json
import threading
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import pytest

from theory_master import supervisor_benchmark_recovery as recovery
from theory_master import supervisor_repair_receipts as receipts
from theory_master import supervisor
from theory_master.invocations import stable_hash
from theory_master.contract_stages import role_contract
from theory_master.native_goal import native_goal_setup_turn
from theory_master.runtime import write_json
from theory_master.runtime_supervisor_review import review_runtime_failure
from theory_master.scientific_objective import frozen_science_digest, serialize_frozen_science
from test_supervisor_repair_receipts_20261022 import stopped as method_stopped, method_receipt


def rows(path, values):
    path.write_text("".join(json.dumps(value) + "\n" for value in values))


@pytest.fixture
def stopped(tmp_path):
    engine = Path(receipts.__file__).resolve().parent
    root, home, benchmark = tmp_path / "research", tmp_path / "task", tmp_path / "benchmark"
    cycle = root / "1"
    for path in (cycle / "checkpoints", cycle / "workspace/.tm-handoff", cycle / "invocation_artifacts",
                 root / ".theory-master", home, benchmark):
        path.mkdir(parents=True)
    (cycle / ".theory-master-cycle").write_text("theory-master/v2\ntest\n")
    lock = {"id": "H-1", "hypothesis": "Original hypothesis", "scope": "Original scope",
            "decisive_test": "Original test", "status": "locked"}
    invocation, thread = "inv-1-investigator-original", "original-thread"
    start, finish = "2026-11-19T02:37:59+08:00", "2026-11-19T02:39:30+08:00"
    checkpoint = cycle / "checkpoints/000001-started.json"
    write_json(checkpoint, {"cycle_id": "1", "hypothesis_lock": lock})
    config = home / "theory-master.config.json"
    write_json(config, {"schema": "theory-master/system/v1", "path_model": "theory-master/task-path/v2",
                       "task_home": str(home), "plan_path": str(home / "plan.md"),
                       "research_root": str(root), "benchmark_root": str(benchmark)})
    write_json(root / ".theory-master/task.json", {"schema": "theory-master/task/v2", "config_path": str(config)})
    (benchmark / "source_manifest.json").write_text('{"public_input":"original"}')
    finding = {"statement": "Original authored claim", "provenance": {
        "source": "benchmark_provided", "split": "validation", "declaration_ref": "benchmark/source_manifest.json"}}
    handoff = cycle / "workspace/.tm-handoff" / f"{invocation}.json"
    write_json(handoff, {"findings": [finding]})
    output = {"findings": [finding]}
    manifest = cycle / "invocation_artifacts" / f"{invocation}.json"
    artifact = cycle / "workspace/calculation.json"
    artifact.write_text('{"authored":"preserved"}')
    write_json(manifest, {"schema": "theory-master/invocation-artifacts/v1", "invocation_id": invocation,
                         "role": "investigator", "stage": "investigate", "workspace": str(cycle / "workspace"),
                         "status": "failed", "created": [{"path": str(artifact), "relative_path": artifact.name,
                             "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(), "size": artifact.stat().st_size}],
                         "modified": [], "deleted": []})
    state = {"status": "failed", "phase": "failed", "cycle_id": "1", "resumable": True,
             "protocol_health": "valid", "scientific_verdict": None, "execution_active": False,
             "hypothesis_lock": lock, "last_safe_checkpoint": str(checkpoint),
             "last_invocation_id": invocation, "active_invocation_id": None,
             "threads": {"attempt-0001:investigator": thread}, "agent_outputs": {"investigator": output},
             "last_invocation_failure": {"invocation_id": invocation, "role": "investigator", "stage": "investigate",
                 "failure_phase": "semantic_closure", "error_type": "ValueError"},
             "recovery": {"action": "error", "kind": "ValueError", "category": "runtime_validation",
                          "message": recovery.ERROR, "at": finish},
             "runtime_source_provenance": {"sdk_git_sha": recovery.ORIGINAL_HEAD,
                 "sdk_git_root": str(engine.parent), "source_root": str(engine.parent),
                 "source_coherent": True, "source_coherence_errors": [],
                 "module_sha256": {name: recovery.ORIGINAL_HASHES[name] for name in recovery._RECORDED_CHAIN},
                 "module_paths": {name: str(engine / name) for name in recovery._RECORDED_CHAIN}}}
    rows(cycle / "invocations.jsonl", [
        {"invocation_id": invocation, "state": "prepared", "role": "investigator", "stage": "investigate",
         "commit_state": "not_committed", "logical_key": "original-slot",
         "contract_ref": role_contract("investigator", phase="investigate").descriptor()},
        {"invocation_id": invocation, "state": "running", "thread_id": thread},
        {"invocation_id": invocation, "state": "failed", "failure_phase": "semantic_closure",
         "error_type": "ValueError", "error": recovery.ERROR, "invocation_artifact_manifest": str(manifest)},
    ])
    rows(cycle / "events.jsonl", [{"type": "role.started", "at": start, "payload": {
        "invocation_id": invocation, "role": "investigator", "stage": "investigate", "thread_id": thread}}])
    binding = {"schema": "theory-master/execution-limit-handoff/v1", "invocation_id": invocation,
               "frozen_objective_sha256": frozen_science_digest(lock),
               "workspace_path": str(cycle / "workspace"), "handoff_path": str(handoff)}
    objective = "Complete this frozen science: " + serialize_frozen_science(lock)
    setup = native_goal_setup_turn(json.dumps({"sdk_execution_limit_handoff": binding}), objective)
    rows(cycle / "runtime_events.jsonl", [
        {"ts": start, "event": "AGENT_INPUT", "role": "investigator", "stage": "investigate",
         "input_kind": "native_goal_setup_turn", "content": setup},
        {"ts": start, "event": "AGENT_INPUT", "role": "investigator", "stage": "investigate",
         "input_kind": "native_goal_objective", "content": objective},
        {"ts": finish, "event": "RUN_FAILED", "error": recovery.ERROR, "error_kind": "ValueError",
         "failure_category": "runtime_validation", "resumable": True},
    ])
    trace = "Traceback (most recent call last):\n" + "".join(
        f'  File "{engine / name}", line 1, in original\n'
        for name in ("invocation_commit.py", "evidence.py", "evidence_provenance.py"))
    (root / ".theory-master/runner.log").write_text(trace + "ValueError: " + recovery.ERROR + "\n")
    write_json(cycle / "research_state.json", state)
    return engine, cycle, state


def validate(stopped):
    engine, cycle, state = stopped
    return recovery.validate_existing_benchmark_provenance(cycle, state, engine=engine)


def test_receipt_proves_only_preserved_delivery_and_alias_repair(stopped):
    _, cycle, state = stopped
    original = copy.deepcopy(state)
    files = {str(p): p.read_bytes() for p in cycle.rglob("*") if p.is_file()}
    record = validate(stopped)
    assert record is not None
    assert record["validator"] == recovery.VALIDATOR
    evidence = json.loads(record["tests"])
    assert evidence["authority"] == "verified_original_success_return_path"
    assert evidence["raw_findings_sha256"] == stable_hash(state["agent_outputs"]["investigator"])
    assert evidence["resume_preparation"] == "saved_delivery_semantic_closure"
    assert set(evidence["evidence_paths"]) == set(record["extra_inputs"])
    assert receipts.inspect_repair_receipt(cycle, state) == record
    assert {k:v for k,v in state.items() if not k.startswith("supervisor_")} == original
    assert {path:Path(path).read_bytes() for path in files} == files
    assert not (cycle / "evidence").exists()


def test_exact_original_run_failed_projection_is_not_a_provider_error(stopped):
    _,cycle,_=stopped
    projection=json.loads((cycle / 'runtime_events.jsonl').read_text().splitlines()[-1])
    assert projection == {'ts':'2026-11-19T02:39:30+08:00','event':'RUN_FAILED',
                          'error':recovery.ERROR,'error_kind':'ValueError',
                          'failure_category':'runtime_validation','resumable':True}
    assert validate(stopped) is not None


@pytest.mark.parametrize("mutation", [
    "phase", "thread", "active_invocation", "raw", "agent_output", "artifact", "manifest",
    "foreign_setup", "objective", "source_head", "source_hash", "trace", "no_benchmark",
    "foreign_config", "declaration_missing", "declaration_symlink", "traversal", "provider_error",
    "pause", "pending_pause", "budget", "protocol", "verdict", "committed", "duplicate_setup", "later_invocation",
    "corrupt_native", "contract", "oversize", "unscoped_provider_error",
    "projection_message", "projection_time", "projection_event",
])
def test_historical_proof_fails_closed(stopped, mutation):
    _, cycle, state = stopped
    if mutation == "phase": state["last_invocation_failure"]["failure_phase"] = "invoke"
    elif mutation == "thread": state["threads"] = {"investigator": "foreign"}
    elif mutation == "active_invocation": state["active_invocation_id"] = "foreign"
    elif mutation == "raw": (cycle / "workspace/.tm-handoff" / f'{state["last_invocation_id"]}.json').write_text('{}')
    elif mutation == "oversize": (cycle / "workspace/.tm-handoff" / f'{state["last_invocation_id"]}.json').write_bytes(b'x'*(1024*1024+1))
    elif mutation == "agent_output": state["agent_outputs"]["investigator"] = {"findings": ["foreign"]}
    elif mutation == "artifact": (cycle / "workspace/calculation.json").write_text('{}')
    elif mutation == "manifest":
        p=cycle / "invocation_artifacts" / f'{state["last_invocation_id"]}.json';d=json.loads(p.read_text());d['status']='completed';write_json(p,d)
    elif mutation in {"foreign_setup", "objective", "duplicate_setup", "provider_error"}:
        p=cycle / "runtime_events.jsonl";data=[json.loads(line) for line in p.read_text().splitlines()]
        if mutation == "foreign_setup": data[0]['invocation_id']='foreign'
        elif mutation == "objective": data[1]['content']='changed science'
        elif mutation == "duplicate_setup": data.append(data[0])
        else: data.append({'ts':data[0]['ts'],'event':'LLM_TURN_FAILED','thread_id':'original-thread','error':'provider failed'})
        rows(p,data)
    elif mutation == "source_head": state['runtime_source_provenance']['sdk_git_sha']='unreviewed'
    elif mutation == "source_hash": state['runtime_source_provenance']['module_sha256']['codex_backend.py']='tampered'
    elif mutation == "trace": (cycle.parent / '.theory-master/runner.log').write_text(recovery.ERROR)
    elif mutation in {"no_benchmark", "foreign_config"}:
        config=Path(json.loads((cycle.parent / '.theory-master/task.json').read_text())['config_path']);d=json.loads(config.read_text())
        if mutation == "no_benchmark":d.pop('benchmark_root')
        else:d['research_root']=str(cycle.parent.parent / 'foreign')
        write_json(config,d)
    elif mutation in {"declaration_missing", "declaration_symlink"}:
        p=cycle.parent.parent / 'benchmark/source_manifest.json';p.unlink()
        if mutation.endswith('symlink'):p.symlink_to(cycle / 'workspace/calculation.json')
    elif mutation == "traversal":
        state['agent_outputs']['investigator']['findings'][0]['provenance']['declaration_ref']='benchmark/../task/source_manifest.json'
        write_json(cycle / 'workspace/.tm-handoff' / f'{state["last_invocation_id"]}.json',state['agent_outputs']['investigator'])
    elif mutation == "pause":state['recovery']['action']='pause'
    elif mutation == "unscoped_provider_error":
        p=cycle / 'runtime_events.jsonl';data=[json.loads(line) for line in p.read_text().splitlines()]
        data.append({'ts':data[0]['ts'],'event':'LLM_FAILED','error':'unscoped provider failure'});rows(p,data)
    elif mutation.startswith('projection_'):
        p=cycle / 'runtime_events.jsonl';data=[json.loads(line) for line in p.read_text().splitlines()]
        if mutation=='projection_message':data[-1]['error']='another unknown error'
        elif mutation=='projection_time':data[-1]['ts']=data[0]['ts']
        else:data[-1]['event']='LLM_FAILED'
        rows(p,data)
    elif mutation == "pending_pause":write_json(cycle / 'recovery_control.json',{'action':'pause'})
    elif mutation == "budget":state['investigator_budget']={'exhausted':True}
    elif mutation == "protocol":state['protocol_health']='violation'
    elif mutation == "verdict":state['scientific_verdict']='passed'
    elif mutation == "corrupt_native":state['native_goal']=[]
    elif mutation in {"committed", "later_invocation", "contract"}:
        p=cycle / 'invocations.jsonl';data=[json.loads(line) for line in p.read_text().splitlines()]
        if mutation=='committed':data[-1]['state']='committed'
        elif mutation=='contract':data[0]['contract_ref']['contract_version']=1
        else:data.append({'invocation_id':'later','state':'prepared'})
        rows(p,data)
    write_json(cycle / 'research_state.json', state)
    assert validate(stopped) is None
    assert receipts.RECEIPT_KEY not in state


@pytest.mark.parametrize('mutation', ['declaration', 'runtime', 'checkpoint', 'source', 'parent_symlink'])
def test_receipt_rechecks_all_hashes_and_safe_paths(stopped, mutation):
    _, cycle, state = stopped
    record=validate(stopped)
    assert record
    if mutation=='declaration':(cycle.parent.parent / 'benchmark/source_manifest.json').write_text('{}')
    elif mutation=='runtime':(cycle / 'runtime_events.jsonl').write_text('{}\n')
    elif mutation=='checkpoint':Path(state['last_safe_checkpoint']).write_text('{}')
    elif mutation=='source':
        record['source_manifest']={}
        write_json(Path(state[receipts.RECEIPT_KEY]['path']),record)
    else:
        path=cycle / 'workspace';target=cycle / 'moved-workspace';path.rename(target);path.symlink_to(target,target_is_directory=True)
    with pytest.raises(ValueError):
        receipts.inspect_repair_receipt(cycle,state)


def test_disabled_repair_consumes_existing_receipt_once_without_model(method_stopped):
    engine, cycle, state, _ = method_stopped
    method_receipt(method_stopped)
    args=dict(backend=None,research_root=cycle.parent,cycle_dir=cycle,state=state,
              state_path=cycle / 'research_state.json',incident_id='offline',existing_receipt_only=True,scheduler_locked=True)
    with patch.object(receipts,'__file__',str(engine / 'supervisor_repair_receipts.py')), \
         patch.object(receipts,'validate_existing_method',side_effect=AssertionError('no validator')), \
         patch('theory_master.supervisor_continuity.attempt_engineering_skill',side_effect=AssertionError('no model')):
        assert review_runtime_failure(**args)['action']=='resume'
        assert review_runtime_failure(**args)['action']=='hold'
    assert len(state['supervisor_consumed_repairs'])==1


@pytest.mark.parametrize('kind',['missing','stale'])
def test_disabled_repair_rejects_missing_or_stale_receipt(method_stopped,kind):
    engine,cycle,state,_=method_stopped
    if kind=='stale':
        method_receipt(method_stopped)
        state['recovery']['at']='new-failure'
    with patch.object(receipts,'__file__',str(engine / 'supervisor_repair_receipts.py')), \
         patch.object(receipts,'validate_existing_method',side_effect=AssertionError('no validator')):
        assert review_runtime_failure(backend=None,research_root=cycle.parent,cycle_dir=cycle,
            state=state,state_path=cycle / 'research_state.json',incident_id='offline',existing_receipt_only=True,scheduler_locked=True)['action']=='hold'


def test_disabled_supervisor_never_enters_automatic_repair(method_stopped):
    _,cycle,_,_=method_stopped
    with patch.object(supervisor,'_consume_existing_repair_credit',return_value=False) as consume, \
         patch.object(supervisor,'_handle_failed_state',side_effect=AssertionError('no automatic repair')):
        assert not supervisor._recover_failed_state(cycle.parent,repair_enabled=False,stop=threading.Event())
    consume.assert_called_once()


def test_disabled_supervisor_uses_real_existing_credit_gate_once(method_stopped):
    engine,cycle,state,_=method_stopped
    method_receipt(method_stopped)
    write_json(cycle / 'research_state.json',state)
    stop=threading.Event()
    with patch.object(receipts,'__file__',str(engine / 'supervisor_repair_receipts.py')), \
         patch.object(supervisor,'_supervisor_backend',side_effect=AssertionError('no backend')), \
         patch.object(supervisor,'_handle_failed_state',side_effect=AssertionError('no automatic repair')):
        assert supervisor._recover_failed_state(cycle.parent,repair_enabled=False,stop=stop)
        assert not supervisor._recover_failed_state(cycle.parent,repair_enabled=False,stop=stop)
    assert len(json.loads((cycle / 'research_state.json').read_text())['supervisor_consumed_repairs'])==1


def test_prepare_is_an_official_offline_receipt_entrypoint(stopped):
    _,cycle,state=stopped
    original=copy.deepcopy(state)
    record=recovery.prepare_benchmark_recovery(cycle.parent)
    saved=json.loads((cycle / 'research_state.json').read_text())
    assert receipts.inspect_repair_receipt(cycle,saved)==record
    assert {k:v for k,v in saved.items() if not k.startswith('supervisor_')}==original


def test_prepare_respects_held_runner_lock(stopped):
    import fcntl
    _,cycle,_=stopped
    with (cycle.parent / '.theory-master/runner.lock').open('a+') as handle:
        fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError,match='runner.lock'):
            recovery.prepare_benchmark_recovery(cycle.parent)


def journal_delivery(stopped):
    from theory_master.native_delivery_journal import record_native_delivery
    _,cycle,state=stopped
    invocation=state['last_invocation_id']
    events=[json.loads(line) for line in (cycle / 'runtime_events.jsonl').read_text().splitlines()]
    owner=SimpleNamespace(cycle_dir=cycle,backend=SimpleNamespace(last_native_goal={
        'status':'complete','thread_id':'original-thread'}),
        _persist=lambda value:write_json(cycle / 'research_state.json',value))
    session=SimpleNamespace(owner=owner,invocation_id=invocation,identifier='original-thread',
        logical_key='original-slot',role='investigator',stage='investigate',
        contract=role_contract('investigator',phase='investigate'),
        raw=json.dumps(state['agent_outputs']['investigator']),
        handoff_path=cycle / 'workspace/.tm-handoff' / f'{invocation}.json',
        native_objective=events[1]['content'],
        limit_binding={'frozen_objective_sha256':frozen_science_digest(state['hypothesis_lock'])},state=state)
    return record_native_delivery(session)


def test_future_delivery_uses_observed_sdk_return_journal(stopped):
    _,cycle,state=stopped
    state['runtime_source_provenance']['sdk_git_sha']='future-reviewed-head'
    path=journal_delivery(stopped)
    record=validate(stopped)
    assert record
    proof=json.loads(record['tests'])
    assert proof['authority']=='sdk_return'
    assert proof['original_source_hashes']=={}
    assert str(path) in record['extra_inputs']
    assert receipts.inspect_repair_receipt(cycle,state)==record


@pytest.mark.parametrize('mutation',['status','invocation','thread','raw','objective','native','foreign_path'])
def test_invalid_future_journal_never_falls_back_to_historical_proof(stopped,mutation):
    _,cycle,state=stopped
    path=journal_delivery(stopped)
    data=json.loads(path.read_text())
    if mutation=='status':data['native_goal']['status']='blocked'
    elif mutation=='invocation':data['invocation_id']='foreign'
    elif mutation=='thread':data['thread_id']='foreign'
    elif mutation=='raw':data['raw']='{}'
    elif mutation=='objective':data['objective_sha256']='different'
    elif mutation=='native':state['native_goal']['status']='usageLimited'
    else:state['native_goal']['delivery_receipt']=str(cycle / 'foreign.json')
    write_json(path,data)
    write_json(cycle / 'research_state.json',state)
    assert validate(stopped) is None
