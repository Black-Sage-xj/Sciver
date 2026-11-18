"""File APIs use registered task roots and never regenerate notebook Markdown."""
import hashlib
import io
import json
import os
import threading
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from theory_master.notebook import graph_database_path
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.task import bootstrap
from tm_observer.canvas_md import ExplorerError, build_index, vault_root
from tm_observer.vault_files import local_file_actions, vault_get, vault_post
from tm_observer.web import ThreadingHTTPServer, make_handler

PLAN = "# Goal\n\nUnderstand a claim.\n\n# Research Contract\n\nUse evidence.\n\n# Final Gate\n\nResolve the claim.\n\n# Required Context\n\nLocal notes.\n"


@pytest.fixture
def notebook(tmp_path):
    root = tmp_path / "notebook"
    root.mkdir()
    plan = root / "research_plan.md"
    plan.write_text(PLAN)
    binding = bootstrap(plan, allocation_root=tmp_path)
    ident = hashlib.sha256(str(binding.research_root).encode()).hexdigest()[:16]
    state = {"runs": [{"observer_id": ident, "task_root": str(binding.research_root),
                        "research_root": str(binding.research_root), "run_id": binding.master_id}]}
    note = root / "notes" / "hypotheses" / "H-0001.md"
    note.parent.mkdir(parents=True)
    note.write_text("# 人工笔记\n\n原始正文。\n")
    with ScienceGraphStore(graph_database_path(root)) as store:
        store.add_hypothesis("H-0001", dossier_ref="notes/hypotheses/H-0001.md")
    return root, binding, ident, state, tmp_path


def route(ident, action):
    return f"/api/canvas-md/tasks/{ident}/{action}"


def test_metadata_and_file_cards_use_the_registered_notebook(notebook):
    root, binding, ident, state, search = notebook
    before = {p: p.read_bytes() for p in root.rglob("*.md")}
    body, _, name = vault_get(route(ident, "vault"), "", search, state)
    metadata = json.loads(body)
    assert metadata["vault_root"] == str(root)
    assert metadata["graph_database"] == str(binding.research_root / "science_graph.sqlite3")
    assert metadata["canvas_relative_path"] == "exploration.canvas"
    assert metadata["local_actions_available"] is False
    assert metadata["move_available"] is True
    assert name is None
    content, mime, filename = vault_get(route(ident, "canvas-download"), "branch=H-0001", search, state)
    assert filename == "branch-H-0001.canvas"
    assert json.loads(content)["nodes"][0]["file"] == "notes/hypotheses/H-0001.md"
    assert {p: p.read_bytes() for p in root.rglob("*.md")} == before
    assert build_index(root)["stats"]["node_count"] == 1


def test_zip_and_document_download_are_portable_and_exclude_private_files(notebook):
    root, _, ident, state, search = notebook
    (root / "assets").mkdir()
    (root / "assets" / "figure.svg").write_text("<svg/>")
    (root / "assets" / "private.json").write_text("private")
    (root / "Projects").mkdir()
    (root / "Projects" / "Reading.md").write_text("# User subdirectory")
    (root / "Reading.md").write_text("# User root note")
    outside = search / "private.md"
    outside.write_text("secret")
    (root / "notes" / "escape.md").symlink_to(outside)
    data, mime, name = vault_get(route(ident, "vault-download"), "", search, state)
    assert mime == "application/zip" and name.endswith(".zip")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "exploration.canvas" in archive.namelist()
        assert "assets/figure.svg" in archive.namelist()
        assert "notes/hypotheses/H-0001.md" in archive.namelist()
        assert {"Projects/Reading.md", "Reading.md"}.issubset(archive.namelist())
        assert not any("private" in n or "escape" in n or "sqlite" in n or n.startswith("/") for n in archive.namelist())
    body, _, filename = vault_get(route(ident, "document-download"), "path=notes/hypotheses/H-0001.md", search, state)
    assert body == (root / "notes/hypotheses/H-0001.md").read_bytes()
    assert filename == "H-0001.md"
    body, _, _ = vault_get(route(ident, "document-download"), "path=Projects/Reading.md", search, state)
    assert body == b"# User subdirectory"
    for ref in ("../private.md", "notes/escape.md"):
        with pytest.raises(ExplorerError):
            vault_get(route(ident, "document-download"), "path=" + ref, search, state)


def test_branch_creation_and_move_update_the_same_task(notebook):
    root, binding, ident, state, search = notebook
    created = vault_post(route(ident, "vault-action"), {"action": "create_branch", "branch": "H-0001"}, search, state)
    assert created["canvas_relative_path"] == "branch-H-0001.canvas"
    target = search / "other notebook"
    moved = vault_post(route(ident, "vault-action"), {"action": "move", "destination": str(target)}, search, state)
    assert moved["moved"] is True
    assert vault_root(search, ident, state) == target
    assert graph_database_path(target) == binding.research_root / "science_graph.sqlite3"
    assert build_index(target)["stats"]["node_count"] == 1
    assert (target / "notes/hypotheses/H-0001.md").read_text().startswith("# 人工笔记")


