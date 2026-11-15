"""Offline release-gate tests; never call a model or publish a repository."""
from __future__ import annotations

import io
import subprocess
import tarfile
import zipfile
from pathlib import Path

from scripts.public_release_guard import inspect_public_artifact, inspect_git_ref, inspect_git_history


def test_wheel_fails_on_private_provider_content(tmp_path: Path) -> None:
    artifact = tmp_path / "package.whl"
    with zipfile.ZipFile(artifact, "w") as wheel:
        wheel.writestr("theory_master/provider.py", 'MODEL = "DO_NOT_" + "PUBLISH"\n')
        wheel.writestr("theory_master/model_profiles/private-profile.toml", "name = 'private'\n")
    findings = inspect_public_artifact(artifact)
    assert any("private-profile.toml" in item for item in findings)


def test_clean_generic_wheel_passes(tmp_path: Path) -> None:
    artifact = tmp_path / "package.whl"
    with zipfile.ZipFile(artifact, "w") as wheel:
        wheel.writestr("theory_master/provider_extensions.py", "PROVIDERS = {}\n")
    assert inspect_public_artifact(artifact) == []


def test_sdist_private_marker_is_detected(tmp_path: Path) -> None:
    artifact = tmp_path / "package.tar.gz"
    payload = ("# private vendor: " + "DO_NOT_" + "PUBLISH\n").encode()
    with tarfile.open(artifact, "w:gz") as archive:
        entry = tarfile.TarInfo("package/theory_master/backend.py")
        entry.size = len(payload)
        archive.addfile(entry, io.BytesIO(payload))
    assert any("backend.py" in item for item in inspect_public_artifact(artifact))


def test_git_ref_checks_commit_not_dirty_checkout(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.org"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    source = tmp_path / "provider.py"
    source.write_text("PROVIDERS = {}\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "provider.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "fixture"], check=True)
    source.write_text("PRIVATE = '" + "DO_NOT_" + "PUBLISH'\n")
    assert inspect_git_ref(tmp_path, "HEAD") == []
    subprocess.run(["git", "-C", str(tmp_path), "add", "provider.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "fixture"], check=True)
    assert any("provider.py" in issue for issue in inspect_git_ref(tmp_path, "HEAD"))


def test_prefixed_archive_credentials_are_blocked(tmp_path: Path) -> None:
    artifact = tmp_path / "package.whl"
    with zipfile.ZipFile(artifact, "w") as wheel:
        wheel.writestr("package/theory_master/config/local/credentials.toml", "token = 'sample'\n")
        wheel.writestr("package/.env", "TOKEN=sample\n")
        wheel.writestr("package/config/.env.example", "TOKEN=\n")
    findings = inspect_public_artifact(artifact)
    assert any("credentials.toml" in item for item in findings)
    assert any("package/.env:" in item for item in findings)
    assert not any(".env.example" in item for item in findings)


def test_large_text_and_archive_links_are_checked(tmp_path: Path) -> None:
    artifact = tmp_path / "package.tar.gz"
    payload = ("x" * 2_000_001 + "DO_NOT_" + "PUBLISH").encode()
    with tarfile.open(artifact, "w:gz") as archive:
        entry = tarfile.TarInfo("package/dist.js")
        entry.size = len(payload)
        archive.addfile(entry, io.BytesIO(payload))
        link = tarfile.TarInfo("package/link")
        link.type, link.linkname = tarfile.SYMTYPE, "/outside"
        archive.addfile(link)
    findings = inspect_public_artifact(artifact)
    assert any("dist.js" in item for item in findings)
    assert any("archive link" in item for item in findings)


def test_history_catches_deleted_private_content_and_metadata(tmp_path: Path) -> None:
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()
    git("init", "-q")
    git("config", "user.email", "test@example.org")
    git("config", "user.name", "Test")
    source = tmp_path / "public.py"
    source.write_text("PUBLIC = True\n")
    git("add", "public.py")
    git("commit", "-qm", "public base")
    base = git("rev-parse", "HEAD")
    source.write_text("PRIVATE = '" + "DO_NOT_" + "PUBLISH'\n")
    git("add", "public.py")
    git("commit", "-qm", "DO_NOT_" + "PUBLISH")
    source.write_text("PUBLIC = True\n")
    git("add", "public.py")
    git("commit", "-qm", "remove private fixture")
    assert inspect_git_ref(tmp_path, "HEAD") == []
    findings = inspect_git_history(tmp_path, base, "HEAD")
    assert any("public.py" in item for item in findings)
    assert any("commit metadata" in item for item in findings)
    git("checkout", "--orphan", "unrelated")
    git("commit", "-qm", "independent")
    assert any("not an ancestor" in item for item in inspect_git_history(tmp_path, base, "HEAD"))
