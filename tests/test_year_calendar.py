"""Tests for the year-calendar heatmap added to the dashboard.

Three guarantees we lock down:

1. With an empty database the helper still returns exactly 365 entries —
   the front-end can't be allowed to render an empty/short grid.
2. With seeded turns, the matching day surfaces non-zero cost + turn count
   and the cost matches the canonical pricing table.
3. The HTML/JS wiring is present so the renderer actually runs and the
   API payload exposes the data under the agreed key.
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from scanner import get_db, init_db, upsert_sessions, insert_turns
from dashboard import _year_calendar, get_dashboard_data, HTML_TEMPLATE
from pricing import PRICING


class TestYearCalendarHelper(unittest.TestCase):
    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpfile.close()
        self.db_path = Path(self.tmpfile.name)
        conn = get_db(self.db_path)
        init_db(conn)
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── 1. Empty DB ─────────────────────────────────────────────────────────
    def test_empty_db_returns_365_zero_days(self):
        conn = self._conn()
        try:
            rows = _year_calendar(conn)
        finally:
            conn.close()
        self.assertEqual(len(rows), 365)
        self.assertTrue(all(r["cost"] == 0 and r["turns"] == 0 for r in rows))
        # Dates are monotonically increasing and unique.
        dates = [r["date"] for r in rows]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(set(dates)), 365)

    def test_window_ends_on_today(self):
        fixed_today = date(2026, 5, 23)
        conn = self._conn()
        try:
            rows = _year_calendar(conn, today=fixed_today)
        finally:
            conn.close()
        self.assertEqual(rows[-1]["date"], fixed_today.isoformat())
        self.assertEqual(
            rows[0]["date"], (fixed_today - timedelta(days=364)).isoformat()
        )

    # ── 2. Seeded turns ─────────────────────────────────────────────────────
    def test_seeded_turn_surfaces_cost_and_turns_on_correct_day(self):
        fixed_today = date(2026, 5, 23)
        target_day = fixed_today - timedelta(days=10)  # well inside the window
        ts = f"{target_day.isoformat()}T12:34:00Z"

        conn = self._conn()
        try:
            sessions = [{
                "session_id": "sess-yc", "project_name": "user/proj",
                "first_timestamp": ts, "last_timestamp": ts,
                "git_branch": "main", "model": "claude-sonnet-4-6",
                "total_input_tokens": 1_000_000, "total_output_tokens": 500_000,
                "total_cache_read": 0, "total_cache_creation": 0,
                "turn_count": 2,
            }]
            upsert_sessions(conn, sessions)
            turns = [
                {
                    "session_id": "sess-yc", "timestamp": ts,
                    "model": "claude-sonnet-4-6",
                    "input_tokens": 600_000, "output_tokens": 300_000,
                    "cache_read_tokens": 0, "cache_creation_tokens": 0,
                    "tool_name": None, "cwd": "/tmp",
                },
                {
                    "session_id": "sess-yc",
                    "timestamp": ts.replace("12:34", "13:00"),
                    "model": "claude-sonnet-4-6",
                    "input_tokens": 400_000, "output_tokens": 200_000,
                    "cache_read_tokens": 0, "cache_creation_tokens": 0,
                    "tool_name": None, "cwd": "/tmp",
                },
            ]
            insert_turns(conn, turns)
            conn.commit()
            rows = _year_calendar(conn, today=fixed_today)
        finally:
            conn.close()

        by_date = {r["date"]: r for r in rows}
        hit = by_date[target_day.isoformat()]
        self.assertEqual(hit["turns"], 2)
        # 1M input @ $3/MTok + 500K output @ $15/MTok = $3 + $7.50 = $10.50
        p = PRICING["claude-sonnet-4-6"]
        expected = (1_000_000 * p["input"] + 500_000 * p["output"]) / 1e6
        self.assertAlmostEqual(hit["cost"], expected, places=4)
        self.assertAlmostEqual(hit["cost"], 10.50, places=4)

        # All other days remain zero.
        for d, r in by_date.items():
            if d != target_day.isoformat():
                self.assertEqual(r["turns"], 0)
                self.assertEqual(r["cost"], 0)

    def test_unbillable_model_contributes_turns_but_not_cost(self):
        fixed_today = date(2026, 5, 23)
        target_day = fixed_today - timedelta(days=5)
        ts = f"{target_day.isoformat()}T09:00:00Z"

        conn = self._conn()
        try:
            upsert_sessions(conn, [{
                "session_id": "sess-x", "project_name": "p",
                "first_timestamp": ts, "last_timestamp": ts,
                "git_branch": "main", "model": "unknown",
                "total_input_tokens": 100, "total_output_tokens": 50,
                "total_cache_read": 0, "total_cache_creation": 0,
                "turn_count": 1,
            }])
            insert_turns(conn, [{
                "session_id": "sess-x", "timestamp": ts, "model": "",
                "input_tokens": 100, "output_tokens": 50,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "tool_name": None, "cwd": "/tmp",
            }])
            conn.commit()
            rows = _year_calendar(conn, today=fixed_today)
        finally:
            conn.close()

        hit = {r["date"]: r for r in rows}[target_day.isoformat()]
        self.assertEqual(hit["turns"], 1)
        self.assertEqual(hit["cost"], 0)

    def test_old_turn_outside_window_is_ignored(self):
        fixed_today = date(2026, 5, 23)
        # 500 days back — well outside the 365-day window.
        old_day = fixed_today - timedelta(days=500)
        ts = f"{old_day.isoformat()}T00:00:00Z"

        conn = self._conn()
        try:
            upsert_sessions(conn, [{
                "session_id": "sess-old", "project_name": "p",
                "first_timestamp": ts, "last_timestamp": ts,
                "git_branch": "main", "model": "claude-opus-4-7",
                "total_input_tokens": 9_000_000, "total_output_tokens": 1_000_000,
                "total_cache_read": 0, "total_cache_creation": 0,
                "turn_count": 1,
            }])
            insert_turns(conn, [{
                "session_id": "sess-old", "timestamp": ts,
                "model": "claude-opus-4-7",
                "input_tokens": 9_000_000, "output_tokens": 1_000_000,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "tool_name": None, "cwd": "/tmp",
            }])
            conn.commit()
            rows = _year_calendar(conn, today=fixed_today)
        finally:
            conn.close()

        self.assertEqual(len(rows), 365)
        self.assertTrue(all(r["cost"] == 0 and r["turns"] == 0 for r in rows))


class TestYearCalendarInDashboardPayload(unittest.TestCase):
    """get_dashboard_data must expose the calendar under the agreed key."""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpfile.close()
        self.db_path = Path(self.tmpfile.name)
        conn = get_db(self.db_path)
        init_db(conn)
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_year_calendar_key_present(self):
        data = get_dashboard_data(db_path=self.db_path)
        self.assertIn("year_calendar", data)
        self.assertIsInstance(data["year_calendar"], list)
        self.assertEqual(len(data["year_calendar"]), 365)
        for r in data["year_calendar"][:3]:
            self.assertIn("date", r)
            self.assertIn("cost", r)
            self.assertIn("turns", r)


class TestYearCalendarHTMLWiring(unittest.TestCase):
    """Sanity-check that the HTML card and JS renderer are wired up."""

    def test_card_container_present(self):
        self.assertIn('id="year-calendar"', HTML_TEMPLATE)

    def test_render_function_defined(self):
        self.assertIn("function renderYearCalendar(", HTML_TEMPLATE)

    def test_apply_filter_invokes_renderer_with_full_year_data(self):
        # The renderer must be called from applyFilter() with the full
        # year_calendar payload — NOT the range-filtered slice. The literal
        # "rawData.year_calendar" guarantees we bypass the active filter.
        self.assertIn("renderYearCalendar(rawData.year_calendar", HTML_TEMPLATE)

    def test_grid_layout_is_53_columns_by_7_rows(self):
        self.assertIn("repeat(53, 12px)", HTML_TEMPLATE)
        self.assertIn("repeat(7, 12px)", HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
