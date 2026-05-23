"""Tests for /api/health."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner
import dashboard


class TestHealthEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "u.db"
        self._orig = dashboard.DB_PATH
        dashboard.DB_PATH = self.db
        dashboard.SHARE_TOKEN = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 18097), dashboard.DashboardHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        time.sleep(0.1)

    def tearDown(self):
        self.server.shutdown()
        dashboard.DB_PATH = self._orig
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_db_returns_no_db(self):
        with urllib.request.urlopen("http://127.0.0.1:18097/api/health") as r:
            d = json.loads(r.read())
        self.assertEqual(d["status"], "no-db")
        self.assertEqual(d["sessions"], 0)

    def test_with_db_returns_counts(self):
        conn = scanner.get_db(self.db)
        scanner.init_db(conn)
        scanner.upsert_sessions(conn, [{
            "session_id": "x", "project_name": "p",
            "first_timestamp": "2026-04-01T00:00:00Z",
            "last_timestamp":  "2026-04-01T00:00:00Z",
            "git_branch": "", "model": "claude-opus-4-7", "turn_count": 1,
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cache_read": 0, "total_cache_creation": 0,
        }])
        conn.commit()
        conn.close()
        with urllib.request.urlopen("http://127.0.0.1:18097/api/health") as r:
            d = json.loads(r.read())
        self.assertEqual(d["status"], "ok")
        self.assertEqual(d["sessions"], 1)


if __name__ == "__main__":
    unittest.main()
