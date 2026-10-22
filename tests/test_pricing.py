from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from theory_master.observability.costs import assess_cost, backfill_run_state_costs
from theory_master.observability.pricing import PRICING_SCHEMA, parse_openai_standard_pricing


class OfficialPricingTest(unittest.TestCase):
    @staticmethod
    def _page() -> str:
        rows = []
        values = {
            "gpt-6-sol": [2, 0.2, 2.5, 10, 4, 0.4, 5, 15],
            "gpt-6-luna": [0.1, 0.01, 0.125, 0.5, 0.2, 0.02, 0.25, 0.75],
            "gpt-5.6-sol": [4, 0.4, 5, 20, 8, 0.8, 10, 30],
            "gpt-5.6-terra": [2, 0.2, 2.5, 12, 4, 0.4, 5, 18],
            "gpt-5.6-luna": [0.2, 0.02, 0.25, 1.2, 0.4, 0.04, 0.5, 1.8],
        }
        for model, prices in values.items():
            cells = "".join(f"<td>${value}</td>" for value in prices)
            rows.append(f"<tr><td>{model}</td>{cells}</tr>")
        return '<div data-content-switcher-pane="true" data-value="standard">' + "".join(rows) + '</div><div data-content-switcher-pane="true" data-value="batch"></div>'

    def test_parser_reads_short_and_long_standard_rates(self):
        models = parse_openai_standard_pricing(self._page())
        self.assertEqual(models["gpt-6-sol"]["short_context"]["input"], 2.0)
        self.assertEqual(models["gpt-6-sol"]["long_context"]["output"], 15.0)
        self.assertEqual(models["gpt-6-luna"]["short_context"]["cached_input"], 0.01)

    def test_codex_cost_uses_cached_tokens_without_double_counting(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "pricing.json"
            cache.write_text(json.dumps({
                "schema": PRICING_SCHEMA,
                "source_url": "https://developers.openai.com/api/docs/pricing",
                "currency": "USD",
                "long_context_threshold_tokens": 272000,
                "fetched_at": "2026-09-26T00:00:00+00:00",
                "models": {
                    "gpt-6-luna": {
                        "short_context": {"input": 0.1, "cached_input": 0.01, "cache_write": 0.125, "output": 0.5},
                        "long_context": {"input": 0.2, "cached_input": 0.02, "cache_write": 0.25, "output": 0.75},
                    }
                },
            }), encoding="utf-8")
            with mock.patch.dict(os.environ, {"THEORYMASTER_PRICING_CACHE": str(cache)}):
                record = assess_cost(
                    provider="CodexHTTPS", model_vendor="openai", model="gpt-6-luna",
                    usage={"input_tokens": 1_000_000, "cached_input_tokens": 800_000, "output_tokens": 100_000},
                    native_cost=None,
                )
            self.assertAlmostEqual(record["amount"], 0.131)
            self.assertEqual(record["source"], "estimated")
            self.assertEqual(record["pricing_band"], "long_context")

    def test_backfill_recovers_cached_usage_from_native_codex_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = root / "pricing.json"
            cache.write_text(json.dumps({
                "schema": PRICING_SCHEMA,
                "source_url": "https://developers.openai.com/api/docs/pricing",
                "currency": "USD",
                "long_context_threshold_tokens": 272000,
                "fetched_at": "2026-09-26T00:00:00+00:00",
                "models": {"gpt-6-luna": {"short_context": {"input": 0.1, "cached_input": 0.01, "cache_write": 0.125, "output": 0.5}}},
            }), encoding="utf-8")
            sessions = root / "sessions" / "2026" / "09" / "26"
            sessions.mkdir(parents=True)
            thread_id = "thr-backfill"
            session = sessions / f"rollout-test-{thread_id}.jsonl"
            session.write_text("\n".join([
                json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {
                    "input_tokens": 100000, "cached_input_tokens": 80000, "cache_write_input_tokens": 0,
                    "output_tokens": 10000, "reasoning_output_tokens": 0,
                }}}}),
                json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t1"}}),
            ]) + "\n", encoding="utf-8")
            run_state = root / "run_state.json"
            unavailable = {"amount": None, "currency": None, "source": "unavailable"}
            run_state.write_text(json.dumps({
                "agents": {"research_lead": {
                    "thread_id": thread_id, "model": "gpt-6-luna", "provider": "CodexHTTPS", "model_vendor": "openai",
                    "cost_records": [unavailable], "cost": unavailable,
                }},
                "project_agents": {"research_lead": {"cost_records": [unavailable], "cost": unavailable}},
            }), encoding="utf-8")
            with mock.patch.dict(os.environ, {"THEORYMASTER_PRICING_CACHE": str(cache)}):
                result = backfill_run_state_costs(run_state, sessions_root=root / "sessions")
            updated = json.loads(run_state.read_text(encoding="utf-8"))
            self.assertEqual(result["changed_records"], 1)
            self.assertAlmostEqual(updated["agents"]["research_lead"]["cost"]["amount"], 0.0078)
            self.assertEqual(updated["agents"]["research_lead"]["cost"]["source"], "estimated")

    def test_backfill_aligns_completed_requests_across_multiple_threads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = root / "pricing.json"
            cache.write_text(json.dumps({
                "schema": PRICING_SCHEMA,
                "source_url": "https://developers.openai.com/api/docs/pricing",
                "currency": "USD",
                "long_context_threshold_tokens": 272000,
                "fetched_at": "2026-09-26T00:00:00+00:00",
                "models": {"gpt-6-luna": {"short_context": {
                    "input": 0.1, "cached_input": 0.01, "cache_write": 0.125, "output": 0.5,
                }}},
            }), encoding="utf-8")
            sessions = root / "sessions"
            turns = [("thread-a", "turn-a", 100, 80, 10), ("thread-b", "turn-b", 200, 100, 20)]
            for thread, turn, input_tokens, cached, output in turns:
                path = sessions / "2026" / f"rollout-{thread}.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                raw = {
                    "input_tokens": input_tokens, "output_tokens": output,
                    "cached_input_tokens": cached, "cache_write_input_tokens": 0,
                    "reasoning_output_tokens": output // 2,
                }
                path.write_text("\n".join([
                    json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": raw}}}),
                    json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": turn}}),
                ]) + "\n", encoding="utf-8")
            state_path = root / "cycle" / "run_state.json"
            state_path.parent.mkdir()
            unavailable = {"amount": None, "currency": None, "source": "unavailable", "raw_usage": {}}
            state_path.write_text(json.dumps({
                "agents": {"research_lead": {
                    "thread_id": "thread-b", "model": "gpt-6-luna", "provider": "CodexHTTPS", "model_vendor": "openai",
                    "cost_records": [dict(unavailable), dict(unavailable)],
                }},
                "project_agents": {},
            }), encoding="utf-8")
            (state_path.parent / "runtime_events.jsonl").write_text("\n".join(
                json.dumps({"event": "LLM_TURN_COMPLETED", "role": "research_lead", "thread_id": thread, "turn_id": turn})
                for thread, turn, *_ in turns
            ) + "\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"THEORYMASTER_PRICING_CACHE": str(cache)}):
                result = backfill_run_state_costs(state_path, sessions_root=sessions)
            updated = json.loads(state_path.read_text(encoding="utf-8"))
            records = updated["agents"]["research_lead"]["cost_records"]
            self.assertEqual(result["changed_records"], 2)
            self.assertEqual([item["raw_usage"]["input_tokens"] for item in records], [100, 200])
            self.assertAlmostEqual(updated["cost"]["amount"], 0.0000288)
            self.assertEqual(updated["tokens_total"], 330)


if __name__ == "__main__":
    unittest.main()
