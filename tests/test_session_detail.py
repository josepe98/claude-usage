"""Tests for session drill-down endpoint."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner
import dashboard


class TestSessionDetail(unittest.TestCase):
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
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_timeline_for_prefix(self):
        d = dashboard._session_detail("abc12345")
        self.assertNotIn("error", d)
        self.assertEqual(d["turn_count_actual"], 2)
        self.assertEqual(len(d["timeline"]), 2)
        self.assertEqual(d["timeline"][0]["tool"], "Bash")
        self.assertEqual(d["timeline"][1]["tool"], "Read")

    def test_cumulative_cost_increases(self):
        d = dashboard._session_detail("abc12345")
        cums = [t["cum_cost"] for t in d["timeline"]]
        self.assertLess(cums[0], cums[1])

    def test_tools_breakdown(self):
        d = dashboard._session_detail("abc12345")
        tools = {t["tool"]: t["count"] for t in d["tools_breakdown"]}
        self.assertEqual(tools.get("Bash"), 1)
        self.assertEqual(tools.get("Read"), 1)

    def test_unknown_prefix_returns_error(self):
        d = dashboard._session_detail("xxxxxxxx")
        self.assertIn("error", d)

    def test_html_has_modal(self):
        self.assertIn('id="session-modal"', dashboard.HTML_TEMPLATE)
        self.assertIn("_openSession", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
