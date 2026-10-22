from __future__ import annotations

"""Single source of truth for Codex execution/write approval policy."""

import re
import shlex
from pathlib import Path
from typing import Any

PROCESS_CONTROL_RE = re.compile(
    r"(?:^|[\s;|&`$()<>])(?:(?:/usr)?/bin/)?(?:sudo\s+)?(?:pkill|killall|kill)\b", re.I
)
DESTRUCTIVE_GIT_RE = re.compile(
    r"(?:^|[\s;|&`$()<>])git\b"
    r"(?:\s+(?:-C\s+\S+|-c\s+\S+|--git-dir[= ]\S+|--work-tree[= ]\S+|-\w+|--[\w-]+))*"
    r"\s+(reset|rebase|push|clean|commit|stash|filter-branch)\b",
    re.I,
)
FORCED_CHECKOUT_RE = re.compile(
    r"(?:^|[\s;|&`$()<>])git\b[^;|&]*\b(?:checkout|switch|restore)\b[^;|&]*(?:\s-\w*f|\s--force|\s--hard)",
    re.I,
)
RECURSIVE_REMOVE_RE = re.compile(
    r"(?:^|[\s;|&`$()<>])rm\b(?:\s+-\w*[rR]\w*|\s+--recursive)", re.I
)

PROCESS_CONTROL_GUARD = (
    "Runtime constraint: never invoke kill, pkill, killall, or equivalent process-control "
    "commands. pkill -f matches this tool's own command line and SIGTERM-aborts the turn "
    "(exit 143). Stop child jobs with unique pid files, timeouts inside the child, or by "
    "letting them finish."
)
REPO_GUARD = (
    "Runtime constraint: this turn may not rewrite repository history or delete trees outside "
    "its own cycle workspace. git reset/rebase/push/clean/commit/stash and forced checkout are "
    "declined; recursive rm is declined outside the cycle workspace."
)


def is_process_control_command(command: str) -> bool:
    return bool(PROCESS_CONTROL_RE.search(" " + command))


def _command_from_approval_params(payload: dict[str, Any]) -> str:
    command = payload.get("command")
    if isinstance(command, str):
        return command
    argv = payload.get("argv")
    if isinstance(argv, list):
        return " ".join(str(part) for part in argv)
    nested = payload.get("item") if isinstance(payload.get("item"), dict) else payload.get("action")
    if isinstance(nested, dict):
        return _command_from_approval_params(nested)
    return ""


def is_history_rewriting_command(command: str) -> bool:
    return bool(DESTRUCTIVE_GIT_RE.search(" " + command) or FORCED_CHECKOUT_RE.search(" " + command))


def _shell_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def _under_roots(path: Path, roots: tuple[Path, ...]) -> bool:
    if not roots:
        return True
    try:
        resolved = path.expanduser() if path.is_absolute() or str(path).startswith("~") else roots[0] / path
        resolved = resolved.resolve()
    except (OSError, RuntimeError):
        return False
    return any(resolved == root or root in resolved.parents for root in roots)


def _command_targets(command: str) -> list[Path]:
    """Path-looking operands of a shell line; flags and bare words are ignored."""
    targets: list[Path] = []
    for token in _shell_tokens(command)[1:]:
        if token.startswith("-") or not token:
            continue
        if token.startswith("/") or token.startswith("~") or "/" in token or token == "..":
            targets.append(Path(token))
    return targets


def _payload_paths(payload: Any) -> list[Path]:
    """Best-effort extraction of file targets from a Codex approval payload."""
    keys = {"path", "file_path", "filePath", "absolute_path", "absolutePath", "abs_path"}
    found: list[Path] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in keys and isinstance(value, str) and value:
                    found.append(Path(value))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


def make_approval_decision(allowed_roots: tuple[Path, ...] = ()) -> Any:
    """Build a Codex approval hook that confines a writing role to allowed_roots.

    Signal commands are always declined (pkill -f self-matches this process). Git history
    rewriting is always declined. Recursive deletes and file changes are declined once a
    target can be resolved outside the allowed roots; an unparsable payload stays accepted so
    that a payload-schema change degrades to the previous permissive behaviour instead of
    stalling the role.
    """
    roots = tuple(root.resolve() for root in allowed_roots)

    def decide(method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        payload = params or {}
        command = _command_from_approval_params(payload)
        if "commandExecution" in method:
            if is_process_control_command(command):
                return {"decision": "decline", "reason": PROCESS_CONTROL_GUARD}
            if is_history_rewriting_command(command):
                return {"decision": "decline", "reason": REPO_GUARD}
            if roots and RECURSIVE_REMOVE_RE.search(" " + command):
                outside = [target for target in _command_targets(command) if not _under_roots(target, roots)]
                if outside:
                    return {"decision": "decline", "reason": f"{REPO_GUARD} Refused target: {outside[0]}"}
            return {"decision": "accept"}
        if method == "item/fileChange/requestApproval":
            if roots:
                outside = [target for target in _payload_paths(payload) if not _under_roots(target, roots)]
                if outside:
                    return {"decision": "decline", "reason": f"{REPO_GUARD} Refused write: {outside[0]}"}
            return {"decision": "accept"}
        return {}

    return decide


approval_decision = make_approval_decision()
