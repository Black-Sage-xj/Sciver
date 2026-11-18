"""No network/model/Marker dependency: exercise component privacy and provider contracts."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import zipfile

import pytest

from theory_master import document_conversion as dc


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("SCIVER_DOCUMENT_CONVERSION_CONFIG", str(tmp_path / "settings" / "conversion.json"))
    monkeypatch.delenv("MINERU_API_KEY", raising=False)


def enable(**fields):
    return dc.save_settings({"expected_revision": dc.load_settings()["revision"], "enabled": True, **fields})


def pdf_file(tmp_path):
    path = tmp_path / "original.pdf"
    path.write_bytes(b"%PDF-1.4\nfixture")
    return path


def archive(**files):
    handle = io.BytesIO()
    with zipfile.ZipFile(handle, "w") as output:
        for name, text in files.items():
            output.writestr(name, text)
    return handle.getvalue()


def test_default_off_does_not_discover_or_execute_at_conversion(monkeypatch, tmp_path):
    monkeypatch.setattr(dc.subprocess, "run", lambda *a, **k: pytest.fail("must not execute"))
    monkeypatch.setattr(dc, "_request", lambda *a, **k: pytest.fail("must not upload"))
    assert dc.load_settings()["enabled"] is False
    with pytest.raises(dc.ConversionDisabled, match="disabled"):
        dc.convert_pdf_to_markdown(tmp_path / "missing.pdf", tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not dc.config_path().exists()


def test_settings_cas_secret_preservation_clear_and_permissions(monkeypatch):
    initial = dc.load_settings()
    with pytest.raises(dc.ConversionConfigurationError, match="expected_revision"):
        dc.save_settings({"enabled": True})
    saved = dc.save_settings({"expected_revision": initial["revision"], "provider": "mineru", "api_key": "private-test-key"})
    assert "api_key" not in saved
    assert saved["api_key_configured"] and saved["api_key_source"] == "settings"
    assert dc.config_path().stat().st_mode & 0o777 == 0o600
    with pytest.raises(dc.SettingsConflict):
        dc.save_settings({"expected_revision": initial["revision"], "enabled": True})
    kept = dc.save_settings({"expected_revision": saved["revision"], "api_key": "", "max_pages": 0})
    assert kept["api_key_configured"] and kept["max_pages"] == 0
    cleared = dc.save_settings({"expected_revision": kept["revision"], "clear_api_key": True})
    assert not cleared["api_key_configured"]
    monkeypatch.setenv("MINERU_API_KEY", "environment-key")
    public = dc.load_settings()
    assert public["api_key_source"] == "environment" and public["available"]
    assert "environment-key" not in json.dumps(public)
    assert "environment-key" not in dc.config_path().read_text()


@pytest.mark.parametrize("fields", [{"provider": "manus"}, {"enabled": "yes"}, {"max_pages": -1}, {"max_pages": 601}, {"timeout_s": 0}, {"extract_images": "false"}, {"api_key": "bad\nkey"}, {"online_base_url": "http://example.org"}, {"online_base_url": "https://user:pass@example.org"}, {"online_base_url": "https://example.org/api"}])
def test_invalid_configuration_is_not_saved(fields):
    with pytest.raises(dc.ConversionConfigurationError):
        enable(**fields)
    assert not dc.config_path().exists()


def test_marker_preserves_images_layout_and_source_metadata(monkeypatch, tmp_path):
    marker = tmp_path / "marker_single"
    marker.write_text("stub")
    marker.chmod(0o700)
    enable(marker_executable=str(marker), max_pages=3)
    def fake_run(command, **kwargs):
        assert command[0] == str(marker)
        assert command[command.index("--page_range") + 1] == "0-2"
        assert "--disable_image_extraction" not in command
        assert kwargs["timeout"] == 240 and "shell" not in kwargs
        root = Path(command[command.index("--output_dir") + 1]) / "paper"
        root.mkdir()
        (root / "full.md").write_text("A formula $x^2$.\n![Figure](image.png)")
        (root / "image.png").write_bytes(b"image")
        (root / "layout.json").write_text('{"page": 1}')
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    result = dc.convert_pdf_to_markdown(pdf_file(tmp_path), tmp_path / "out")
    metadata = json.loads((result.parent / "conversion.json").read_text())
    assert metadata["max_pages"] == 3 and metadata["page_limit_applied"]
    assert metadata["scientific_status"] == "materialized_unverified"
    assert metadata["source_sha256"] == dc._pdf_hash(tmp_path / "original.pdf")
    retained = result.parent / metadata["artifact_directory"] / "paper"
    assert (retained / "image.png").read_bytes() == b"image"
    assert (retained / "layout.json").exists()
    assert metadata["artifact_directory"] in result.read_text()
    assert "source" not in dc.load_settings()


def test_missing_marker_never_uses_cloud(monkeypatch, tmp_path):
    enable(marker_executable="missing_marker_forever")
    monkeypatch.setattr(dc, "_request", lambda *a, **k: pytest.fail("no implicit fallback"))
    with pytest.raises(dc.ConversionUnavailable, match="Marker"):
        dc.convert_pdf_to_markdown(pdf_file(tmp_path), tmp_path / "out")


def test_stale_markdown_is_never_reused(monkeypatch, tmp_path):
    marker = tmp_path / "marker"
    marker.write_text("stub")
    marker.chmod(0o700)
    enable(marker_executable=str(marker))
    target = tmp_path / "out"
    target.mkdir()
    (target / "paper.md").write_text("stale")
    monkeypatch.setattr(dc.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))
    with pytest.raises(dc.ConversionError, match="nonempty Markdown"):
        dc.convert_pdf_to_markdown(pdf_file(tmp_path), target)
    assert (target / "paper.md").read_text() == "stale"


def test_marker_timeout(monkeypatch, tmp_path):
    marker = tmp_path / "marker"
    marker.write_text("stub")
    marker.chmod(0o700)
    enable(marker_executable=str(marker))
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(a[0], k["timeout"])
    monkeypatch.setattr(dc.subprocess, "run", timeout)
    with pytest.raises(dc.ConversionError, match="timed out"):
        dc.convert_pdf_to_markdown(pdf_file(tmp_path), tmp_path / "out")


def test_mineru_uses_upload_poll_download_and_resumes_same_batch(monkeypatch, tmp_path):
    enable(provider="mineru", api_key="secret", max_pages=7)
    calls = []
    result_zip = archive(**{"full.md": "A paper ![plot](images/1.png)", "images/1.png": "image", "layout.json": "{}"})
    def request(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/file-urls/batch"):
            assert kwargs["api_key"] == "secret"
            body = json.loads(kwargs["data"])
            assert body["files"][0]["page_ranges"] == "1-7"
            return json.dumps({"code": 0, "data": {"batch_id": "b-1", "file_urls": ["https://upload.example.org/signed?secret=x"]}}).encode()
        if "upload.example.org" in url:
            assert "api_key" not in kwargs and kwargs["method"] == "PUT"
            assert kwargs["data"].startswith(b"%PDF-")
            return b""
        if "/extract-results/batch/" in url:
            assert kwargs["api_key"] == "secret"
            return json.dumps({"code": 0, "data": {"extract_result": [{"state": "done", "full_zip_url": "https://cdn.example.org/result.zip"}]}}).encode()
        assert "api_key" not in kwargs
        return result_zip
    monkeypatch.setattr(dc, "_request", request)
    pdf = pdf_file(tmp_path)
    result = dc.convert_pdf_to_markdown(pdf, tmp_path / "out")
    assert "A paper" in result.read_text()
    metadata = json.loads((result.parent / "conversion.json").read_text())
    assert metadata["remote_batch_id"] == "b-1"
    dc.convert_pdf_to_markdown(pdf, tmp_path / "out")
    assert sum(url.endswith("/file-urls/batch") for url, _ in calls) == 1
    assert sum("upload.example.org" in url for url, _ in calls) == 1
    state = next(result.parent.glob("mineru-job-*.json"))
    assert state.stat().st_mode & 0o777 == 0o600
    assert "upload_url" not in state.read_text()
    assert "secret" not in (result.parent / "conversion.json").read_text()


def test_mineru_missing_key_does_not_upload(monkeypatch, tmp_path):
    enable(provider="mineru")
    monkeypatch.setattr(dc, "_request", lambda *a, **k: pytest.fail("no key no upload"))
    with pytest.raises(dc.ConversionUnavailable, match="API key"):
        dc.convert_pdf_to_markdown(pdf_file(tmp_path), tmp_path / "out")


def test_mineru_uncertain_submission_is_not_repeated(monkeypatch, tmp_path):
    enable(provider="mineru", api_key="secret")
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise dc.ConversionError("network failure")
    monkeypatch.setattr(dc, "_request", fail)
    pdf = pdf_file(tmp_path)
    with pytest.raises(dc.ConversionError, match="network"):
        dc.convert_pdf_to_markdown(pdf, tmp_path / "out")
    with pytest.raises(dc.ConversionError, match="uncertain"):
        dc.convert_pdf_to_markdown(pdf, tmp_path / "out")
    assert len(calls) == 1


def test_mineru_poll_failure_reuses_remote_job(monkeypatch, tmp_path):
    enable(provider="mineru", api_key="secret")
    created = []
    def request(url, **kwargs):
        if url.endswith("file-urls/batch"):
            created.append(url)
            return b'{"code":0,"data":{"batch_id":"existing","file_urls":["https://upload.example.org/a"]}}'
        if "upload.example.org" in url:
            return b""
        raise dc.ConversionError("poll network failed")
    monkeypatch.setattr(dc, "_request", request)
    pdf = pdf_file(tmp_path)
    for _ in range(2):
        with pytest.raises(dc.ConversionError, match="poll network"):
            dc.convert_pdf_to_markdown(pdf, tmp_path / "out")
    assert len(created) == 1


@pytest.mark.parametrize("path", ["../escape.md", "/absolute.md", "dir/../../escape.md", "dir\\escape.md"])
def test_mineru_zip_rejects_path_traversal(tmp_path, path):
    with pytest.raises(dc.ConversionError, match="unsafe path"):
        dc._extract_zip(archive(**{path: "payload"}), tmp_path)


def test_zip_rejects_symlinks_and_expansion_limit(monkeypatch, tmp_path):
    handle = io.BytesIO()
    with zipfile.ZipFile(handle, "w") as output:
        link = zipfile.ZipInfo("link")
        link.external_attr = (0o120777 << 16)
        output.writestr(link, "../outside")
    with pytest.raises(dc.ConversionError, match="unsafe path"):
        dc._extract_zip(handle.getvalue(), tmp_path)
    monkeypatch.setattr(dc, "MAX_EXTRACTED_BYTES", 1)
    with pytest.raises(dc.ConversionError, match="extraction limit"):
        dc._extract_zip(archive(**{"full.md": "long"}), tmp_path)


def test_http_never_sends_token_cross_origin(monkeypatch):
    monkeypatch.setattr(dc, "_safe_https", lambda url: None)
    with pytest.raises(dc.ConversionError, match="origin mismatch"):
        dc._request("https://elsewhere.example/", api_key="secret", api_origin="https://mineru.net")
    with pytest.raises(dc.ConversionError, match="redirect"):
        dc._NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://elsewhere.example/")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://example.org", "https://user:pass@example.org", "https://127.0.0.1/", "https://[::1]/"])
def test_http_rejects_unsafe_urls(url):
    with pytest.raises(dc.ConversionError):
        dc._safe_https(url)


def test_reader_keeps_image_links_and_page_scope_in_literature_navigation(monkeypatch, tmp_path):
    """Follow the public reader→converter→corpus path with a local converter stub."""
    import csv
    import importlib.util
    import re
    import sys
    from theory_master.literature_service import _catalog
    from theory_master.skills import skills_root

    scripts = skills_root() / "tm-scholar" / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("sciver_reader_image_scope_fixture", scripts / "read_papers.py")
    reader = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(reader)
    marker = tmp_path / "marker_single"
    marker.write_text("stub")
    marker.chmod(0o700)
    enable(marker_executable=str(marker), max_pages=2, extract_images=True)

    def converted(command, **kwargs):
        assert command[command.index("--page_range") + 1] == "0-1"
        destination = Path(command[command.index("--output_dir") + 1])
        (destination / "full.md").write_text("# Image and page-scope fixture\n\nDOI: 10.1234/sciver-images\n\n![Measured curve](curve.png)\nOnly the configured pages.")
        (destination / "curve.png").write_bytes(b"retained-image")
        (destination / "layout.json").write_text('{"pages": [1, 2]}')
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(dc.subprocess, "run", converted)
    monkeypatch.setattr(reader, "_download", lambda *a, **k: (b"%PDF-1.4\nfixture", "application/pdf"))
    root = tmp_path / "literature-job"
    retrieval = root / "retrieval"
    retrieval.mkdir(parents=True)
    papers = retrieval / "papers.json"
    papers.write_text(json.dumps([{"title": "Image and page-scope fixture", "doi": "10.1234/sciver-images",
                                  "is_open_access": True, "open_access_url": "https://example.org/article.pdf"}]))
    reading = root / "reading"
    monkeypatch.setattr(sys, "argv", ["read_papers.py", "--papers-json", str(papers), "--output-dir", str(reading)])
    assert reader.main() == 0
    with (reading / "pdf_manifest.csv").open(newline="") as handle:
        record = next(csv.DictReader(handle))
    assert record["status"] == "read" and record["max_pages"] == "2"
    converted_md = Path(record["converted_markdown_path"])
    provenance = json.loads(Path(record["conversion_manifest"]).read_text())
    assert provenance["max_pages"] == 2 and provenance["page_limit_applied"] is True
    assert Path(provenance["source_pdf"]).read_bytes().startswith(b"%PDF-")
    assert provenance["scientific_status"] == "materialized_unverified"
    for markdown in (Path(record["text_path"]), converted_md):
        image = re.search(r"!\[[^\]]*\]\(([^)]+)\)", markdown.read_text())
        assert image is not None
        assert (markdown.parent / image[1]).read_bytes() == b"retained-image"
    _, corpus, sources = _catalog(root, [{"papers_json": str(papers), "reading": {"pdf_manifest": str(reading / "pdf_manifest.csv")}}])
    index = Path(corpus["evidence_index_md"]).read_text()
    assert "2" in index and "page" in index.lower()
    assert record["conversion_manifest"] in index and record["converted_markdown_path"] in index
    assert sources[0]["claim_verification"] == "not_reviewed"
    assert sources[0]["materialization"]["record"]["max_pages"] == "2"
