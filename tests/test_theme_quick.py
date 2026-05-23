"""Tests for the theme quick-switch dropdown."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


class TestThemeQuickSwitch(unittest.TestCase):
    def test_dropdown_present(self):
        self.assertIn('id="theme-quick"', dashboard.HTML_TEMPLATE)

    def test_populate_fn_present(self):
        self.assertIn("_populateThemeDropdown", dashboard.HTML_TEMPLATE)

    def test_change_handler_calls_setTheme(self):
        self.assertIn("setTheme(t.css, t.id)", dashboard.HTML_TEMPLATE)

    def test_populated_on_load(self):
        self.assertIn("loadData(); _populateThemeDropdown();", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
