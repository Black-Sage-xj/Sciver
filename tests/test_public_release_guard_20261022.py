"""Offline release-gate tests; never call a model or publish a repository."""
from __future__ import annotations

import io
import subprocess
import tarfile
import zipfile
from pathlib import Path

from scripts.public_release_guard import inspect_public_artifact, inspect_git_ref


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
