"""Tests for cost-per-turn histogram."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner
import dashboard


class TestCostHistogram(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "u.db"

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_returns_none(self):
        conn = scanner.get_db(self.db)
        scanner.init_db(conn)
        self.assertIsNone(dashboard._cost_per_turn_stats(conn))
        conn.close()

    def test_percentiles_and_buckets(self):
        conn = scanner.get_db(self.db)
        scanner.init_db(conn)
        scanner.upsert_sessions(conn, [{
            "session_id": "s", "project_name": "p",
            "first_timestamp": "2026-04-01T00:00:00Z",
            "last_timestamp":  "2026-04-01T00:00:00Z",
            "git_branch": "", "model": "claude-haiku-4-5", "turn_count": 100,
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cache_read": 0, "total_cache_creation": 0,
        }])
        # 100 turns: 99 cheap (~$0.0001 each) + 1 expensive ($1)
        turns = []
        for i in range(99):
            turns.append({
                "session_id": "s", "timestamp": f"2026-04-01T{i:02d}:00:00Z",
                "model": "claude-haiku-4-5",
                "input_tokens": 100, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "tool_name": None, "cwd": "/p", "message_id": f"m{i}",
            })
        # 1 expensive turn: 1M output @ $5/MTok = $5
        turns.append({
            "session_id": "s", "timestamp": "2026-04-01T23:00:00Z",
            "model": "claude-haiku-4-5",
            "input_tokens": 0, "output_tokens": 1_000_000,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "tool_name": None, "cwd": "/p", "message_id": "m_big",
        })
        scanner.insert_turns(conn, turns)
        conn.commit()
        h = dashboard._cost_per_turn_stats(conn)
        conn.close()
        self.assertEqual(h["n"], 100)
        self.assertGreater(h["max"], 4)
        self.assertLess(h["p50"], 0.01)
        self.assertEqual(len(h["buckets"]), 12)

    def test_html_has_canvas_and_renderer(self):
        self.assertIn('id="chart-histo"', dashboard.HTML_TEMPLATE)
        self.assertIn("renderHistogram", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