def test_remote_folder_action_is_rejected_and_loopback_proxy_is_not_local(notebook):
    _, _, ident, state, search = notebook
    with pytest.raises(ExplorerError) as error:
        vault_post(route(ident, "vault-action"), {"action": "open_folder"}, search, state, local=False)
    assert error.value.status == 409
    with patch("tm_observer.vault_files.sys.platform", "darwin"):
        assert local_file_actions(host="localhost:8787", client="127.0.0.1")
        assert not local_file_actions(host="remote.example", client="127.0.0.1")
        assert not local_file_actions(host="localhost:8787", client="127.0.0.1", forwarded=True)


def test_http_download_headers_and_post_intent_gate(notebook):
    _, _, ident, state, search = notebook
    class Cache:
        search_root = search
        _at = 1
        def get(self):
            return state
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Cache()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(url + route(ident, "vault-download")) as response:
            assert response.headers["Content-Type"] == "application/zip"
            assert "attachment" in response.headers["Content-Disposition"]
            assert response.headers["Cache-Control"] == "no-store"
        body = json.dumps({"action": "refresh_canvas"}).encode()
        request = Request(url + route(ident, "vault-action"), data=body,
                          headers={"Content-Type": "application/json"})
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert error.value.code == 403
        request.add_header("Origin", url)
        request.add_header("X-TM-Operator-Intent", "1")
        with urlopen(request) as response:
            assert json.load(response)["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.skipif(os.environ.get("TM_BROWSER_E2E") != "1", reason="opt-in Chromium check")
def test_vault_actions_in_chromium(notebook, tmp_path):
    from playwright.sync_api import sync_playwright, expect
    from tm_observer.vault_ui import VAULT_CSS, VAULT_HTML, VAULT_SCRIPT
    root, _, ident, state, search = notebook
    class Cache:
        search_root = search
        _at = 1
        def get(self):
            return state
    base = make_handler(Cache())
    page_html = ("<!doctype html><html><head><meta charset='utf-8'>" + VAULT_CSS
        + "</head><body><button id='ex-vault-open'>笔记库</button><section id='ex-reader'></section>"
        + VAULT_HTML + "<script>" + VAULT_SCRIPT
        + "const vaultUi=SciverVaultUI.create({getTask:()=>" + json.dumps(ident)
        + ",getBranch:()=>'H-0001',getHypotheses:()=>[{id:'H-0001',label:'Local hypothesis'}]});"
        + "vaultUi.warm();document.getElementById('ex-reader').innerHTML=vaultUi.documentTools('notes/hypotheses/H-0001.md');"
        + "</script></body></html>")
    class Handler(base):
        def do_GET(self):
            if self.path == "/browser-test":
                self._send(page_html.encode(), "text/html; charset=utf-8")
            else:
                super().do_GET()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1200, "height": 1000}, accept_downloads=True)
            page.add_init_script("window.showSaveFilePicker=undefined")
            page.goto(f"http://127.0.0.1:{server.server_port}/browser-test")
            page.locator("#ex-vault-open").click()
            expect(page.locator("#ex-vault-path")).to_have_text(str(root))
            expect(page.locator("#ex-vault-canvas")).to_have_text(str(root / "exploration.canvas"))
            page.locator("#ex-vault-create-branch").click()
            expect(page.locator("#ex-vault-status")).to_contain_text("branch-H-0001.canvas")
            assert (root / "branch-H-0001.canvas").is_file()
            with page.expect_download() as result:
                page.locator("#ex-vault-download").click()
            downloaded = result.value
            downloaded.save_as(tmp_path / "notebook.zip")
            with zipfile.ZipFile(tmp_path / "notebook.zip") as archive:
                assert "notes/hypotheses/H-0001.md" in archive.namelist()
            page.locator("#ex-vault-location").evaluate("e=>e.open=true")
            target = search / "browser chosen notebook"
            page.locator("#ex-vault-destination").fill(str(target))
            page.locator("#ex-vault-move").click()
            expect(page.locator("#ex-vault-path")).to_have_text(str(target))
            expect(page.locator("#ex-vault-status")).to_contain_text("笔记库已移动")
            page.locator("#ex-vault form button").click()
            note = target / "notes/hypotheses/H-0001.md"
            note.write_text("# Edited in Obsidian\nSame unique file.\n")
            with page.expect_download() as result:
                page.locator('[data-vault-document-action="save"]').click()
            result.value.save_as(tmp_path / "document.md")
            assert (tmp_path / "document.md").read_bytes() == note.read_bytes()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
