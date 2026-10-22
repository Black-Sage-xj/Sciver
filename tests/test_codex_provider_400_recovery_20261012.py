"""Cycle69: preserve provider 400 and explicitly override only a resumed role."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from theory_master.backend_config import (
    RoleRoute, build_model_profile, load_model_profile_file, parse_role_spec,
)
from theory_master.codex_backend import (
    CodexBackend, CodexProviderRequestRejected, provider_request_rejection,
)
from theory_master.workflow import Workflow


def provider_error_event():
    return json.dumps({
        "type": "error", "status": 400, "error": {
            "type": "invalid_request_error",
            "message": "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.",
        }
    })


class InvalidModelHandle:
    def stream(self):
        error = provider_error_event()
        yield SimpleNamespace(method="error", payload=SimpleNamespace(error=error))
        yield SimpleNamespace(method="turn/completed", payload=SimpleNamespace(
            turn=SimpleNamespace(id="turn-bad-model", final_response=None, error=error)
        ))


class InvalidModelThread:
    def __init__(self):
        self.calls = 0

    def turn(self, _):
        self.calls += 1
        return InvalidModelHandle()

    def read(self, *, include_turns=False):
        raise AssertionError("Provider explicitly rejected model; no read can recover output")


class CodexProviderRejectionTests(unittest.TestCase):
    def test_http400_invalid_model_is_not_empty_response_and_not_retried(self):
        backend = CodexBackend(Path("/tmp"))
        thread = InvalidModelThread()
        backend.threads["thread-bad-model"] = thread
        backend.ensure_thread = lambda role, tid, model, provider: "thread-bad-model"
        backend.OUTER_TURN_RETRIES = 5
        backend.OUTER_RETRY_DELAYS = (0, 0, 0, 0, 0)
        with self.assertRaisesRegex(CodexProviderRequestRejected,
                                    "HTTP 400 invalid_request_error.*gpt-6.1-sol.*not supported"):
            backend.invoke("research_lead", "return frontier updates",
                           "thread-bad-model", "gpt-6.1-sol", "CodexHTTPS")
        self.assertEqual(thread.calls, 1)

    def test_only_explicit_400_is_classified(self):
        self.assertIsNone(provider_request_rejection(None))
        self.assertIsNone(provider_request_rejection("connection timed out"))
        self.assertIsNone(provider_request_rejection(json.dumps({
            "status":503,"error":{"type":"server_error","message":"Service unavailable"}
        })))
        self.assertIsInstance(provider_request_rejection(provider_error_event()),
                              CodexProviderRequestRejected)

    def test_user_selected_tier_two_does_not_degrade_due_to_one_account_rejection(self):
        path = Path(__file__).resolve().parents[1] / "theory_master" / "model_profiles" / "codex.toml"
        selected = load_model_profile_file(path)
        for role in ("research_lead", "challenger", "investigator", "judge"):
            self.assertEqual(selected[role].model, "gpt-6-sol")
            self.assertEqual(selected[role].provider, "CodexHTTPS")
        self.assertEqual(selected["research_lead"].reasoning_effort, "high")
        self.assertEqual(selected["challenger"].reasoning_effort, "medium")
        self.assertEqual(selected["investigator"].reasoning_effort, "low")
        self.assertEqual(selected["judge"].reasoning_effort, "medium")
        self.assertEqual(selected["literature"].model, "gpt-6-luna")

    def test_explicit_resume_override_preserves_old_frozen_route_ledger(self):
        old=build_model_profile("test", "codex")
        old["research_lead"]=RoleRoute("gpt-6.1-sol","CodexHTTPS","openai","high")
        current=dict(old)
        current["research_lead"]=RoleRoute("gpt-6-sol","CodexHTTPS","openai","high")
        from theory_master.abc_pipeline import AgentSemanticBackend
        backend=CodexBackend.__new__(CodexBackend)
        backend.routes=dict(current)
        sem=AgentSemanticBackend(backend,model="gpt-6-sol",provider="CodexHTTPS")
        wf=Workflow.__new__(Workflow)
        wf.routes=dict(current)
        wf.backend=backend
        wf.collector=Mock()
        wf.semantic_backend=sem
        wf.route_resolver=lambda: dict(current)
        wf.resume_route_overrides=dict([parse_role_spec("research_lead=gpt-6-sol@CodexHTTPS")])
        with tempfile.TemporaryDirectory() as tmp:
            wf.cycle_dir=Path(tmp)
            snapshot={role:{"model":r.model,"provider":r.provider,
                            "reasoning_effort":r.reasoning_effort,
                            "model_vendor":r.model_vendor}
                      for role,r in old.items()}
            state={"runtime_role_routes":snapshot,"scientific_verdict":"supported",
                   "role_outputs":{"judge":{"verdict":"supported"}},
                   "hypothesis_lock":{"id":"H-0051","decisive_test":"unchanged"}}
            orig=json.dumps(state,sort_keys=True)
            wf._restore_resumed_cycle_routes(state)
            self.assertEqual(wf.routes["research_lead"].model,"gpt-6-sol")
            self.assertEqual(wf.routes["research_lead"].reasoning_effort,"high")
            self.assertEqual(wf.routes["judge"].model,old["judge"].model)
            self.assertEqual(wf.routes["investigator"].model,old["investigator"].model)
            self.assertEqual(wf.semantic_backend.model,"gpt-6-sol")
            self.assertEqual(state["runtime_role_routes"],snapshot)
            self.assertEqual(state["role_outputs"]["judge"]["verdict"],"supported")
            self.assertEqual(state["hypothesis_lock"]["decisive_test"],"unchanged")
            item=state["runtime_resume_route_overrides"]["research_lead"]
            self.assertEqual((item["previous_model"],item["effective_model"],
                              item["source"]),
                             ("gpt-6.1-sol","gpt-6-sol","explicit_resume_cli"))
            wf._refresh_next_cycle_routes()
            self.assertEqual(wf.routes["research_lead"].model,"gpt-6-sol")

    def test_resume_without_explicit_override_remains_frozen(self):
        old=build_model_profile("test","codex")
        old["research_lead"]=RoleRoute("gpt-6.1-sol","CodexHTTPS","openai","high")
        backend=CodexBackend.__new__(CodexBackend)
        backend.routes=dict(old)
        wf=Workflow.__new__(Workflow)
        wf.routes=dict(old)
        wf.backend=backend
        wf.collector=None
        wf.semantic_backend=None
        wf.route_resolver=lambda: dict(old)
        wf.resume_route_overrides={}
        with tempfile.TemporaryDirectory() as tmp:
            wf.cycle_dir=Path(tmp)
            state={"runtime_role_routes":{
                role:{"model":route.model,"provider":route.provider,
                      "reasoning_effort":route.reasoning_effort}
                for role,route in old.items()
            }}
            wf._restore_resumed_cycle_routes(state)
            self.assertEqual(wf.routes["research_lead"].model,"gpt-6.1-sol")
            self.assertNotIn("runtime_resume_route_overrides",state)


if __name__=="__main__":
    unittest.main()
