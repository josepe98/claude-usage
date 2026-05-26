"""Tests for /api/reset endpoint."""
import json
import sys
import threading
import time
import tempfile
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner
import dashboard


class TestResetEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "u.db"
        # Seed a populated DB
        conn = scanner.get_db(self.db)
        scanner.init_db(conn)
        scanner.upsert_sessions(conn, [{
            "session_id": "abc", "project_name": "p",
            "first_timestamp": "2026-04-01T00:00:00Z",
            "last_timestamp":  "2026-04-01T00:00:00Z",
            "git_branch": "", "model": "claude-opus-4-7", "turn_count": 1,
            "total_input_tokens": 100, "total_output_tokens": 50,
            "total_cache_read": 0, "total_cache_creation": 0,
        }])
        conn.commit()
        conn.close()
        self._orig = dashboard.DB_PATH
        dashboard.DB_PATH = self.db
        dashboard.SHARE_TOKEN = None
        # Bind to an OS-assigned port so concurrent runs / leaked sockets from
        # earlier tests in the same process don't fight over a fixed port.
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        time.sleep(0.1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        dashboard.DB_PATH = self._orig
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reset_clears_db(self):
        # Before: 1 session
        c = sqlite3_count(self.db, "sessions")
        self.assertEqual(c, 1)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/reset", method="POST")
        with urllib.request.urlopen(req) as r:
            d = json.loads(r.read())
        self.assertTrue(d["ok"])
        # After: 0 sessions (schema is recreated though)
        c = sqlite3_count(self.db, "sessions")
        self.assertEqual(c, 0)


def sqlite3_count(db, table):
    import sqlite3
    if not db.exists():
        return 0
    conn = sqlite3.connect(db)
    n = conn.execute(f"select count(*) from {table}").fetchone()[0]
    conn.close()
    return n


class TestResetWiring(unittest.TestCase):
    def test_html_has_reset_button(self):
        self.assertIn('id="reset-btn"', dashboard.HTML_TEMPLATE)
        self.assertIn("_confirmReset", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
