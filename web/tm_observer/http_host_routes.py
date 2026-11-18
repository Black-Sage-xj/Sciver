"""Domain routes; request dependencies are provided explicitly by the server."""
from __future__ import annotations

import ipaddress
import json
import subprocess
from http import HTTPStatus
from urllib.parse import parse_qs, quote
from .access_settings import AccessSettingsError
from .auth import SESSION_COOKIE, SESSION_TTL_S
from .codex_usage import AccountSwitchConflict
from .canvas_md import ExplorerError
from .workspace_scope import WorkspaceScopeError
from .http_context import RequestContext, RouteServices


def get_system(self, path: str, state: dict, ctx: RequestContext, services: RouteServices) -> bool:
    if path == "/healthz":
        system = state.get("system") if isinstance(state.get("system"), dict) else {}
        refresh_failed = bool(state.get("refresh_error"))
        payload = (
            json.dumps(
                {
                    "ok": not refresh_failed,
                    "refresh_status": state.get("refresh_status"),
                    "cache_age_s": state.get("cache_age_s"),
                    "generated_at": state.get("generated_at"),
                    "task_count": system.get("task_count"),
                    "static_views_ok": None
                    if ctx.static_manager is None or ctx.static_manager.last_result is None
                    else bool(ctx.static_manager.last_result.ok),
                },
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
        self._send(payload, "application/json; charset=utf-8",
                   HTTPStatus.SERVICE_UNAVAILABLE if refresh_failed else HTTPStatus.OK)
        return True
    if path == "/observer_state.json":
        payload = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self._send(payload, "application/json; charset=utf-8")
        return True
    if path == "/services-workers.json":
        # Sanitized read-only projection of discovered task Workers.
        data = services.services_workers_snapshot(state)
        self._send((json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8"),
                   "application/json; charset=utf-8", cache_control="no-store")
        return True
    if path == "/services-selection.json":
        from theory_master.service_provider_switch import SelectionConflict, service_selection
        try:
            data, status = service_selection(), HTTPStatus.OK
        except (SelectionConflict, OSError, ValueError) as exc:
            data, status = {"error": str(exc)}, HTTPStatus.CONFLICT
        self._send((json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8"),
                   "application/json; charset=utf-8", status)
        return True
    if path == "/codex-usage-cached.json":
        # History immediately, refresh only in a daemon background
        # thread when due. Never block the Run page on provider RPC.
        snapshot = ctx.quota_history.snapshot(request_update=True)
        payload = (json.dumps(snapshot, ensure_ascii=False) + "\n").encode("utf-8")
        self._send(payload, "application/json; charset=utf-8")
        return True
    if path == "/codex-usage.json":
        # Services keeps its explicit, live, uncached refresh semantics;
        # good results also refresh the shared Run history.
        live = services.collect_codex_usage()
        ctx.quota_history.observe(live)
        payload = (json.dumps(live, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self._send(payload, "application/json; charset=utf-8")
        return True
    if path == "/workflow-field-notes.json":
        items = services.field_note_catalog(services._synced_field_notes(ctx.notes_state_dir))
        payload = (json.dumps({"items": items}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self._send(payload, "application/json; charset=utf-8")
        return True
    if path.startswith("/artifacts/"):
        name = path[len("/artifacts/") :]
        allowed = {"architecture.html", "workflow.html", "lifecycle.html", "changes.html"}
        if ctx.static_manager is None or name not in allowed:
            self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        artifact = ctx.static_manager.state_dir / "static" / name
        try:
            payload = artifact.read_bytes()
        except OSError:
            self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        self._send(payload, "text/html; charset=utf-8")
        return True
    if path.startswith("/run-artifacts/") and path.endswith("/sequence.html"):
        parts = path.strip("/").split("/")
        if ctx.static_manager is None or len(parts) != 3 or parts[0] != "run-artifacts" or parts[2] != "sequence.html":
            self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        observer_id = parts[1]
        if len(observer_id) != 16 or any(ch not in "0123456789abcdef" for ch in observer_id):
            self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        if not any(row.get("observer_id") == observer_id for row in state.get("runs", [])):
            self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        artifact = ctx.static_manager.state_dir / "runs" / observer_id / "sequence.html"
        try:
            payload = artifact.read_bytes()
        except OSError:
            self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return True
        self._send(payload, "text/html; charset=utf-8")
        return True
    return False


def post_settings(self, path: str, ctx: RequestContext, services: RouteServices) -> bool:
    if path == "/api/settings/access":
        if not self._authorized():
            self._send_unauthorized()
            return True
        host, origin = self.headers.get("Host", ""), self.headers.get("Origin", "")
        if (origin not in {"http://" + host, "https://" + host}
                or self.headers.get("X-TM-Operator-Intent") != "1"
                or (ctx.auth is None and (not services.is_local_host(host)
                    or not ipaddress.ip_address(self.client_address[0]).is_loopback))):
            self._send(b'{"error":"origin or local access rejected"}', "application/json", HTTPStatus.FORBIDDEN)
            return True
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not self.headers.get("Content-Type", "").startswith("application/json") or not 2 <= size <= 16000:
                raise AccessSettingsError("登录设置请求格式无效")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise AccessSettingsError("登录设置请求格式无效")
            result = ctx.access.save(payload)
            ctx.auth = ctx.access.current
            self._send(json.dumps(result).encode(), "application/json", set_cookie=f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        except (ValueError, OSError) as exc:
            message = str(exc) if isinstance(exc, AccessSettingsError) else "登录设置未保存，请检查本地状态目录"
            self._send(json.dumps({"error": message}, ensure_ascii=False).encode(), "application/json", HTTPStatus.BAD_REQUEST)
        return True
    if path == "/api/settings/document-conversion":
        if not self._authorized():
            self._send_unauthorized()
            return True
        origin, host = self.headers.get("Origin"), self.headers.get("Host")
        if (not origin or not host or origin not in {f"https://{host}", f"http://{host}"}
                or self.headers.get("X-TM-Operator-Intent") != "1"):
            self._send(b'{"error":"origin or operator intent rejected"}',
                       "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
            return True
        from theory_master.document_conversion import save_settings
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not self.headers.get("Content-Type", "").startswith("application/json") or not 2 <= size <= 16000:
                raise ValueError("invalid JSON request")
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("expected an object")
            result, code = {"ok": True, **save_settings(payload)}, HTTPStatus.OK
        except (ValueError, UnicodeError) as exc:
            result, code = {"error": "PDF 转换设置无效，请检查输入并重新读取当前配置"}, HTTPStatus(getattr(exc, "status", 400))
        except (OSError, RuntimeError):
            result, code = {"error": "PDF 转换设置未保存，请检查服务器配置后重试"}, HTTPStatus.CONFLICT
        self._send(json.dumps(result, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", code)
        return True
    path = self.path.split("?", 1)[0]
    matched = services.vault_route_action(path)
    if matched and matched[1] == "vault-action":
        if not self._authorized():
            self._send_unauthorized()
            return True
        origin, host = self.headers.get("Origin"), self.headers.get("Host")
        if (not origin or not host or origin not in {f"https://{host}", f"http://{host}"}
                or self.headers.get("X-TM-Operator-Intent") != "1"):
            self._send(b'{"error":"origin or operator intent rejected"}\n',
                       "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
            return True
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if (not self.headers.get("Content-Type", "").startswith("application/json")
                    or size < 2 or size > 16000):
                raise ValueError("invalid JSON request")
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
            if ctx.isolated_workspace and isinstance(payload, dict) and payload.get("action") == "move":
                services.workspace_path(payload.get("destination"), ctx.cache.search_root)
            result = services.vault_post(path, payload, ctx.cache.search_root, ctx.cache.get(),
                                local=self._local_file_actions())
            if isinstance(payload, dict) and payload.get("action") == "move":
                ctx.cache._at = 0.0
            status = HTTPStatus.OK
        except (ExplorerError, WorkspaceScopeError) as exc:
            result, status = {"error": str(exc)}, HTTPStatus(exc.status)
        except (ValueError, UnicodeError):
            result, status = {"error": "无效笔记库请求"}, HTTPStatus.BAD_REQUEST
        except (OSError, RuntimeError):
            result, status = {"error": "笔记库操作未完成，请刷新后检查"}, HTTPStatus.CONFLICT
        self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"),
                   "application/json; charset=utf-8", status)
        return True
    return False


def post_host_forms(self, path: str, ctx: RequestContext, services: RouteServices) -> bool:
    if path == "/workflow-field-notes/update":
        if not self._authorized():
            self._send_unauthorized()
            return True
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and host and origin not in {f"https://{host}", f"http://{host}"}:
            self._send(b'{"error":"origin rejected"}\n', "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
            return True
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 8192:
            self._send(b'{"error":"invalid request"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        try:
            request_payload = json.loads(self.rfile.read(length).decode("utf-8", "replace"))
        except json.JSONDecodeError:
            self._send(b'{"error":"invalid json"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        if not isinstance(request_payload, dict) or not isinstance(request_payload.get("key"), str) or not isinstance(request_payload.get("description"), str):
            self._send(b'{"error":"key and description are required"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        key = request_payload["key"]
        try:
            services._synced_field_notes(ctx.notes_state_dir)
            services.save_override(
                ctx.notes_state_dir,
                key=key,
                description=request_payload["description"],
                known_keys=services.field_note_keys(),
            )
            item = next(entry for entry in services.field_note_catalog(services._synced_field_notes(ctx.notes_state_dir)) if entry["key"] == key)
        except (ValueError, OSError, StopIteration) as exc:
            payload = (json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n").encode("utf-8")
            self._send(payload, "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        payload = (json.dumps({"ok": True, "item": item}, ensure_ascii=False) + "\n").encode("utf-8")
        self._send(payload, "application/json; charset=utf-8")
        return True
    if path == "/change-requests/create":
        if not self._authorized():
            self._send_unauthorized()
            return True
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and host and origin not in {f"https://{host}", f"http://{host}"}:
            self._send(b'{"error":"origin rejected"}\n', "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
            return True
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 65536:
            self._send(services.change_requests_html("Invalid request size.").encode("utf-8"), "text/html; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        fields = parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
        one = lambda key: (fields.get(key) or [""])[0].strip()
        request_fields = {
            "title": one("title"),
            "status": "proposed",
            "summary": one("summary"),
            "change_types": services._csv_or_lines(one("change_types")),
            "sdk_design_refs": services._csv_or_lines(one("sdk_design_refs")),
            "problem": {"owner": one("owner"), "stage": one("stage"), "issue": one("issue")},
            "design_motivation": one("design_motivation"),
            "current_failure_mode": one("current_failure_mode"),
            "proposal": one("proposal"),
            "cost_audit": {"summary": one("cost")},
            "risk_audit": {"summary": one("risk")},
            "priority": one("priority"),
            "affected_flow_nodes": services._csv_or_lines(one("affected_flow_nodes")),
            "affected_files": services._csv_or_lines(one("affected_files")),
            "validation": services._csv_or_lines(one("validation")),
        }
        try:
            created = services.create_request(request_fields, allowed_flow_nodes=services.flow_node_ids())
        except (ValueError, OSError) as exc:
            self._send(services.change_requests_html(str(exc)).encode("utf-8"), "text/html; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", f"/workflow-stage-preview/?request={quote(created['request_id'], safe='')}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True
    if path == "/codex-account/reset-credit":
        if ctx.auth is None or not self._authorized():
            self._send_unauthorized()
            return True
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if not origin or not host or origin not in {f"https://{host}", f"http://{host}"}:
            self._send(b'{"error":"origin rejected"}\n', "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
            return True
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 1024:
            self._send(b'{"error":"invalid request"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        fields = parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
        account = (fields.get("account") or [""])[0]
        attempt = (fields.get("idempotency_key") or [""])[0]
        if set(fields) != {"account", "idempotency_key"} or len(fields["account"]) != 1 or len(fields["idempotency_key"]) != 1:
            self._send(b'{"error":"invalid request"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        try:
            outcome = services.consume_reset_credit(account, attempt)
        except ValueError as exc:
            payload = (json.dumps({"error": str(exc)}) + "\n").encode("utf-8")
            self._send(payload, "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        except (OSError, RuntimeError, subprocess.SubprocessError, TimeoutError):
            self._send(b'{"error":"Codex reset request failed; check quota before retry"}\n', "application/json; charset=utf-8", HTTPStatus.SERVICE_UNAVAILABLE)
            return True
        payload = (json.dumps({"ok": True, "outcome": outcome}) + "\n").encode("utf-8")
        self._send(payload, "application/json; charset=utf-8")
        return True
    if path == "/services-selection/switch":
        if not self._authorized():
            self._send_unauthorized()
            return True
        origin, host = self.headers.get("Origin"), self.headers.get("Host")
        if (not origin or not host or origin not in {f"https://{host}", f"http://{host}"}
                or self.headers.get("X-TM-Operator-Intent") != "1"):
            self._send(b'{"error":"origin or operator intent rejected"}\n',
                       "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
            return True
        if not self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
            self._send(b'{"error":"invalid content type"}\n',
                       "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= 1024:
            self._send(b'{"error":"invalid request length"}\n',
                       "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        fields = parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
        if set(fields) != {"account"} or len(fields["account"]) != 1:
            self._send(b'{"error":"invalid request"}\n',
                       "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        from theory_master.service_provider_switch import SelectionConflict, switch_service_provider
        try:
            result = switch_service_provider(fields["account"][0])
            status = HTTPStatus.OK
        except ValueError as exc:
            result, status = {"error": str(exc)}, HTTPStatus.BAD_REQUEST
        except SelectionConflict as exc:
            result, status = {"error": str(exc)}, HTTPStatus.CONFLICT
        except (OSError, RuntimeError) as exc:
            result, status = {"error": "切换未完成：" + type(exc).__name__}, HTTPStatus.SERVICE_UNAVAILABLE
        self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"),
                   "application/json; charset=utf-8", status)
        return True
    if path == "/codex-account/switch":
        if not self._authorized():
            self._send_unauthorized()
            return True
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and host and origin not in {f"https://{host}", f"http://{host}"}:
            self._send(b'{"error":"origin rejected"}\n', "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
            return True
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 1024:
            self._send(b'{"error":"invalid request"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        fields = parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
        account = (fields.get("account") or [""])[0]
        try:
            selected = services.switch_active_account(account)
        except AccountSwitchConflict as exc:
            payload = (json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n").encode("utf-8")
            self._send(payload, "application/json; charset=utf-8", HTTPStatus.CONFLICT)
            return True
        except (ValueError, OSError) as exc:
            payload = (json.dumps({"error": str(exc)}) + "\n").encode("utf-8")
            self._send(payload, "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
            return True
        payload = (json.dumps({"ok": True, "active_account": selected}) + "\n").encode("utf-8")
        self._send(payload, "application/json; charset=utf-8")
        return True
    return False


def post_login(self, path: str, ctx: RequestContext, services: RouteServices) -> bool:
    if ctx.auth is None or path != "/login":
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)
        return True
    try:
        length = int(self.headers.get("Content-Length", "0"))
    except ValueError:
        length = 0
    if length <= 0 or length > 8192:
        self._send(services.login_html("Invalid login request.").encode("utf-8"), "text/html; charset=utf-8", HTTPStatus.BAD_REQUEST)
        return True
    body = self.rfile.read(length).decode("utf-8", "replace")
    fields = parse_qs(body, keep_blank_values=True)
    username = (fields.get("username") or [""])[0]
    password = (fields.get("password") or [""])[0]
    account = services.match_account(ctx.auth, username, password)
    if account is None:
        self._send(services.login_html("Incorrect username or password.").encode("utf-8"), "text/html; charset=utf-8", HTTPStatus.UNAUTHORIZED)
        return True
    token = services.issue_session_token(account)
    self.send_response(HTTPStatus.SEE_OTHER)
    self.send_header("Location", "/")
    self.send_header(
        "Set-Cookie",
        f"{SESSION_COOKIE}={token}; Path=/; Max-Age={SESSION_TTL_S}; HttpOnly; "
        + ("Secure; " if ctx.access.externally_managed or not services.is_local_host(self.headers.get("Host", "")) else "")
        + "SameSite=Strict",
    )
    self.send_header("Cache-Control", "no-store")
    self.send_header("Content-Length", "0")
    self.end_headers()
    return False
