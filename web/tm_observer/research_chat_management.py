"""Chat organization and exports; metadata has its own short-lived lock."""
from __future__ import annotations

import fcntl
import json
import os
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any

from . import research_chat as chat


def _assistant_linked_path(record: dict, requested: Path, root: Path) -> bool:
    from .research_chat_attachments import artifact_targets
    resolved = (requested if requested.is_absolute() else root / requested).resolve()
    if not resolved.is_relative_to(root):
        return False
    for message in record.get("messages", []):
        if message.get("role") != "assistant":
            continue
        for target in artifact_targets(str(message.get("content") or "")):
            try:
                value = Path(target)
                linked = value if value.is_absolute() else root / value
                if linked.resolve() == resolved:
                    return True
            except (OSError, ValueError):
                continue
    return False


def session_artifact_roots(state_dir: Path, search_root: Path, state: dict, record: dict, *, strict_task: bool = True) -> list[Path]:
    """Use server-owned task bindings and this account/session's preparation area."""
    roots = []
    task = chat._task(state, record.get("task_id"))
    if task:
        from .canvas_md import vault_root, ExplorerError
        try:
            roots.append(vault_root(search_root, record["task_id"], state))
        except ExplorerError as exc:
            if strict_task:
                raise chat.ResearchChatError(str(exc), exc.status) from exc
    # Preparation artifacts remain readable after this session becomes a project.
    from .research_preparation import workspace, PreparationError
    try:
        roots.append(workspace(state_dir, record["id"]))
    except PreparationError:
        pass
    return roots


def restore_message_artifacts(state_dir: Path, search_root: Path, state: dict, record: dict) -> None:
    """Add browser-readable copies to historical responses without rewriting them."""
    from .research_chat_attachments import AttachmentError, capture_artifacts
    roots = session_artifact_roots(state_dir, search_root, state, record, strict_task=False)
    roots.append(state_dir / "research-chat-model")
    for message in record.get("messages", []):
        if message.get("role") != "assistant":
            continue
        try:
            found = capture_artifacts(state_dir, record["id"], str(message.get("content") or ""), roots,
                                      reuse_existing=True)
        except (AttachmentError, OSError, ValueError):
            continue
        message["artifacts"] = list({item["id"]: item for item in [*(message.get("artifacts") or []), *found]}.values())


def _metadata(state_dir: Path) -> dict[str, Any]:
    path = chat._store(state_dir) / "organization.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def list_sessions(state_dir: Path, *, query: str = "", task_id: str = "", view: str = "active") -> list[dict]:
    organization = _metadata(state_dir)
    needle = query.strip().casefold()[:500]
    result = []
    for file in chat._store(state_dir).glob("*.json"):
        if not chat._ID.fullmatch(file.stem):
            continue
        try:
            record = chat._read(file.parent, file.stem)
        except (OSError, ValueError):
            continue
        meta = organization.get(file.stem) or {}
        if view == "trash":
            if not meta.get("deleted_at"):
                continue
        elif meta.get("deleted_at"):
            continue
        elif view == "archived":
            if not meta.get("archived"):
                continue
        elif view != "all" and meta.get("archived"):
            continue
        if task_id and record.get("task_id") != task_id:
            continue
        title = str(record.get("title") or "科研对话")
        match = None
        if needle:
            match = next((message for message in record.get("messages", [])
                          if needle in str(message.get("content") or "").casefold()), None)
            if needle not in title.casefold() and match is None:
                continue
        text = str((match or {}).get("content") or "")
        at = text.casefold().find(needle) if needle else 0
        row = {key: record.get(key) for key in ("id", "title", "task_id", "mode", "updated_at")}
        row.update(pinned=bool(meta.get("pinned")), archived=bool(meta.get("archived")),
                   deleted=bool(meta.get("deleted_at")), message_count=len(record.get("messages", [])),
                   snippet=text[max(0, at - 40):max(0, at - 40) + 180],
                   match_message_id=(match or {}).get("id"))
        result.append(row)
    return sorted(result, key=lambda item: (item["pinned"], item.get("updated_at") or ""), reverse=True)


