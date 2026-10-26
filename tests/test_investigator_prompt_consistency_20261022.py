"""Production Investigator prompt layers: identity, Native Goal v2, setup.

No live model and no research Cycle. The three normal research Stage Cards are
legacy/observer text; preservation uses a regular Stage and its own Contract.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from theory_master.identity import identities_root, identity_block
from theory_master.native_goal import (
    CODEX_GOAL_MAX_CHARS,
    FROZEN_CONTEXT_SHA_MARKER,
    NATIVE_GOAL_SAFE_CHARS,
    investigator_goal,
    native_goal_setup_turn,
)
from theory_master.prompt_stages import stage_context
from theory_master.skills import developer_instructions
from theory_master.stage_registry import stage_card_text


ROOT = Path(__file__).resolve().parents[1]
UNIVERSAL_BLIND = "freeze predictions, controls and errors before blind outcomes"
STAGE_CARD_MARKERS = ("Current objective:", "Exit / handoff:", "KEEP RESEARCHING")
SCENARIOS = {
    "theory": {
        "hypothesis": "The signed operator identity holds for every admissible graph",
        "scope": "Finite connected graphs; no sampled population",
        "decisive_test": "Prove the identity from the frozen axioms; a counterexample falsifies it",
    },
    "numerical": {
        "hypothesis": "The prespecified contrast is positive on held-out networks",
        "scope": "Fixed protocol, units, and error bound",
        "decisive_test": "Compare the frozen holdout after the prediction and error check",
    },
    "mixed": {
        "hypothesis": "The lemma implies the measured shell response",
        "scope": "Proof of the lemma plus one frozen numerical comparison",
        "decisive_test": "Prove the lemma, then run the isolated numerical check with its error bound",
    },
}


def _copies_match(name: str) -> str:
    runtime = (identities_root() / name).read_text(encoding="utf-8")
    source = (ROOT / "identities" / name).read_text(encoding="utf-8")
    if source != runtime:
        raise AssertionError(f"{name} source and packaged copy diverged")
    return runtime


def _goal(stage: str, science: dict, **extra) -> str:
    context = {"frozen_objective": science, **extra}
    return investigator_goal(stage, context, layout="v2", allow_setup_reference=True)


class InvestigatorPromptConsistencyTests(unittest.TestCase):
    def test_lengths_stay_inside_native_goal_budgets(self):
        identity = identity_block("investigator")
        goal = _goal("investigate", SCENARIOS["mixed"])
        self.assertIn("历史证据复用", identity)
        self.assertGreater(len(identity), 1000)
        self.assertLessEqual(len(goal), NATIVE_GOAL_SAFE_CHARS)
        self.assertLessEqual(len(goal), CODEX_GOAL_MAX_CHARS)
        # Cycle62-sized frozen science stays inline under the safety budget.
        cycle62 = {
            "hypothesis": "H" * 186,
            "scope": "scope" * 73,
            "decisive_test": "first shell ΔE Q C0 Q1 " * 55,
        }
        rendered = _goal("investigate", cycle62)
        self.assertLessEqual(len(rendered), NATIVE_GOAL_SAFE_CHARS)
        self.assertNotIn(FROZEN_CONTEXT_SHA_MARKER, rendered)
        self.assertIn(cycle62["decisive_test"], rendered)

    def test_production_chain_omits_stage_cards(self):
        from theory_master.backends import FakeBackend
        from theory_master.native_goal import NativeGoalIncomplete
        from theory_master.observability.collector import Collector
        from theory_master.specification import TaskSpec
        from theory_master.task import bootstrap
        from theory_master.workflow import Workflow

        seen = []
        real = investigator_goal

        def wrapped(stage, context, **kwargs):
            text = real(stage, context, **kwargs)
            seen.append((stage, kwargs, text))
            return text

        class CaptureBackend:
            def __init__(self):
                self.fake = FakeBackend(reworks=0)
                self.goals = []
                self.setups = []

            def ensure_thread(self, role, thread_id, model, provider):
                return self.fake.ensure_thread(role, thread_id, model, provider)

            def invoke(self, role, prompt, thread_id, model, provider):
                return self.fake.invoke(role, prompt, thread_id, model, provider)

            def drop_thread(self, thread_id):
                return self.fake.drop_thread(thread_id)

            def invoke_goal(self, role, *, goal_objective, context_prompt, handoff_path,
                            thread_id, model, provider, token_allowance=None,
                            tokens_before_goal=0):
                self.goals.append(goal_objective)
                self.setups.append(context_prompt)
                raise NativeGoalIncomplete("usageLimited", thread_id or "thread")

        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "research_plan.md"
            plan.write_text(
                "# Goal\nPrompt consistency\n# Research Contract\nSynthetic tests\n"
                "# Final Gate\nIndependent Judge must assess\n# Required Context\n- fixture\n",
                encoding="utf-8",
            )
            spec = TaskSpec.from_config(bootstrap(plan).config_path)
            backend = CaptureBackend()
            collector = Collector(spec)
            with mock.patch("theory_master.native_goal.investigator_goal", wrapped):
                state = Workflow(spec, backend, collector=collector).run()
        self.assertEqual([item[0] for item in seen], ["investigate"])
        self.assertEqual(seen[0][1], {"layout": "v2", "allow_setup_reference": True})
        self.assertEqual(backend.goals, [seen[0][2]])
        from theory_master.contracts import role_contract
        contract = role_contract("investigator", phase="investigate")
        # Capture the actual Native Goal Setup path: schema ownership moved out
        # of Identity, so checking only the standalone projection is insufficient.
        self.assertIn(contract.prompt_projection({}), backend.setups[0])
        finding_schema = contract.prompt_schema["findings"][0]
        for field in ("statement", "method", "result", "provenance"):
            self.assertIn(json.dumps(field), backend.setups[0])
        for field in ("source", "split", "declaration_ref", "frozen_before_current_cycle"):
            self.assertIn(json.dumps(field), backend.setups[0])
        for enum_value in ("benchmark_provided", "pre_specified_synthetic", "agent_generated",
                           "external_source", "validation", "holdout", "gate"):
            self.assertIn(enum_value, backend.setups[0])
        self.assertIn("optional", finding_schema["provenance"]["source"])
        contract.validate({"findings": [{"statement": "Self-contained result without optional fields"}]}, {})
        declared = {"statement": "Bounded result", "provenance": {
            "source": "benchmark_provided", "split": "holdout",
            "declaration_ref": "input/declaration.json", "frozen_before_current_cycle": True,
        }}
        contract.validate({"findings": [declared]}, {})
        with self.assertRaises(ValueError):
            contract.validate({"findings": [{"statement": "Unsupported strong identity", "provenance": {
                "source": "benchmark_provided", "split": "holdout",
            }}]}, {})
        identity = _copies_match("investigator.md")
        for schema_token in ("`statement`", "`method`", "`result`", "`provenance`", "declaration_ref"):
            self.assertNotIn(schema_token, identity)
        wire = "\n".join(backend.goals + backend.setups)
        for marker in STAGE_CARD_MARKERS:
            self.assertNotIn(marker, wire)
        card = stage_card_text("investigator", "investigate")
        self.assertNotIn(card, wire)
        self.assertIn("FROZEN SCIENTIFIC OBJECTIVE", backend.goals[0])
        lock = state["hypothesis_lock"]
        for key in ("hypothesis", "scope", "decisive_test"):
            self.assertIn(lock[key], backend.goals[0])
        events = [
            json.loads(line)
            for line in (Path(collector.snapshot()["run_dir"]) / "runtime_events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        kinds = [
            event.get("input_kind")
            for event in events
            if event.get("event") == "AGENT_INPUT" and event.get("role") == "investigator"
        ]
        self.assertEqual(kinds, ["native_goal_setup_turn", "native_goal_objective"])
        invocation = (ROOT / "theory_master" / "workflow_invocation.py").read_text(encoding="utf-8")
        self.assertIn('"investigate", "judge_rework", "integrate_literature"', invocation)
        self.assertIn('layout="v2"', invocation)

    def test_three_stages_keep_completion_boundaries(self):
        rework_request = "finish the exact missing comparison"
        for stage in ("investigate", "judge_rework", "integrate_literature"):
            extra = {}
            if stage == "judge_rework":
                extra["judge_objections"] = [{"requested_resolution": rework_request}]
            context = stage_context(
                "investigator", stage,
                {"hypothesis_lock": dict(SCENARIOS["mixed"]), "cycle_workspace": "/tmp/prompt-consistency", **extra},
            )
            goal = investigator_goal(stage, context, layout="v2", allow_setup_reference=True)
            setup = native_goal_setup_turn(
                json.dumps(context, ensure_ascii=False) + "\n\nSDK DELIVERY INTERFACE",
                goal,
            )
            with self.subTest(stage=stage):
                self.assertLessEqual(len(goal), NATIVE_GOAL_SAFE_CHARS)
                self.assertNotIn(UNIVERSAL_BLIND, goal)
                self.assertNotIn("usageLimited", goal)
                self.assertNotIn("budgetLimited", goal)
                for marker in STAGE_CARD_MARKERS:
                    self.assertNotIn(marker, goal)
                    self.assertNotIn(marker, setup)
                self.assertEqual(goal.count("Never self-terminate or request native blocked"), 1)
                self.assertIn("do not skip a required independent check", goal)
                self.assertIn("independent Judge", goal)
                self.assertIn("actual Investigator token budget boundary", goal)
                for value in SCENARIOS["mixed"].values():
                    self.assertIn(value, goal)
                    self.assertNotIn(value, setup)
                self.assertNotIn('"frozen_objective"', setup)
            if stage == "judge_rework":
                self.assertIn(rework_request, goal)
                self.assertIn("a partial Finding resolves the Judge request", goal)
                self.assertIn("post-hoc generated data as independent holdout/gate Evidence", goal)
                self.assertNotIn("SCIENTIFIC COMPLETION CHAIN", goal)
            if stage == "integrate_literature":
                self.assertIn("do not invent a blind dataset for a proof", goal)
                self.assertIn("source summary is not research completion", goal)
            if stage == "investigate":
                self.assertIn("partial work is not completion", goal)
                self.assertIn("A proof needs no blind dataset", goal)
                self.assertIn("data isolation and error checks", goal)
                self.assertIn("cannot impersonate new independent evidence", goal)

    @mock.patch.dict("os.environ", {"THEORYMASTER_COMPUTE_PYTHON": sys.executable})
    def test_new_thread_loads_identity_and_resumed_thread_does_not(self):
        from theory_master.backends import CodexBackend

        sdk = SimpleNamespace(Sandbox=SimpleNamespace(workspace_write="write", read_only="read"))
        runs = []
        objectives = []

        def run_setup(prompt):
            runs.append(prompt)

        def stop_after_wire(thread, objective, *, on_event=None):
            objectives.append(objective)
            raise RuntimeError("stop-after-wire")

        with tempfile.TemporaryDirectory() as tmp:
            backend = CodexBackend(Path(tmp))
            backend.cycle_id = "cycle-prompt"
            thread = SimpleNamespace(id="inv-thread", run=run_setup, _client=SimpleNamespace())
            client = mock.Mock()
            client.thread_start.return_value = thread
            client.thread_resume.return_value = thread
            handoff = Path(tmp) / "handoff.json"
            goal = _goal("investigate", SCENARIOS["theory"])
            raw_setup = json.dumps({"frozen_objective": SCENARIOS["theory"], "note": "nav"})
            with mock.patch.dict("sys.modules", {"openai_codex": sdk}), \
                 mock.patch.object(backend, "_ensure_client", return_value=client), \
                 mock.patch("theory_master.native_goal.run_native_goal", stop_after_wire):
                new_id = backend.ensure_thread("investigator", None, "model", "provider")
                with self.assertRaisesRegex(RuntimeError, "stop-after-wire"):
                    backend.invoke_goal(
                        "investigator", goal_objective=goal, context_prompt=raw_setup,
                        handoff_path=str(handoff), thread_id=new_id, model="model", provider="provider",
                    )
                started = client.thread_start.call_args.kwargs["developer_instructions"]
                self.assertIn("历史证据复用", started)
                self.assertIn("纯理论证明不必制造盲测数据", started)
                self.assertNotIn(UNIVERSAL_BLIND, started)
                for marker in STAGE_CARD_MARKERS:
                    self.assertNotIn(marker, started)
                self.assertEqual(objectives, [goal])
                self.assertEqual(len(runs), 1)
                self.assertIn("not a second research target", runs[0])
                self.assertNotIn(UNIVERSAL_BLIND, runs[0])

                runs.clear()
                objectives.clear()
                backend.drop_thread(new_id)
                resumed = backend.ensure_thread("investigator", new_id, "model", "provider")
                self.assertNotIn("developer_instructions", client.thread_resume.call_args.kwargs)
                with self.assertRaisesRegex(RuntimeError, "stop-after-wire"):
                    backend.invoke_goal(
                        "investigator", goal_objective=goal, context_prompt=raw_setup,
                        handoff_path=str(handoff), thread_id=resumed, model="model", provider="provider",
                    )
                self.assertEqual(objectives, [goal])
                self.assertEqual(len(runs), 1)
                self.assertIn("nav", runs[0])
                self.assertNotIn(SCENARIOS["theory"]["decisive_test"], runs[0])

    def test_overlong_frozen_hypothesis_uses_setup_reference(self):
        science = {
            "hypothesis": "Lemma " + ("H" * 5000),
            "scope": "Exact frozen scope with no summary allowed",
            "decisive_test": "Prove every step; do not replace the proof with a sample",
        }
        context = {"frozen_objective": science, "workspace_access": {"cycle_workspace": "/tmp/long-h"}}
        with self.assertRaisesRegex(ValueError, "4000-character"):
            investigator_goal("investigate", context, layout="v2")
        goal = investigator_goal("investigate", context, layout="v2", allow_setup_reference=True)
        raw = json.dumps(context, ensure_ascii=False) + "\n\nSDK DELIVERY INTERFACE"
        setup = native_goal_setup_turn(raw, goal)
        self.assertLessEqual(len(goal), NATIVE_GOAL_SAFE_CHARS)
        self.assertIn(FROZEN_CONTEXT_SHA_MARKER, goal)
        self.assertNotIn(science["hypothesis"], goal)
        self.assertIn(science["hypothesis"], setup)
        self.assertIn(science["scope"], setup)
        self.assertIn(science["decisive_test"], setup)
        self.assertIn("A proof needs no blind dataset", goal)
        self.assertNotIn(UNIVERSAL_BLIND, goal)
        from theory_master.native_goal import verify_native_goal_context_reference
        verify_native_goal_context_reference(goal, setup)
        changed = json.dumps(
            {"frozen_objective": {**science, "hypothesis": science["hypothesis"] + " altered"}},
            ensure_ascii=False,
        )
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_native_goal_context_reference(goal, changed)

    def test_object_specific_verification_and_historical_reuse(self):
        identity = _copies_match("investigator.md")
        handoff = _copies_match("_investigator_handoff.md")
        developer = developer_instructions("investigator")
        self.assertEqual(developer.count("历史证据复用"), 1)
        self.assertIn("已暴露样本不能冒充盲测或 Final Gate 证据", identity)
        self.assertIn("纯理论证明不必制造盲测数据", identity)
        self.assertNotIn("网络、协议、力域", identity)
        self.assertIn("不得因历史相似而跳过", identity)
        self.assertIn("有目的地检索", handoff)
        self.assertNotIn("SDK 负责生命周期", handoff)
        self.assertEqual(identity.count("供 Judge 独立裁决"), 1)
        for name, science in SCENARIOS.items():
            goal = _goal("investigate", science)
            with self.subTest(scenario=name):
                for value in science.values():
                    self.assertIn(value, goal)
                self.assertNotIn(UNIVERSAL_BLIND, goal)
                self.assertIn("A proof needs no blind dataset", goal)
                self.assertIn("data isolation and error checks", goal)
                self.assertIn("cannot impersonate new independent evidence", goal)
                self.assertIn("cite an applicable prior result when it already answers this frozen step", goal)
                self.assertEqual(goal.count("applicable prior result"), 1)
                self.assertNotIn("历史证据复用", goal)
                self.assertIn("Never self-terminate or request native blocked", goal)
                self.assertIn("Unperformed tests are not inconclusive", goal)
                self.assertIn("silently weaken the frozen estimand", goal)


if __name__ == "__main__":
    unittest.main()
