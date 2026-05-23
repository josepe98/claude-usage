"""Tests for billable vs non-billable transparency."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


class TestBillableBadge(unittest.TestCase):
    def test_html_has_cost_sub_helper(self):
        self.assertIn("_costSub", dashboard.HTML_TEMPLATE)
        self.assertIn("_costTitle", dashboard.HTML_TEMPLATE)

    def test_totals_track_nonbillable(self):
        # applyFilter must compute the count + list of non-billable models
        self.assertIn("nonBillableModels", dashboard.HTML_TEMPLATE)
        self.assertIn("nonBillableCount", dashboard.HTML_TEMPLATE)

    def test_excluded_models_message(self):
        # The sub-line should mention excluded models when count > 0
        self.assertIn("model' + (n === 1 ? '' : 's') + ' excluded", dashboard.HTML_TEMPLATE)

    def test_tooltip_on_stat_card(self):
        # title attribute is rendered on the stat-card div for hover
        self.assertIn('title="${s.title ? esc(s.title) : \'\'}"', dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
