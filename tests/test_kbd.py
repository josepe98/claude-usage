"""Keyboard shortcuts smoke tests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


class TestKeyboardShortcuts(unittest.TestCase):
    def test_keydown_listener_present(self):
        self.assertIn("addEventListener('keydown'", dashboard.HTML_TEMPLATE)

    def test_slash_focuses_search(self):
        self.assertIn("sessions-search", dashboard.HTML_TEMPLATE)
        self.assertIn("case '/'", dashboard.HTML_TEMPLATE)

    def test_r_triggers_rescan(self):
        self.assertIn("rescan-btn", dashboard.HTML_TEMPLATE)
        self.assertIn("case 'r'", dashboard.HTML_TEMPLATE)

    def test_help_dialog(self):
        self.assertIn("case '?'", dashboard.HTML_TEMPLATE)

    def test_ignores_input_focus(self):
        # Must not fire shortcuts while typing in an input
        self.assertIn("INPUT|TEXTAREA|SELECT", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
