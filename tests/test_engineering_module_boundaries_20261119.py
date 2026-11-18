"""Keep execution owners, SDK compatibility and public facades separate."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "theory_master"
WEB = ROOT / "web" / "tm_observer"


def imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.module
            else:
                yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def test_private_sdk_imports_have_one_compatibility_owner():
    offenders = []
    for path in CORE.rglob("*.py"):
        if path.name == "codex_sdk_compat.py":
            continue
        for module in imports(path):
            if module.startswith("openai_codex._"):
                offenders.append(f"{path.name}: {module}")
    assert offenders == []


def test_worker_implementations_do_not_depend_on_the_public_facade():
    owners = [p for p in CORE.glob("worker_*.py") if p.name != "worker_runtime.py"]
    offenders = [(p.name, module) for p in owners for module in imports(p)
                 if module.rsplit(".", 1)[-1] == "worker_runtime"]
    assert offenders == []


def test_http_domains_do_not_depend_on_the_server_facade():
    paths = [*WEB.glob("http_*.py"), WEB / "web_pages.py", WEB / "state_cache.py"]
    assert [(p.name, module) for p in paths for module in imports(p)
            if module in {"web", "tm_observer.web"}] == []


@pytest.mark.parametrize("relative,budget", [
    ("theory_master/workflow.py", 1000),
    ("theory_master/worker_runtime.py", 250),
    ("theory_master/cycle_runner.py", 400),
    ("theory_master/invocation_execution.py", 200),
    ("theory_master/qualification_session.py", 180),
    ("web/tm_observer/web.py", 500),
    ("web/tm_observer/http_handler.py", 400),
])
def test_coordinators_keep_policy_in_their_responsibility_owners(relative, budget):
    # These are architectural entrypoints, not arbitrary limits on domain code.
    # A new domain responsibility belongs in a reviewable owner module.
    assert len((ROOT / relative).read_text().splitlines()) <= budget
