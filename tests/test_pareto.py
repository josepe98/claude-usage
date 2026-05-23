"""Tests for cost concentration / Pareto."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


class TestCostConcentration(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(dashboard._cost_concentration([]))

    def test_zero_total_returns_none(self):
        self.assertIsNone(dashboard._cost_concentration([{"cost": 0}]))

    def test_pareto_distribution(self):
        # 100 sessions, 5 of them carry 90% of cost
        big = [{"session_id": f"big{i}", "project": "p", "cost": 18.0} for i in range(5)]
        small = [{"session_id": f"sm{i}", "project": "p", "cost": 0.1} for i in range(95)]
        c = dashboard._cost_concentration(big + small, top_n=5)
        self.assertEqual(c["top_n"], 5)
        self.assertGreater(c["pct"], 90)
        self.assertEqual(len(c["top_sessions"]), 5)
        self.assertTrue(all(s["session_id"].startswith("big") for s in c["top_sessions"]))

    def test_html_has_pareto_card(self):
        self.assertIn('id="pareto-card"', dashboard.HTML_TEMPLATE)
        self.assertIn("renderPareto", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
