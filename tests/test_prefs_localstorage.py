"""Tests for localStorage preference persistence."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


class TestLocalStoragePrefs(unittest.TestCase):
    def test_storage_key_present(self):
        self.assertIn("claude-usage-prefs/v1", dashboard.HTML_TEMPLATE)

    def test_load_save_reset_helpers(self):
        self.assertIn("_loadPrefs", dashboard.HTML_TEMPLATE)
        self.assertIn("_savePrefs", dashboard.HTML_TEMPLATE)
        self.assertIn("_resetPrefs", dashboard.HTML_TEMPLATE)

    def test_setRange_writes_through(self):
        # When the user clicks a range button, the new range must persist.
        self.assertIn("_savePrefs({ range: range })", dashboard.HTML_TEMPLATE)

    def test_setHourlyTZ_writes_through(self):
        self.assertIn("_savePrefs({ hourlyTZ: mode })", dashboard.HTML_TEMPLATE)

    def test_models_write_through(self):
        self.assertIn("models: Array.from(selectedModels)", dashboard.HTML_TEMPLATE)

    def test_reset_button_present(self):
        self.assertIn("Reset prefs", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
