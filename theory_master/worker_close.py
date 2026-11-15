"""Close one auxiliary Worker through pause, verified release and archive.

This control path never invokes a model. The durable intent fences automatic
resume and allocation while an independent helper waits for the owner to exit.
"""
from __future__ import annotations

import argparse
import fcntl
import sys
import time
from pathlib import Path
from typing import Any

from .multi_worker import CycleClaims, _read_json, _save_json, utcnow
from . import worker_runtime as runtime


def _finish(spec: Any, worker: dict[str, Any]) -> dict[str, Any]:
    """Caller holds scheduler.lock; no process may still own the Cycle."""
    root = Path(spec.research_root).resolve()
    wid = worker['worker_id']
    claims = CycleClaims(root, runtime._graph_path(spec))
    owned = [c for c in claims.list_claims() if c.get('worker_id') == wid]
    if any(c.get('claim_id') != worker.get('claim_id')
           or c.get('fencing_epoch') != worker.get('epoch')
           or (c.get('cycle_id') is not None and c.get('cycle_id') != worker.get('current_cycle'))
           for c in owned):
        raise ValueError('Worker claim changed; refusing to close')
    if worker.get('current_cycle') and worker.get('status') != 'RELEASED':
        runtime.reconcile_released_worker_cycle(root, worker)
    for claim in owned:
        claims.update(wid, claim['claim_id'], claim['fencing_epoch'], action='release')
    # Capacity is persisted before hiding; failed reconciliation leaves a
    # visible, retryable card, and never silently abandons an owned claim.
    from .worker_mode import reduce_one_worker_request
    visible = sum(not w.get('dismissed') for w in runtime.list_workers(root)) - 1
    capacity = reduce_one_worker_request(root, registered_count=max(1, visible), require_pending=False,
                                         operation_id=f"close:{wid}")
    runtime.save_worker(root, wid, status='RELEASED', dismissed=True,
                        claim_id=None, epoch=None, pid=None, pid_start_ticks=None,
                        closed_at=utcnow().isoformat(), close_error=None)
    return {'worker_id': wid, 'action': 'close', 'status': 'closed',
            'history_preserved': True, **capacity}


def close_worker(spec: Any, worker_id: str, *, launch: bool = True) -> dict[str, Any]:
    if worker_id == 'worker-1':
        raise ValueError('Agent 1 is the permanent primary Agent')
    root = Path(spec.research_root).resolve()
    directory = runtime.worker_dir(root, worker_id)
    lock_path = root / '.theory-master' / 'scheduler.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        worker = runtime.read_worker(root, worker_id)
        if not worker or worker.get('schema') != runtime.SCHEMA:
            raise ValueError('Worker does not exist')
        if worker.get('dismissed'):
            return {'worker_id': worker_id, 'action': 'close', 'status': 'closed', 'history_preserved': True}
        # PRESERVING may have a separately owned paid job. Closing it requires
        # that job to finish first; never interfere with its checkpoint writes.
        if worker.get('status') == 'PRESERVING':
            raise ValueError('Wait for research preservation to finish before closing')
        live = runtime.process_identity_alive(worker.get('pid'), worker.get('pid_start_ticks'))
        if live:
            cycle = str(worker.get('current_cycle') or '')
            if cycle and (not cycle.isascii() or not cycle.isdigit() or int(cycle) < 1):
                raise ValueError('Worker has an invalid Cycle')
            if cycle:
                cycle_dir = root / cycle
                state_path = cycle_dir / 'research_state.json'
                if cycle_dir.is_symlink() or state_path.is_symlink():
                    raise ValueError('Unsafe Worker checkpoint')
                claim = runtime._worker_live_claim(CycleClaims(root, runtime._graph_path(spec)), worker)
                if not runtime._checkpoint_matches_claim(_read_json(state_path), worker_id, claim, cycle):
                    raise ValueError('Worker/Cycle claim mismatch; refusing to request pause')
            control = root / cycle / 'recovery_control.json' if cycle else None
            if control is not None and control.is_symlink():
                raise ValueError('Unsafe Worker control file')
            pending = _read_json(control) if control is not None and control.exists() else {}
            if control is not None and control.exists() and pending.get('action') not in {'pause', 'kill'}:
                raise ValueError('Worker already has another pending control action')
            if control is not None and not control.exists():
                _save_json(control, {'action': 'pause', 'reason': 'operator_close_worker',
                                     'requested_at': utcnow().isoformat()})
        # Kept separate from status because the running owner still updates it.
        runtime.save_worker(root, worker_id, close_requested=True, close_error=None)
        if not live:
            try:
                return _finish(spec, runtime.read_worker(root, worker_id))
            except (OSError, ValueError, RuntimeError) as exc:
                runtime.save_worker(root, worker_id, close_error=str(exc)[:500])
                raise
    if launch:
        try:
            with (directory / 'close.log').open('a', encoding='utf-8') as output:
                runtime._launch_detached_service(
                    [sys.executable, '-m', 'theory_master.worker_close',
                     '--research-root', str(root), '--worker', worker_id],
                    root=root, env=runtime._task_scoped_process_env(root), output=output,
                    kind='worker-close')
        except (OSError, RuntimeError) as exc:
            runtime.save_worker(root, worker_id, close_error=str(exc)[:500])
            raise
    return {'worker_id': worker_id, 'action': 'close', 'status': 'closing',
            'effective_at': 'after_safe_pause_and_process_exit', 'history_preserved': True}


def watch_close(spec: Any, worker_id: str) -> int:
    root = Path(spec.research_root).resolve()
    with (runtime.worker_dir(root, worker_id) / 'close_owner.lock').open('a+') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        while True:
            worker = runtime.read_worker(root, worker_id)
            if not worker.get('close_requested') or worker.get('dismissed'):
                return 0
            try:
                outcome = close_worker(spec, worker_id, launch=False)
                if outcome['status'] == 'closed':
                    return 0
            except (OSError, ValueError, RuntimeError) as exc:
                runtime.save_worker(root, worker_id, close_error=str(exc)[:500])
                return 1  # Visible error; retry requires explicit operator action.
            time.sleep(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--research-root', type=Path, required=True)
    parser.add_argument('--worker', required=True)
    args = parser.parse_args()
    from .specification import TaskSpec
    return watch_close(TaskSpec.from_task_root(args.research_root), args.worker)


if __name__ == '__main__':
    raise SystemExit(main())
