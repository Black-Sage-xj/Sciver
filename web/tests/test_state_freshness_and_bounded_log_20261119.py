"""Offline freshness, HTTP disclosure and bounded historical reads."""
from __future__ import annotations

import json
import threading
import tracemalloc
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import Mock

import pytest

from tm_observer.bounded_log import tail_lines
from tm_observer.collector import _event_tail
from tm_observer.state_cache import StateCache
from tm_observer.web import make_handler


def snapshot(root, generation):
    return {"schema": "tm-observer/state/v1", "generated_at": str(generation),
            "search_root": str(root), "system": {"task_count": 0}, "runs": []}


def test_failed_refresh_discloses_old_snapshot_without_mutating_it_and_recovers(tmp_path):
    source = Mock(side_effect=[snapshot(tmp_path, 1), OSError("synthetic"), snapshot(tmp_path, 2)])
    cache = StateCache(tmp_path, ttl_s=1, state_collector=source, git_collector=lambda _: {})
    cache._refresh_state()
    original = cache._state
    first = cache.get()
    assert first["refresh_status"] == "fresh" and first["last_success_at"]
    cache._refresh_state()
    stale = cache.get()
    assert stale["refresh_status"] == "stale" and stale["refresh_error"] == "OSError"
    assert stale["generated_at"] == "1" and stale["last_success_at"] == first["last_success_at"]
    assert "refresh_status" not in original
    cache._refresh_state()
    fresh = cache.get()
    assert fresh["refresh_status"] == "fresh" and fresh["refresh_error"] is None
    assert fresh["generated_at"] == "2" and source.call_count == 3


def test_git_failure_does_not_mislabel_valid_task_collection(tmp_path):
    cache = StateCache(tmp_path, state_collector=lambda *a, **k: snapshot(tmp_path, 1),
                       git_collector=Mock(side_effect=OSError("git unavailable")))
    cache._refresh_state()
    cache._refresh_git()
    value = cache.get()
    assert value["refresh_status"] == "fresh" and value["refresh_error"] is None


def test_publication_failure_releases_single_flight_and_preserves_last_good_state(tmp_path):
    malformed = snapshot(tmp_path, 2)
    malformed["runs"] = [None]
    source = Mock(side_effect=[snapshot(tmp_path, 1), malformed, snapshot(tmp_path, 3)])
    cache = StateCache(tmp_path, state_collector=source, git_collector=lambda _: {})
    cache._refresh_state()
    cache._refreshing = True
    cache._refresh_state()
    assert cache._refreshing is False
    assert cache.get()["generated_at"] == "1"
    assert cache.get()["refresh_error"] == "AttributeError"
    cache._refresh_state()
    assert cache.get()["generated_at"] == "3"
    assert cache.get()["refresh_error"] is None


@pytest.mark.parametrize("invalid", [None, [], "bad"])
def test_invalid_git_collection_releases_refresh_slot(tmp_path, invalid):
    cache = StateCache(tmp_path, git_collector=lambda _: invalid)
    cache._git_refreshing = True
    cache._refresh_git()
    assert cache._git_refreshing is False
    assert cache._git_last_error == "ValueError"


def test_http_exposes_refresh_failure_on_state_page_and_health(tmp_path):
    source = Mock(side_effect=[snapshot(tmp_path, 1), OSError("private synthetic detail")])
    cache = StateCache(tmp_path, state_collector=source, git_collector=lambda _: {})
    cache._refresh_state()
    cache._refresh_state()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/observer_state.json", timeout=3) as response:
            value = json.load(response)
            assert response.headers["X-Sciver-State-Freshness"] == "stale"
            assert response.headers["X-Sciver-State-Refresh-Failed"] == "1"
            assert value["refresh_error"] == "OSError"
            assert "private synthetic detail" not in str(value)
        with pytest.raises(HTTPError) as failure:
            urlopen(base + "/healthz", timeout=3)
        assert failure.value.code == 503
        assert json.load(failure.value)["ok"] is False
        with urlopen(base + "/run/", timeout=3) as response:
            page = response.read().decode()
            assert 'data-state-stale role="status"' in page
            assert "尚未确认最新运行状态" in page
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_event_tail_memory_does_not_scale_with_history(tmp_path):
    path = tmp_path / "events.jsonl"
    row = json.dumps({"event": "TOOL_ACTIVITY", "detail": "x" * 1000}) + "\n"
    with path.open("w") as stream:
        for _ in range(8000):
            stream.write(row)
    tracemalloc.start()
    try:
        [last] = _event_tail(path, limit=1)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert last["event"] == "TOOL_ACTIVITY"
    assert path.stat().st_size > 8_000_000 and peak < 2_000_000
    assert _event_tail(path, limit=0) == []


def test_tail_discards_partial_first_line_and_preserves_complete_unicode(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("prefix-that-must-be-discarded\n最后一行\n", encoding="utf-8")
    suffix = "最后一行\n".encode()
    assert tail_lines(path, max_bytes=len(suffix)) == ["最后一行"]
    assert tail_lines(path, max_bytes=len(suffix) + 3) == ["最后一行"]
    assert tail_lines(path, max_bytes=3) == []
    assert tail_lines(path, limit=0) == []
    link = tmp_path / "linked"
    link.symlink_to(path)
    assert tail_lines(link) == []


def test_oversized_single_record_does_not_escape_the_read_bound(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"event": "x" * 500_000}))
    assert _event_tail(path) == []


def test_console_configuration_cannot_create_extra_script_elements():
    from bs4 import BeautifulSoup
    from tm_observer.run_console_client import script
    hostile = "</script><script>alert(1)</script>&"
    html = script(hostile, selected_worker=hostile, selected_history_cycle=hostile,
                  secondary=hostile, pinned_worker_account=None,
                  quota_snapshot={"detail": hostile}, hot_role_route=False)
    document = BeautifulSoup(html, "html.parser")
    assert len(document.select("script")) == 2
    config = json.loads(document.select_one("#tm-run-config").string)
    assert config["observerId"] == hostile
    assert config["selectedWorker"] == hostile
    assert config["quotaSnapshot"]["detail"] == hostile
