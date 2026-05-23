"""Tests for per-project budgets."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


class TestPerProjectBudgets(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pb.json"
            with mock.patch.object(dashboard, "PROJECT_BUDGETS_PATH", path):
                dashboard._save_project_budgets({"myproj": 50.0})
                self.assertEqual(dashboard._load_project_budgets(), {"myproj": 50.0})

    def test_status_ok_warn_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pb.json"
            with mock.patch.object(dashboard, "PROJECT_BUDGETS_PATH", path):
                dashboard._save_project_budgets({
                    "low": 100, "mid": 100, "high": 100,
                })
                s = dashboard._project_budget_status({
                    "low": 50,   # 50% -> ok
                    "mid": 85,   # 85% -> warn
                    "high": 120, # 120% -> over
                })
                self.assertEqual(s["low"]["state"], "ok")
                self.assertEqual(s["mid"]["state"], "warn")
                self.assertEqual(s["high"]["state"], "over")

    def test_missing_project_not_in_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pb.json"
            with mock.patch.object(dashboard, "PROJECT_BUDGETS_PATH", path):
                dashboard._save_project_budgets({"x": 50})
                # Project with no MTD spend yet — still shows in status
                s = dashboard._project_budget_status({})
                self.assertIn("x", s)
                self.assertEqual(s["x"]["state"], "ok")

    def test_zero_or_neg_cap_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pb.json"
            with mock.patch.object(dashboard, "PROJECT_BUDGETS_PATH", path):
                dashboard._save_project_budgets({"x": 0, "y": -5, "z": 50})
                s = dashboard._project_budget_status({"x": 1, "y": 1, "z": 25})
                self.assertNotIn("x", s)
                self.assertNotIn("y", s)
                self.assertIn("z", s)


if __name__ == "__main__":
    unittest.main()
