"""Match Research Lead citations against files actually opened this round.

The check is source relevance, not a required tool-call count. A node or report
named in the selection rationale must appear in this round's read trace.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

_PATH_TOKEN = re.compile(
    r"(?:(?:/|\./|\.\./)[^\s\"'`|,;<>]+|(?:[\w./\-]+\.(?:md|json|jsonl|txt|canvas|csv|tsv|parquet|feather|npy|npz|h5|hdf5)))"
)


def _clean_path_token(value: str) -> str:
    return str(value or "").strip().rstrip(").,]:")


def _is_concrete_read_path(value: str) -> bool:
    token = _clean_path_token(value)
    if not token or any(mark in token for mark in ("*", "?", "[", "]", "{", "}")):
        return False
    return Path(token).suffix.lower() in {".md", ".json", ".jsonl", ".txt", ".canvas", ".py", ".toml", ".yaml", ".yml", ".csv", ".tsv", ".parquet", ".feather", ".npy", ".npz", ".h5", ".hdf5"}


def _python_open_paths(code: str) -> list[str]:
    """Extract concrete file reads from simple Python snippets used by agents."""
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    constants: dict[str, str] = {}
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                constants[node.targets[0].id] = node.value.value
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_open = isinstance(func, ast.Name) and func.id == "open"
        is_path_open = isinstance(func, ast.Attribute) and func.attr == "open"
        is_read_text = isinstance(func, ast.Attribute) and func.attr in {"read_text", "read_bytes"}
        value = None
        if is_open and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                value = arg.value
            elif isinstance(arg, ast.Name):
                value = constants.get(arg.id)
        elif (is_path_open or is_read_text) and isinstance(func.value, ast.Call):
            ctor = func.value
            if isinstance(ctor.func, ast.Name) and ctor.func.id == "Path" and ctor.args:
                arg = ctor.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    value = arg.value
                elif isinstance(arg, ast.Name):
                    value = constants.get(arg.id)
        if value and _is_concrete_read_path(value):
            found.append(_clean_path_token(value))
    return list(dict.fromkeys(found))


def _shell_words(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _python_open_paths_from_detail(detail: str) -> list[str]:
    """Recover concrete Python reads before nested shell quoting loses literals."""
    text = str(detail or "").replace('\\"', '"').replace("\\'", "'")
    constants: dict[str, str] = {}
    for match in re.finditer(r"\b([A-Za-z_]\w*)\s*=\s*(['\"])([^'\"]+)\2", text):
        constants[match.group(1)] = match.group(3)
    found: list[str] = []
    open_pattern = re.compile(
        r"\bopen\(\s*(?P<arg>[A-Za-z_]\w*|'[^']+'|\"[^\"]+\")"
        r"(?:\s*,\s*(?P<mode>'[^']*'|\"[^\"]*\"))?"
    )
    for match in open_pattern.finditer(text):
        arg = match.group("arg")
        mode = match.group("mode")
        if mode is not None and not any(flag in mode.lower() for flag in ("r", "+")):
            continue
        value = constants.get(arg) if re.fullmatch(r"[A-Za-z_]\w*", arg) else arg[1:-1]
        if value and _is_concrete_read_path(value):
            found.append(_clean_path_token(value))
    path_read_pattern = re.compile(
        r"\bPath\(\s*(?P<arg>[A-Za-z_]\w*|'[^']+'|\"[^\"]+\")\s*\)"
        r"\.(?:read_text|read_bytes|open)\s*\("
    )
    for match in path_read_pattern.finditer(text):
        arg = match.group("arg")
        value = constants.get(arg) if re.fullmatch(r"[A-Za-z_]\w*", arg) else arg[1:-1]
        if value and _is_concrete_read_path(value):
            found.append(_clean_path_token(value))
    # Dataframe/array readers frequently receive a loop variable (for example
    # pd.read_csv(base / name)). When a command contains a known read API,
    # retain concrete data-file literals mentioned by that command as read
    # provenance even when static reconstruction of the full joined path is not
    # possible.
    if re.search(r"\b(?:read_csv|read_table|read_parquet|read_json|read_excel|read_feather|numpy\.load|np\.load)\s*\(", text):
        for match in re.finditer(r"['\"]([^'\"]+\.(?:csv|tsv|parquet|feather|json|npy|npz|h5|hdf5))['\"]", text):
            value = _clean_path_token(match.group(1))
            if _is_concrete_read_path(value):
                found.append(value)
    return list(dict.fromkeys(found))


def _command_read_paths(detail: str) -> list[str]:
    found = _python_open_paths_from_detail(detail)
    try:
        words = _shell_words(detail)
    except ValueError:
        return found
    if len(words) >= 3 and words[0].rsplit("/", 1)[-1] in {"bash", "sh"} and words[1] in {"-c", "-lc"}:
        try:
            words = _shell_words(words[2])
        except ValueError:
            return []
    separators = {"&&", ";", "|", "||", "&"}
    segments: list[list[str]] = [[]]
    for word in words:
        if word in separators:
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(word)
    read_commands = {"cat", "sed", "head", "tail", "rg", "grep", "less", "more"}
    python_commands = {"python", "python3"}
    for segment in segments:
        if not segment:
            continue
        command = segment[0].rsplit("/", 1)[-1]
        if command in read_commands:
            for word in segment[1:]:
                if word.startswith("-") or not _is_concrete_read_path(word):
                    continue
                found.append(_clean_path_token(word))
        elif command in python_commands and "-c" in segment:
            index = segment.index("-c")
            if index + 1 < len(segment):
                found.extend(_python_open_paths(segment[index + 1]))
    return list(dict.fromkeys(found))


def paths_from_event(mapped: dict[str, Any] | None) -> list[str]:
    if not isinstance(mapped, dict):
        return []
    if mapped.get("event") not in (None, "TOOL_ACTIVITY"):
        return []
    found: list[str] = []
    tool = str(mapped.get("tool") or "")
    detail = str(mapped.get("detail") or "")
    if mapped.get("path") and mapped.get("event") is None and _is_concrete_read_path(str(mapped["path"])):
        found.append(_clean_path_token(str(mapped["path"])))
    if tool == "command":
        found.extend(_command_read_paths(detail))
        return list(dict.fromkeys(found))
    if tool in {"file_read", "read_file"} and mapped.get("path"):
        value = _clean_path_token(str(mapped["path"]))
        if _is_concrete_read_path(value):
            found.append(value)
    for token in _PATH_TOKEN.findall(tool if tool not in {"file_read", "read_file"} else ""):
        value = _clean_path_token(token)
        if _is_concrete_read_path(value):
            found.append(value)
    return list(dict.fromkeys(found))

def _norm(value: str) -> str:
    return str(value).replace("\\", "/").rstrip("/")


def _opened(source: str, reads: list[str]) -> bool:
    src = _norm(source)
    name = Path(src).name
    for item in reads:
        norm = _norm(item)
        if not norm:
            continue
        if src and (src == norm or src in norm or norm.endswith("/" + name) or norm.endswith(name)):
            return True
    return False


def _node_cited(node_id: str, rationale: str) -> bool:
    if not node_id or len(node_id) < 2:
        return False
    return re.search(rf"(?<![\w-]){re.escape(node_id)}(?![\w-])", rationale) is not None


def unread_selection_sources(rationale: str, reads: list[str], entry: dict[str, Any] | None) -> list[str]:
    """Return explicitly cited entry sources absent from this round's read trace.

    Candidate selection is a workflow handoff over candidates already grounded
    during frontier_proposal.  The v2 selection contract does not require the
    Lead to repeat Canvas/report citations in its short decision.  We therefore
    verify citations when present, but absence of a citation is not itself a
    read-trace failure.  Whether frontier_proposal actually opened its required
    scientific sources is audited separately from this selection handoff.
    """
    packet = entry if isinstance(entry, dict) else {}
    text = str(rationale or "")
    missing: list[str] = []
    cited = False
    branch_canvas = str(packet.get("branch_canvas") or "")
    branch_nodes = [packet.get("start_node_id"), packet.get("branch_anchor_id"), *(packet.get("parent_ids") or [])]
    control_nodes = list(packet.get("scientific_model_ids") or [])
    for node_id, canvas in (
        *[(node_id, branch_canvas) for node_id in branch_nodes],
        *[(node_id, branch_canvas) for node_id in control_nodes],
    ):
        if not isinstance(node_id, str) or not _node_cited(node_id, text):
            continue
        cited = True
        if not _opened(canvas, reads):
            missing.append(node_id)
    reports = [
        *(packet.get("linked_reports") or []),
        packet.get("prior_note"),
        packet.get("scientific_model"),
        packet.get("research_plan"),
    ]
    for report in reports:
        if not isinstance(report, str) or not report:
            continue
        name = Path(report).name
        stem = Path(report).stem
        if report not in text and not (len(name) >= 4 and name in text) and not (len(stem) >= 4 and stem in text):
            continue
        cited = True
        if not _opened(report, reads):
            missing.append(report)
    return list(dict.fromkeys(missing))
