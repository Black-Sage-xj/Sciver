"""Offline transaction checks for adopting an interrupted old Cycle."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from theory_master.worker_migration import adopt_stopped_cycle
from theory_master.worker_runtime import list_workers
from theory_master.multi_worker import BranchClaims
from theory_master.science_graph_store import ScienceGraphStore


def test_adopt_existing_checkpoint_without_creating_cycle(tmp_path):
    root=tmp_path/'research';root.mkdir()
    cv=tmp_path/'exploration.canvas'
    cv.write_text(json.dumps({'nodes': [], 'edges': []}))  # display-only projection
    graph=tmp_path/'science_graph.sqlite3'
    with ScienceGraphStore(graph) as store:
        store.add_hypothesis('H-0089', title='Frozen hypothesis',
                             summary='主张：受检命题；条件：冻结范围；状态：待检验。')
        store.register_cycle_hypothesis(cycle_id='126', hypothesis_id='H-0089')
        store.set_frontier(branch_id='frontier:H-0089', hypothesis_id='H-0089',
                           status='active', reason='current eligible frontier', preferred=True)
    plan=tmp_path/'research_plan.md';plan.write_text('Goal')
    cycle=root/'126';(cycle/'checkpoints').mkdir(parents=True)
    checkpoint=cycle/'checkpoints'/'000007-attempt-started.json';checkpoint.write_text('{}')
    state={'cycle_id':'126','status':'active','resumable':True,'execution_active':True,
           'last_safe_checkpoint':str(checkpoint),'hypothesis_lock':{'id':'H-0089','status':'locked'},
           'runtime_codex_account':'1', 'phase':'investigator:running'}
    (cycle/'research_state.json').write_text(json.dumps(state))
    backup=root/'.theory-master'/'migration-backups'/'fixture';backup.mkdir(parents=True)
    files={'cycle126/research_state.json':cycle/'research_state.json','science_graph.sqlite3':graph,
           'research_plan.md':plan}
    manifest={'cycle':'126','files':[]}
    for name,source in files.items():
        target=backup/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source.read_bytes())
        manifest['files'].append({'backup':name,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    (backup/'manifest.json').write_text(json.dumps(manifest))
    spec=SimpleNamespace(research_root=root,branch_canvas=cv,document=plan)
    created=adopt_stopped_cycle(spec,'126',snapshot=backup)
    assert created['worker_id']=='worker-1' and created['cycle']=='126'
    assert created['entry_node_id']=='H-0089'
    assert created['account']=='1'
    assert [p.name for p in root.iterdir() if p.name.isdigit()]==['126']
    updated=json.loads((cycle/'research_state.json').read_text())
    assert updated['hypothesis_lock']==state['hypothesis_lock']
    assert updated['status']=='interrupted' and updated['resumable']
    assert updated['worker_id']=='worker-1' and updated['claimed_frontier_id']=='H-0089'
    assert BranchClaims(root,graph).status()['claims']['H-0089']['worker_id']=='worker-1'
    assert list_workers(root)[0]['status']=='PAUSED'
    assert json.loads((backup/'cycle126/research_state.json').read_text())==state
    with pytest.raises((RuntimeError,ValueError)):
        adopt_stopped_cycle(spec,'126',snapshot=backup)


def test_adoption_requires_completed_backup(tmp_path):
    root=tmp_path/'research';root.mkdir();backup=root/'.theory-master'/'migration-backups'/'bad'
    spec=SimpleNamespace(research_root=root,branch_canvas=tmp_path/'canvas.json')
    with pytest.raises((RuntimeError,ValueError)):
        adopt_stopped_cycle(spec,'126',snapshot=backup)
