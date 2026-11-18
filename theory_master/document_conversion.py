"""Opt-in PDF materialization shared by scientific and preparation Literature.

No installation, weight download or cloud upload is performed by discovery or
settings. Conversion requires an explicit enabled configuration on this host.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from typing import Any, Iterator, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import zipfile


class ConversionError(RuntimeError):
    """The component could not produce verifiable reading material."""


class ConversionDisabled(ConversionError):
    pass


class ConversionUnavailable(ConversionError):
    pass


class ConversionConfigurationError(ValueError):
    status = 400


class SettingsConflict(ConversionConfigurationError):
    status = 409


_DEFAULTS: dict[str, Any] = {
    "enabled": False, "provider": "marker", "marker_executable": "",
    "max_pages": 20, "timeout_s": 240, "extract_images": True,
    "online_base_url": "https://mineru.net", "mineru_model": "pipeline",
    "language": "ch", "revision": "0",
}
MAX_PDF_BYTES = 24 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
MAX_ARTIFACT_FILES = 4096


def config_path() -> Path:
    explicit = os.environ.get("SCIVER_DOCUMENT_CONVERSION_CONFIG", "").strip()
    if explicit:
        return Path(explicit).expanduser().absolute()
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "sciver" / "document-conversion.json"


def _read_settings() -> dict[str, Any]:
    path = config_path()
    if path.is_symlink():
        raise ConversionConfigurationError("PDF conversion configuration must not be a symbolic link")
    if not path.exists():
        return dict(_DEFAULTS)
    try:
        if path.stat().st_size > 65536:
            raise ValueError("oversized configuration")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("configuration must be an object")
        result = dict(_DEFAULTS)
        result.update(raw)
        _validate_settings(result)
        return result
    except (OSError, ValueError, TypeError) as exc:
        raise ConversionConfigurationError("PDF conversion configuration is invalid") from exc


def _api_key(settings: Mapping[str, Any]) -> str:
    return os.environ.get("MINERU_API_KEY", "").strip() or str(settings.get("api_key", "")).strip()


def _marker_executable(settings: Mapping[str, Any]) -> str:
    candidate = str(settings.get("marker_executable", "")).strip() or "marker_single"
    return shutil.which(candidate) or ""


def _public(settings: Mapping[str, Any]) -> dict[str, Any]:
    public = {key: settings.get(key, value) for key, value in _DEFAULTS.items()}
    key = _api_key(settings)
    public["api_key_configured"] = bool(key)
    public["api_key_source"] = "environment" if os.environ.get("MINERU_API_KEY", "").strip() else ("settings" if key else "")
    available = bool(_marker_executable(settings)) if settings["provider"] == "marker" else bool(key)
    public["available"] = available
    public["availability_reason"] = "" if available else ("Marker executable is unavailable on this server" if settings["provider"] == "marker" else "MinerU API key is not configured")
    public["config_path"] = str(config_path())
    return public


def load_settings() -> dict[str, Any]:
    """Safe settings projection; never starts a converter or exposes an API key."""
    return _public(_read_settings())


def _validate_settings(settings: Mapping[str, Any]) -> None:
    if type(settings.get("enabled")) is not bool or type(settings.get("extract_images")) is not bool:
        raise ConversionConfigurationError("enabled and extract_images must be booleans")
    if settings.get("provider") not in {"marker", "mineru"}:
        raise ConversionConfigurationError("PDF provider must be marker or mineru")
    for field, low, high in (("max_pages", 0, 600), ("timeout_s", 10, 3600)):
        if type(settings.get(field)) is not int or not low <= settings[field] <= high:
            raise ConversionConfigurationError(f"{field} must be an integer in {low}..{high}")
    for field in ("marker_executable", "online_base_url", "language", "mineru_model", "revision"):
        if not isinstance(settings.get(field), str) or len(settings[field]) > 4096 or "\x00" in settings[field]:
            raise ConversionConfigurationError(f"Invalid {field}")
    url = urlsplit(settings["online_base_url"])
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        raise ConversionConfigurationError("online_base_url must be an HTTPS origin without credentials or path")
    if settings["mineru_model"] not in {"pipeline", "vlm"}:
        raise ConversionConfigurationError("mineru_model must be pipeline or vlm")
    if not re.fullmatch(r"[A-Za-z_\-]{1,32}", settings["language"]):
        raise ConversionConfigurationError("Invalid document language")
    secret = settings.get("api_key", "")
    if not isinstance(secret, str) or len(secret) > 8192 or any(c in secret for c in "\r\n\x00"):
        raise ConversionConfigurationError("Invalid MinerU API key")


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def save_settings(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Compare-and-swap host settings. Empty API key preserves existing secret."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("expected_revision"), str):
        raise ConversionConfigurationError("expected_revision is required")
    allowed = set(_DEFAULTS) - {"revision"} | {"api_key", "clear_api_key", "expected_revision"}
    if set(payload) - allowed:
        raise ConversionConfigurationError("Unknown PDF conversion setting")
    path = config_path()
    with _file_lock(path.with_suffix(".lock")):
        settings = _read_settings()
        if payload["expected_revision"] != settings["revision"]:
            raise SettingsConflict("PDF conversion settings changed; reload before saving")
        if "clear_api_key" in payload and type(payload["clear_api_key"]) is not bool:
            raise ConversionConfigurationError("clear_api_key must be a boolean")
        for field in set(_DEFAULTS) - {"revision"}:
            if field in payload:
                settings[field] = payload[field]
        if "api_key" in payload and not isinstance(payload["api_key"], str):
            raise ConversionConfigurationError("api_key must be text")
        if payload.get("clear_api_key"):
            settings.pop("api_key", None)
        elif str(payload.get("api_key", "")).strip():
            settings["api_key"] = payload["api_key"].strip()
        settings["revision"] = uuid.uuid4().hex
        _validate_settings(settings)
        _atomic_json(path, settings)
    return _public(settings)


def _pdf_hash(pdf: Path) -> str:
    if not pdf.is_file() or pdf.is_symlink():
        raise ValueError("PDF input file is missing or is a symbolic link")
    if pdf.stat().st_size > MAX_PDF_BYTES:
        raise ValueError("PDF exceeds converter input size limit")
    with pdf.open("rb") as handle:
        if not handle.read(5).startswith(b"%PDF-"):
            raise ValueError("input is not a valid PDF header")
        handle.seek(0)
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _validate_tree(directory: Path) -> None:
    total, count = 0, 0
    for file in directory.rglob("*"):
        if file.is_symlink():
            raise ConversionError("Converter produced a symbolic link")
        if file.is_file():
            if not stat.S_ISREG(file.stat().st_mode):
                raise ConversionError("Converter produced an unsupported file")
            count += 1
            total += file.stat().st_size
            if count > MAX_ARTIFACT_FILES or total > MAX_EXTRACTED_BYTES:
                raise ConversionError("Converter artifacts exceed size limit")


def _materialize(staging: Path, output: Path, metadata: dict[str, Any]) -> Path:
    _validate_tree(staging)
    candidates = sorted(path for path in staging.rglob("*.md") if path.is_file() and path.stat().st_size)
    if not candidates:
        raise ConversionError("Converter produced no nonempty Markdown; PDF remains unverified")
    chosen = next((p for p in candidates if p.name == "full.md"), candidates[0])
    if not chosen.read_text(encoding="utf-8", errors="replace").strip():
        raise ConversionError("Converter output contains no readable text")
    # Preserve images and layout JSON alongside their original relative Markdown.
    artifact_name = "conversion-" + uuid.uuid4().hex
    retained = output / artifact_name
    shutil.copytree(staging, retained)
    relative = chosen.relative_to(staging)
    metadata.update({"markdown": str(Path(artifact_name) / relative), "artifact_directory": artifact_name,
                     "completed_at": datetime.now(timezone.utc).isoformat(),
                     "read_scope": "requested_pages" if metadata["max_pages"] else "all_available_pages",
                     "page_limit_applied": bool(metadata["max_pages"]),
                     "page_count_verified": False})
    _atomic_json(output / "conversion.json", metadata)
    # Stable compatibility path, with local image links rebased into retained artifacts.
    text = chosen.read_text(encoding="utf-8", errors="replace")
    prefix = (Path(artifact_name) / relative.parent).as_posix()
    text = re.sub(r"(!?\[[^\]]*\]\()([^\s)#][^)]*)(\))", lambda m: m[0] if urlsplit(m[2]).scheme or m[2].startswith(("/", "#")) else m[1] + prefix + "/" + m[2] + m[3], text)
    fd, temp = tempfile.mkstemp(prefix=".paper-", dir=output)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp, output / "paper.md")
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return output / "paper.md"


def _marker(pdf: Path, staging: Path, settings: Mapping[str, Any], pages: int, marker_bin: str) -> None:
    executable = _marker_executable({**settings, **({"marker_executable": marker_bin} if marker_bin else {})})
    if not executable:
        raise ConversionUnavailable("Marker converter unavailable on this server; configure its executable in Settings")
    command = [executable, str(pdf), "--output_format", "markdown", "--output_dir", str(staging), "--disable_tqdm"]
    if not settings["extract_images"]:
        command.append("--disable_image_extraction")
    if pages:
        command.extend(["--page_range", f"0-{pages - 1}"])
    try:
        proc = subprocess.run(command, capture_output=True, text=True, check=False, timeout=settings["timeout_s"])
    except subprocess.TimeoutExpired as exc:
        raise ConversionError("Marker conversion timed out; PDF remains unverified") from exc
    except OSError as exc:
        raise ConversionUnavailable("Marker executable could not start") from exc
    if proc.returncode:
        # Converter diagnostics may include source URLs; retain locally, do not expose tokens.
        (staging / "converter-error.log").write_text((proc.stderr or proc.stdout)[-8000:], encoding="utf-8")
        raise ConversionError("Marker conversion failed; PDF remains unverified")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ConversionError("Document conversion HTTP redirect refused")


def _safe_https(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ConversionError("Document conversion requires a public HTTPS URL")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ConversionError("Document conversion URL resolves to a private address")
    except (OSError, ValueError) as exc:
        raise ConversionError("Document conversion URL could not be validated") from exc


def _request(url: str, *, method: str = "GET", data: bytes | None = None,
             api_key: str = "", api_origin: str = "", timeout: float = 30,
             max_bytes: int = MAX_ARCHIVE_BYTES) -> bytes:
    _safe_https(url)
    headers = {"User-Agent": "Sciver-document-conversion/1"}
    if api_key:
        if urlsplit(url)[:2] != urlsplit(api_origin)[:2]:
            raise ConversionError("API authorization origin mismatch")
        headers.update({"Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ConversionError("Document conversion response exceeds size limit")
            return body
    except (HTTPError, URLError, OSError) as exc:
        # Never include URLs (signed upload/download credentials) or response bodies.
        raise ConversionError("Document conversion HTTP request failed") from exc


def _mineru_api(settings: Mapping[str, Any], endpoint: str, *, payload: dict | None = None, timeout: float = 30) -> dict:
    origin = settings["online_base_url"].rstrip("/")
    raw = _request(origin + endpoint, method="POST" if payload is not None else "GET",
                   data=json.dumps(payload).encode() if payload is not None else None,
                   api_key=_api_key(settings), api_origin=origin, timeout=timeout, max_bytes=1024 * 1024)
    try:
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("code") != 0 or not isinstance(result.get("data"), dict):
            raise ValueError("unsuccessful API response")
        return result["data"]
    except (ValueError, TypeError) as exc:
        raise ConversionError("MinerU rejected the request or returned invalid metadata") from exc


def _extract_zip(content: bytes, destination: Path) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARTIFACT_FILES or sum(i.file_size for i in infos) > MAX_EXTRACTED_BYTES:
                raise ConversionError("MinerU archive exceeds extraction limit")
            for info in infos:
                name = PurePosixPath(info.filename)
                mode = info.external_attr >> 16
                if name.is_absolute() or ".." in name.parts or "\\" in info.filename or "\x00" in info.filename or stat.S_ISLNK(mode):
                    raise ConversionError("MinerU archive contains an unsafe path")
                if info.flag_bits & 1 or (mode and stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                    raise ConversionError("MinerU archive contains unsupported entries")
                target = destination.joinpath(*name.parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        if isinstance(exc, ConversionError):
            raise
        raise ConversionError("MinerU result archive could not be extracted") from exc


def _mineru(pdf: Path, output: Path, staging: Path, settings: Mapping[str, Any], pages: int, digest: str) -> dict[str, Any]:
    if not _api_key(settings):
        raise ConversionUnavailable("MinerU API key is not configured in Settings or MINERU_API_KEY")
    origin = settings["online_base_url"].rstrip("/")
    fingerprint = hashlib.sha256(json.dumps({"source": digest, "origin": origin, "pages": pages,
        "model": settings["mineru_model"], "language": settings["language"]}, sort_keys=True).encode()).hexdigest()
    state_path = output / ("mineru-job-" + fingerprint[:24] + ".json")
    deadline = time.monotonic() + settings["timeout_s"]
    def remaining() -> float:
        value = deadline - time.monotonic()
        if value <= 0:
            raise ConversionError("MinerU conversion timed out; existing remote task is retained for retry")
        return min(value, 60)
    # Serializes the upload task on this exact PDF/output, so retries resume it.
    with _file_lock(state_path.with_suffix(".lock")):
        job: dict[str, Any] = {}
        if state_path.exists():
            try:
                job = json.loads(state_path.read_text(encoding="utf-8"))
                if job.get("fingerprint") != fingerprint:
                    raise ValueError("wrong job fingerprint")
            except (OSError, ValueError, TypeError) as exc:
                raise ConversionError("MinerU saved job is invalid; refusing duplicate submission") from exc
        if not job:
            # Record intent before the POST; an ambiguous network failure is never automatically billed again.
            job = {"fingerprint": fingerprint, "source_sha256": digest, "state": "submission_started"}
            _atomic_json(state_path, job)
            file: dict[str, Any] = {"name": pdf.name, "data_id": digest}
            if pages:
                file["page_ranges"] = f"1-{pages}"
            data = _mineru_api(settings, "/api/v4/file-urls/batch", payload={"files": [file],
                "model_version": settings["mineru_model"], "language": settings["language"],
                "enable_formula": True, "enable_table": True}, timeout=remaining())
            batch_id, urls = data.get("batch_id"), data.get("file_urls")
            if not isinstance(batch_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", batch_id) or not isinstance(urls, list) or len(urls) != 1 or not isinstance(urls[0], str):
                raise ConversionError("MinerU upload metadata is invalid; refusing duplicate submission")
            job.update({"batch_id": batch_id, "upload_url": urls[0], "state": "upload_pending"})
            _atomic_json(state_path, job)
        if not job.get("batch_id"):
            raise ConversionError("MinerU submission result is uncertain; inspect saved job before submitting again")
        if job["state"] == "upload_pending":
            _request(job["upload_url"], method="PUT", data=pdf.read_bytes(), timeout=remaining(), max_bytes=1024 * 1024)
            job.update({"state": "submitted"})
            job.pop("upload_url", None)
            _atomic_json(state_path, job)
        endpoint = "/api/v4/extract-results/batch/" + quote(job["batch_id"], safe="")
        while True:
            data = _mineru_api(settings, endpoint, timeout=remaining())
            rows = data.get("extract_result")
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise ConversionError("MinerU result metadata is invalid")
            row = rows[0]
            if row.get("data_id") and row["data_id"] != digest:
                raise ConversionError("MinerU result does not match the source document")
            job["state"] = str(row.get("state", "unknown"))
            _atomic_json(state_path, job)
            if job["state"] == "failed":
                raise ConversionError("MinerU conversion failed; remote task retained for inspection")
            if job["state"] == "done":
                url = row.get("full_zip_url")
                if not isinstance(url, str):
                    raise ConversionError("MinerU returned no result archive")
                content = _request(url, timeout=remaining())
                _extract_zip(content, staging)
                return {"remote_batch_id": job["batch_id"], "remote_job_file": state_path.name,
                        "remote_api_origin": origin, "mineru_model": settings["mineru_model"]}
            if job["state"] not in {"waiting-file", "pending", "running", "converting"}:
                raise ConversionError("MinerU returned an unknown task state")
            time.sleep(min(2, remaining()))


def convert_pdf_to_markdown(pdf: Path, output: Path, *, max_pages: int | None = None, marker_bin: str = "") -> Path:
    settings = _read_settings()
    if not settings["enabled"]:
        raise ConversionDisabled("PDF conversion is disabled; enable a configured provider in Settings")
    pages = settings["max_pages"] if max_pages is None else max_pages
    if type(pages) is not int or not 0 <= pages <= 600:
        raise ValueError("max_pages must be in 0..600; 0 reads all available pages")
    pdf, output = Path(pdf).absolute(), Path(output).absolute()
    digest = _pdf_hash(pdf)
    if output.is_symlink():
        raise ValueError("Conversion output directory must not be a symbolic link")
    output.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, Any] = {"provider": settings["provider"], "source_sha256": digest,
        "source_pdf": str(pdf), "max_pages": pages, "extract_images": settings["extract_images"],
        "settings_revision": settings["revision"], "scientific_status": "materialized_unverified"}
    with _file_lock(output / ".conversion.lock"):
        with tempfile.TemporaryDirectory(prefix="conversion-run-", dir=output) as tmp:
            staging = Path(tmp)
            if settings["provider"] == "marker":
                _marker(pdf, staging, settings, pages, marker_bin)
            else:
                metadata.update(_mineru(pdf, output, staging, settings, pages, digest))
            if _pdf_hash(pdf) != digest:
                raise ConversionError("PDF changed during conversion; generated material rejected")
            return _materialize(staging, output, metadata)
