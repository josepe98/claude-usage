"""Tests for the augmented session drill-down (cost + cumulative cost)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner
import dashboard


class TestSessionDetailCostFields(unittest.TestCase):
    """The inline session-detail card now includes per-turn cost,
    cumulative cost across turns, and a top-level total_cost."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "u.db"
        self._orig = dashboard.DB_PATH
        dashboard.DB_PATH = self.db
        conn = scanner.get_db(self.db)
        scanner.init_db(conn)
        scanner.upsert_sessions(conn, [{
            "session_id": "abc12345-foo", "project_name": "myproj",
            "first_timestamp": "2026-04-01T10:00:00Z",
            "last_timestamp":  "2026-04-01T11:00:00Z",
            "git_branch": "main", "model": "claude-opus-4-7", "turn_count": 2,
            "total_input_tokens": 100, "total_output_tokens": 50,
            "total_cache_read": 0, "total_cache_creation": 0,
        }])
        scanner.insert_turns(conn, [
            {"session_id": "abc12345-foo", "timestamp": "2026-04-01T10:30:00Z",
             "model": "claude-opus-4-7", "input_tokens": 60, "output_tokens": 30,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": "Bash", "cwd": "/p", "message_id": "m1"},
            {"session_id": "abc12345-foo", "timestamp": "2026-04-01T10:45:00Z",
             "model": "claude-opus-4-7", "input_tokens": 40, "output_tokens": 20,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": "Read", "cwd": "/p", "message_id": "m2"},
        ])
        conn.commit()
        conn.close()

    def tearDown(self):
        dashboard.DB_PATH = self._orig
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_turn_history_has_cost_and_cum_cost(self):
        d = dashboard.get_session_detail("abc12345-foo")
        self.assertNotIn("error", d)
        self.assertEqual(len(d["turn_history"]), 2)
        for turn in d["turn_history"]:
            self.assertIn("cost", turn)
            self.assertIn("cum_cost", turn)

    def test_cumulative_cost_is_running_sum(self):
        d = dashboard.get_session_detail("abc12345-foo")
        t0, t1 = d["turn_history"]
        self.assertAlmostEqual(t0["cum_cost"], t0["cost"], places=10)
        self.assertAlmostEqual(t1["cum_cost"], t0["cost"] + t1["cost"], places=10)
        self.assertGreater(t1["cum_cost"], t0["cum_cost"])

    def test_total_cost_matches_sum_of_turn_costs(self):
        d = dashboard.get_session_detail("abc12345-foo")
        self.assertIn("total_cost", d)
        expected = sum(t["cost"] for t in d["turn_history"])
        self.assertAlmostEqual(d["total_cost"], expected, places=10)

    def test_existing_fields_still_present(self):
        d = dashboard.get_session_detail("abc12345-foo")
        self.assertIn("tool_usage", d)
        self.assertIn("cwd_usage", d)
        self.assertIn("turn_history", d)
        tools = {t["tool_name"] for t in d["tool_usage"]}
        self.assertEqual(tools, {"Bash", "Read"})

    def test_modal_dom_and_helpers_are_gone(self):
        self.assertNotIn('id="session-modal"', dashboard.HTML_TEMPLATE)
        self.assertNotIn("_openSession", dashboard.HTML_TEMPLATE)
        self.assertNotIn("_renderSessionModal", dashboard.HTML_TEMPLATE)
        self.assertNotIn("_closeSessionModal", dashboard.HTML_TEMPLATE)
        self.assertNotIn("/api/session-detail", dashboard.HTML_TEMPLATE)
        self.assertFalse(hasattr(dashboard, "_session_detail"))


if __name__ == "__main__":
    unittest.main()
