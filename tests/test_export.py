"""Tests for JSON / CSV export endpoints and cli export command."""
import io
import json
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner
import dashboard


def _seed(db):
    conn = scanner.get_db(db)
    scanner.init_db(conn)
    scanner.upsert_sessions(conn, [{
        "session_id": "se", "project_name": "myproj",
        "first_timestamp": "2026-04-01T10:00:00Z",
        "last_timestamp":  "2026-04-01T10:30:00Z",
        "git_branch": "main", "model": "claude-opus-4-7", "turn_count": 0,
        "total_input_tokens": 0, "total_output_tokens": 0,
        "total_cache_read": 0, "total_cache_creation": 0,
    }])
    scanner.insert_turns(conn, [{
        "session_id": "se", "timestamp": "2026-04-01T10:15:00Z",
        "model": "claude-opus-4-7", "input_tokens": 100, "output_tokens": 50,
        "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "tool_name": "Bash", "cwd": "/x", "message_id": "m1",
    }])
    conn.commit()
    conn.close()


class TestExportRawTurns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "u.db"
        _seed(self.db)
        # Patch DB_PATH so dashboard reads our temp DB
        self._orig = dashboard.DB_PATH
        dashboard.DB_PATH = self.db

    def tearDown(self):
        dashboard.DB_PATH = self._orig
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_returns_list_of_dicts(self):
        turns = dashboard._export_raw_turns(db_path=self.db)
        self.assertIsInstance(turns, list)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["model"], "claude-opus-4-7")
        self.assertEqual(turns[0]["tool_name"], "Bash")


class TestExportCSV(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "u.db"
        _seed(self.db)
        self._orig = dashboard.DB_PATH
        dashboard.DB_PATH = self.db

    def tearDown(self):
        dashboard.DB_PATH = self._orig
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_daily_csv_has_header_and_row(self):
        csv = dashboard._export_csv("daily", db_path=self.db)
        self.assertIn("day,model,input,output", csv)
        self.assertIn("claude-opus-4-7", csv)

    def test_sessions_csv_includes_project(self):
        csv = dashboard._export_csv("sessions", db_path=self.db)
        self.assertIn("session_id", csv)
        self.assertIn("myproj", csv)

    def test_projects_csv_aggregates_by_project(self):
        csv = dashboard._export_csv("projects", db_path=self.db)
        self.assertIn("myproj", csv)
        self.assertIn("project,sessions,turns", csv)

    def test_unknown_type_returns_error_csv(self):
        csv = dashboard._export_csv("garbage", db_path=self.db)
        self.assertIn("unknown export type", csv)


if __name__ == "__main__":
    unittest.main()
