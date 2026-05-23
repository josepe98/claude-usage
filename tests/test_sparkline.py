"""Tests for inline session sparklines."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner
import dashboard


class TestSparkline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "u.db"
        conn = scanner.get_db(self.db)
        scanner.init_db(conn)
        scanner.upsert_sessions(conn, [{
            "session_id": "abc", "project_name": "p",
            "first_timestamp": "2026-04-01T10:00:00Z",
            "last_timestamp":  "2026-04-01T10:30:00Z",
            "git_branch": "", "model": "claude-opus-4-7", "turn_count": 3,
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cache_read": 0, "total_cache_creation": 0,
        }])
        scanner.insert_turns(conn, [
            {"session_id": "abc", "timestamp": "2026-04-01T10:00:00Z",
             "model": "claude-opus-4-7", "input_tokens": 1, "output_tokens": 1,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": None, "cwd": "/p", "message_id": "m1"},
            {"session_id": "abc", "timestamp": "2026-04-01T10:15:00Z",
             "model": "claude-opus-4-7", "input_tokens": 1, "output_tokens": 1,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": None, "cwd": "/p", "message_id": "m2"},
            {"session_id": "abc", "timestamp": "2026-04-01T10:30:00Z",
             "model": "claude-opus-4-7", "input_tokens": 1, "output_tokens": 1,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": None, "cwd": "/p", "message_id": "m3"},
        ])
        conn.commit()
        self.conn = conn

    def tearDown(self):
        self.conn.close()
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_30_bins(self):
        s = dashboard._session_sparklines(self.conn, ["abc"])
        self.assertIn("abc", s)
        self.assertEqual(len(s["abc"]), 30)
        # 3 turns distributed across the span
        self.assertEqual(sum(s["abc"]), 3)

    def test_single_turn_session(self):
        # A session with exactly one turn collapses to [1]
        single = scanner.get_db(self.db)
        scanner.upsert_sessions(single, [{
            "session_id": "def", "project_name": "p",
            "first_timestamp": "2026-04-02T10:00:00Z",
            "last_timestamp":  "2026-04-02T10:00:00Z",
            "git_branch": "", "model": "claude-opus-4-7", "turn_count": 1,
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cache_read": 0, "total_cache_creation": 0,
        }])
        scanner.insert_turns(single, [
            {"session_id": "def", "timestamp": "2026-04-02T10:00:00Z",
             "model": "claude-opus-4-7", "input_tokens": 1, "output_tokens": 1,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": None, "cwd": "/p", "message_id": "x"},
        ])
        single.commit()
        s = dashboard._session_sparklines(single, ["def"])
        single.close()
        self.assertEqual(sum(s["def"]), 1)

    def test_empty_session_ids(self):
        self.assertEqual(dashboard._session_sparklines(self.conn, []), {})

    def test_html_has_render_fn(self):
        self.assertIn("_renderSparkline", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
