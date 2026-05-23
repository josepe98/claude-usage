"""Tests for day-of-week × hour heatmap."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner
import dashboard


class TestDowHourHeatmap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "u.db"

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_7x24_grid(self):
        conn = scanner.get_db(self.db)
        scanner.init_db(conn)
        scanner.upsert_sessions(conn, [{
            "session_id": "s", "project_name": "p",
            "first_timestamp": "2026-04-01T10:00:00Z",
            "last_timestamp":  "2026-04-01T10:00:00Z",
            "git_branch": "", "model": "claude-opus-4-7", "turn_count": 1,
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cache_read": 0, "total_cache_creation": 0,
        }])
        # Wednesday 2026-04-01 was a Wednesday, 14:00 UTC
        scanner.insert_turns(conn, [{
            "session_id": "s", "timestamp": "2026-04-01T14:30:00Z",
            "model": "claude-opus-4-7", "input_tokens": 100, "output_tokens": 50,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "tool_name": None, "cwd": "/p", "message_id": "m1",
        }])
        conn.commit()
        grid = dashboard._dow_hour_heatmap(conn)
        conn.close()
        self.assertEqual(len(grid), 7)
        self.assertEqual(len(grid[0]), 24)
        # Apr 1 2026 was a Wednesday (=2 in our remap 0=Mon)
        self.assertEqual(grid[2][14]["turns"], 1)
        self.assertEqual(grid[2][14]["tokens"], 150)

    def test_html_has_grid(self):
        self.assertIn('id="dow-hour-grid"', dashboard.HTML_TEMPLATE)
        self.assertIn("renderDowHourHeatmap", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
