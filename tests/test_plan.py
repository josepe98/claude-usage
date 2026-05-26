"""Tests for plan tier comparison."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


class TestPlanComparison(unittest.TestCase):
    def test_low_spend_recommends_pro(self):
        r = dashboard._plan_comparison(50)
        self.assertEqual(r["recommended"], "Pro")

    def test_medium_spend_recommends_max5x(self):
        r = dashboard._plan_comparison(300)
        self.assertEqual(r["recommended"], "Max-5x")

    def test_high_spend_recommends_max20x(self):
        r = dashboard._plan_comparison(2000)
        self.assertEqual(r["recommended"], "Max-20x")

    def test_zero_spend_still_recommends_something(self):
        r = dashboard._plan_comparison(0)
        # Should pick the cheapest (Pro) at $0 — anything > 0 included
        self.assertEqual(r["recommended"], "Pro")

    def test_html_has_plan_card(self):
        self.assertIn('id="plan-card"', dashboard.HTML_TEMPLATE)
        self.assertIn("renderPlanCard", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
