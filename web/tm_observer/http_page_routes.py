"""Domain routes; request dependencies are provided explicitly by the server."""
from __future__ import annotations

import html
from http import HTTPStatus
from urllib.parse import parse_qs, urlencode
from .canvas_md import ExplorerError
from .http_context import RequestContext, RouteServices


def get_page(self, path: str, state: dict, ctx: RequestContext, services: RouteServices) -> bool:
    if path in {"/", "/task-console/"}:
        destination = "/research-chat/" if path == "/" else services._console_landing(self.headers.get("Cookie"), state)
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", destination)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True
    remembered = None
    if path in {"/run", "/run/"}:
        query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        body = services.run_index_body(
            state,
            query=(query.get("q") or [""])[0],
            status=(query.get("status") or [""])[0],
            page=(query.get("page") or ["1"])[0],
        )
        payload = services._page("Sciver Observatory — Task List", body, active="run").encode("utf-8")
    elif path in {"/settings", "/settings/"}:
        params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        selected_id = (params.get("observer_id") or [""])[0]
        selected_run = None
        if selected_id:
            if len(selected_id) != 16 or not all(c in "0123456789abcdef" for c in selected_id):
                self._send(b"task not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return True
            selected_run = next((row for row in state.get("runs", [])
                                if row.get("observer_id") == selected_id), None)
            if selected_run is None:
                self._send(b"task not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return True
        payload = services._page("Sciver Observatory — Workspace Settings",
                        services.workspace_settings_body(state.get("runs", []), selected_run, access=ctx.access.public()),
                        active="settings").encode("utf-8")
    elif path.startswith("/run/"):
        parts = path.strip("/").split("/")
        settings_page = len(parts) == 3 and parts[0] == "run" and parts[2] == "settings"
        supervisor_page = len(parts) == 3 and parts[0] == "run" and parts[2] == "supervisor"
        ident = parts[1] if (len(parts) == 2 and parts[0] == "run") or settings_page or supervisor_page else ""
        run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(char in "0123456789abcdef" for char in ident) else None
        if run is None:
            self._send(b"task not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        if settings_page:
            # Existing deep links now navigate to Workspace > Settings.
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/settings/?observer_id=" + ident)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        if supervisor_page:
            selected_session = (params.get("session") or [""])[0]
            # Existing SUP-* links include a cycle but no SIS session. Keep them on the
            # legacy log view; notification-created SIS sessions use the shared chat.
            use_legacy = ((params.get("legacy") or [""])[0] == "1"
                          or (bool((params.get("cycle") or [""])[0]) and not selected_session))
            if use_legacy:
                from .run_monitor import data as monitor_data
                from .supervisor_view import page_body as supervisor_page_body
                try:
                    body = supervisor_page_body(run, (params.get("cycle") or [""])[0], monitor_data(run))
                except (ValueError, OSError):
                    self._send(b"supervisor session not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return True
                payload = services._page("Sciver Observatory — Supervisor", body, active="run",
                                back_href=f"/run/{ident}/", breadcrumb_leaf="Supervisor").encode("utf-8")
            else:
                from .research_chat_ui import page_body as shared_chat_page_body
                from .supervisor_chat import snapshot as supervisor_chat_snapshot
                from theory_master.supervisor_incident_session import IncidentSessionError
                try:
                    view = supervisor_chat_snapshot(run, selected_session or None)
                    body = shared_chat_page_body(view, mode="supervisor")
                except (IncidentSessionError, OSError, ValueError):
                    self._send(b"supervisor incident session not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return True
                payload = services._page("Sciver Observatory — Supervisor", body, active="research_chat",
                                back_href=f"/run/{ident}/", breadcrumb_leaf="Supervisor").encode("utf-8")
            self._send(payload, "text/html; charset=utf-8")
            return True
        try:
            services.vault_root(ctx.cache.search_root, ident, state)
            canvas_available = True
        except ExplorerError:
            canvas_available = False
        tab = (params.get("tab") or ["activity"])[0]
        task_href = f"/run/{ident}/"
        remembered = services._console_cookie(ident)
        parent_href = (task_href if tab in {"research", "events", "thread", "artifacts"}
                       else "/run/")
        payload = services._page(
            "Sciver Observatory — Task Console",
            services.run_detail_body(run, tab=tab, canvas_available=canvas_available,
                            history_cycle=(params.get("history_cycle") or [""])[0],
                            worker_id=(params.get("worker") or [None])[0],
                            quota_snapshot=ctx.quota_snapshot(request_update=True)),
            active="run", back_href=parent_href,
            breadcrumb_leaf=(
                "Events" if tab == "events" else
                "Research" if tab in {"research", "thread", "artifacts"} else None),
        ).encode("utf-8")
    elif path in {"/research-plan", "/research-plan/"}:
        params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        selected = (params.get("observer_id") or [""])[0]
        if not selected:
            previous = services._remembered_console(self.headers.get("Cookie"), state)
            if previous:
                selected = previous.strip("/").split("/")[1]
        try:
            payload = services.research_plan_html(state, selected).encode("utf-8")
        except ValueError:
            self._send(b"task not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
    elif path in {"/research-explorer-tmp-c", "/research-explorer-tmp-c/",
                  "/research-explorer-tmp-b", "/research-explorer-tmp-b/",
                  "/research-explorer-tmp", "/research-explorer-tmp/",
                  "/research-explorer-tmp-v0", "/research-explorer-tmp-v0/",
                  "/research-explorer-tmp-a0", "/research-explorer-tmp-a0/"}:
        query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        selected = (query.get("observer_id") or [""])[0]
        target_params = {"observer_id": selected} if selected else {}
        target = "/research-explorer/" + ("?" + urlencode(target_params) if target_params else "")
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True
    elif path in {"/research-explorer", "/research-explorer/"}:
        query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        selected = (query.get("observer_id") or [""])[0]
        payload = services.explorer_html(ctx.cache.search_root, state, selected).encode("utf-8")
    elif path in {"/architecture", "/architecture/"}:
        try:
            payload = services.architecture_html(ctx.static_manager).encode("utf-8")
        except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
            payload = services._workflow_unavailable_html("Architecture 暂时不可用", exc,
                                                 active="architecture").encode("utf-8")
            self._send(payload, "text/html; charset=utf-8", HTTPStatus.SERVICE_UNAVAILABLE)
            return True
    elif path in {"/workflow", "/workflow/"}:
        # Older Workflow links/bookmarks must resolve to the current page.
        query_string = self.path.partition("?")[2]
        target = "/workflow-stage-preview/" + ("?" + query_string if query_string else "")
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True
    elif path in {"/workflow-stage-preview", "/workflow-stage-preview/"}:
        query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        if ctx.isolated_workspace and query.get("request"):
            self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        selected_request_id = (query.get("request") or [None])[0]
        workflow_version = (query.get("workflow_version") or [None])[0]
        display_mode = (query.get("view") or ["overview"])[0]
        try:
            services.workflow_snapshot(workflow_version)
        except ValueError as exc:
            payload = services._page("Workflow version not found", f"<h1>404</h1><p>{html.escape(str(exc))}</p>").encode("utf-8")
            self._send(payload, "text/html; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        if display_mode not in {"overview", "full"}:
            payload = services._page("Invalid Workflow view", "<h1>400</h1><p>Unsupported Workflow display mode</p>").encode("utf-8")
            self._send(payload, "text/html; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        try:
            page = services.stage_workflow_html(
                selected_request_id,
                field_notes_state_dir=ctx.notes_state_dir,
                workflow_version=workflow_version,
                display_mode=display_mode,
                include_change_requests=not ctx.isolated_workspace,
            )
        except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
            payload = services._workflow_unavailable_html("Workflow 暂时不可用", exc,
                                                 active="workflow").encode("utf-8")
            self._send(payload, "text/html; charset=utf-8", HTTPStatus.SERVICE_UNAVAILABLE)
            return True
        payload = page.encode("utf-8")
    elif path in {"/services", "/services/"}:
        payload = services.services_html(
            state, ctx.static_manager,
            quota_snapshot=ctx.quota_snapshot(),
            back_href=services._remembered_console(self.headers.get("Cookie"), state),
        ).encode("utf-8")
    else:
        payload = services._page("Not found", "<h1>404</h1>").encode("utf-8")
        self._send(payload, "text/html; charset=utf-8", HTTPStatus.NOT_FOUND)
        return True
    self._send(payload, "text/html; charset=utf-8", set_cookie=remembered)
    return False
