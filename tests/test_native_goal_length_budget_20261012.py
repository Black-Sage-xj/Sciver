"""Freeze-exact Native Goal with a strict 4000-character provider ceiling.

No live Codex, science test, or filesystem mutation.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from theory_master.native_goal import (
    investigator_goal, verify_native_goal_context_reference, frozen_science_digest,
    CODEX_GOAL_MAX_CHARS, NATIVE_GOAL_SAFE_CHARS,
    FROZEN_CONTEXT_SHA_MARKER, run_native_goal,
)
from theory_master.codex_backend import CodexBackend


def frozen(hypothesis="H", scope="S", decisive_test="T"):
    return {"hypothesis": hypothesis, "scope": scope, "decisive_test": decisive_test}


class NativeGoalBudgetTests(unittest.TestCase):
    def test_exact_4003_case_compacts_without_any_scientific_truncation(self):
        # Cycle62 had 186+365+1180 frozen science characters (v2 was 4003).
        science = frozen("H"*186,"scope"*73,"first shell ΔE Q C0 Q1 "*55)
        ctx = {"frozen_objective": science}
        rendered=investigator_goal("investigate",ctx,layout="v2")
        self.assertLessEqual(len(rendered),NATIVE_GOAL_SAFE_CHARS)
        self.assertLess(len(rendered),CODEX_GOAL_MAX_CHARS)
        for k,value in science.items():
            self.assertIn(value,rendered,k)
        self.assertNotIn(FROZEN_CONTEXT_SHA_MARKER,rendered)
        self.assertIn("execute the complete decisive test",rendered)
        self.assertIn("independent checks",rendered)

    def test_compact_inline_goal_keeps_scientific_whitespace_verbatim(self):
        science = frozen(
            "H: parameter  A must differ from B\n  under fixed boundary.",
            "Scope includes tabs\tand  two spaces.",
            "Compare ΔE > 0 with 95% CI; do not drop the null case.",
        )
        goal = investigator_goal(
            "investigate", {"frozen_objective": science}, layout="v2",
            allow_setup_reference=True,
        )
        exact_json = json.dumps(
            science, ensure_ascii=False, separators=(",", ":"),
        )
        self.assertIn("FROZEN SCIENTIFIC OBJECTIVE (immutable):\n" + exact_json, goal)
        self.assertNotIn('"hypothesis": "', goal)
        self.assertNotIn(FROZEN_CONTEXT_SHA_MARKER, goal)
        self.assertLessEqual(len(goal), NATIVE_GOAL_SAFE_CHARS)

    def test_two_mode_boundary_is_based_on_complete_rendered_goal(self):
        # The short decision considers *all* Goal instructions, not just
        # len(hypothesis)+len(scope)+len(decisive_test).
        short = frozen("H", "S", "T" * 1800)
        short_goal = investigator_goal(
            "investigate", {"frozen_objective": short}, layout="v2",
            allow_setup_reference=True,
        )
        self.assertIn(short["decisive_test"], short_goal)
        self.assertNotIn(FROZEN_CONTEXT_SHA_MARKER, short_goal)
        long = frozen("H", "S", "T" * 3600)
        long_context = {"frozen_objective": long}
        long_goal = investigator_goal(
            "investigate", long_context, layout="v2", allow_setup_reference=True,
        )
        self.assertNotIn(long["decisive_test"], long_goal)
        self.assertIn(FROZEN_CONTEXT_SHA_MARKER + frozen_science_digest(long), long_goal)
        self.assertLessEqual(len(long_goal), NATIVE_GOAL_SAFE_CHARS)
        verify_native_goal_context_reference(
            long_goal, json.dumps(long_context, ensure_ascii=False),
        )
        self.assertEqual(long_context["frozen_objective"], long)

    def test_exact_3800_character_boundary(self):
        base = investigator_goal(
            "investigate", {"frozen_objective": frozen("H", "S", "T")},
            layout="v2", allow_setup_reference=True,
        )
        n = NATIVE_GOAL_SAFE_CHARS - len(base) + 1
        self.assertGreater(n, 1)
        science = frozen("H", "S", "T" * n)
        inline = investigator_goal(
            "investigate", {"frozen_objective": science}, layout="v2",
            allow_setup_reference=True,
        )
        self.assertEqual(len(inline), NATIVE_GOAL_SAFE_CHARS)
        self.assertIn(science["decisive_test"], inline)
        self.assertNotIn(FROZEN_CONTEXT_SHA_MARKER, inline)
        one_over = frozen("H", "S", "T" * (n + 1))
        referenced = investigator_goal(
            "investigate", {"frozen_objective": one_over}, layout="v2",
            allow_setup_reference=True,
        )
        self.assertIn(FROZEN_CONTEXT_SHA_MARKER, referenced)
        self.assertNotIn(one_over["decisive_test"], referenced)

    def test_inline_goal_sends_nonduplicated_setup_and_long_goal_keeps_full_setup(self):
        from theory_master.native_goal import native_goal_setup_turn
        science = frozen("H frozen", "S frozen", "Decision must compare ΔE > 0")
        payload = {"frozen_objective": science,
                   "judge_objections": [{"requested_resolution": "run exact comparison"}],
                   "workspace_access": {"cycle_workspace": "/tmp/working"}}
        setup = json.dumps(payload, ensure_ascii=False) + "\n\nSDK DELIVERY INTERFACE"
        short_goal = investigator_goal("judge_rework", payload, layout="v2", allow_setup_reference=True)
        wire = native_goal_setup_turn(setup, short_goal)
        self.assertNotIn('"frozen_objective"', wire)
        self.assertIn("run exact comparison", wire)
        self.assertIn("/tmp/working", wire)
        self.assertIn("SDK DELIVERY INTERFACE", wire)
        self.assertIn(json.dumps(science, ensure_ascii=False, separators=(",", ":")), short_goal)
        large = frozen("H", "S", "T" * 6000)
        long_payload = {"frozen_objective": large}
        long_goal = investigator_goal("investigate", long_payload, layout="v2", allow_setup_reference=True)
        long_setup = native_goal_setup_turn(json.dumps(long_payload), long_goal)
        self.assertIn('"frozen_objective"', long_setup)
        verify_native_goal_context_reference(long_goal, long_setup)


    def test_setup_reference_only_for_overlong_exact_science(self):
        scientific = frozen("H"*200,"S"*500,"ΔEⱼ=Jⱼ−Qⱼ; check signed prediction "*250)
        self.assertGreater(len(scientific["decisive_test"]),4000)
        with self.assertRaisesRegex(ValueError,"4000-character"):
            investigator_goal("investigate",{"frozen_objective":scientific},layout="v2")
        ref=investigator_goal("investigate",{"frozen_objective":scientific},
                              layout="v2",allow_setup_reference=True)
        self.assertLessEqual(len(ref),NATIVE_GOAL_SAFE_CHARS)
        self.assertIn(FROZEN_CONTEXT_SHA_MARKER + frozen_science_digest(scientific),ref)
        self.assertIn("FULL, exact frozen_objective",ref)
        setup = json.dumps({"frozen_objective":scientific,"working_root":"/tmp/test"},
                           ensure_ascii=False) + "\n\nSDK DELIVERY INTERFACE"
        verify_native_goal_context_reference(ref,setup)
        with self.assertRaisesRegex(ValueError,"hash mismatch"):
            verify_native_goal_context_reference(
                ref,json.dumps({"frozen_objective":{**scientific,"scope":"CHANGED"}},
                               ensure_ascii=False))
        with self.assertRaisesRegex(ValueError,"absent or malformed"):
            verify_native_goal_context_reference(ref,"{}")
        with self.assertRaisesRegex(ValueError,"absent or malformed"):
            verify_native_goal_context_reference(ref,"not-json")

    def test_rework_never_loses_exact_requested_resolution(self):
        s=frozen("H","S","D")
        ctx={"frozen_objective":s,"judge_objections":[{"requested_resolution":"Do full independent test"}]}
        goal=investigator_goal("judge_rework",ctx,layout="v2")
        self.assertIn("Do full independent test",goal)
        self.assertIn("JUDGE REWORK",goal)
        self.assertIn("D",goal)

    def test_oversized_judge_rework_preserves_all_objections_in_setup_with_sha(self):
        science=frozen("H","S","D")
        ctx={"frozen_objective":science,"judge_objections":[
            {"requested_resolution":"Required experiment A: " + "R"*4400},
            {"requested_resolution":"Required comparison B: independent network"}
        ]}
        goal=investigator_goal("judge_rework",ctx,layout="v2",allow_setup_reference=True)
        self.assertLessEqual(len(goal),NATIVE_GOAL_SAFE_CHARS)
        self.assertIn("JUDGE_REQUESTS_CONTEXT_SHA256=",goal)
        verify_native_goal_context_reference(goal,json.dumps(ctx,ensure_ascii=False))
        ctx["judge_objections"][1]["requested_resolution"]="CHANGED"
        with self.assertRaisesRegex(ValueError,"requested_resolution hash mismatch"):
            verify_native_goal_context_reference(goal,json.dumps(ctx,ensure_ascii=False))

    def test_backend_rejects_bad_reference_before_setup_turn(self):
        s=frozen("H","S","D"*7000)
        objective=investigator_goal("investigate",{"frozen_objective":s},
                                    layout="v2",allow_setup_reference=True)
        backend=CodexBackend.__new__(CodexBackend)
        backend.ensure_thread=MagicMock(side_effect=AssertionError("do not open thread"))
        with self.assertRaisesRegex(ValueError,"hash mismatch"):
            backend.invoke_goal(
                "investigator",goal_objective=objective,context_prompt=json.dumps(
                    {"frozen_objective":{**s,"scope":"corrupt"}}),
                handoff_path="/tmp/not-existing.json",thread_id=None,
                model="test",provider="test")
        backend.ensure_thread.assert_not_called()

    def test_hard_provider_limit_guard_in_native_stream(self):
        thread=MagicMock()
        with self.assertRaisesRegex(ValueError,"4000-character"):
            run_native_goal(thread,"x"*(CODEX_GOAL_MAX_CHARS+1))
        thread._client.start_goal_operation.assert_not_called()

    def test_invalid_science_rejected_not_summary_substituted(self):
        for bad in ({}, {"hypothesis":"H","scope":"","decisive_test":"D"},
                    {"hypothesis":"H","scope":"S","decisive_test":""}):
            with self.assertRaisesRegex(ValueError,"complete frozen hypothesis"):
                investigator_goal("investigate",{"frozen_objective":bad},
                                  layout="v2",allow_setup_reference=True)


if __name__ == "__main__":
    unittest.main()
