"""Tests for the Cost-by-Branch breakdown helper + dashboard wiring."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


def _session(project, branch, **overrides):
    base = {
        "project": project,
        "branch": branch,
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_creation": 0,
        "turns": 0,
        "cost": 0.0,
        "model": "claude-sonnet-4",
    }
    base.update(overrides)
    return base


class TestCostByBranchHelper(unittest.TestCase):
    def test_empty_returns_empty_list(self):
        self.assertEqual(dashboard._cost_by_branch([]), [])
        self.assertEqual(dashboard._cost_by_branch(None), [])

    def test_aggregates_by_project_and_branch_tuple(self):
        sessions = [
            _session("repo-a", "main", input=100, output=50, turns=3, cost=1.0),
            _session("repo-a", "main", input=200, output=80, turns=2, cost=2.0),
            _session("repo-a", "feat/x", input=10, output=5, turns=1, cost=0.25),
            _session("repo-b", "main", input=1, output=1, turns=1, cost=0.05),
        ]
        rows = dashboard._cost_by_branch(sessions)
        self.assertEqual(len(rows), 3)
        keyed = {(r["project"], r["branch"]): r for r in rows}

        main_a = keyed[("repo-a", "main")]
        self.assertEqual(main_a["sessions"], 2)
        self.assertEqual(main_a["turns"], 5)
        self.assertEqual(main_a["input"], 300)
        self.assertEqual(main_a["output"], 130)
        self.assertAlmostEqual(main_a["cost"], 3.0)

        self.assertEqual(keyed[("repo-a", "feat/x")]["sessions"], 1)
        self.assertEqual(keyed[("repo-b", "main")]["sessions"], 1)

    def test_empty_branch_labelled_default(self):
        sessions = [
            _session("repo-a", "", input=5, output=5, turns=1, cost=0.10),
            _session("repo-a", None, input=2, output=2, turns=1, cost=0.05),
            _session("repo-a", "   ", input=1, output=1, turns=1, cost=0.02),
            _session("repo-a", "main", input=20, output=10, turns=1, cost=0.30),
        ]
        rows = dashboard._cost_by_branch(sessions)
        keyed = {(r["project"], r["branch"]): r for r in rows}
        self.assertIn(("repo-a", "(default)"), keyed)
        self.assertIn(("repo-a", "main"), keyed)
        # blank / None / whitespace all collapse into the same "(default)" bucket
        self.assertEqual(keyed[("repo-a", "(default)")]["sessions"], 3)
        self.assertEqual(keyed[("repo-a", "(default)")]["input"], 8)
        self.assertEqual(keyed[("repo-a", "(default)")]["output"], 8)
        self.assertAlmostEqual(keyed[("repo-a", "(default)")]["cost"], 0.17)

    def test_cache_columns_aggregated(self):
        sessions = [
            _session("p", "main", cache_read=100, cache_creation=50, cost=0.5),
            _session("p", "main", cache_read=200, cache_creation=25, cost=0.3),
            _session("p", "dev",  cache_read=10,  cache_creation=5,  cost=0.1),
        ]
        rows = dashboard._cost_by_branch(sessions)
        keyed = {(r["project"], r["branch"]): r for r in rows}
        self.assertEqual(keyed[("p", "main")]["cache_read"], 300)
        self.assertEqual(keyed[("p", "main")]["cache_creation"], 75)
        self.assertEqual(keyed[("p", "dev")]["cache_read"], 10)
        self.assertEqual(keyed[("p", "dev")]["cache_creation"], 5)

    def test_sorted_by_cost_desc(self):
        sessions = [
            _session("p", "cheap", cost=0.01),
            _session("p", "big",   cost=99.9),
            _session("p", "mid",   cost=10.0),
        ]
        rows = dashboard._cost_by_branch(sessions)
        self.assertEqual([r["branch"] for r in rows], ["big", "mid", "cheap"])


class TestCostByBranchUI(unittest.TestCase):
    def test_html_has_card_and_js_wiring(self):
        html = dashboard.HTML_TEMPLATE
        # Card + table body
        self.assertIn('id="cost-by-branch-card"', html)
        self.assertIn('id="branch-only-cost-body"', html)
        # All 9 columns sortable
        for col in ("project", "branch", "sessions", "turns", "input",
                    "output", "cache_read", "cache_creation", "cost"):
            self.assertIn(f"setBranchOnlySort('{col}')", html)
            self.assertIn(f'id="cbsort-{col}"', html)
        # JS helpers
        self.assertIn("function _costByBranch", html)
        self.assertIn("function sortBranchOnly", html)
        self.assertIn("function renderBranchOnlyCostTable", html)
        self.assertIn("function exportBranchCSV", html)
        # CSV export reuses downloadCSV
        self.assertIn("downloadCSV('branches'", html)
        # Wired into applyFilter pipeline
        self.assertIn("renderBranchOnlyCostTable(lastByBranch.slice(0, 20))", html)


if __name__ == "__main__":
    unittest.main()
