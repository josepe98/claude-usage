"""Tests for /api/text/* AppleScript-friendly endpoints.

Each endpoint must:
  * return text/plain (no JSON)
  * return a single bare ASCII string (no trailing newline)
  * survive an empty DB without 500ing
"""

import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer
from pathlib import Path
from unittest import mock

import dashboard
from dashboard import DashboardHandler, get_text_stats
from scanner import get_db, init_db, upsert_sessions, insert_turns


def _seed_db(db_path):
    """Insert one turn today + one turn earlier this month + one active session."""
    now = datetime.now(timezone.utc)
    today_ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    # 10 days ago, but still within the current calendar month if possible.
    # Fall back to "yesterday" if we're early in the month so the month bucket
    # always contains at least 2 rows.
    earlier = now - timedelta(days=10)
    if earlier.month != now.month:
        earlier = now - timedelta(days=1)
    earlier_ts = earlier.strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = get_db(db_path)
    init_db(conn)
    upsert_sessions(conn, [{
        "session_id": "sess-active", "project_name": "p/active",
        "first_timestamp": today_ts, "last_timestamp": today_ts,
        "git_branch": "main", "model": "claude-sonnet-4-6",
        "total_input_tokens": 1_000_000, "total_output_tokens": 200_000,
        "total_cache_read": 0, "total_cache_creation": 0, "turn_count": 2,
    }, {
        "session_id": "sess-stale", "project_name": "p/stale",
        "first_timestamp": earlier_ts, "last_timestamp": earlier_ts,
        "git_branch": "main", "model": "claude-sonnet-4-6",
        "total_input_tokens": 500_000, "total_output_tokens": 100_000,
        "total_cache_read": 0, "total_cache_creation": 0, "turn_count": 1,
    }])
    insert_turns(conn, [
        {
            "session_id": "sess-active", "timestamp": today_ts,
            "model": "claude-sonnet-4-6",
            # 1M input + 200k output sonnet = 1*3 + 0.2*15 = $6.00
            "input_tokens": 1_000_000, "output_tokens": 200_000,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "tool_name": None, "cwd": "/tmp",
        },
        {
            "session_id": "sess-stale", "timestamp": earlier_ts,
            "model": "claude-sonnet-4-6",
            # 500k input + 100k output = 0.5*3 + 0.1*15 = $3.00
            "input_tokens": 500_000, "output_tokens": 100_000,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "tool_name": None, "cwd": "/tmp",
        },
    ])
    conn.commit()
    conn.close()


class TestGetTextStats(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        self.db_path = Path(f.name)
        _seed_db(self.db_path)

    def tearDown(self):
        os.unlink(self.db_path)

    def test_today_and_month_cost_computed_from_pricing(self):
        stats = get_text_stats(db_path=self.db_path)
        # Today: only the active session ran -> $6.00
        self.assertEqual(stats["today_cost"], 6.00)
        # Month: both turns are in the same month bucket -> $9.00.
        # If "earlier" got pushed to yesterday because we ran on day 1-9, the
        # second row is still in the month bucket, so month_cost stays $9.00.
        self.assertEqual(stats["month_cost"], 9.00)

    def test_active_sessions_only_counts_recent(self):
        stats = get_text_stats(db_path=self.db_path)
        # Only sess-active touched the DB within the last 5h.
        self.assertEqual(stats["active_sessions"], 1)

    def test_budget_pct_uses_env(self):
        with mock.patch.dict(os.environ, {"CLAUDE_USAGE_MONTHLY_BUDGET": "30"}):
            stats = get_text_stats(db_path=self.db_path)
        # $9 / $30 = 30%
        self.assertEqual(stats["budget_pct"], 30)

    def test_budget_pct_zero_when_unset(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_USAGE_MONTHLY_BUDGET"}
        with mock.patch.dict(os.environ, env, clear=True):
            stats = get_text_stats(db_path=self.db_path)
        self.assertEqual(stats["budget_pct"], 0)

    def test_no_db_returns_zeros(self):
        missing = Path(tempfile.gettempdir()) / "definitely-not-a-real-db-xyz.db"
        if missing.exists():
            missing.unlink()
        stats = get_text_stats(db_path=missing)
        self.assertEqual(stats, {
            "today_cost": 0.0, "month_cost": 0.0,
            "active_sessions": 0, "budget_pct": 0,
        })


class TestTextEndpointsHTTP(unittest.TestCase):
    """End-to-end: hit the HTTP server and check the wire format."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls._tmpdir.name) / "usage.db"
        _seed_db(cls.db_path)
        cls._orig_db = dashboard.DB_PATH
        dashboard.DB_PATH = cls.db_path

        cls.server = HTTPServer(("127.0.0.1", 0), DashboardHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        dashboard.DB_PATH = cls._orig_db
        cls._tmpdir.cleanup()

    def _get(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url) as resp:
            return resp.status, resp.headers["Content-Type"], resp.read().decode("utf-8")

    def test_today_cost_endpoint(self):
        status, ctype, body = self._get("/api/text/today-cost")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/plain"), ctype)
        # Bare 2-decimal float, no whitespace.
        self.assertRegex(body, r"^\d+\.\d{2}$")
        self.assertEqual(body, "6.00")

    def test_month_cost_endpoint(self):
        status, ctype, body = self._get("/api/text/month-cost")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/plain"), ctype)
        self.assertRegex(body, r"^\d+\.\d{2}$")
        self.assertEqual(body, "9.00")

    def test_active_sessions_endpoint(self):
        status, ctype, body = self._get("/api/text/active-sessions")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/plain"), ctype)
        # Integer, no decimals, no whitespace.
        self.assertRegex(body, r"^\d+$")
        self.assertEqual(body, "1")

    def test_budget_pct_endpoint_default_zero(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_USAGE_MONTHLY_BUDGET"}
        with mock.patch.dict(os.environ, env, clear=True):
            status, ctype, body = self._get("/api/text/budget-pct")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/plain"), ctype)
        self.assertRegex(body, r"^\d+$")
        # No budget set -> 0
        self.assertEqual(body, "0")

    def test_unknown_text_endpoint_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/text/does-not-exist")
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
