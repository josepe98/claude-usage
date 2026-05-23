"""Tests for sessions search input."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


class TestSearch(unittest.TestCase):
    def test_search_input_in_html(self):
        self.assertIn('id="sessions-search"', dashboard.HTML_TEMPLATE)
        self.assertIn("_onSearchInput", dashboard.HTML_TEMPLATE)

    def test_match_helper_present(self):
        self.assertIn("_matchesSearch", dashboard.HTML_TEMPLATE)

    def test_search_wired_into_filtered_sessions(self):
        # The session filter must include _matchesSearch so typing
        # narrows down the table.
        self.assertIn("_matchesSearch(s) &&", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
