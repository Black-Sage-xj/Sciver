"""Domain routes; request dependencies are provided explicitly by the server."""
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote
from .auth import SESSION_COOKIE, authorize_header, session_from_cookie
from .vault_files import local_file_actions
from .workspace_scope import host_route
from .http_context import RequestContext


from http.server import BaseHTTPRequestHandler
from . import http_chat_routes as chat, http_supervisor_routes as supervisor
from . import http_task_routes as task, http_host_routes as host, http_page_routes as pages


class ObserverRequestHandler(BaseHTTPRequestHandler):
    """HTTP transport only; domain services own routing and mutations."""
    server_version = "TMObservatory/0.2"
    context: RequestContext
    services_provider: object

    def _send(self, payload: bytes, content_type: str,
              status: HTTPStatus = HTTPStatus.OK, *, set_cookie: str | None = None,
              cache_control: str = "no-store", etag: str | None = None,
              retry_after: int | None = None, download_name: str | None = None) -> None:
        ctx = self.context
        freshness = getattr(self, "_snapshot_meta", None) or {}
        if content_type.startswith("text/html"):
            if ctx.auth is not None and b'<!-- sciver-account-menu -->' in payload:
                account_menu = '''<div class="tm-account-menu">
                  <button type="button" class="tm-account-trigger" data-account-open aria-haspopup="dialog">
                    <span class="tm-account-avatar" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg></span>
                    <span class="tm-account-identity"><strong data-account-current>正在读取账号…</strong><small>管理账号</small></span><span aria-hidden="true">⋯</span>
                  </button>
                  <dialog class="tm-account-dialog" aria-labelledby="tm-account-dialog-title">
                    <div class="tm-account-dialog-head"><div><h2 id="tm-account-dialog-title">账号</h2><p>选择已登录的账号切换工作区</p></div><button type="button" class="tm-account-close" data-account-close aria-label="关闭">×</button></div>
                    <div class="tm-account-list" data-account-list aria-live="polite">正在读取账号…</div>
                    <div class="tm-account-error" data-account-error role="status"></div>
                    <div class="tm-account-actions"><a href="/login">＋ 登录其他账号</a><form method="post" action="/logout"><button type="submit">退出当前账号</button></form></div>
                  </dialog></div>
                  <script>(()=>{const list=document.querySelector('[data-account-list]'),current=document.querySelector('[data-account-current]'),dialog=document.querySelector('.tm-account-dialog'),error=document.querySelector('[data-account-error]');
                  const refresh=()=>fetch('/api/account-sessions').then(r=>{if(!r.ok)throw Error();return r.json()}).then(data=>{
                    current.textContent=data.current||'当前账号';list.replaceChildren();error.textContent='';
                    if(!data.accounts.length){list.textContent='暂无已保存账号';return}
                    data.accounts.forEach(name=>{const b=document.createElement('button');b.type='button';b.className='tm-account-row';
                    const label=document.createElement('span');label.textContent=name;b.append(label);
                    if(name===data.current){const badge=document.createElement('small');badge.textContent='当前账号 ✓';b.append(badge);b.disabled=true}
                    else {const hint=document.createElement('small');hint.textContent='切换';b.append(hint)}
                    b.onclick=async()=>{b.disabled=true;error.textContent='';try{const r=await fetch('/api/account-sessions/switch',
                    {method:'POST',headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},body:JSON.stringify({username:name})});
                    if(!r.ok)throw Error();location.assign('/')}catch(e){error.textContent='切换失败，请重新登录';b.disabled=false}};list.append(b)})
                  }).catch(()=>{current.textContent='账号';list.textContent='无法读取账号，请刷新页面重试'});
                  document.querySelector('[data-account-open]').addEventListener('click',()=>{dialog.showModal();refresh()});
                  document.querySelector('[data-account-close]').addEventListener('click',()=>dialog.close());
                  dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close()});refresh();})();</script>'''.encode()
                payload = payload.replace(b'<!-- sciver-account-menu -->', account_menu, 1)
            elif ctx.auth is None:
                payload = payload.replace(b'<!-- sciver-account-menu -->', b'<div class="tm-sidebar-foot"><span class="tm-indicator" aria-hidden="true"></span><span class="tm-sidebar-foot-text">Observatory Console</span></div>', 1)
            if freshness.get("refresh_error"):
                age = max(0, int(freshness.get("cache_age_s") or 0))
                warning = (f'<aside class="card warning" data-state-stale role="status">'
                           f'任务状态更新暂时失败；以下保留 {age} 秒前的快照，尚未确认最新运行状态。'
                           '</aside>').encode("utf-8")
                if re.search(br"<body\b[^>]*>", payload):
                    payload = re.sub(br"(<body\b[^>]*>)", lambda m: m[0] + warning,
                                     payload, count=1)
                else:
                    payload = warning + payload
            marker = (f'<meta name="sciver-workspace" content="{ctx.workspace_id}">'
                      '<script>(()=>{const scope=document.querySelector(\'meta[name="sciver-workspace"]\').content;'
                      'const original=window.fetch;if(!original)return;window.fetch=function(input,init){'
                      'const url=new URL(typeof input==="string"||input instanceof URL?String(input):input.url,location.href);'
                      'if(url.origin===location.origin){'
                      'const headers=new Headers(init?.headers||input?.headers);headers.set("X-Sciver-Workspace",scope);'
                      'return original.call(this,input,{...init,headers});}return original.call(this,input,init);};})();</script>').encode()
            if b"<head>" in payload:
                payload = payload.replace(b"<head>", b"<head>" + marker, 1)
            elif b"<head " in payload:
                start = payload.index(b"<head ")
                end = payload.find(b">", start)
                if end >= 0:
                    payload = payload[:end + 1] + marker + payload[end + 1:]
        if etag and status == HTTPStatus.OK and self.headers.get("If-None-Match", "").strip() == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("Cache-Control", cache_control)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if download_name:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(download_name, safe=""))
            self.send_header("X-Content-Type-Options", "nosniff")
        if content_type.startswith("image/") or content_type == "application/pdf":
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "sandbox; default-src 'none'; style-src 'unsafe-inline'")
        self.send_header("Cache-Control", cache_control)
        if freshness.get("refresh_status"):
            self.send_header("X-Sciver-State-Freshness", str(freshness["refresh_status"]))
            self.send_header("X-Sciver-State-Age", str(freshness.get("cache_age_s") or 0))
            self.send_header("X-Sciver-State-Refresh-Failed", "1" if freshness.get("refresh_error") else "0")
        if etag:
            self.send_header("ETag", etag)
        if retry_after is not None:
            self.send_header("Retry-After", str(max(1, int(retry_after))))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _local_file_actions(self) -> bool:
        ctx = self.context
        return local_file_actions(host=self.headers.get("Host", ""), client=self.client_address[0],
                                  forwarded=bool(self.headers.get("Forwarded") or self.headers.get("X-Forwarded-For")))

    def _authorized(self) -> bool:
        ctx = self.context
        if ctx.auth is None:
            return True
        return authorize_header(self.headers.get("Authorization"), ctx.auth) or session_from_cookie(
            self.headers.get("Cookie"), ctx.auth
        )

    def _send_unauthorized(self) -> None:
        ctx = self.context
        payload = b"authentication required\n"
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Sciver Observatory", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _workspace_route_allowed(self, path: str) -> bool:
        ctx = self.context
        if ctx.isolated_workspace and host_route(path, method=self.command):
            if not self._authorized():
                self._send_unauthorized()
            else:
                self._send('{"error":"此设置由服务管理员管理"}'.encode(),
                           "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
            return False
        return True

    def do_PUT(self) -> None:
        ctx = self.context
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

    def handle_one_request(self) -> None:
        ctx = self.context
        self._snapshot_meta = None
        self._tm_request_started = time.monotonic()
        self._tm_response_status = 0
        try:
            super().handle_one_request()
        finally:
            elapsed_ms = (time.monotonic() - self._tm_request_started) * 1000.0
            status = int(getattr(self, "_tm_response_status", 0) or 0)
            if elapsed_ms >= 250.0 or status >= 400:
                raw_path = (getattr(self, "path", "") or "-").split("?", 1)[0]
                parts = raw_path.strip("/").split("/") if raw_path != "-" else []
                if len(parts) >= 4 and parts[:3] == ["api", "canvas-md", "tasks"]:
                    route = "/api/canvas-md/tasks/:task/" + (parts[4] if len(parts) >= 5 else "root")
                elif len(parts) >= 3 and parts[:2] in (["api", "research-chat"], ["api", "supervisor-session"]):
                    route = "/" + "/".join(parts[:2]) + "/:session/" + (parts[3] if len(parts) >= 4 else "root")
                elif len(parts) >= 2 and parts[0] == "run":
                    route = "/run/:task/" + (parts[2] if len(parts) > 2 else "")
                elif len(parts) >= 2 and parts[0] == "supervisor-notification":
                    route = "/supervisor-notification/:id"
                else:
                    route = raw_path[:120]
                record = {"event": "tm_http_slow_request" if elapsed_ms >= 250.0 else "tm_http_error",
                          "method": getattr(self, "command", "-"), "route": route,
                          "status": status, "duration_ms": round(elapsed_ms, 1)}
                try:
                    sys.stderr.write("TM_HTTP " + json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                    sys.stderr.flush()
                except OSError:
                    pass

    def send_response(self, code: int, message: str | None = None) -> None:
        ctx = self.context
        self._tm_response_status = int(code)
        super().send_response(code, message)

    def log_message(self, fmt: str, *args: object) -> None:
        ctx = self.context
        return

    do_PATCH = do_PUT
    do_DELETE = do_PUT

    def do_GET(self) -> None:
        ctx = self.context
        services = self.services_provider()
        path = self.path.split("?", 1)[0]
        if path == "/favicon.svg":
            icon_path = Path(__file__).resolve().parent / "static" / "sciver-mark.svg"
            try:
                payload = icon_path.read_bytes()
            except OSError:
                self._send(b"favicon not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            else:
                self._send(payload, "image/svg+xml; charset=utf-8", cache_control="public, max-age=86400")
            return
        if ctx.auth is not None and path == "/login":
            payload = services.login_html().encode("utf-8")
            self._send(payload, "text/html; charset=utf-8")
            return
        if not self._authorized():
            if path in {"/healthz", "/observer_state.json"}:
                self._send_unauthorized()
            else:
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/login")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
            return
        scope = self.headers.get("X-Sciver-Workspace")
        if scope is not None and scope != ctx.workspace_id:
            self._send('{"error":"工作区已切换，请刷新页面后重试"}'.encode(),
                       "application/json; charset=utf-8", HTTPStatus.CONFLICT)
            return
        if not self._workspace_route_allowed(path):
            return
        if path == "/research-chat/widget.js":
            script = Path(__file__).resolve().parent / "research_chat_widget.js"
            self._send(script.read_bytes(), "text/javascript; charset=utf-8")
            return
        if path == "/research-chat/history.js":
            self._send((Path(__file__).parent / "research_chat_history.js").read_bytes(), "text/javascript; charset=utf-8")
            return
        if path.startswith("/research-chat/assets/"):
            relative = path.removeprefix("/research-chat/assets/")
            dist = Path(__file__).resolve().parent / "research_chat_dist"
            try:
                target = (dist / relative).resolve(strict=True)
                if (not relative or ".." in Path(relative).parts or dist.resolve() not in target.parents
                        or target.is_symlink() or not target.is_file()):
                    raise OSError("asset not found")
                content_type = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                                ".svg": "image/svg+xml", ".woff2": "font/woff2"}.get(
                                    target.suffix.lower(), "application/octet-stream")
                payload = target.read_bytes()
                etag = '"' + hashlib.sha256(payload).hexdigest() + '"'
                self._send(payload, content_type, cache_control="private, max-age=300, must-revalidate", etag=etag)
            except OSError:
                self._send(b"chat asset not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
            return
        if path == "/vendor/loquix-incident.js":
            bundle = Path(__file__).resolve().parent / "vendor" / "loquix-incident.js"
            if not bundle.is_file() or bundle.is_symlink():
                self._send(b"chat bundle missing\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            payload = bundle.read_bytes()
            etag = '"' + hashlib.sha256(payload).hexdigest() + '"'
            self._send(payload, "text/javascript; charset=utf-8",
                       cache_control="private, max-age=300, must-revalidate", etag=etag)
            return
        state = ctx.cache.get()
        self._snapshot_meta = {k: state.get(k) for k in ("refresh_status", "refresh_error", "cache_age_s", "last_success_at")}
        if state.get("refresh_status") in {"loading", "unavailable"}:
            if path.startswith("/api/") or path in {"/healthz", "/observer_state.json"}:
                self._send(b"Task state is initializing; retry shortly.\n",
                           "text/plain; charset=utf-8", HTTPStatus.SERVICE_UNAVAILABLE, retry_after=1)
            else:
                # A cold-cache race can affect a browser navigation even while
                # another request is successfully building the first snapshot.
                # Do not strand the user on a raw-text 503 page: retry the same
                # navigation automatically, with a manual link as a fallback.
                retry_path = html.escape(self.path, quote=True)
                page = (
                    "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                    "<meta http-equiv=\"refresh\" content=\"1\">"
                    "<title>Sciver · 正在初始化</title></head><body>"
                    "<main><h1>任务状态正在初始化</h1>"
                    "<p>页面将在 1 秒后自动重试。</p>"
                    f"<p><a href=\"{retry_path}\">立即重试</a></p>"
                    "</main></body></html>"
                )
                self._send(page.encode("utf-8"), "text/html; charset=utf-8",
                           HTTPStatus.SERVICE_UNAVAILABLE, retry_after=1)
            return
        if chat.get_chat(self, path, state, ctx, services):
            return
        if supervisor.get_streams(self, path, state, ctx, services):
            return
        if task.get_explorer(self, path, state, ctx, services):
            return
        if host.get_system(self, path, state, ctx, services):
            return
        if supervisor.get_reviews(self, path, state, ctx, services):
            return
        if task.get_task(self, path, state, ctx, services):
            return
        if pages.get_page(self, path, state, ctx, services):
            return

    def do_POST(self) -> None:
        ctx = self.context
        services = self.services_provider()
        path = self.path.split("?", 1)[0]
        if path == "/logout" and ctx.auth is not None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        # Another tab may have changed this origin's login cookie. Bind
        # browser writes to the workspace that rendered the current page.
        scope = self.headers.get("X-Sciver-Workspace")
        if path != "/login" and scope is not None and scope != ctx.workspace_id and self._authorized():
            self._send('{"error":"工作区已切换，请刷新页面后重试"}'.encode(),
                       "application/json; charset=utf-8", HTTPStatus.CONFLICT)
            return
        if not self._workspace_route_allowed(path):
            return
        if host.post_settings(self, path, ctx, services):
            return
        if chat.post_chat(self, path, ctx, services):
            return
        if supervisor.post_supervisor(self, path, ctx, services):
            return
        if task.post_task(self, path, ctx, services):
            return
        if host.post_host_forms(self, path, ctx, services):
            return
        if host.post_login(self, path, ctx, services):
            return


def build_handler(context: RequestContext, services_provider):
    class Handler(ObserverRequestHandler):
        pass
    Handler.context = context
    Handler.services_provider = staticmethod(services_provider)
    return Handler
