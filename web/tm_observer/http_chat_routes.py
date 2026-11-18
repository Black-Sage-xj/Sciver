"""Domain routes; request dependencies are provided explicitly by the server."""
from __future__ import annotations

import html
import json
import subprocess
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, unquote
from .research_chat import ResearchChatError
from .canvas_md import ExplorerError
from .workspace_scope import WorkspaceScopeError
from .http_context import RequestContext, RouteServices


def get_chat(self, path: str, state: dict, ctx: RequestContext, services: RouteServices) -> bool:
    if path == "/research-chat/open":
        from .research_chat_links import document_destination
        query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        try:
            destination = document_destination(ctx.cache.search_root, state,
                (query.get("task_id") or [""])[0], (query.get("target") or [""])[0])
        except ExplorerError as exc:
            body = '<h2>暂时无法打开文档</h2><p>' + html.escape(str(exc)) + '</p>'
            self._send(services._page("文档不可预览", body, active="research_chat").encode("utf-8"),
                       "text/html; charset=utf-8", exc.status)
        else:
            if (query.get("resolve") or [""])[0] == "1":
                self._send(json.dumps({"destination": destination}).encode("utf-8"),
                           "application/json; charset=utf-8", cache_control="private, no-store")
                return True
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", destination)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
        return True
    if path == "/api/research-chat/sessions":
        from .research_chat_management import list_sessions
        params = parse_qs(self.path.partition("?")[2])
        rows = list_sessions(ctx.notes_state_dir, query=(params.get("q") or [""])[0],
                             task_id=(params.get("task_id") or [""])[0], view=(params.get("view") or ["active"])[0])
        self._send(json.dumps({"sessions": rows}, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        return True
    if path.startswith("/api/research-chat/"):
        parts = path.strip("/").split("/")
        params = parse_qs(self.path.partition("?")[2])
        if len(parts) >= 4 and (parts[3] in {"attachments", "export", "artifact"}):
            from . import research_chat_attachments as attachments
            from .research_chat_management import export_session, artifact_content
            try:
                if parts[3] == "attachments" and len(parts) == 4:
                    result = {"attachments": attachments.list_attachments(ctx.notes_state_dir, parts[2])}
                    self._send(json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                elif parts[3] == "attachments" and len(parts) == 5:
                    result = {"attachment": attachments.get_attachment(ctx.notes_state_dir, parts[2], parts[4])}
                    self._send(json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                elif parts[3] == "attachments" and len(parts) == 6 and parts[5] == "content":
                    body, kind, name = attachments.attachment_content(ctx.notes_state_dir, parts[2], parts[4])
                    self._send(body, kind, download_name=name if "download" in params or kind == "application/octet-stream" else None)
                elif parts[3] == "export" and len(parts) == 4:
                    body, kind, name = export_session(ctx.notes_state_dir, parts[2], (params.get("format") or ["markdown"])[0])
                    self._send(body, kind, download_name=name)
                elif parts[3] == "artifact" and len(parts) == 4:
                    body, kind, name = artifact_content(ctx.notes_state_dir, ctx.cache.search_root, state, parts[2], (params.get("path") or [""])[0])
                    self._send(body, kind, download_name=name if "download" in params or kind == "application/octet-stream" else None)
                else:
                    raise ResearchChatError("文件入口不存在", 404)
            except (ResearchChatError, attachments.AttachmentError) as exc:
                self._send(json.dumps({"error": str(exc)}, ensure_ascii=False).encode(), "application/json; charset=utf-8", HTTPStatus(exc.status))
            return True
    if path.startswith("/api/research-chat/"):
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[3] == "literature":
            from .research_chat_literature import ChatLiteratureError, list_studies, read_report, read_study
            try:
                if len(parts) == 4:
                    result = list_studies(ctx.notes_state_dir, parts[2])
                    self._send(json.dumps(result, ensure_ascii=False).encode("utf-8"),
                               "application/json; charset=utf-8")
                elif len(parts) == 5:
                    result = read_study(ctx.notes_state_dir, parts[2], parts[4])
                    self._send(json.dumps(result, ensure_ascii=False).encode("utf-8"),
                               "application/json; charset=utf-8")
                elif len(parts) == 6 and parts[5] == "report":
                    report = read_report(ctx.notes_state_dir, parts[2], parts[4])
                    from .research_chat_ui import _asset_version
                    asset_version = _asset_version()
                    payload = json.dumps({"text": report}, ensure_ascii=False).replace("<", "\\u003c")
                    body = ('<style>.tm-literature-report{max-width:960px;box-sizing:border-box;'
                            'margin:0 auto;padding:24px clamp(8px,3vw,32px);font-size:15px;line-height:1.85;'
                            'overflow-wrap:anywhere;color:var(--tm-text);'
                            '--sc-chat-bg:var(--tm-bg);--sc-chat-panel:var(--tm-panel);'
                            '--sc-chat-text:var(--tm-text);--sc-chat-border:var(--tm-border);'
                            '--sc-chat-muted:var(--tm-muted);--sc-chat-accent:var(--tm-accent)}'
                            '.tm-literature-report :is(h1,h2,h3,h4){color:inherit;line-height:1.4;margin:1.5em 0 .6em}'
                            '.tm-literature-report h1{font-size:1.8em}.tm-literature-report h2{font-size:1.35em}'
                            '.tm-literature-report a{color:var(--tm-accent);text-decoration:underline;text-underline-offset:3px}'
                            '.tm-literature-report p{margin:0 0 1em}.tm-literature-report li{margin:.35em 0}'
                            '.tm-literature-report blockquote{margin:1em 0;padding-left:1em;border-left:3px solid var(--tm-border)}'
                            '</style><article class="tm-literature-report" aria-label="Literature 调研报告">'
                            '<div data-literature-report><pre style="white-space:pre-wrap;'
                            'overflow-wrap:anywhere;font:inherit;line-height:1.8">'
                            + html.escape(report) + '</pre></div></article>'
                            + '<script type="application/json" id="tm-literature-report-json">' + payload + '</script>'
                            + f'<link rel="stylesheet" href="/research-chat/assets/research-chat.css?v={asset_version}">'
                            + f'<script type="module" src="/research-chat/assets/research-chat.js?v={asset_version}"></script>')
                    self._send(services._page("Literature", body, active="research_chat").encode("utf-8"),
                               "text/html; charset=utf-8")
                else:
                    raise ChatLiteratureError("文献入口不存在", 404)
            except ChatLiteratureError as exc:
                self._send(json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8", HTTPStatus(exc.status))
            return True
    if path == "/api/research-chat/models":
        from .research_chat_models import refresh_catalog
        params = parse_qs(self.path.partition("?")[2])
        catalog = refresh_catalog(force=(params.get("refresh") or [""])[0] == "1")
        self._send(json.dumps(catalog, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")
        return True
    if path == "/api/settings/document-conversion":
        from theory_master.document_conversion import load_settings
        try:
            result, code = {"ok": True, **load_settings()}, HTTPStatus.OK
        except (OSError, ValueError, RuntimeError):
            result, code = {"error": "PDF 转换配置不可用，请检查服务器配置文件"}, HTTPStatus.CONFLICT
        self._send(json.dumps(result, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", code)
        return True
    if path in {"/research-chat", "/research-chat/"}:
        from .research_chat_ui import page_body, embedded_page
        query = parse_qs(self.path.partition("?")[2])
        selected = (query.get("session") or [None])[0]
        if (query.get("new") or [""])[0] == "1":
            selected = "__new__"
        try:
            from .research_chat_management import list_sessions
            sessions = list_sessions(ctx.notes_state_dir)
            if selected is None:
                selected = next((row["id"] for row in sessions if row.get("mode") in {"project", "frontier"}), "__new__")
            view = services.research_chat_snapshot(ctx.notes_state_dir, state, selected)
            view["sessions"] = sessions
        except ResearchChatError as exc:
            self._send((json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n").encode("utf-8"),
                       "application/json; charset=utf-8", HTTPStatus(exc.status))
            return True
        embedded = (query.get("embed") or [""])[0] == "1"
        task_id = (query.get("task_id") or [""])[0]
        page = (embedded_page(view, task_id=task_id) if embedded else
                services._page("Research Chat", page_body(view, task_id=task_id), active="research_chat"))
        self._send(page.encode("utf-8"), "text/html; charset=utf-8")
        return True
    if path.startswith("/api/research-chat/") and path.endswith("/preparation"):
        parts = path.strip("/").split("/")
        if len(parts) == 4:
            from .research_chat import preparation_state
            try:
                result = {"ok": True, **preparation_state(ctx.notes_state_dir, parts[2])}
                code = HTTPStatus.OK
            except ResearchChatError as exc:
                result, code = {"error": str(exc)}, HTTPStatus(exc.status)
            self._send(json.dumps(result, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8", code)
            return True
    if path.startswith("/api/research-chat/") and path.endswith("/progress"):
        parts = path.strip("/").split("/")
        if len(parts) == 4:
            params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
            try:
                offset = int((params.get("offset") or ["0"])[0])
                if offset < 0 or offset > 10_000_000:
                    raise ValueError("offset out of range")
            except (TypeError, ValueError, OverflowError):
                self._send(b'{"error":"invalid progress offset"}',
                           "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                return True
            try:
                view = services.research_chat_snapshot(ctx.notes_state_dir, state, parts[2])
                session = view.get("session") or {}
                active_run = session.get("active_run") if isinstance(session.get("active_run"), dict) else {}
                full_text = str(session.get("stream_text", "") or "")
                reset = offset > len(full_text)
                start = 0 if reset else offset
                payload = json.dumps({"status": session.get("stream_status"),
                                      "streaming": session.get("streaming", False),
                                      "text": full_text[start:], "next_offset": len(full_text),
                                      "reset": reset, "error": active_run.get("error_type"),
                                      "active_run": active_run, "queue": session.get("queue", []),
                                      "queue_paused": session.get("queue_paused", False),
                                      "pending_questions": session.get("pending_questions", [])},
                                     ensure_ascii=False).encode("utf-8")
                self._send(payload, "application/json; charset=utf-8")
            except ResearchChatError as exc:
                self._send(json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8", HTTPStatus(exc.status))
            return True
    if path.startswith("/api/research-chat/") and path.endswith("/state"):
        parts = path.strip("/").split("/")
        if len(parts) == 4:
            try:
                view = services.research_chat_snapshot(ctx.notes_state_dir, state, parts[2])
                session = view.get("session") or {}
                payload = json.dumps({"id": session.get("id"), "title": session.get("title"),
                                      "mode": session.get("mode"), "model": session.get("model"),
                                      "messages": session.get("messages", []),
                                      "runs": session.get("runs", []), "branches": session.get("branches", []),
                                      "branch_id": session.get("branch_id"),
                                      "current_branch_id": session.get("current_branch_id", session.get("branch_id")),
                                      "queue": session.get("queue", []), "queue_paused": session.get("queue_paused", False),
                                      "pending_questions": session.get("pending_questions", []),
                                      "active_run": session.get("active_run"),
                                      "streaming": session.get("streaming", False),
                                      "stream_status": session.get("stream_status"),
                                      "stream_text": session.get("stream_text", ""),
                                      "pending_action": session.get("pending_action")},
                                     ensure_ascii=False).encode("utf-8")
                self._send(payload, "application/json; charset=utf-8")
            except ResearchChatError as exc:
                self._send(json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8", HTTPStatus(exc.status))
            return True
    if path.startswith("/api/research-chat/") and path.endswith("/settings"):
        parts = path.strip("/").split("/")
        if len(parts) == 4:
            try:
                view = services.research_chat_snapshot(ctx.notes_state_dir, state, parts[2])
                session = view.get("session") or {}
                payload = json.dumps({
                    "model": session.get("model") or "gpt-6.1-sol",
                    "reasoning_effort": session.get("reasoning_effort") or "medium",
                    "available_models": view.get("available_models", []),
                    "available_reasoning_efforts": view.get("available_reasoning_efforts", []),
                    "model_reasoning_efforts": view.get("model_reasoning_efforts", {}),
                    "model_default_reasoning_efforts": view.get("model_default_reasoning_efforts", {}),
                    "model_catalog": view.get("model_catalog", {}),
                }, ensure_ascii=False).encode("utf-8")
                self._send(payload, "application/json; charset=utf-8")
            except ResearchChatError as exc:
                self._send(json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8", HTTPStatus(exc.status))
            return True
    return False


def post_chat(self, path: str, ctx: RequestContext, services: RouteServices) -> bool:
    if path.startswith("/api/research-chat/"):
        if not self._authorized():
            self._send_unauthorized()
            return True
        origin, host = self.headers.get("Origin"), self.headers.get("Host")
        if (not origin or not host or origin not in {f"https://{host}", f"http://{host}"}
                or self.headers.get("X-TM-Operator-Intent") != "1"):
            self._send(b'{"error":"origin or operator intent rejected"}\n',
                       "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
            return True
        if path.endswith("/attachments"):
            from . import research_chat_attachments as attachments
            try:
                parts = path.strip("/").split("/")
                if len(parts) != 4:
                    raise ResearchChatError("无效的附件入口", 404)
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > 20_000_000:
                    raise ResearchChatError("附件大小须为 1 字节至 20 MB", 413)
                name = unquote(self.headers.get("X-File-Name", ""))
                result = attachments.store_upload(ctx.notes_state_dir, parts[2], name,
                                                  self.headers.get("Content-Type", "application/octet-stream"), self.rfile.read(size))
                self._send(json.dumps({"attachment": result}, ensure_ascii=False).encode(), "application/json; charset=utf-8", HTTPStatus.CREATED)
            except (ResearchChatError, attachments.AttachmentError) as exc:
                self._send(json.dumps({"error": str(exc)}, ensure_ascii=False).encode(), "application/json; charset=utf-8", HTTPStatus(exc.status))
            except (ValueError, OSError):
                self._send(b'{"error":"invalid attachment upload"}', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._send(b'{"error":"expected JSON"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        # Allow the 120,000-character composer plus structured sources
        # and preparation drafts, including their UTF-8 encoding.
        limit = 2_000_000
        if size < 2 or size > limit:
            self._send(b'{"error":"invalid size"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        from .research_chat import (create, begin_send, begin_retry, stop as stop_send,
                                     set_model, set_reasoning_effort, rename_session, apply_action,
                                     update_preparation, create_prepared_project)
        try:
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(data, dict):
                raise ResearchChatError("无效 JSON")
            parts = path.strip("/").split("/")
            state = ctx.cache.get()
            if state.get("refresh_status") in {"loading", "unavailable"}:
                self._send(b"Task state is initializing; retry shortly.\n",
                           "text/plain; charset=utf-8", HTTPStatus.SERVICE_UNAVAILABLE, retry_after=1)
                return True
            code = HTTPStatus.OK
            if parts == ["api", "research-chat", "import-preview"]:
                from .project_setup import source_inventory
                if ctx.isolated_workspace:
                    services.workspace_path(data.get("source_directory"), ctx.cache.search_root)
                result = source_inventory(str(data.get("source_directory") or ""))
            elif parts == ["api", "research-chat", "session"]:
                if ctx.isolated_workspace and data.get("source_directory"):
                    services.workspace_path(data["source_directory"], ctx.cache.search_root)
                result = create(ctx.notes_state_dir, state, data)
            elif parts == ["api", "research-chat", "sources"]:
                from .research_chat_sources import prepare_source
                try:
                    result = {"source": prepare_source(ctx.cache.search_root, state,
                              str(data.get("task_id") or ""), data.get("source"))}
                except ExplorerError as exc:
                    raise ResearchChatError(str(exc), exc.status) from exc
            elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "message":
                result = begin_send(ctx.notes_state_dir, ctx.cache.search_root, state, parts[2], data)
                code = HTTPStatus.ACCEPTED
            elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "retry":
                result = begin_retry(ctx.notes_state_dir, ctx.cache.search_root, state, parts[2])
                code = HTTPStatus.ACCEPTED
            elif len(parts) == 4 and parts[3] == "queue":
                from .research_chat import (queue_message, update_queued_message, remove_queued_message, resume_queue)
                action = data.get("action", "add")
                if action == "add":
                    result = queue_message(ctx.notes_state_dir, ctx.cache.search_root, state, parts[2], data)
                elif action == "edit":
                    result = update_queued_message(ctx.notes_state_dir, parts[2], data.get("queue_id"), data)
                elif action == "remove":
                    result = remove_queued_message(ctx.notes_state_dir, parts[2], data.get("queue_id"))
                elif action == "resume":
                    result = resume_queue(ctx.notes_state_dir, ctx.cache.search_root, state, parts[2])
                else:
                    raise ResearchChatError("无效队列操作")
            elif len(parts) == 4 and parts[3] == "steer":
                from .research_chat import steer
                result = steer(ctx.notes_state_dir, ctx.cache.search_root, state, parts[2], data)
            elif len(parts) == 4 and parts[3] == "branch":
                from .research_chat import switch_branch
                result = switch_branch(ctx.notes_state_dir, parts[2], data.get("branch_id"))
            elif len(parts) == 4 and parts[3] == "fork":
                from .research_chat import fork_branch
                result = fork_branch(ctx.notes_state_dir, parts[2], data)
            elif len(parts) == 4 and parts[3] == "regenerate":
                from .research_chat import begin_regenerate
                result = begin_regenerate(ctx.notes_state_dir, ctx.cache.search_root, state, parts[2], data)
            elif len(parts) == 4 and parts[3] == "answer":
                from .research_chat import answer_question
                result = answer_question(ctx.notes_state_dir, parts[2], data, search_root=ctx.cache.search_root, state=state)
            elif len(parts) == 4 and parts[3] == "manage":
                from .research_chat_management import manage_session
                result = manage_session(ctx.notes_state_dir, parts[2], data)
            elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "stop":
                result = stop_send(ctx.notes_state_dir, parts[2])
            elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "model":
                result = set_model(ctx.notes_state_dir, parts[2], data.get("model"))
            elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "effort":
                result = set_reasoning_effort(ctx.notes_state_dir, parts[2], data.get("effort"))
            elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "title":
                result = rename_session(ctx.notes_state_dir, parts[2], data.get("title"))
            elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "preparation":
                result = {"ok": True, **update_preparation(ctx.notes_state_dir, parts[2], data)}
            elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "create-project":
                result = {"ok": True, **create_prepared_project(ctx.notes_state_dir, ctx.cache.search_root, parts[2], data)}
                ctx.cache.include_created_task(Path(result["task_root"]))
            elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "action":
                result = apply_action(ctx.notes_state_dir, ctx.cache.search_root, state, parts[2], data)
                if result.get("task_id"):
                    ctx.cache.refresh_after_write()
                else:
                    ctx.cache._at = 0.0
            else:
                raise ResearchChatError("API 不存在", 404)
        except (ResearchChatError, WorkspaceScopeError) as exc:
            result, code = {"error": str(exc)}, HTTPStatus(exc.status)
        except (UnicodeError, ValueError, json.JSONDecodeError, TypeError):
            result, code = {"error": "无效 JSON"}, HTTPStatus.BAD_REQUEST
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            result, code = {"error": "Research Chat 服务暂不可用"}, HTTPStatus.SERVICE_UNAVAILABLE
        self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"),
                   "application/json; charset=utf-8", code)
        return True
    return False
