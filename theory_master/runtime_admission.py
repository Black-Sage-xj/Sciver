"""Shared admission protocol for single-owner runners and Cycle Claim owners.

The admission lock is always acquired before a claim-registry lock. A held
runner lock is execution ownership even if its JSON projection was lost.
"""
from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def admission_lock(root: Path) -> Iterator[None]:
    directory = Path(root).resolve() / '.theory-master'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'owner_admission.lock').open('a+') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _record(path: Path, schema: str) -> dict:
    if path.is_symlink():
        raise RuntimeError('Unsafe runtime ownership registry: ' + path.name)
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('Unsafe runtime ownership registry: ' + path.name)
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError('Unreadable runtime ownership registry: ' + path.name) from exc
    if not isinstance(value, dict) or value.get('schema') != schema:
        raise RuntimeError('Invalid runtime ownership registry: ' + path.name)
    return value


def assert_no_worker_claims(root: Path) -> None:
    """Caller holds admission_lock; both claim generations remain authoritative."""
    directory = Path(root).resolve() / '.theory-master'
    _record(directory / 'runtime.json', 'theory-master/runtime/v1')
    for name, schema in (('branch_claims.json', 'theory-master/branch-claims/v1'),
                         ('cycle_claims.json', 'theory-master/cycle-claims/v1')):
        record = _record(directory / name, schema)
        claims = record.get('claims') if record else {}
        if not isinstance(claims, dict):
            raise RuntimeError('Invalid claim registry: ' + name)
        if claims:
            raise RuntimeError('Existing multi-Worker claims block single-owner runtime')


def assert_no_single_owner(root: Path) -> None:
    """Caller holds admission_lock; check the lock and strict persisted identity."""
    from .runtime_manager import runtime_health
    directory = Path(root).resolve() / '.theory-master'
    _record(directory / 'runtime.json', 'theory-master/runtime/v1')
    for name, schema in (('branch_claims.json', 'theory-master/branch-claims/v1'),
                         ('cycle_claims.json', 'theory-master/cycle-claims/v1')):
        record = _record(directory / name, schema)
        if not isinstance(record.get('claims') if record else {}, dict):
            raise RuntimeError('Invalid claim registry: ' + name)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'runner.lock').open('a+') as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Single-owner runner.lock is held; Worker admission refused') from exc
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    if runtime_health(root).get('active'):
        raise ValueError('Legacy single-Worker runtime is active; claims must not run concurrently')
