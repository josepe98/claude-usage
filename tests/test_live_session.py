"""Tests for the live active-session detection."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner
import dashboard


class TestActiveSessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.projects = Path(self.tmp) / "projects"
        self.projects.mkdir()
        self.db = Path(self.tmp) / "u.db"
        # Patch scanner project dirs and dashboard DB so detection scans
        # our fixture instead of the user's real files.
        self._orig_dirs = scanner.DEFAULT_PROJECTS_DIRS
        self._orig_db = dashboard.DB_PATH
        scanner.DEFAULT_PROJECTS_DIRS = [self.projects]
        dashboard.DB_PATH = self.db
        # Seed one session in DB
        conn = scanner.get_db(self.db)
        scanner.init_db(conn)
        scanner.upsert_sessions(conn, [{
            "session_id": "abc123", "project_name": "myproj",
            "first_timestamp": "2026-04-01T10:00:00Z",
            "last_timestamp":  "2026-04-01T10:30:00Z",
            "git_branch": "", "model": "claude-opus-4-7", "turn_count": 5,
            "total_input_tokens": 1000, "total_output_tokens": 500,
            "total_cache_read": 0, "total_cache_creation": 0,
        }])
        conn.commit()
        conn.close()

    def tearDown(self):
        scanner.DEFAULT_PROJECTS_DIRS = self._orig_dirs
        dashboard.DB_PATH = self._orig_db
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def _call(self, **kw):
        # Common kwargs: lock down the real environment so tests don't pick
        # up the user's actual Cowork / Claude Code activity.
        return dashboard._active_sessions(
            projects_dirs=[self.projects],
            include_cowork=False,
            db_path=self.db,
            **kw,
        )

    def test_recent_jsonl_is_active(self):
        f = self.projects / "abc123.jsonl"
        f.write_text("{}\n")
        active = self._call()
        ids = {a["session_id"] for a in active}
        self.assertIn("abc123"[:8], ids)

    def test_old_jsonl_is_not_active(self):
        f = self.projects / "abc123.jsonl"
        f.write_text("{}\n")
        old = time.time() - 600
        os.utime(f, (old, old))
        active = self._call(window_seconds=300)
        self.assertEqual(len(active), 0)

    def test_empty_when_no_jsonl(self):
        self.assertEqual(self._call(), [])

    def test_cost_is_calculated(self):
        f = self.projects / "abc123.jsonl"
        f.write_text("{}\n")
        active = self._call()
        self.assertEqual(len(active), 1)
        # opus pricing: 1000 in @ $5/MT + 500 out @ $25/MT = $0.005 + $0.0125 = $0.0175
        self.assertAlmostEqual(active[0]["cost"], 0.0175, places=4)


class TestLiveWidgetWiring(unittest.TestCase):
    def test_html_has_live_widget(self):
        self.assertIn('id="live-widget"', dashboard.HTML_TEMPLATE)

    def test_startLivePolling_present(self):
        self.assertIn("startLivePolling", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
