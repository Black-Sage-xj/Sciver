"""Verify an SDK-cleared completion from its original official tool response."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace


def _original_clear_source(state: dict) -> None:
    source = state.get('runtime_source_provenance') or {}
    head = source.get('sdk_git_sha', '')
    expected = (source.get('module_sha256') or {}).get('codex_goal_transport.py')
    if (source.get('source_coherent') is not True or source.get('source_coherence_errors')
            or not re.fullmatch('[0-9a-f]{40}', head) or not expected):
        raise ValueError('Original SDK transport identity is missing')
    package = Path(__file__).resolve().parent
    result = subprocess.run(['git', 'show', head + ':theory_master/codex_goal_transport.py'],
                            cwd=package.parent, capture_output=True, timeout=10, check=True)
    if hashlib.sha256(result.stdout).hexdigest() != expected:
        raise ValueError('Original clearing transport source changed')
    tree = ast.parse(result.stdout)
    deliver = next((node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                    and node.name == '_deliver'), None)
    complete = next((node for node in deliver.body if isinstance(node, ast.If)
                     and ast.unparse(node.test) == "goal.status == 'complete'"), None) if deliver else None
    calls = [(node.lineno, node.func.id if isinstance(node.func, ast.Name) else node.func.attr)
             for node in ast.walk(complete) if isinstance(node, ast.Call)
             and isinstance(node.func, (ast.Name, ast.Attribute))] if complete else []
    clear = [line for line, name in calls if name == 'clear_goal']
    reads = [line for line, name in calls if name == 'read_goal_findings']
    if not clear or not reads or min(clear) >= min(reads):
        raise ValueError('Original SDK did not clear its Goal before validating the handoff')

def read_cleared_completion(cycle: Path, state: dict, authorization: dict, previous: dict,
                            inputs: dict):
    from .codex_accounts import runtime_home
    from .operator_completed_delivery import _read
    proof = authorization.get('archived_goal_completion') or {}
    path = Path(str(proof.get('rollout_path') or ''))
    root = (runtime_home() / 'sessions').resolve()
    if not path.is_absolute() or not path.resolve().is_relative_to(root):
        raise ValueError('Completion archive is outside the original Codex runtime')
    raw = _read(path, inputs, 64 * 1024 * 1024)
    length = proof.get('byte_length', len(raw))
    if not isinstance(length, int) or not 0 < length <= len(raw):
        raise ValueError('Invalid original completion archive boundary')
    original, tail = raw[:length], raw[length:]
    if not original.endswith(b'\n') or hashlib.sha256(original).hexdigest() != proof.get('sha256'):
        raise ValueError('Original completion archive changed')
    for line in tail.splitlines():
        row = json.loads(line)
        payload = row.get('payload') or {}
        if (row.get('type') != 'event_msg' or payload.get('type') != 'thread_settings_applied'
                or payload.get('thread_id') != previous['thread_id']):
            raise ValueError('A later operation changed the completion archive')
    _original_clear_source(state)
    rows = [json.loads(line) for line in original.splitlines() if line.strip()]
    thread = previous['thread_id']
    if not any(row.get('type') == 'session_meta' and (row.get('payload') or {}).get('id') == thread
               for row in rows):
        raise ValueError('Completion archive belongs to another thread')
    exact = re.compile(r'\s*const\s+([A-Za-z_$][\w$]*)\s*=\s*await\s+tools\.update_goal\(\{\s*status\s*:\s*["\']complete["\']\s*\}\);\s*text\(\1\);\s*')
    call_ids = set()
    for row in rows:
        item = row.get('payload') or {}
        script = str(item.get('input') or '').replace('\\n', '\n')
        if (row.get('type') == 'response_item' and item.get('type') == 'custom_tool_call'
                and item.get('name') == 'exec' and exact.fullmatch(script)):
            call_ids.add(item.get('call_id'))
    candidates = []
    for index, row in enumerate(rows):
        item = row.get('payload') or {}
        if (row.get('type') != 'response_item' or item.get('type') != 'custom_tool_call_output'
                or item.get('call_id') not in call_ids):
            continue
        for block in item.get('output') or []:
            try:
                response = json.loads(block.get('text') or '')
            except (ValueError, AttributeError):
                continue
            goal = response.get('goal') if isinstance(response, dict) else None
            if isinstance(goal, dict) and goal.get('status') == 'complete':
                candidates.append((item['call_id'], goal, index))
    if len(candidates) != 1 or candidates[0][0] != proof.get('call_id'):
        raise ValueError('No unique original official complete response')
    _, goal, index = candidates[0]
    if any((row.get('payload') or {}).get('type') == 'thread_goal_updated' for row in rows[index + 1:]):
        raise ValueError('Another Goal update follows the completion response')
    return SimpleNamespace(thread_id=goal['threadId'], objective=goal['objective'],
        created_at=goal['createdAt'], status=goal['status'], tokens_used=goal['tokensUsed'],
        time_used_seconds=goal['timeUsedSeconds'], token_budget=goal.get('tokenBudget'))