def manage_session(state_dir: Path, ident: str, payload: dict) -> dict:
    chat._read(chat._store(state_dir), ident)
    action = payload.get("action")
    if action not in {"pin", "archive", "delete", "restore"}:
        raise chat.ResearchChatError("无效的会话管理操作")
    with chat._JOBS_LOCK:
        job = chat._JOBS.get(ident) or {}
        if action in {"archive", "delete"} and job.get("status") in {"starting", "streaming", "stopping"}:
            raise chat.ResearchChatError("请先停止当前回复，再归档或删除会话", 409)
    root = chat._store(state_dir)
    with (root / "organization.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        data = _metadata(state_dir)
        meta = data.setdefault(ident, {})
        if action == "pin":
            meta["pinned"] = bool(payload.get("value", not meta.get("pinned")))
        elif action == "archive":
            meta["archived"] = bool(payload.get("value", not meta.get("archived")))
        elif action == "delete":
            meta["deleted_at"] = chat._stamp()
        else:
            meta.pop("deleted_at", None)
            meta["archived"] = False
        temp = root / (".organization." + uuid.uuid4().hex + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, root / "organization.json")
    return {"ok": True, **meta}


def export_session(state_dir: Path, ident: str, fmt: str = "markdown") -> tuple[bytes, str, str]:
    record = chat._read(chat._store(state_dir), ident)
    messages = record.get("messages", [])
    # Export user-visible conversation fields rather than provider configuration.
    exported = {"title": record.get("title"), "created_at": record.get("created_at"),
                "messages": [{key: message.get(key) for key in ("id", "role", "content", "created_at", "sources", "attachments", "run")
                              if key in message} for message in messages]}
    if fmt == "json":
        return (json.dumps(exported, ensure_ascii=False, indent=2).encode("utf-8"),
                "application/json; charset=utf-8", "chat-" + ident[:8] + ".json")
    if fmt != "markdown":
        raise chat.ResearchChatError("导出格式须为 markdown 或 json")
    lines = ["# " + str(record.get("title") or "科研对话"), ""]
    for message in messages:
        lines.extend(["## " + {"user": "用户", "assistant": "Sciver", "system": "系统"}.get(message.get("role"), "消息"),
                      str(message.get("created_at") or ""), "", str(message.get("content") or ""), ""])
        for attachment in message.get("attachments") or []:
            lines.append("- 附件：" + str(attachment.get("name") or attachment.get("id") or "文件"))
    return ("\n".join(lines).encode("utf-8"), "text/markdown; charset=utf-8", "chat-" + ident[:8] + ".md")


def artifact_content(state_dir: Path, search_root: Path, state: dict, ident: str, target: str) -> tuple[bytes, str, str]:
    """Serve files produced inside this chat's workspace or its bound task."""
    record = chat._read(chat._store(state_dir), ident)
    if not target or len(target) > 4096 or "\x00" in target or "\\" in target:
        raise chat.ResearchChatError("无效的成果路径")
    roots = session_artifact_roots(state_dir, search_root, state, record)
    from urllib.parse import unquote, urlsplit
    try:
        parsed = urlsplit(re.sub(r":\d+(?::\d+)?(?=#|$)", "", target))
    except ValueError as exc:
        raise chat.ResearchChatError("无效的成果路径") from exc
    if parsed.scheme not in {"", "file"} or parsed.netloc not in {"", "localhost"}:
        raise chat.ResearchChatError("只支持当前聊天中的本地成果")
    decoded = unquote(parsed.path)
    if "\x00" in decoded or "\\" in decoded:
        raise chat.ResearchChatError("无效的成果路径")
    value = Path(decoded)
    # The legacy workspace is shared. A filename mentioned by the user or a
    # substring of another link must never grant access to a different chat's file.
    shared = (state_dir / "research-chat-model").resolve()
    if _assistant_linked_path(record, value, shared):
        roots.append(shared)
    if value.name.lower() in {"auth.json", "credentials.json", "config.toml"} or value.suffix.lower() in {".pem", ".key", ".p12"}:
        raise chat.ResearchChatError("此文件不可作为聊天成果打开", 403)
    for root in roots:
        root = root.resolve()
        file = value if value.is_absolute() else root / value
        resolved = file.resolve()
        if not resolved.is_relative_to(root):
            continue
        relative = resolved.relative_to(root)
        if any(part.startswith(".") for part in relative.parts) or any(part.startswith(".") for part in file.parts if part not in root.parts):
            continue
        if not resolved.is_file() or resolved.stat().st_size > 20_000_000:
            continue
        kind = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        if kind not in {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain", "text/markdown", "text/csv"}:
            kind = "application/octet-stream"
        from .research_chat_attachments import _artifact_bytes, AttachmentError
        try:
            return _artifact_bytes(file, root), kind, resolved.name
        except (AttachmentError, OSError):
            continue
    raise chat.ResearchChatError("此成果不在当前任务或聊天的可读取范围内", 404)
