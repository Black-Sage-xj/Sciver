from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theory_master.backends import CodexBackend, FakeBackend
from theory_master.abc_pipeline import AgentSemanticBackend
from theory_master.observability.collector import Collector
from theory_master.provider_errors import ProviderQuotaExhausted, is_provider_quota_exhausted
from theory_master.specification import TaskSpec
from theory_master.task import bootstrap
from theory_master.workflow import Workflow


class _EventRecorder:
    def __init__(self):
        self.events = []

    def on_codex(self, mapped, *, role, thread_id):
        self.events.append(dict(mapped))


class _QuotaHandle:
    def __init__(self):
        self.yields = 0

    def stream(self):
        self.yields += 1
        yield SimpleNamespace(method="error", payload=SimpleNamespace(message="You've hit your usage limit"))
        self.yields += 1
        yield SimpleNamespace(method="error", payload=SimpleNamespace(message="Reconnecting... 1/5"))


class _QuotaThread:
    def __init__(self):
        self.handle = _QuotaHandle()

    def turn(self, _payload):
        return self.handle


class QuotaBackend(FakeBackend):
    def invoke(self, role, prompt, thread_id, model, provider):
        if role == "investigator":
            raise ProviderQuotaExhausted("You've hit your usage limit")
        return super().invoke(role, prompt, thread_id, model, provider)


class ProviderQuotaPauseTest(unittest.TestCase):
    def _make_spec(self, root: Path) -> TaskSpec:
        plan = root / "research_plan.md"
        plan.write_text(
            """# Goal
exercise provider quota pause

# Research Contract
use deterministic fixtures

# Final Gate
provider quota pause is persisted as resumable runtime state

# Required Context
- quota test
""",
            encoding="utf-8",
        )
        binding = bootstrap(plan)
        return TaskSpec.from_config(binding.config_path)

    def test_classifier_requires_explicit_exhaustion(self):
        self.assertTrue(is_provider_quota_exhausted("You've hit your usage limit"))
        self.assertTrue(is_provider_quota_exhausted("429 insufficient_quota: quota exceeded"))
        self.assertTrue(is_provider_quota_exhausted("account budget exhausted"))
        self.assertTrue(is_provider_quota_exhausted(
            'unexpected status 403 Forbidden: 预扣费额度失败, 用户剩余额度: ＄0.005316, '
            '需要预扣费额度: ＄0.014648, url: https://api.fixtureprovider.com/v1/responses'
        ))
        self.assertFalse(is_provider_quota_exhausted('403 Forbidden: invalid API key'))
        self.assertFalse(is_provider_quota_exhausted('401 Unauthorized: Missing bearer or basic authentication'))
        self.assertFalse(is_provider_quota_exhausted('403 预扣费额度失败: invalid signature'))
        self.assertFalse(is_provider_quota_exhausted("429 Too Many Requests"))
        self.assertFalse(is_provider_quota_exhausted("rate limit exceeded; retry after 20 seconds"))

    def test_backend_stops_stream_immediately_without_outer_reconnect(self):
        backend = CodexBackend(Path("/tmp"))
        thread = _QuotaThread()
        recorder = _EventRecorder()
        backend.threads["quota-thread"] = thread
        backend.collector = recorder
        calls = {"ensure": 0}

        def ensure(role, thread_id, model, provider):
            calls["ensure"] += 1
            return "quota-thread"

        backend.ensure_thread = ensure
        with self.assertRaisesRegex(ProviderQuotaExhausted, "usage limit"):
            backend.invoke("investigator", "return JSON", "quota-thread", "model", "provider")

        self.assertEqual(calls["ensure"], 1)
        self.assertEqual(thread.handle.yields, 1)
        self.assertTrue(any(event.get("event") == "LLM_FAILED" for event in recorder.events))
        self.assertFalse(any(event.get("event") == "LLM_RECONNECTING" for event in recorder.events))

    def test_semantic_annotator_does_not_retry_quota_as_missing_final_response(self):
        class SemanticQuotaBackend:
            def __init__(self):
                self.ensure_calls = 0
                self.invoke_calls = 0

            def ensure_thread(self, role, thread_id, model, provider):
                self.ensure_calls += 1
                return f"semantic-{self.ensure_calls}"

            def invoke(self, role, prompt, thread_id, model, provider):
                self.invoke_calls += 1
                raise ProviderQuotaExhausted("You've hit your usage limit")

        backend = SemanticQuotaBackend()
        semantic = AgentSemanticBackend(backend, model="model", provider="provider")
        with self.assertRaisesRegex(ProviderQuotaExhausted, "usage limit"):
            semantic.decide(
                task="classify closure",
                state={},
                questions=[{"id": "q1", "options": ["yes", "no"]}],
            )
        self.assertEqual(backend.ensure_calls, 1)
        self.assertEqual(backend.invoke_calls, 1)

    def test_codex_thread_keeps_client_account_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            runtime = root / "runtime"
            runtime.mkdir()
            with patch("theory_master.codex_backend._codex_home", return_value=runtime):
                backend = CodexBackend(workspace)
            backend.client_account = "1"
            thread = SimpleNamespace(id="thread-1")
            client = unittest.mock.Mock()
            client.thread_resume.return_value = thread
            sdk = SimpleNamespace(Sandbox=SimpleNamespace(workspace_write="write", read_only="read"))
            with patch.dict(sys.modules, {"openai_codex": sdk}), \
                 patch.object(backend, "_ensure_client", return_value=client), \
                 patch("theory_master.codex_accounts.selected_account", return_value="2"):
                identifier = backend.ensure_thread("investigator", "thread-1", "model", "CodexHTTPS")
            self.assertEqual(identifier, "thread-1")
            self.assertEqual(backend.thread_accounts[identifier], "1")

    def test_workflow_converts_quota_to_resumable_pause(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = self._make_spec(Path(temp))
            collector = Collector(spec, system_failure_actions=True)
            with patch.object(collector, "_spawn_error_repair") as repair, \
                 patch("theory_master.codex_accounts.selected_account", return_value="1"):
                state = Workflow(spec, QuotaBackend(reworks=0), collector=collector).run()
            repair.assert_not_called()
            self.assertEqual(state["status"], "paused")
            self.assertEqual(state["phase"], "provider_quota_exhausted")
            self.assertTrue(state["resumable"])
            self.assertFalse(state["execution_active"])
            self.assertEqual(state["recovery"]["code"], "provider_quota_exhausted")
            self.assertEqual(state["recovery"]["provider"], "CodexHTTPS")
            self.assertEqual(state["recovery"]["codex_account"], "1")
            self.assertIsNotNone(state.get("last_safe_checkpoint"))
            snap = collector.snapshot()
            self.assertEqual(snap["status"], "PAUSED")
            self.assertEqual((snap.get("repair_handoff") or {}).get("status"), "disabled")

            events = [
                json.loads(line)
                for line in (Path(state["cycle_dir"]) / "events.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(events[-1]["type"], "run.paused")
            self.assertEqual(events[-1]["payload"]["pause_kind"], "provider_quota_exhausted")
            self.assertEqual(events[-1]["payload"]["provider"], "CodexHTTPS")
            self.assertEqual(events[-1]["payload"]["codex_account"], "1")


if __name__ == "__main__":
    unittest.main()
