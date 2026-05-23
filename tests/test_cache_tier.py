"""Tests for 5-minute vs 1-hour ephemeral cache tier accounting."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pricing import PRICING, calc_cost
from scanner import parse_jsonl_file
from cowork import parse_audit_file


class TestPricingTiers(unittest.TestCase):
    """Every model must have both cache_write_5m and cache_write_1h, with
    the documented 1.6x ratio between them (base*2.0 / base*1.25)."""

    def test_every_model_has_both_tiers(self):
        for model, p in PRICING.items():
            self.assertIn("cache_write_5m", p, model)
            self.assertIn("cache_write_1h", p, model)

    def test_1h_is_1_6x_of_5m(self):
        for model, p in PRICING.items():
            self.assertAlmostEqual(
                p["cache_write_1h"], p["cache_write_5m"] * 1.6,
                places=2, msg=f"{model}: 1h should be 1.6x 5m",
            )

    def test_backward_compat_cache_write_alias(self):
        """Legacy callers using `p['cache_write']` should keep getting the
        5m rate so they don't silently start double-pricing."""
        for model, p in PRICING.items():
            self.assertEqual(p["cache_write"], p["cache_write_5m"], model)


class TestCalcCostTiers(unittest.TestCase):
    def test_opus_5m_only(self):
        # 1M tokens at the 5m rate (6.25 USD/MTok for opus)
        self.assertAlmostEqual(
            calc_cost("claude-opus-4-7", 0, 0, 0, 1_000_000), 6.25, places=2,
        )

    def test_opus_1h_only(self):
        # 1M tokens at the 1h rate (6.25 * 1.6 = 10.00 USD/MTok)
        self.assertAlmostEqual(
            calc_cost("claude-opus-4-7", 0, 0, 0, 0, 1_000_000), 10.00, places=2,
        )

    def test_mixed_tiers(self):
        # 500k 5m + 500k 1h = 3.125 + 5.00 = 8.125
        c = calc_cost("claude-opus-4-7", 0, 0, 0, 500_000, 500_000)
        self.assertAlmostEqual(c, 8.125, places=3)

    def test_kwarg_default_keeps_old_signature(self):
        """Old code calling calc_cost(...) without the cache_1h arg must
        still work and not silently start charging 0 for the 1h tier."""
        c = calc_cost("claude-opus-4-7", 100, 50, 200, 300)
        # = 100*5/1e6 + 50*25/1e6 + 200*0.50/1e6 + 300*6.25/1e6 (no 1h)
        expected = (100*5 + 50*25 + 200*0.50 + 300*6.25) / 1_000_000
        self.assertAlmostEqual(c, expected, places=6)


class TestScannerSplitsCacheBreakdown(unittest.TestCase):
    """parse_jsonl_file must split cache_creation into 5m / 1h when the
    JSONL carries a `usage.cache_creation` breakdown, and treat the whole
    creation count as 5m when it doesn't."""

    def _write(self, usage):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
        f.write(json.dumps({
            "type": "assistant",
            "sessionId": "sid-test",
            "timestamp": "2026-04-25T12:00:00Z",
            "cwd": "/tmp/fixture",
            "message": {
                "id": "msg-test",
                "model": "claude-opus-4-7",
                "content": [],
                "usage": usage,
            },
        }) + "\n")
        f.close()
        return f.name

    def test_with_breakdown(self):
        usage = {
            "input_tokens": 10, "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 300,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 100,
                "ephemeral_1h_input_tokens": 200,
            },
        }
        path = self._write(usage)
        try:
            _, turns, _ = parse_jsonl_file(path)
            self.assertEqual(turns[0]["cache_creation_tokens"], 100)
            self.assertEqual(turns[0]["cache_1h_tokens"], 200)
        finally:
            os.unlink(path)

    def test_without_breakdown_treats_all_as_5m(self):
        # Older JSONL without `cache_creation` sub-object: everything is 5m.
        usage = {
            "input_tokens": 10, "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 300,
        }
        path = self._write(usage)
        try:
            _, turns, _ = parse_jsonl_file(path)
            self.assertEqual(turns[0]["cache_creation_tokens"], 300)
            self.assertEqual(turns[0]["cache_1h_tokens"], 0)
        finally:
            os.unlink(path)

    def test_all_1h_zero_5m(self):
        # The case we actually see in the wild — Cowork puts everything in 1h.
        usage = {
            "input_tokens": 10, "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 200,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 0,
                "ephemeral_1h_input_tokens": 200,
            },
        }
        path = self._write(usage)
        try:
            _, turns, _ = parse_jsonl_file(path)
            self.assertEqual(turns[0]["cache_creation_tokens"], 0)
            self.assertEqual(turns[0]["cache_1h_tokens"], 200)
        finally:
            os.unlink(path)


class TestCoworkProportionalSplit(unittest.TestCase):
    """For Cowork audit logs, result.modelUsage has per-model totals but no
    1h/5m split; the only 1h count is at result.usage. We distribute it
    proportionally by each model's share of cacheCreationInputTokens."""

    def _write(self, modelUsage, ephemeral_1h):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
        f.write(json.dumps({
            "type": "result",
            "session_id": "sid-cowork",
            "_audit_timestamp": "2026-04-25T12:00:00Z",
            "modelUsage": modelUsage,
            "usage": {"cache_creation": {
                "ephemeral_5m_input_tokens": 0,
                "ephemeral_1h_input_tokens": ephemeral_1h,
            }},
        }) + "\n")
        f.close()
        return f.name

    def test_proportional_split_70_30(self):
        path = self._write({
            "claude-opus-4-7":  {"inputTokens": 1, "outputTokens": 1, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 700},
            "claude-haiku-4-5": {"inputTokens": 1, "outputTokens": 1, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 300},
        }, ephemeral_1h=1000)
        try:
            _, turns, _ = parse_audit_file(path)
            by_model = {t["model"]: t for t in turns}
            self.assertEqual(by_model["claude-opus-4-7"]["cache_1h_tokens"], 700)
            self.assertEqual(by_model["claude-haiku-4-5"]["cache_1h_tokens"], 300)
            self.assertEqual(by_model["claude-opus-4-7"]["cache_creation_tokens"], 0)
            self.assertEqual(by_model["claude-haiku-4-5"]["cache_creation_tokens"], 0)
        finally:
            os.unlink(path)

    def test_no_1h_tokens_keeps_everything_as_5m(self):
        path = self._write({
            "claude-opus-4-7": {"inputTokens": 1, "outputTokens": 1, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 500},
        }, ephemeral_1h=0)
        try:
            _, turns, _ = parse_audit_file(path)
            self.assertEqual(turns[0]["cache_1h_tokens"], 0)
            self.assertEqual(turns[0]["cache_creation_tokens"], 500)
        finally:
            os.unlink(path)

    def test_1h_never_exceeds_model_cache_creation(self):
        # Edge case: ephemeral_1h_total > sum of cacheCreationInputTokens
        # (can happen if Anthropic's bookkeeping is slightly off). Per-model
        # cache_1h must be clamped to that model's cache_creation.
        path = self._write({
            "claude-opus-4-7": {"inputTokens": 1, "outputTokens": 1, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 100},
        }, ephemeral_1h=999)
        try:
            _, turns, _ = parse_audit_file(path)
            self.assertLessEqual(turns[0]["cache_1h_tokens"], 100)
            self.assertGreaterEqual(turns[0]["cache_creation_tokens"], 0)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
