"""Tests for the streak counter (_compute_streak + payload integration)."""

import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from scanner import get_db, init_db, insert_turns, upsert_sessions
from dashboard import _compute_streak, get_dashboard_data


def _ts(d: date, hour: int = 12) -> str:
    """ISO-8601 UTC timestamp for the given calendar date."""
    return datetime(d.year, d.month, d.day, hour, 0, 0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _make_turn(session_id: str, d: date, *, hour: int = 12):
    return {
        "session_id":              session_id,
        "timestamp":               _ts(d, hour),
        "model":                   "claude-sonnet-4-6",
        "input_tokens":            10,
        "output_tokens":           5,
        "cache_read_tokens":       0,
        "cache_creation_tokens":   0,
        "tool_name":               None,
        "cwd":                     "/tmp",
        "message_id":              f"m-{session_id}-{d.isoformat()}-{hour}",
    }


def _make_session(session_id: str, first: date, last: date):
    return {
        "session_id":           session_id,
        "project_name":         "user/proj",
        "first_timestamp":      _ts(first),
        "last_timestamp":       _ts(last),
        "git_branch":           "main",
        "model":                "claude-sonnet-4-6",
        "total_input_tokens":   10,
        "total_output_tokens":  5,
        "total_cache_read":     0,
        "total_cache_creation": 0,
        "turn_count":           1,
    }


class TestComputeStreak(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.conn = get_db(self.db_path)
        init_db(self.conn)
        # _compute_streak reads rows via row_factory access patterns; make it
        # match what dashboard.get_dashboard_data sets up at runtime.
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def _seed(self, days):
        """Insert one turn per provided date plus the matching session rows."""
        sessions, turns = [], []
        for i, d in enumerate(days):
            sid = f"sess-{i}-{d.isoformat()}"
            sessions.append(_make_session(sid, d, d))
            turns.append(_make_turn(sid, d))
        if sessions:
            upsert_sessions(self.conn, sessions)
            insert_turns(self.conn, turns)
            self.conn.commit()

    def test_empty_db_returns_zero(self):
        self.assertEqual(_compute_streak(self.conn), 0)

    def test_three_consecutive_days_returns_three(self):
        today = date(2026, 5, 23)
        self._seed([today - timedelta(days=2), today - timedelta(days=1), today])
        self.assertEqual(_compute_streak(self.conn, today=today), 3)

    def test_only_today_returns_one(self):
        today = date(2026, 5, 23)
        self._seed([today])
        self.assertEqual(_compute_streak(self.conn, today=today), 1)

    def test_gap_counts_only_unbroken_run_anchored_today(self):
        # Activity on T-5, T-4 (the old streak) and T-1, T (the new streak).
        # The gap on T-3 and T-2 breaks the old run, so only the trailing
        # two-day run anchored at today counts.
        today = date(2026, 5, 23)
        self._seed([
            today - timedelta(days=5),
            today - timedelta(days=4),
            today - timedelta(days=1),
            today,
        ])
        self.assertEqual(_compute_streak(self.conn, today=today), 2)

    def test_streak_zero_when_today_missing(self):
        # The streak is anchored at today — yesterday + day-before-yesterday
        # alone does not count. (This is the "no activity today" case.)
        today = date(2026, 5, 23)
        self._seed([today - timedelta(days=2), today - timedelta(days=1)])
        self.assertEqual(_compute_streak(self.conn, today=today), 0)

    def test_future_timestamps_are_ignored(self):
        # A future-dated row from a bad clock must not inflate the streak.
        today = date(2026, 5, 23)
        self._seed([
            today - timedelta(days=1),
            today,
            today + timedelta(days=1),
            today + timedelta(days=5),
        ])
        self.assertEqual(_compute_streak(self.conn, today=today), 2)

    def test_multiple_turns_per_day_count_once(self):
        # Days only matter as a set; many turns on the same day == one day.
        today = date(2026, 5, 23)
        sessions, turns = [], []
        sid = "sess-multi"
        sessions.append(_make_session(sid, today, today))
        for hour in (9, 10, 11, 13, 17):
            turns.append(_make_turn(sid, today, hour=hour))
        upsert_sessions(self.conn, sessions)
        insert_turns(self.conn, turns)
        self.conn.commit()
        self.assertEqual(_compute_streak(self.conn, today=today), 1)


class TestStreakInDashboardPayload(unittest.TestCase):
    """The streak must be exposed on the /api/data payload dict."""

    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        conn = get_db(self.db_path)
        init_db(conn)
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_payload_contains_streak_key(self):
        data = get_dashboard_data(db_path=self.db_path)
        self.assertIn("streak", data)
        self.assertIsInstance(data["streak"], int)

    def test_payload_streak_is_zero_for_empty_db(self):
        data = get_dashboard_data(db_path=self.db_path)
        self.assertEqual(data["streak"], 0)


if __name__ == "__main__":
    unittest.main()
