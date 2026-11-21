"""Current-generation model pricing (Fable/Mythos 5.x, Opus/Sonnet/Haiku 5.x)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner
from pricing import PRICING, calc_cost, get_pricing


class FablePricingTests(unittest.TestCase):
    # Rates from platform.claude.com/docs/en/about-claude/pricing, 2026-11-20.
    def test_fable_rates(self):
        rates = lambda m: tuple(get_pricing(m)[k] for k in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h"))
        self.assertEqual(rates("claude-fable-5-2"), (8.0, 40.0, 0.20, 10.0, 16.0))
        self.assertEqual(rates("claude-fable-5-1"), (10.0, 50.0, 0.25, 12.5, 20.0))
        self.assertEqual(rates("claude-fable-5"), (10.0, 50.0, 1.00, 12.5, 20.0))

    def test_fable_cost_uses_model_specific_cache_read(self):
        # 1M cache-read tokens: 5.2 is 0.025x input, 5 is 0.1x.
        self.assertAlmostEqual(calc_cost("claude-fable-5-2", 0, 0, 1_000_000, 0), 0.20)
        self.assertAlmostEqual(calc_cost("claude-fable-5", 0, 0, 1_000_000, 0), 1.00)

    def test_mythos_matches_fable(self):
        for v in ("5", "5-1", "5-2"):
            self.assertEqual(get_pricing(f"claude-mythos-{v}"), get_pricing(f"claude-fable-{v}"))


class LookupTests(unittest.TestCase):
    def test_longest_prefix_wins(self):
        self.assertEqual(get_pricing("claude-opus-5-5-20261001"), PRICING["claude-opus-5-5"])
        self.assertEqual(get_pricing("claude-fable-5-1-20260907"), PRICING["claude-fable-5-1"])
        self.assertEqual(get_pricing("claude-opus-5-20260601"), PRICING["claude-opus-5"])

    def test_current_models_have_exact_entries(self):
        self.assertEqual(get_pricing("claude-opus-5-5")["input"], 4.0)
        self.assertEqual(get_pricing("claude-opus-5-5")["cache_read"], 0.20)
        self.assertEqual(get_pricing("claude-sonnet-5-5")["input"], 2.0)
        self.assertEqual(get_pricing("claude-haiku-5-5")["input"], 0.10)

    def test_non_anthropic_models_unpriced(self):
        self.assertIsNone(get_pricing("minimax-m3"))
        self.assertIsNone(get_pricing("gemma4:12b"))


class ModelPriorityTests(unittest.TestCase):
    def test_fable_outranks_opus(self):
        # A Fable session's Opus subagent turn must not relabel the session.
        self.assertGreater(scanner._model_priority("claude-fable-5-2"), scanner._model_priority("claude-opus-5-5"))
        self.assertEqual(scanner._model_priority("claude-mythos-5-2"), scanner._model_priority("claude-fable-5-2"))


if __name__ == "__main__":
    unittest.main()
