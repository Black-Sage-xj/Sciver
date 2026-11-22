from __future__ import annotations

import base64
import http.client
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode

from tm_observer.account_gateway import MAX_BODY_BYTES, make_gateway_handler
from tm_observer.access_settings import hash_password
from tm_observer.auth import (
    SESSION_COOKIE, SESSION_TTL_S, BasicAuthCredentials, ObservatoryAuth,
    account_from_cookie, account_from_header, authorize_header,
    issue_session_token, session_from_cookie,
)


def basic(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def cookie(account: BasicAuthCredentials) -> str:
    return SESSION_COOKIE + "=" + issue_session_token(account)


class AccountIdentityTests(unittest.TestCase):
    def setUp(self):
        self.owner = BasicAuthCredentials("owner", "owner-test-password")
        self.second = BasicAuthCredentials("科研者", hash_password("second-test-password"))
        self.realm = ObservatoryAuth((self.owner, self.second))

    def test_basic_identity_and_existing_boolean_api(self):
        for account, password in ((self.owner, self.owner.password), (self.second, "second-test-password")):
            header = basic(account.username, password)
            self.assertIs(account_from_header(header, self.realm), account)
            self.assertTrue(authorize_header(header, self.realm))
        for header in (None, "Bearer token", "Basic @@@", "Basic /w==", basic("owner", "wrong")):
            self.assertIsNone(account_from_header(header, self.realm))
            self.assertFalse(authorize_header(header, self.realm))

    def test_existing_cookie_resolves_to_its_account(self):
        for account in (self.owner, self.second):
            header = "other=keep; " + cookie(account)
            self.assertIs(account_from_cookie(header, self.realm), account)
            self.assertTrue(session_from_cookie(header, self.realm))
            self.assertIs(account_from_cookie(header, account), account)
        self.assertIsNone(account_from_cookie(cookie(self.owner), self.second))
        expired = SESSION_COOKIE + "=" + issue_session_token(self.owner, now=time.time() - 60, ttl_s=1)
        for header in (None, "", "unrelated=value", SESSION_COOKIE + "=invalid", expired):
            self.assertIsNone(account_from_cookie(header, self.realm))
            self.assertFalse(session_from_cookie(header, self.realm))


class AccountGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner = BasicAuthCredentials("owner", "owner-test-password")
        cls.second = BasicAuthCredentials("sciver1", "second-test-password")
        cls.realm = ObservatoryAuth((cls.owner, cls.second))
        cls.events = []
        cls.release_stream = threading.Event()
        cls.servers = []
        cls.threads = []

        def backend(label):
            class Backend(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def reply(self):
                    length = int(self.headers.get("Content-Length", "0"))
                    body = self.rfile.read(length)
                    event = {"account": label, "method": self.command, "path": self.path,
                             "headers": dict(self.headers), "body": body.decode("utf-8")}
                    cls.events.append(event)
                    if self.path == "/stream":
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Transfer-Encoding", "chunked")
                        self.end_headers()
                        first = b"data: first\n\n"
                        self.wfile.write(f"{len(first):x}\r\n".encode() + first + b"\r\n")
                        self.wfile.flush()
                        cls.release_stream.wait(timeout=5)
                        last = b"data: last\n\n"
                        self.wfile.write(f"{len(last):x}\r\n".encode() + last + b"\r\n0\r\n\r\n")
                        self.wfile.flush()
                        return
                    payload = json.dumps(event).encode()
                    self.send_response(303 if self.path == "/redirect" else 200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.send_header("Connection", "X-Backend-Hop")
                    self.send_header("X-Backend-Hop", "must disappear")
                    if self.path == "/redirect":
                        self.send_header("Location", "/next")
                        self.send_header("Set-Cookie", "one=value; Path=/")
                        self.send_header("Set-Cookie", "two=value; Path=/")
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(payload)

                do_GET = reply
                do_POST = reply
                do_HEAD = reply

                def log_message(self, *_args):
                    pass

            return Backend

        def serve(handler):
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.servers.append(server)
            cls.threads.append(thread)
            return server

        first = serve(backend("owner"))
        second = serve(backend("sciver1"))
        cls.gateway = serve(make_gateway_handler(cls.realm, {
            "owner": first.server_address, "sciver1": second.server_address,
        }))

    @classmethod
    def tearDownClass(cls):
        cls.release_stream.set()
        for server in cls.servers:
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join(timeout=3)

    def setUp(self):
        self.events.clear()

    def request(self, method, path, *, headers=None, body=None):
        connection = http.client.HTTPConnection(*self.gateway.server_address, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            result = (response.status, response.headers, response.read())
        finally:
            connection.close()
        self.assertEqual(result[1]["Cache-Control"], "no-store")
        return result

    def test_login_page_and_head_do_not_reach_a_backend(self):
        status, _headers, body = self.request("GET", "/login")
        self.assertEqual(status, 200)
        self.assertIn(b'name="username"', body)
        status, headers, body = self.request("HEAD", "/login")
        self.assertEqual(status, 200)
        self.assertGreater(int(headers["Content-Length"]), 0)
        self.assertEqual(body, b"")
        self.assertEqual(self.events, [])

    def test_unauthenticated_pages_redirect_and_apis_return_401(self):
        for path in ("/", "/run/anything/", "/settings/?username=owner"):
            status, headers, _body = self.request("GET", path)
            self.assertEqual(status, 303)
            self.assertEqual(headers["Location"], "/login")
        for path in ("/healthz", "/observer_state.json?account=owner", "/api", "/api/research-chat/sessions"):
            status, headers, _body = self.request("GET", path)
            self.assertEqual(status, 401)
            self.assertIn("Basic", headers["WWW-Authenticate"])
        self.assertEqual(self.events, [])

    def test_login_issues_compatible_local_and_secure_remote_cookies(self):
        for host in ("127.0.0.1:8787", "tm.example.test"):
            status, headers, body = self.request("POST", "/login", headers={"Host": host},
                body=urlencode({"username": "sciver1", "password": self.second.password}))
            self.assertEqual((status, body, headers["Location"]), (303, b"", "/"))
            value = headers["Set-Cookie"]
            self.assertIn(f"Max-Age={SESSION_TTL_S}", value)
            self.assertIn("HttpOnly", value)
            self.assertIn("SameSite=Strict", value)
            self.assertEqual("Secure;" in value, host == "tm.example.test")
            self.assertIs(account_from_cookie(value, self.realm), self.second)
            status, _headers, response_body = self.request("GET", "/", headers={"Cookie": value})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(response_body)["account"], "sciver1")

    def test_wrong_and_ambiguous_login_are_rejected(self):
        status, _headers, body = self.request("POST", "/login", body="username=owner&password=wrong")
        self.assertEqual(status, 401)
        self.assertIn(b"Incorrect username or password", body)
        status, _headers, _body = self.request("POST", "/login", body="username=owner&username=sciver1&password=wrong")
        self.assertEqual(status, 400)
        self.assertEqual(self.events, [])

    def test_logout_then_login_to_another_workspace(self):
        status, headers, _ = self.request("POST", "/logout", headers={"Cookie": cookie(self.owner)}, body="")
        self.assertEqual((status, headers["Location"]), (303, "/login"))
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertIsNone(account_from_cookie(headers["Set-Cookie"], self.realm))
        status, headers, _ = self.request("POST", "/login", body=urlencode({
            "username": self.second.username, "password": self.second.password,
        }))
        self.assertEqual(status, 303)
        status, _, body = self.request("GET", "/", headers={"Cookie": headers["Set-Cookie"]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["account"], self.second.username)

    def test_remembered_accounts_can_switch_without_password(self):
        saved = []
        for account in (self.owner, self.second):
            status, headers, _ = self.request("POST", "/login", body=urlencode({"username": account.username, "password": account.password}))
            self.assertEqual(status, 303)
            saved.append(headers.get_all("Set-Cookie")[1].split(";", 1)[0])
        cookies = "; ".join(saved + [cookie(self.second)])
        status, _, body = self.request("GET", "/api/account-sessions", headers={"Cookie": cookies})
        self.assertEqual(json.loads(body), {"accounts": ["owner", "sciver1"], "current": "sciver1"})
        headers = {"Cookie": cookies, "Host": "127.0.0.1:8787", "Origin": "http://127.0.0.1:8787", "X-TM-Operator-Intent": "1"}
        status, response, _ = self.request("POST", "/api/account-sessions/switch", headers=headers, body=json.dumps({"username": "owner"}))
        self.assertEqual(status, 200)
        self.assertIs(account_from_cookie(response["Set-Cookie"], self.realm), self.owner)
        status, _, _ = self.request("POST", "/api/account-sessions/switch", headers={"Cookie": cookies}, body='{"username":"owner"}')
        self.assertEqual(status, 403)

    def test_account_comes_only_from_verified_credentials(self):
        for account in (self.owner, self.second):
            for headers in ({"Cookie": cookie(account)}, {"Authorization": basic(account.username, account.password)}):
                status, _response_headers, body = self.request("GET", "/?account=owner&username=owner", headers={
                    **headers, "X-Sciver-Account": "owner", "X-Account": "owner",
                })
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["account"], account.username)

    def test_post_preserves_body_host_origin_cookie_and_intent(self):
        value = cookie(self.second)
        status, _headers, body = self.request("POST", "/api/research-chat/session/send?keep=1", headers={
            "Host": "tm.example.test", "Origin": "https://tm.example.test", "Cookie": value,
            "Content-Type": "application/json", "X-TM-Operator-Intent": "1",
            "Connection": "X-Request-Hop", "X-Request-Hop": "must disappear",
        }, body='{"content":"hello"}')
        self.assertEqual(status, 200)
        event = json.loads(body)
        self.assertEqual(event["account"], "sciver1")
        self.assertEqual(event["path"], "/api/research-chat/session/send?keep=1")
        self.assertEqual(event["body"], '{"content":"hello"}')
        for name, expected in (("Host", "tm.example.test"), ("Origin", "https://tm.example.test"),
                               ("Cookie", value), ("X-TM-Operator-Intent", "1")):
            self.assertEqual(event["headers"][name], expected)
        self.assertNotIn("X-Request-Hop", event["headers"])

    def test_backend_headers_redirect_and_head(self):
        headers = {"Cookie": cookie(self.owner)}
        status, response_headers, _body = self.request("GET", "/redirect", headers=headers)
        self.assertEqual(status, 303)
        self.assertEqual(response_headers["Location"], "/next")
        self.assertEqual(response_headers.get_all("Set-Cookie"), ["one=value; Path=/", "two=value; Path=/"])
        self.assertIsNone(response_headers.get("X-Backend-Hop"))
        status, response_headers, body = self.request("HEAD", "/", headers=headers)
        self.assertEqual(status, 200)
        self.assertGreater(int(response_headers["Content-Length"]), 0)
        self.assertEqual(body, b"")

    def test_conflicting_valid_identities_are_rejected(self):
        status, _headers, _body = self.request("GET", "/", headers={
            "Authorization": basic("owner", self.owner.password), "Cookie": cookie(self.second),
        })
        self.assertEqual(status, 401)
        self.assertEqual(self.events, [])
        status, _headers, _body = self.request("GET", "/", headers={
            "Authorization": basic("owner", self.owner.password), "Cookie": cookie(self.owner),
        })
        self.assertEqual(status, 200)

    def test_body_limit_and_invalid_framing_are_rejected_before_reading(self):
        for extra, expected in (({"Content-Length": str(MAX_BODY_BYTES + 1)}, 413),
                                ({"Content-Length": "-1"}, 400),
                                ({"Content-Length": "not-a-number"}, 400),
                                ({"Transfer-Encoding": "chunked"}, 400)):
            status, _headers, _body = self.request("POST", "/api/action", headers={
                "Cookie": cookie(self.owner), **extra,
            })
            self.assertEqual(status, expected)
        self.assertEqual(self.events, [])

    def test_proxy_flushes_chunked_stream_without_waiting_for_completion(self):
        self.release_stream.clear()
        connection = http.client.HTTPConnection(*self.gateway.server_address, timeout=3)
        try:
            connection.request("GET", "/stream", headers={"Cookie": cookie(self.second)})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Type"], "text/event-stream")
            self.assertIsNone(response.headers.get("Transfer-Encoding"))
            self.assertEqual(response.read1(1024), b"data: first\n\n")
            self.release_stream.set()
            self.assertEqual(response.read(), b"data: last\n\n")
        finally:
            self.release_stream.set()
            connection.close()

    def test_missing_and_unavailable_backend_return_502(self):
        for targets in ({}, {"owner": ("127.0.0.1", 1)}):
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_gateway_handler(self.realm, targets))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                connection.request("GET", "/", headers={"Cookie": cookie(self.owner)})
                response = connection.getresponse()
                self.assertEqual(response.status, 502)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(response.read(), b"account workspace unavailable\n")
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
