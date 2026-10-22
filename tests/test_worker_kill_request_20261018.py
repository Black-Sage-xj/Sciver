"""Worker-scoped safe Cycle termination never targets another Agent or the Master."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master import worker_runtime as wr
from tm_observer.run_control import _validated_value


def _fixture(tmp_path: Path, monkeypatch):
    root=tmp_path
    worker_id="worker-1"
    worker_dir=root/".theory-master/workers"/worker_id
    worker_dir.mkdir(parents=True)
    (root/"4").mkdir()
    wr._save_json(worker_dir/"runtime.json",{
        "schema":wr.SCHEMA,"worker_id":worker_id,
        "status":"RUNNING","current_cycle":"4","pid":123456,
        "pid_start_ticks":101,"claim_id":"claim123","epoch":1,
        "entry_node_id":"H-0001","target_h_id":"H-0001",
    })
    wr._save_json(root/".theory-master/cycle_claims.json",{
        "schema":"theory-master/cycle-claims/v1","epoch":1,"history":[],
        "claims":{"claim123":{"claim_id":"claim123","fencing_epoch":1,"worker_id":worker_id,
                              "cycle_id":"4","hypothesis_id":"H-0001","status":"claimed"}}})
    wr._save_json(root/"4/research_state.json",{
        "worker_id":worker_id,"hypothesis_lock":{"id":"H-0001"},
        "status":"active","execution_active":True,
        "sdk_runtime_capabilities":{"abandon_cycle_v1":True}
    })
    monkeypatch.setattr(wr,"process_identity_alive",lambda pid,ticks: pid==123456 and ticks==101)
    spec=SimpleNamespace(research_root=root,branch_canvas=root/"canvas.json")
    return spec,root


def test_kill_request_is_worker_scoped_and_checkpoint_only(tmp_path, monkeypatch):
    spec,root=_fixture(tmp_path,monkeypatch)
    assert _validated_value("worker_control",{"worker_id":"worker-1","operation":"kill"})
    assert wr.worker_action(spec,"worker-1","kill")["status"]=="STOP_REQUESTED"
    command=json.loads((root/"4/recovery_control.json").read_text())
    assert command["action"]=="abandon_cycle"
    assert command["source_action"]=="kill"
    assert wr.read_worker(root,"worker-1")["status"]=="STOP_REQUESTED"
    assert not (root/"5/recovery_control.json").exists()
    with pytest.raises(ValueError):
        wr.worker_action(spec,"worker-1","kill")


@pytest.mark.parametrize("change",[
    {"state":{"worker_id":"worker-2"}},
    {"state":{"hypothesis_lock":{"id":"H-0002"}}},
    {"worker":{"claim_id":"stale-claim"}},
    {"state":{"sdk_runtime_capabilities":{}}},
    {"state":{"execution_active":False}},
    {"worker":{"current_cycle":"5"}},
    {"worker":{"pid_start_ticks":999}},
])
def test_kill_rejects_unverified_worker_or_cycle(tmp_path,monkeypatch,change):
    spec,root=_fixture(tmp_path,monkeypatch)
    if "state" in change:
        path=root/"4/research_state.json"
        state=json.loads(path.read_text())
        state.update(change["state"])
        wr._save_json(path,state)
    else:
        path=root/".theory-master/workers/worker-1/runtime.json"
        state=json.loads(path.read_text())
        state.update(change["worker"])
        wr._save_json(path,state)
    with pytest.raises(ValueError):
        wr.worker_action(spec,"worker-1","kill")
    assert not (root/"4/recovery_control.json").exists()
