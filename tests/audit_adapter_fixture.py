"""Deterministic external-adapter fixture; no launcher or model is executed."""
from pathlib import Path

from theory_master.audit_adapter import AuditAdapter


def create_audit_adapter(root: Path) -> tuple[AuditAdapter, dict[str, str]]:
    directory = root / "audit-adapter"
    directory.mkdir(exist_ok=True)
    skill = directory / "SKILL.md"
    relay = directory / "relay.py"
    skill.write_text("# Test-only audit adapter\n", encoding="utf-8")
    relay.write_text("raise AssertionError('fixture relay must never execute')\n", encoding="utf-8")
    adapter = AuditAdapter(skill.resolve(), relay.resolve(), root.resolve())
    return adapter, {
        "THEORY_MASTER_AUDIT_SKILL": str(adapter.skill),
        "THEORY_MASTER_AUDIT_RELAY": str(adapter.relay),
        "THEORY_MASTER_AUDIT_WORKDIR": str(adapter.workdir),
    }
