"""Tests for time-keyed pricing history."""
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pricing


class TestPricingHistory(unittest.TestCase):
    def test_empty_history_falls_back_to_current(self):
        # Default PRICING_HISTORY is [] -> always falls back to PRICING.
        p = pricing.get_pricing_at("claude-opus-4-7", "2026-01-01")
        self.assertEqual(p, pricing.PRICING["claude-opus-4-7"])

    def test_old_pricing_used_for_old_timestamp(self):
        # Inject a fake history: prior to 2026-04-01, opus was $4/$20 instead of $5/$25
        fake_history = [
            {"effective": "2026-04-01", "pricing": {
                "claude-opus-4-7": {"input": 4.0, "output": 20.0, "cache_read": 0.4, "cache_write": 5.0}
            }},
        ]
        with mock.patch.object(pricing, "PRICING_HISTORY", fake_history):
            old = pricing.get_pricing_at("claude-opus-4-7", "2026-04-15")
            self.assertEqual(old["input"], 4.0)
            # Before any history entry -> falls back to current PRICING
            current = pricing.get_pricing_at("claude-opus-4-7", "2026-03-15")
            self.assertEqual(current["input"], pricing.PRICING["claude-opus-4-7"]["input"])

    def test_calc_cost_at_uses_historic_rate(self):
        fake_history = [
            {"effective": "2026-04-01", "pricing": {
                "claude-opus-4-7": {"input": 4.0, "output": 20.0, "cache_read": 0.4, "cache_write": 5.0}
            }},
        ]
        with mock.patch.object(pricing, "PRICING_HISTORY", fake_history):
            # 1M input on Apr 15: $4 (historic) not $5 (current)
            c = pricing.calc_cost_at("claude-opus-4-7", 1_000_000, 0, 0, 0, "2026-04-15")
            self.assertAlmostEqual(c, 4.0)

    def test_datetime_object_accepted(self):
        fake_history = [
            {"effective": "2026-04-01", "pricing": {
                "claude-opus-4-7": {"input": 4.0, "output": 20.0, "cache_read": 0.4, "cache_write": 5.0}
            }},
        ]
        with mock.patch.object(pricing, "PRICING_HISTORY", fake_history):
            p = pricing.get_pricing_at("claude-opus-4-7", datetime(2026, 5, 1))
            self.assertEqual(p["input"], 4.0)

    def test_no_timestamp_falls_back_to_current(self):
        # Calling without a timestamp = use current pricing
        self.assertEqual(
            pricing.get_pricing_at("claude-opus-4-7", None),
            pricing.PRICING["claude-opus-4-7"],
        )


if __name__ == "__main__":
    unittest.main()
