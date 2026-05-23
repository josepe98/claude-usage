"""Tests for budget watchdog."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


class TestBudgetStatus(unittest.TestCase):
    def test_unconfigured_when_no_file(self):
        with mock.patch.object(dashboard, "BUDGET_CONFIG_PATH", Path("/tmp/nonexistent-xyzzy.json")):
            s = dashboard._budget_status(5.0)
        self.assertFalse(s["configured"])

    def test_status_below_threshold(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write(json.dumps({"monthly_usd": 100}))
            path = Path(f.name)
        with mock.patch.object(dashboard, "BUDGET_CONFIG_PATH", path):
            s = dashboard._budget_status(50.0)
        self.assertTrue(s["configured"])
        self.assertEqual(s["monthly_usd"], 100)
        self.assertEqual(s["pct"], 0.5)
        self.assertIsNone(s["crossed_threshold"])
        os.unlink(path)

    def test_status_80pct_threshold(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write(json.dumps({"monthly_usd": 100}))
            path = Path(f.name)
        with mock.patch.object(dashboard, "BUDGET_CONFIG_PATH", path):
            s = dashboard._budget_status(82.5)
        self.assertEqual(s["crossed_threshold"], 0.8)
        os.unlink(path)

    def test_status_over_budget(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write(json.dumps({"monthly_usd": 50}))
            path = Path(f.name)
        with mock.patch.object(dashboard, "BUDGET_CONFIG_PATH", path):
            s = dashboard._budget_status(75.0)
        self.assertEqual(s["crossed_threshold"], 1.0)
        self.assertEqual(s["pct"], 1.5)
        os.unlink(path)

    def test_zero_or_negative_cap_ignored(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write(json.dumps({"monthly_usd": 0}))
            path = Path(f.name)
        with mock.patch.object(dashboard, "BUDGET_CONFIG_PATH", path):
            s = dashboard._budget_status(50)
        self.assertFalse(s["configured"])
        os.unlink(path)


class TestBudgetSaveLoad(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "budget.json"
            with mock.patch.object(dashboard, "BUDGET_CONFIG_PATH", path):
                dashboard._save_budget({"monthly_usd": 42})
                self.assertEqual(dashboard._load_budget(), {"monthly_usd": 42})


class TestBudgetUI(unittest.TestCase):
    def test_html_has_budget_bar(self):
        self.assertIn('id="budget-bar"', dashboard.HTML_TEMPLATE)
        self.assertIn("renderBudget", dashboard.HTML_TEMPLATE)
        self.assertIn("_editBudget", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
