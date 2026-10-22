import unittest

from theory_master.observability.costs import PricingRule, PricingRuleAdapter, aggregate_cost, assess_cost


class CostAccountingTest(unittest.TestCase):
    def test_native_cost_has_priority(self):
        adapter = PricingRuleAdapter(PricingRule("test", "2026-09-21", "CNY", input_per_million=1))
        record = adapter.assess(provider="p", model_vendor="v", model="m", usage={"input_tokens": 1_000_000}, native_cost={"amount": 3.2, "currency": "CNY"})
        self.assertEqual(record["source"], "provider_reported")
        self.assertEqual(record["amount"], 3.2)

    def test_calculated_cost_keeps_pricing_snapshot(self):
        adapter = PricingRuleAdapter(PricingRule("p-2026-09", "2026-09-01", "CNY", input_per_million=2, output_per_million=4))
        record = adapter.assess(provider="p", model_vendor="v", model="m", usage={"input_tokens": 1_000_000, "output_tokens": 500_000}, native_cost=None)
        self.assertEqual(record["amount"], 4)
        self.assertEqual(record["pricing_rule_id"], "p-2026-09")

    def test_unonboarded_provider_is_explicitly_unavailable(self):
        record = assess_cost(provider="unknown", model_vendor="v", model="m", usage={"input_tokens": 10}, native_cost=None)
        self.assertEqual(record["source"], "unavailable")
        self.assertIsNone(record["amount"])

    def test_deepseek_uses_native_cost_adapter(self):
        record = assess_cost(
            provider="DeepSeek",
            model_vendor="openai",
            model="gpt-5.6-luna",
            usage={"input_tokens": 10},
            native_cost={"amount": 1.5, "currency": "CNY"},
        )
        self.assertEqual(record["source"], "provider_reported")
        self.assertEqual(record["amount"], 1.5)
        self.assertEqual(record["provider"], "deepseek")

    def test_aggregate_does_not_mix_currencies(self):
        total = aggregate_cost([{"amount": 1, "currency": "CNY", "source": "calculated"}, {"amount": 1, "currency": "USD", "source": "calculated"}])
        self.assertIsNone(total["amount"])
