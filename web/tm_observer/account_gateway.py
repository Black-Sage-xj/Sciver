"""Authenticate once and proxy each account to its fixed workspace server."""
from __future__ import annotations

import http.client
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs

from .access_settings import is_local_host
from .auth import (
    AuthRealm, SESSION_COOKIE, SESSION_TTL_S, account_from_cookie,
    account_from_header, issue_session_token, match_account,
)

MAX_BODY_BYTES = 32 * 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 120.0
CLIENT_TIMEOUT_SECONDS = 30.0
_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade",
})


def _hop_headers(headers) -> set[str]:
    names = set(_HOP_HEADERS)
    for value in headers.get_all("Connection", []):
        names.update(part.strip().lower() for part in value.split(",") if part.strip())
    return names


def make_gateway_handler(auth: AuthRealm, targets: dict[str, tuple[str, int]]):
    """Use verified credentials as the sole selector for a fixed backend map.

    Backends keep their own authentication checks. Original Host, Origin,
    Authorization and Cookie headers reach the selected backend unchanged.
    """
    routes = dict(targets)
    for host, port in routes.values():
        if not isinstance(host, str) or not host or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid account backend target")

    class GatewayHandler(BaseHTTPRequestHandler):
        server_version = "SciverAccountGateway/0.1"
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(CLIENT_TIMEOUT_SECONDS)

        def _send(self, status: int, body: bytes = b"", *, content_type: str = "text/plain; charset=utf-8",
                  headers: tuple[tuple[str, str], ...] = ()) -> None:
            self.close_connection = True
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            for name, value in headers:
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _login_page(self, status: int = HTTPStatus.OK, error: str | None = None) -> None:
            # web.serve imports this factory when account workspaces are enabled.
            from .web import login_html
            self._send(status, login_html(error).encode("utf-8"), content_type="text/html; charset=utf-8")

        def _body_length(self) -> int | None:
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") is not None or len(lengths) > 1:
                self._send(HTTPStatus.BAD_REQUEST, b"invalid request framing\n")
                return None
            raw = lengths[0].strip() if lengths else "0"
            if not raw.isascii() or not raw.isdecimal():
                self._send(HTTPStatus.BAD_REQUEST, b"invalid content length\n")
                return None
            try:
                length = int(raw)
            except ValueError:
                self._send(HTTPStatus.BAD_REQUEST, b"invalid content length\n")
                return None
            if length > MAX_BODY_BYTES:
                self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"request body too large\n")
                return None
            return length

        def _body(self, length: int) -> bytes | None:
            try:
                body = self.rfile.read(length)
            except (OSError, TimeoutError):
                self._send(HTTPStatus.REQUEST_TIMEOUT, b"request body timed out\n")
                return None
            if len(body) != length:
                self._send(HTTPStatus.BAD_REQUEST, b"incomplete request body\n")
                return None
            return body

        def _unauthorized(self, path: str) -> None:
            if path in {"/healthz", "/observer_state.json", "/api"} or path.startswith("/api/"):
                self._send(HTTPStatus.UNAUTHORIZED, b"authentication required\n", headers=(
                    ("WWW-Authenticate", 'Basic realm="Sciver Observatory", charset="UTF-8"'),
                ))
            else:
                self._send(HTTPStatus.SEE_OTHER, headers=(("Location", "/login"),))

        def _login(self, length: int) -> None:
            if not 0 < length <= 8192:
                self._login_page(HTTPStatus.BAD_REQUEST, "Invalid login request.")
                return
            body = self._body(length)
            if body is None:
                return
            try:
                fields = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=16)
            except (ValueError, UnicodeDecodeError):
                self._login_page(HTTPStatus.BAD_REQUEST, "Invalid login request.")
                return
            username = fields.get("username", [""])
            password = fields.get("password", [""])
            if len(username) != 1 or len(password) != 1:
                self._login_page(HTTPStatus.BAD_REQUEST, "Invalid login request.")
                return
            account = match_account(auth, username[0], password[0])
            if account is None:
                self._login_page(HTTPStatus.UNAUTHORIZED, "Incorrect username or password.")
                return
            token = issue_session_token(account)
            cookie = f"{SESSION_COOKIE}={token}; Path=/; Max-Age={SESSION_TTL_S}; HttpOnly; "
            if not is_local_host(self.headers.get("Host", "")):
                cookie += "Secure; "
            self._send(HTTPStatus.SEE_OTHER, headers=(
                ("Location", "/"), ("Set-Cookie", cookie + "SameSite=Strict"),
            ))

        def _proxy(self, target: tuple[str, int], body: bytes) -> None:
            connection = http.client.HTTPConnection(*target, timeout=CONNECT_TIMEOUT_SECONDS)
            sent_headers = False
            try:
                connection.connect()
                if connection.sock is not None:
                    connection.sock.settimeout(READ_TIMEOUT_SECONDS)
                connection.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
                excluded = _hop_headers(self.headers) | {"content-length", "host", "expect"}
                for name, value in self.headers.items():
                    if name.lower() not in excluded:
                        connection.putheader(name, value)
                connection.putheader("Host", self.headers.get("Host", f"{target[0]}:{target[1]}"))
                if body or self.command == "POST" or self.headers.get("Content-Length") is not None:
                    connection.putheader("Content-Length", str(len(body)))
                connection.putheader("Connection", "close")
                connection.endheaders(body)
                response = connection.getresponse()
                self.close_connection = True
                self.send_response(response.status, response.reason)
                excluded = _hop_headers(response.headers) | {"cache-control"}
                for name, value in response.getheaders():
                    if name.lower() not in excluded:
                        self.send_header(name, value)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                sent_headers = True
                if self.command != "HEAD":
                    while chunk := response.read1(64 * 1024):
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except (OSError, http.client.HTTPException, ValueError):
                if not sent_headers:
                    self._send(HTTPStatus.BAD_GATEWAY, b"account workspace unavailable\n")
                else:
                    self.close_connection = True
            finally:
                connection.close()

        def _dispatch(self) -> None:
            self.close_connection = True
            if not self.path.startswith("/") or self.path.startswith("//"):
                self._send(HTTPStatus.BAD_REQUEST, b"invalid request target\n")
                return
            if len(self.headers.get_all("Authorization", [])) > 1 or len(self.headers.get_all("Cookie", [])) > 1:
                self._send(HTTPStatus.BAD_REQUEST, b"ambiguous authentication headers\n")
                return
            length = self._body_length()
            if length is None:
                return
            path = self.path.partition("?")[0]
            if path == "/login":
                if self.command == "POST":
                    self._login(length)
                else:
                    self._login_page()
                return
            basic = account_from_header(self.headers.get("Authorization"), auth)
            cookie = account_from_cookie(self.headers.get("Cookie"), auth)
            if basic is not None and cookie is not None and basic.username != cookie.username:
                self._send(HTTPStatus.UNAUTHORIZED, b"conflicting authenticated accounts\n")
                return
            account = basic or cookie
            if account is None:
                self._unauthorized(path)
                return
            target = routes.get(account.username)
            if target is None:
                self._send(HTTPStatus.BAD_GATEWAY, b"account workspace unavailable\n")
                return
            body = self._body(length)
            if body is not None:
                self._proxy(target, body)

        do_GET = _dispatch
        do_POST = _dispatch
        do_HEAD = _dispatch

        def do_PUT(self) -> None:
            self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"method not allowed\n")

        do_PATCH = do_PUT
        do_DELETE = do_PUT
        do_OPTIONS = do_PUT

        def log_message(self, fmt: str, *args: object) -> None:
            # Requests can contain user material; do not log paths or headers.
            return

    return GatewayHandler
