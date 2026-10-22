"""Typed local-route repair; remote provider health needs independent evidence.

Configuration validity is distinct from provider availability and from whether
an old Cycle can consume that configuration at its guarded resume boundary.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from .supervisor_repair_receipts import CONFIG_VALIDATOR, _file_hash, _record_validation, source_manifest, failure_binding
from .supervisor_recovery import failure_details

LOCAL_ERRORS = frozenset({
    'unknown task role model/provider', 'invalid task role reasoning effort',
    'invalid task role activation mode', 'invalid task role route entry',
})


def configuration_failure(state: dict) -> bool:
    message = str(failure_details(state).get('message') or '').lower()
    return message in LOCAL_ERRORS or any(word in message for word in (
        'model is not supported', 'authentication failed', 'invalid_api_key',
        '403 forbidden', 'permission denied', 'unauthorized access'))


def validate_configuration(root: Path, cycle: Path, state: dict, *, log: str) -> dict:
    """Only local parser failures have a deterministic offline repair validator."""
    from .backend_config import read_task_role_overrides, task_route_catalog
    failure = failure_details(state)
    blocked = {'phase': 'blocked', 'validator': CONFIG_VALIDATOR}
    if failure.get('kind') != 'ValueError' or failure.get('message') not in LOCAL_ERRORS:
        return {**blocked, 'reason': 'provider_probe_required: local configuration cannot certify remote model support or authentication'}
    import re
    if (str(failure['message']) not in log or not re.search(r'File "[^"\n]*/theory_master/backend_config\.py", line \d+', log)):
        return {**blocked, 'reason': 'Configuration failure lacks attributable backend_config traceback'}
    read_task_role_overrides(root)
    supported = {(row['model'], row['provider']) for row in task_route_catalog()}
    frozen = dict(state.get('runtime_role_routes') or {})
    frozen.update(state.get('runtime_hot_role_routes') or {})
    if any(not isinstance(row, dict) or (row.get('model'), row.get('provider')) not in supported for row in frozen.values()):
        return {**blocked, 'reason': 'frozen_route_requires_repair: changing future defaults does not repair this Cycle route'}
    path = root / '.theory-master/web-role-routes.json'
    engine = Path(__file__).resolve().parent
    return _record_validation(cycle, state, engine, validator=CONFIG_VALIDATOR,
        module='web-role-routes.json', change_id='existing-local-route-config',
        tests='SDK parser succeeds for attributable local parser exception; frozen routes supported',
        manifest=source_manifest(engine), disposition='already_fixed',
        extra_inputs={str(path.resolve()): _file_hash(path)})


def attempt_configuration_repair(*, root: Path, cycle: Path, state: dict, review: dict,
                                 backend, model: str, provider: str) -> dict:
    from .supervisor_runtime_repair import _log_for_repair, _register_auto_change, _verify_auto_change
    from .backend_config import read_task_role_overrides, task_route_catalog
    from .runtime import atomic
    log = _log_for_repair(root, log_path=Path(review['log_path']) if review.get('log_path') else None)
    try:
        result = validate_configuration(root, cycle, state, log=log)
        return result
    except ValueError:
        pass  # Current route file still fails its parser.
    if failure_details(state).get('message') not in LOCAL_ERRORS or backend is None:
        return {'phase': 'blocked', 'reason': 'Configuration repair needs attributable local evidence and read-only planner'}
    path = root / '.theory-master/web-role-routes.json'
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('Unsafe configuration target')
    original_binding = failure_binding(cycle, state)
    before = path.read_bytes()
    payload = json.loads(before)
    prompt = ('Diagnose this local route parser failure using read-only tools. Return strict JSON '
        '{"roles":{role:{"model":string,"provider":string,"reasoning_effort":string,"activation":string}},"reason":string}. '
        'Propose only corrected existing role rows from the supported catalog. Do not change model tier merely '
        'to bypass a failure, credentials, scientific state or frozen Cycle routes. SDK validates and applies '
        'the proposal; remote authentication requires a separate provider probe.\n'
        + json.dumps({'failure': failure_details(state), 'configuration': payload,
                      'catalog': task_route_catalog(), 'handoff': review.get('repair_suggestion')}))
    thread = None
    try:
        thread, raw = backend.invoke_runtime_review(prompt, model, provider)
        proposal = json.loads(raw)
    finally:
        if thread and callable(getattr(backend, 'drop_thread', None)):
            backend.drop_thread(thread)
    if not isinstance(proposal, dict) or set(proposal) != {'roles', 'reason'} or not proposal['reason']:
        raise ValueError('Invalid typed configuration proposal')
    if not isinstance(proposal['roles'], dict) or set(proposal['roles']) != set(payload.get('roles') or {}):
        raise ValueError('Configuration repair cannot add or remove roles')
    after = json.dumps({**payload, 'roles': proposal['roles']}, ensure_ascii=False, indent=2) + '\n'
    with tempfile.TemporaryDirectory(prefix='tm-route-validator-') as temp:
        frozen = Path(temp)
        (frozen / '.theory-master').mkdir()
        (frozen / '.theory-master/web-role-routes.json').write_text(after)
        read_task_role_overrides(frozen)
    change_id = _register_auto_change(incident_id=str(review.get('session_id') or 'configuration'),
        cycle_dir=cycle, relative=Path('web-role-routes.json'), before=before, after=after.encode(),
        scope='Typed local route configuration repair; SDK parser validation')
    if not change_id or path.read_bytes() != before:
        raise ValueError('Configuration registration or compare-and-swap failed')
    current = json.loads((cycle / 'research_state.json').read_text())
    if failure_binding(cycle, current) != original_binding:
        raise ValueError('Cycle changed during configuration planning')
    atomic(path, after)
    if not _verify_auto_change(change_id, tests='Typed route proposal passed frozen SDK parser', after=after.encode()):
        return {'phase': 'blocked', 'reason': 'Configuration applied; change-register verification pending'}
    return validate_configuration(root, cycle, state, log=log)
