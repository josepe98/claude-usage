"""Tests for the tool-usage feature (chart + cli command + API field)."""
import json
import os
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner
import dashboard


class TestToolUsageInDashboardData(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "u.db"
        conn = scanner.get_db(self.db_path)
        scanner.init_db(conn)
        # Insert one session and a handful of turns with different tool_names.
        scanner.upsert_sessions(conn, [{
            "session_id": "sid1", "project_name": "p1",
            "first_timestamp": "2026-04-01T10:00:00Z",
            "last_timestamp":  "2026-04-01T11:00:00Z",
            "git_branch": "", "model": "claude-opus-4-7",
            "turn_count": 0,
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cache_read": 0, "total_cache_creation": 0,
        }])
        scanner.insert_turns(conn, [
            {"session_id": "sid1", "timestamp": "2026-04-01T10:00:00Z",
             "model": "claude-opus-4-7", "input_tokens": 10, "output_tokens": 5,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": "Bash", "cwd": "/x", "message_id": "m1"},
            {"session_id": "sid1", "timestamp": "2026-04-01T10:30:00Z",
             "model": "claude-opus-4-7", "input_tokens": 20, "output_tokens": 8,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": "Bash", "cwd": "/x", "message_id": "m2"},
            {"session_id": "sid1", "timestamp": "2026-04-01T10:45:00Z",
             "model": "claude-opus-4-7", "input_tokens": 5, "output_tokens": 2,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": "Edit", "cwd": "/x", "message_id": "m3"},
            {"session_id": "sid1", "timestamp": "2026-04-01T11:00:00Z",
             "model": "claude-opus-4-7", "input_tokens": 3, "output_tokens": 1,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": "", "cwd": "/x", "message_id": "m4"},
        ])
        conn.commit()
        conn.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_api_includes_tools_daily(self):
        data = dashboard.get_dashboard_data(self.db_path)
        self.assertIn("tools_daily", data)
        rows = data["tools_daily"]
        # Bash twice, Edit once, empty once (rendered as placeholder).
        tools = {r["tool"] for r in rows}
        self.assertIn("Bash", tools)
        self.assertIn("Edit", tools)
        # Empty tool_name should be normalised to a placeholder so it's still
        # visible in the breakdown.
        self.assertTrue(any("no tool" in t for t in tools))

    def test_bash_aggregate_turns_correct(self):
        data = dashboard.get_dashboard_data(self.db_path)
        bash_rows = [r for r in data["tools_daily"] if r["tool"] == "Bash"]
        total_turns = sum(r["turns"] for r in bash_rows)
        total_tokens = sum(r["tokens"] for r in bash_rows)
        self.assertEqual(total_turns, 2)
        self.assertEqual(total_tokens, (10+5) + (20+8))


class TestToolUsageHTMLWiring(unittest.TestCase):
    def test_html_has_tools_canvas(self):
        # The HTML template should include the chart canvas and render fn.
        self.assertIn('id="chart-tools"', dashboard.HTML_TEMPLATE)
        self.assertIn("renderToolsChart", dashboard.HTML_TEMPLATE)
        # And it must be wired into the filter pipeline so a range change
        # re-renders it.
        self.assertIn("filteredTools", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
