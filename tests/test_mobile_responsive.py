"""Mobile responsive CSS smoke tests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


class TestMobileResponsive(unittest.TestCase):
    def test_640px_media_query_present(self):
        self.assertIn("@media (max-width: 640px)", dashboard.HTML_TEMPLATE)

    def test_filter_bar_stacks_vertically(self):
        # filter-bar uses column layout so model pills and range buttons
        # are always on separate rows (not just at mobile breakpoints)
        self.assertIn("flex-direction: column", dashboard.HTML_TEMPLATE)
        self.assertIn("filter-row", dashboard.HTML_TEMPLATE)

    def test_stat_grid_collapses_to_2_columns(self):
        self.assertIn("#stats-row { grid-template-columns: repeat(2, 1fr)", dashboard.HTML_TEMPLATE)

    def test_tables_scroll_horizontally(self):
        self.assertIn(".table-card { overflow-x: auto", dashboard.HTML_TEMPLATE)

    def test_hide_mobile_helper_class(self):
        # Used to selectively hide low-priority columns on small screens
        self.assertIn(".hide-mobile,", dashboard.HTML_TEMPLATE)
        self.assertIn("td.hide-mobile { display: none", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
