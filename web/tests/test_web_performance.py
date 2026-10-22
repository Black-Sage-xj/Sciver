"""Deterministic performance contracts for Web state/cache paths; no real tasks or LLM calls."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from tm_observer.web import StateCache, make_handler


def _state(root: Path, generation: int) -> dict:
    provenance = {
        "git_head": {"source": str(root / ".git"), "field": "HEAD", "method": "deferred"},
        "git_branch": {"source": str(root / ".git"), "field": "branch", "method": "deferred"},
        "git_dirty": {"source": str(root), "field": "worktree", "method": "deferred"},
    }
    return {
        "schema": "tm-observer/state/v1", "generated_at": str(generation),
        "search_root": str(root), "system": {
            "git_head": None, "git_branch": None, "git_dirty": None,
            "observer_version": "test", "task_count": 0, "provenance": provenance,
        }, "runs": [],
    }


def _git_snapshot(root: Path) -> dict:
    return {
        "git_head": "abc", "git_branch": "test", "git_dirty": False,
        "provenance": {
            "git_head": {"source": str(root / ".git"), "field": "HEAD", "method": "git"},
            "git_branch": {"source": str(root / ".git"), "field": "branch", "method": "git"},
            "git_dirty": {"source": str(root), "field": "worktree", "method": "git"},
        },
    }


class StateCachePerformanceTests(unittest.TestCase):
    def test_cold_concurrent_requests_share_one_bounded_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = threading.Lock()
            calls = 0

            def collect(_root: Path, *, include_git_info: bool = True) -> dict:
                nonlocal calls
                with lock:
                    calls += 1
                    generation = calls
                time.sleep(0.08)
                return _state(root, generation)

            git_finished = threading.Event()

            def collect_git(path: Path) -> dict:
                result = _git_snapshot(path)
                git_finished.set()
                return result

            with patch("tm_observer.web.collect_state", side_effect=collect), \
                 patch("tm_observer.web.collect_git_info", side_effect=collect_git):
                cache = StateCache(root, ttl_s=1, cold_wait_s=1, git_ttl_s=60)
                with ThreadPoolExecutor(max_workers=8) as pool:
                    snapshots = list(pool.map(lambda _i: cache.get(), range(8)))
                self.assertEqual(calls, 1)
                self.assertTrue(any(row.get("generated_at") == "1" for row in snapshots))
                self.assertTrue(all(row.get("generated_at") == "1" or row.get("refresh_status") == "loading"
                                    for row in snapshots))
                self.assertTrue(git_finished.wait(1))
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline and cache.get()["system"].get("git_head") != "abc":
                    time.sleep(0.01)
                self.assertEqual(cache.get()["system"]["git_head"], "abc")

    def test_cold_timeout_returns_retryable_loading_state_not_false_empty_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            started = threading.Event()
            release = threading.Event()

            def collect(_root: Path, *, include_git_info: bool = True) -> dict:
                started.set()
                release.wait(1)
                return _state(root, 1)

            with patch("tm_observer.web.collect_state", side_effect=collect), \
                 patch("tm_observer.web.collect_git_info", side_effect=lambda path: _git_snapshot(path)):
                cache = StateCache(root, ttl_s=1, cold_wait_s=0.05, git_ttl_s=60)
                server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    from urllib.error import HTTPError
                    from urllib.request import urlopen
                    with self.assertRaises(HTTPError) as ctx:
                        urlopen(f"http://127.0.0.1:{server.server_address[1]}/healthz", timeout=2)
                    self.assertEqual(ctx.exception.code, 503)
                    self.assertEqual(ctx.exception.headers.get("Retry-After"), "1")
                    self.assertTrue(started.wait(1))

                    # Browser navigations must recover from the same cold-start
                    # race automatically instead of displaying the raw 503 text.
                    with self.assertRaises(HTTPError) as page_ctx:
                        urlopen(
                            f"http://127.0.0.1:{server.server_address[1]}/run/0123456789abcdef/",
                            timeout=2,
                        )
                    self.assertEqual(page_ctx.exception.code, 503)
                    self.assertIn("text/html", page_ctx.exception.headers.get("Content-Type", ""))
                    self.assertEqual(page_ctx.exception.headers.get("Retry-After"), "1")
                    page = page_ctx.exception.read().decode("utf-8")
                    self.assertIn('http-equiv="refresh" content="1"', page)
                    self.assertIn('href="/run/0123456789abcdef/"', page)
                    self.assertIn("任务状态正在初始化", page)
                finally:
                    release.set()
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    def test_expired_snapshot_returns_immediately_while_single_refresh_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = threading.Lock()
            calls = 0
            second_started = threading.Event()
            release_second = threading.Event()
            second_finished = threading.Event()

            def collect(_root: Path, *, include_git_info: bool = True) -> dict:
                nonlocal calls
                with lock:
                    calls += 1
                    generation = calls
                if generation == 2:
                    second_started.set()
                    release_second.wait(2)
                    second_finished.set()
                return _state(root, generation)

            with patch("tm_observer.web.collect_state", side_effect=collect), \
                 patch("tm_observer.web.collect_git_info", side_effect=lambda path: _git_snapshot(path)):
                cache = StateCache(root, ttl_s=0.2, cold_wait_s=1, git_ttl_s=60)
                first = cache.get()
                with cache._condition:
                    cache._at = time.monotonic() - 1

                start = time.perf_counter()
                stale = cache.get()
                elapsed = time.perf_counter() - start
                self.assertLess(elapsed, 0.08, "expired cache must not make the HTTP caller wait for collection")
                self.assertEqual(stale["generated_at"], first["generated_at"])
                self.assertTrue(second_started.wait(1))

                with ThreadPoolExecutor(max_workers=8) as pool:
                    start = time.perf_counter()
                    concurrent = list(pool.map(lambda _i: cache.get(), range(8)))
                    elapsed = time.perf_counter() - start
                self.assertLess(elapsed, 0.08)
                self.assertEqual({row["generated_at"] for row in concurrent}, {"1"})
                self.assertEqual(calls, 2, "concurrent stale reads must not start duplicate refreshes")

                release_second.set()
                self.assertTrue(second_finished.wait(1))
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    with cache._condition:
                        if cache._state and cache._state["generated_at"] == "2":
                            break
                    time.sleep(0.01)
                self.assertEqual(cache.get()["generated_at"], "2")


class ChatProgressDeltaTests(unittest.TestCase):
    def test_progress_endpoint_returns_only_new_text_and_recovers_invalid_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(StateCache(root, cold_wait_s=1)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 2)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = f"http://127.0.0.1:{server.server_address[1]}/api/research-chat/{'a' * 32}/progress"
            view = {"session": {"stream_status": "streaming", "streaming": True,
                                 "stream_text": "abcdefgh", "active_run": {}}}
            with patch("tm_observer.web.research_chat_snapshot", return_value=view):
                from urllib.request import urlopen
                with urlopen(url + "?offset=3", timeout=5) as response:
                    delta = json.loads(response.read())
                self.assertEqual(delta["text"], "defgh")
                self.assertEqual(delta["next_offset"], 8)
                self.assertFalse(delta["reset"])
                with urlopen(url + "?offset=99", timeout=5) as response:
                    recovered = json.loads(response.read())
                self.assertEqual(recovered["text"], "abcdefgh")
                self.assertEqual(recovered["next_offset"], 8)
                self.assertTrue(recovered["reset"])


class StaticAssetCacheTests(unittest.TestCase):
    def test_chat_asset_uses_revalidatable_etag_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(StateCache(Path(directory))))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            self.addCleanup(thread.join, 2)
            port = server.server_address[1]

            connection = HTTPConnection("127.0.0.1", port, timeout=5)
            self.addCleanup(connection.close)
            connection.request("GET", "/research-chat/assets/research-chat.js")
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertGreater(len(body), 1000)
            etag = response.getheader("ETag")
            self.assertTrue(etag)
            self.assertEqual(response.getheader("Cache-Control"), "private, max-age=300, must-revalidate")

            connection.request("GET", "/research-chat/assets/research-chat.js", headers={"If-None-Match": etag})
            cached = connection.getresponse()
            self.assertEqual(cached.status, 304)
            self.assertEqual(cached.read(), b"")
            self.assertEqual(cached.getheader("ETag"), etag)


if __name__ == "__main__":
    unittest.main()
