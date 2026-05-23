"""Tests for the A/B period-comparison mode (toggle, helpers, renderers, prefs).

The compare mode is a pure client-side feature — `/api/data` already returns
the full history, and the JS in `dashboard.HTML_TEMPLATE` filters it twice
when the mode is on. These tests assert the wiring is in place without
spinning up a browser: they look for the HTML scaffolding, the JS state
variables, the helper / renderer function definitions, and the localStorage
keys used to persist user preference.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


class TestABCompareUI(unittest.TestCase):
    def test_compare_toggle_present(self):
        """The filter bar must include an on/off compare toggle button."""
        self.assertIn('id="compare-toggle"', dashboard.HTML_TEMPLATE)
        self.assertIn('onclick="toggleCompareMode()"', dashboard.HTML_TEMPLATE)

    def test_dual_pill_rows_present(self):
        """Compare row exposes one set of pills per period."""
        self.assertIn('id="compare-row"', dashboard.HTML_TEMPLATE)
        self.assertIn('data-rangea="30d"', dashboard.HTML_TEMPLATE)
        self.assertIn('data-rangeb="30d"', dashboard.HTML_TEMPLATE)
        # All 8 ranges should be available on both A and B rows.
        for r in ("today", "week", "month", "prev-month", "7d", "30d", "90d", "all"):
            self.assertIn(f'data-rangea="{r}"', dashboard.HTML_TEMPLATE)
            self.assertIn(f'data-rangeb="{r}"', dashboard.HTML_TEMPLATE)

    def test_compare_row_hidden_by_default(self):
        """The compare row gets the .visible class only after the toggle flips on."""
        # CSS rule must default it to display:none and reveal it with .visible
        self.assertRegex(
            dashboard.HTML_TEMPLATE,
            r"\.compare-row\s*\{[^}]*display:\s*none",
        )
        self.assertRegex(
            dashboard.HTML_TEMPLATE,
            r"\.compare-row\.visible\s*\{[^}]*display:\s*flex",
        )

    def test_ab_color_tags_defined(self):
        """A and B use distinct accent colours so charts/tables stay readable."""
        self.assertIn('.ab-tag-a', dashboard.HTML_TEMPLATE)
        self.assertIn('.ab-tag-b', dashboard.HTML_TEMPLATE)
        self.assertIn('period-a', dashboard.HTML_TEMPLATE)
        self.assertIn('period-b', dashboard.HTML_TEMPLATE)


class TestABCompareJS(unittest.TestCase):
    def test_state_vars_declared(self):
        """compareMode + selectedRangeB must be module-level state, hydrated from prefs."""
        self.assertIn("let compareMode", dashboard.HTML_TEMPLATE)
        self.assertIn("let selectedRangeB", dashboard.HTML_TEMPLATE)
        self.assertIn("_loadPrefs().compareMode", dashboard.HTML_TEMPLATE)
        self.assertIn("_loadPrefs().rangeB", dashboard.HTML_TEMPLATE)

    def test_setters_persist_to_localstorage(self):
        """Toggling mode + picking Period B must write through to prefs."""
        self.assertIn("_savePrefs({ compareMode: compareMode })", dashboard.HTML_TEMPLATE)
        self.assertIn("_savePrefs({ rangeB: range })", dashboard.HTML_TEMPLATE)

    def test_compute_period_helper_exists(self):
        """The extracted helper takes a range and returns totals/breakdowns."""
        self.assertIn("function computePeriod(range)", dashboard.HTML_TEMPLATE)
        # Must be called by applyFilter for each window
        self.assertIn("computePeriod(selectedRange)", dashboard.HTML_TEMPLATE)
        self.assertIn("computePeriod(selectedRangeB)", dashboard.HTML_TEMPLATE)

    def test_dual_renderers_present(self):
        """Stats/charts/tables get parallel *AB renderers for compare mode."""
        for name in ("renderStatsAB", "renderDailyChartAB",
                     "renderModelCostTableAB", "renderProjectCostTableAB"):
            self.assertIn("function " + name, dashboard.HTML_TEMPLATE)

    def test_delta_pct_helper(self):
        """A small helper formats the Δ% badge consistently across renderers."""
        self.assertIn("function _abPctLabel", dashboard.HTML_TEMPLATE)
        # Must be used by both stats and the cost tables
        self.assertGreaterEqual(dashboard.HTML_TEMPLATE.count("_abPctLabel("), 3)

    def test_session_table_colour_codes_by_period(self):
        """Each row picks period-a / period-b / period-ab from its _abClass tag."""
        self.assertIn("s._abClass", dashboard.HTML_TEMPLATE)
        # CSS for each class is present (left-edge accent strip)
        self.assertIn(".session-row.period-a", dashboard.HTML_TEMPLATE)
        self.assertIn(".session-row.period-b", dashboard.HTML_TEMPLATE)
        self.assertIn(".session-row.period-ab", dashboard.HTML_TEMPLATE)


class TestABCompareWiring(unittest.TestCase):
    def test_apply_filter_branches_on_compare_mode(self):
        """applyFilter calls *AB renderers only when compareMode is true."""
        # Extract the applyFilter body — relies on the same brace-matching
        # helper that test_dashboard.py uses.
        signature = "function applyFilter()"
        start = dashboard.HTML_TEMPLATE.index(signature)
        brace_start = dashboard.HTML_TEMPLATE.index("{", start)
        depth = 0
        end = None
        for idx in range(brace_start, len(dashboard.HTML_TEMPLATE)):
            ch = dashboard.HTML_TEMPLATE[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = idx + 1
                    break
        self.assertIsNotNone(end)
        body = dashboard.HTML_TEMPLATE[start:end]

        # compareMode controls which renderer family is invoked.
        self.assertIn("compareMode", body)
        self.assertIn("renderStatsAB", body)
        self.assertIn("renderStats(pa.totals", body)  # non-compare path still wired

    def test_compare_ui_synced_on_first_load(self):
        """updateCompareUI runs as part of first-load bootstrap."""
        self.assertIn("function updateCompareUI", dashboard.HTML_TEMPLATE)
        self.assertIn("updateCompareUI()", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
