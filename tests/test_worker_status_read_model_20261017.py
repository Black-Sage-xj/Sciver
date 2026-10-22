"""Legacy Supervisor must not mark a current V2 Worker cycle ORPHANED."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from theory_master.observability.models import blank_run_state
from theory_master.observability.projection import reconcile_runtime_state
from theory_master import cli_discovery


def test_tui_uses_live_current_worker_not_stale_global_runner():
    with TemporaryDirectory() as tmp:
        root=Path(tmp)
        snap=blank_run_state()
        snap.update({'research_root':str(root),'status':'RUNNING','current_agent':'investigator'})
        state={'status':'active','cycle_id':'130','worker_id':'worker-1',
               'execution_active':True,'owner_pid':99999999}
        worker={'worker_id':'worker-1','status':'RUNNING','current_cycle':'130',
                'pid':12345,'process_alive':True}
        with patch('theory_master.worker_runtime.list_workers',return_value=[worker]), \
             patch('theory_master.runtime_manager.runtime_health',return_value={'state':'ORPHANED','active':False}):
            reconcile_runtime_state(snap,state)
        assert snap['status']=='RUNNING'
        assert snap['runtime_health']=='WORKER_RUNNING'
        assert snap['execution_active'] is True
        assert snap['owner_pid']==12345


def test_tui_rejects_worker_identity_for_wrong_cycle():
    with TemporaryDirectory() as tmp:
        root=Path(tmp)
        snap=blank_run_state()
        snap.update({'research_root':str(root),'status':'RUNNING','current_agent':'investigator'})
        state={'status':'active','cycle_id':'130','worker_id':'worker-1',
               'execution_active':True,'owner_pid':99999999}
        worker={'worker_id':'worker-1','status':'RUNNING','current_cycle':'129',
                'pid':12345,'process_alive':True}
        with patch('theory_master.worker_runtime.list_workers',return_value=[worker]), \
             patch('theory_master.runtime_manager.runtime_health',return_value={'state':'ORPHANED','active':False}):
            reconcile_runtime_state(snap,state)
        assert snap['status']=='ORPHANED'
