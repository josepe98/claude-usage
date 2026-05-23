"""Tests for period-delta UI wiring (HTML template + helpers)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


class TestPeriodDelta(unittest.TestCase):
    def test_html_has_delta_helpers(self):
        self.assertIn("_prevWindow", dashboard.HTML_TEMPLATE)
        self.assertIn("_deltaBadge", dashboard.HTML_TEMPLATE)

    def test_html_has_delta_classes(self):
        self.assertIn(".delta", dashboard.HTML_TEMPLATE)
        self.assertIn(".delta-up", dashboard.HTML_TEMPLATE)
        self.assertIn(".delta-down", dashboard.HTML_TEMPLATE)

    def test_renderStats_takes_prev_arg(self):
        # New signature must accept (t, prev) — old (t) would still work
        # because prev defaults to null/undefined, but we want callers to
        # actually pass prev.
        self.assertIn("function renderStats(t, prev)", dashboard.HTML_TEMPLATE)
        # After A/B compare refactor, applyFilter routes through computePeriod
        # objects (pa.totals / pa.prevTotals) instead of locals; accept either.
        self.assertTrue(
            "renderStats(totals, prevTotals)" in dashboard.HTML_TEMPLATE
            or "renderStats(pa.totals, pa.prevTotals)" in dashboard.HTML_TEMPLATE,
            "renderStats must be called with current + previous totals",
        )

    def test_stats_array_includes_delta(self):
        # Each stat object should declare a delta field
        self.assertIn("delta: prev && _deltaBadge(t.sessions", dashboard.HTML_TEMPLATE)
        self.assertIn("delta: prev && _deltaBadge(t.cost", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
