"""Tests for session tags."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


class TestTagsLoadSave(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tags.json"
            with mock.patch.object(dashboard, "TAGS_CONFIG_PATH", path):
                dashboard._save_tags({"abc12345": ["work", "urgent"]})
                self.assertEqual(dashboard._load_tags(), {"abc12345": ["work", "urgent"]})

    def test_missing_file_returns_empty(self):
        with mock.patch.object(dashboard, "TAGS_CONFIG_PATH", Path("/tmp/__nonexistent_xyzzy__.json")):
            self.assertEqual(dashboard._load_tags(), {})


class TestTagsUI(unittest.TestCase):
    def test_render_tags_helper_present(self):
        self.assertIn("_renderTags", dashboard.HTML_TEMPLATE)
        self.assertIn("_promptTags", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
