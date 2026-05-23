"""Browser smoke tests for dashboard.py using Playwright.

Requires playwright to be installed:
    pip install -r requirements-dev.txt
    playwright install chromium

These tests spin up the same live HTTP server as TestDashboardHTTP in
test_dashboard.py, then drive a headless Chromium browser against it.
"""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from scanner import get_db, init_db, upsert_sessions, insert_turns
from dashboard import DashboardHandler

try:
    from http.server import HTTPServer
except ImportError:
    HTTPServer = None


@unittest.skipUnless(HAS_PLAYWRIGHT, "playwright not installed — run: pip install -r requirements-dev.txt")
class TestBrowserSmoke(unittest.TestCase):
    """Browser-level smoke tests using Playwright + headless Chromium."""

    @classmethod
    def setUpClass(cls):
        # ── Patch DB_PATH and projects dir (same pattern as TestDashboardHTTP) ──
        import dashboard as _d
        import scanner as _s

        cls._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmpdir.name)
        tmp_projects = tmp / "projects"
        tmp_projects.mkdir()

        cls._patches = {
            (_d, "DB_PATH"):               (_d.DB_PATH,               tmp / "usage.db"),
            (_s, "DB_PATH"):               (_s.DB_PATH,               tmp / "usage.db"),
            (_s, "PROJECTS_DIR"):          (_s.PROJECTS_DIR,          tmp_projects),
            (_s, "DEFAULT_PROJECTS_DIRS"): (_s.DEFAULT_PROJECTS_DIRS, [tmp_projects]),
        }
        for (mod, name), (_orig, new) in cls._patches.items():
            setattr(mod, name, new)

        # ── Insert fixture data ───────────────────────────────────────────────
        db_path = tmp / "usage.db"
        conn = get_db(db_path)
        init_db(conn)

        sessions = [
            {
                "session_id": "sess-browser-01",
                "project_name": "user/projectalpha",
                "first_timestamp": "2026-04-10T08:00:00Z",
                "last_timestamp":  "2026-04-10T09:30:00Z",
                "git_branch": "main",
                "model": "claude-sonnet-4-6",
                "total_input_tokens":   8000,
                "total_output_tokens":  3000,
                "total_cache_read":      400,
                "total_cache_creation":  150,
                "turn_count": 12,
            },
            {
                "session_id": "sess-browser-02",
                "project_name": "user/projectbeta",
                "first_timestamp": "2026-04-12T14:00:00Z",
                "last_timestamp":  "2026-04-12T15:00:00Z",
                "git_branch": "dev",
                "model": "claude-opus-4-7",
                "total_input_tokens":   5000,
                "total_output_tokens":  2000,
                "total_cache_read":      200,
                "total_cache_creation":   80,
                "turn_count": 8,
            },
            {
                "session_id": "sess-browser-03",
                "project_name": "user/projectalpha",
                "first_timestamp": "2026-04-15T10:00:00Z",
                "last_timestamp":  "2026-04-15T11:00:00Z",
                "git_branch": "feature",
                "model": "claude-sonnet-4-6",
                "total_input_tokens":   6000,
                "total_output_tokens":  2500,
                "total_cache_read":      300,
                "total_cache_creation":  100,
                "turn_count": 10,
            },
        ]
        upsert_sessions(conn, sessions)

        # Turns spread across 3 dates and multiple hours
        turns = [
            # 2026-04-10: hours 8 and 11
            {
                "session_id": "sess-browser-01",
                "timestamp": "2026-04-10T08:30:00Z",
                "model": "claude-sonnet-4-6",
                "input_tokens": 700, "output_tokens": 280,
                "cache_read_tokens": 40, "cache_creation_tokens": 15,
                "tool_name": None, "cwd": "/tmp",
            },
            {
                "session_id": "sess-browser-01",
                "timestamp": "2026-04-10T11:15:00Z",
                "model": "claude-sonnet-4-6",
                "input_tokens": 600, "output_tokens": 250,
                "cache_read_tokens": 30, "cache_creation_tokens": 10,
                "tool_name": None, "cwd": "/tmp",
            },
            # 2026-04-12: hour 14
            {
                "session_id": "sess-browser-02",
                "timestamp": "2026-04-12T14:30:00Z",
                "model": "claude-opus-4-7",
                "input_tokens": 900, "output_tokens": 400,
                "cache_read_tokens": 50, "cache_creation_tokens": 20,
                "tool_name": None, "cwd": "/tmp",
            },
            {
                "session_id": "sess-browser-02",
                "timestamp": "2026-04-12T14:50:00Z",
                "model": "claude-opus-4-7",
                "input_tokens": 800, "output_tokens": 350,
                "cache_read_tokens": 45, "cache_creation_tokens": 18,
                "tool_name": None, "cwd": "/tmp",
            },
            # 2026-04-15: hours 10 and 16
            {
                "session_id": "sess-browser-03",
                "timestamp": "2026-04-15T10:20:00Z",
                "model": "claude-sonnet-4-6",
                "input_tokens": 550, "output_tokens": 220,
                "cache_read_tokens": 25, "cache_creation_tokens": 8,
                "tool_name": None, "cwd": "/tmp",
            },
            {
                "session_id": "sess-browser-03",
                "timestamp": "2026-04-15T16:05:00Z",
                "model": "claude-sonnet-4-6",
                "input_tokens": 480, "output_tokens": 190,
                "cache_read_tokens": 20, "cache_creation_tokens": 6,
                "tool_name": None, "cwd": "/tmp",
            },
        ]
        insert_turns(conn, turns)
        conn.commit()
        conn.close()

        # ── Start HTTP server on a random port ────────────────────────────────
        cls.server = HTTPServer(("127.0.0.1", 0), DashboardHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

        # ── Launch Playwright Chromium (headless) ─────────────────────────────
        cls._playwright = sync_playwright().start()
        cls.browser = cls._playwright.chromium.launch(headless=True)
        cls.page = cls.browser.new_page()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._playwright.stop()
        cls.server.shutdown()
        for (mod, name), (orig, _new) in cls._patches.items():
            setattr(mod, name, orig)
        cls._tmpdir.cleanup()

    def setUp(self):
        # Reset console error list before each test so tests are independent.
        self.console_errors = []
        self.page.on("console", self._capture_console_errors)

    def _capture_console_errors(self, msg):
        if msg.type == "error":
            self.console_errors.append(msg.text)

    # ── Tests ─────────────────────────────────────────────────────────────────

    def test_no_js_errors(self):
        """The dashboard index page must load without any JS console errors."""
        self.page.goto(
            f"http://127.0.0.1:{self.port}/",
            wait_until="networkidle",
        )
        self.assertEqual(
            self.console_errors,
            [],
            f"JS console errors on page load: {self.console_errors}",
        )

    def test_stat_cards_populated(self):
        """All stat card values must be non-empty and non-zero after load."""
        self.page.goto(
            f"http://127.0.0.1:{self.port}/",
            wait_until="networkidle",
        )
        values = self.page.locator(".stat-card .value").all()
        self.assertGreaterEqual(
            len(values), 6,
            f"Expected at least 6 stat cards, found {len(values)}",
        )
        for card in values:
            text = card.inner_text().strip()
            self.assertTrue(
                text and text != "0",
                f"Stat card value is empty or zero: {repr(text)}",
            )

    def test_all_charts_rendered(self):
        """All 4 chart canvases must exist and have non-zero dimensions."""
        self.page.goto(
            f"http://127.0.0.1:{self.port}/",
            wait_until="networkidle",
        )
        chart_ids = ["chart-daily", "chart-hourly", "chart-model", "chart-project"]
        for chart_id in chart_ids:
            dims = self.page.evaluate(
                """(id) => {
                    const el = document.getElementById(id);
                    if (!el) return null;
                    return { width: el.offsetWidth, height: el.offsetHeight };
                }""",
                chart_id,
            )
            self.assertIsNotNone(dims, f"Canvas #{chart_id} not found in DOM")
            self.assertGreater(
                dims["width"], 0,
                f"Canvas #{chart_id} has zero width",
            )
            self.assertGreater(
                dims["height"], 0,
                f"Canvas #{chart_id} has zero height",
            )

    def test_date_range_filter(self):
        """Clicking the '7d' range button must not produce JS errors."""
        self.page.goto(
            f"http://127.0.0.1:{self.port}/",
            wait_until="networkidle",
        )
        # Reset errors captured during navigation
        self.console_errors.clear()

        self.page.locator('[data-range="7d"]').click()
        self.page.wait_for_timeout(500)

        self.assertEqual(
            self.console_errors,
            [],
            f"JS errors after clicking 7d range: {self.console_errors}",
        )

    def test_themes_page_loads(self):
        """The /themes page must return HTTP 200 and display theme cards."""
        resp = self.page.goto(
            f"http://127.0.0.1:{self.port}/themes",
            wait_until="networkidle",
        )
        self.assertEqual(resp.status, 200)
        # Wait for theme cards to render (they're injected by JS)
        self.page.wait_for_timeout(500)
        theme_names = self.page.locator(".theme-name").all()
        self.assertGreater(
            len(theme_names), 0,
            "Expected at least one theme card on /themes page",
        )
        names = [el.inner_text() for el in theme_names]
        self.assertIn("Apple", names, f"Expected 'Apple' theme in list, got: {names}")

    def test_model_filter_toggle(self):
        """Clicking 'None' then 'All' model filter buttons must not emit JS errors."""
        self.page.goto(
            f"http://127.0.0.1:{self.port}/",
            wait_until="networkidle",
        )
        self.console_errors.clear()

        # Deselect all models
        self.page.locator("button.filter-btn", has_text="None").click()
        self.page.wait_for_timeout(500)

        # Re-select all models
        self.page.locator("button.filter-btn", has_text="All").click()
        self.page.wait_for_timeout(500)

        self.assertEqual(
            self.console_errors,
            [],
            f"JS errors during model filter toggle: {self.console_errors}",
        )


if __name__ == "__main__":
    unittest.main()
