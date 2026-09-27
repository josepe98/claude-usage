"""Tests for time-on-task tracking."""
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli
import dashboard
from scanner import get_db, init_db, insert_turns, upsert_sessions


def _mk_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return Path(f.name)


class TestSessionActiveMinutes(unittest.TestCase):
    def test_single_turn_session_is_zero(self):
        self.assertEqual(
            dashboard._session_active_minutes(["2026-05-20T10:00:00Z"]),
            0.0,
        )

    def test_empty_session_is_zero(self):
        self.assertEqual(dashboard._session_active_minutes([]), 0.0)
        self.assertEqual(dashboard._session_active_minutes([None, ""]), 0.0)

    def test_small_gaps_under_threshold_count(self):
        # 1 + 2 + 3 = 6 minutes of active time
        ts = [
            "2026-05-20T10:00:00Z",
            "2026-05-20T10:01:00Z",
            "2026-05-20T10:03:00Z",
            "2026-05-20T10:06:00Z",
        ]
        self.assertEqual(dashboard._session_active_minutes(ts), 6.0)

    def test_large_gap_at_or_above_threshold_is_break(self):
        # 2-min gap + 5-min "break" + 1-min gap = 3 minutes active
        ts = [
            "2026-05-20T10:00:00Z",
            "2026-05-20T10:02:00Z",
            "2026-05-20T10:07:00Z",  # exactly 5 min — counted as break
            "2026-05-20T10:08:00Z",
        ]
        self.assertEqual(dashboard._session_active_minutes(ts), 3.0)

    def test_unsorted_timestamps_are_sorted(self):
        ts = [
            "2026-05-20T10:03:00Z",
            "2026-05-20T10:00:00Z",
            "2026-05-20T10:01:00Z",
        ]
        # Sorted: 10:00, 10:01, 10:03 -> 1 + 2 = 3 active minutes
        self.assertEqual(dashboard._session_active_minutes(ts), 3.0)


class TestTimeOnTaskAggregation(unittest.TestCase):
    def setUp(self):
        self.db_path = _mk_db()
        self.conn = get_db(self.db_path)
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def _insert_session(self, sid, turns):
        upsert_sessions(self.conn, [{
            "session_id": sid, "project_name": "p",
            "first_timestamp": turns[0]["timestamp"],
            "last_timestamp": turns[-1]["timestamp"],
            "git_branch": "main", "model": "claude-sonnet-4-6",
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cache_read": 0, "total_cache_creation": 0,
            "turn_count": len(turns),
        }])
        insert_turns(self.conn, [
            {
                "session_id": sid, "timestamp": t["timestamp"],
                "model": "claude-sonnet-4-6", "input_tokens": 0,
                "output_tokens": 0, "cache_read_tokens": 0,
                "cache_creation_tokens": 0, "tool_name": None, "cwd": "/",
            } for t in turns
        ])
        self.conn.commit()

    def test_returns_30_days(self):
        rows = dashboard._time_on_task(self.conn)
        self.assertEqual(len(rows), 30)
        self.assertTrue(all("day" in r and "active_minutes" in r for r in rows))

    def test_zero_when_no_data(self):
        rows = dashboard._time_on_task(self.conn)
        self.assertEqual(sum(r["active_minutes"] for r in rows), 0.0)

    def test_multi_day_aggregation(self):
        d1 = date(2026, 5, 20)
        d2 = date(2026, 5, 21)
        # day 1: 4 minutes active
        self._insert_session("s1", [
            {"timestamp": d1.isoformat() + "T10:00:00Z"},
            {"timestamp": d1.isoformat() + "T10:01:00Z"},
            {"timestamp": d1.isoformat() + "T10:04:00Z"},
        ])
        # day 2: 2 minutes active in one session, 10-min break, then 1 more
        self._insert_session("s2", [
            {"timestamp": d2.isoformat() + "T09:00:00Z"},
            {"timestamp": d2.isoformat() + "T09:02:00Z"},
            {"timestamp": d2.isoformat() + "T09:12:00Z"},  # 10-min break
            {"timestamp": d2.isoformat() + "T09:13:00Z"},
        ])
        rows = dashboard._time_on_task(self.conn, date=d2, days=2)
        per_day = {r["day"]: r["active_minutes"] for r in rows}
        self.assertEqual(per_day[d1.isoformat()], 4.0)
        self.assertEqual(per_day[d2.isoformat()], 3.0)

    def test_break_excluded(self):
        d = date(2026, 5, 20)
        # Two clusters separated by a 30-min break
        self._insert_session("s1", [
            {"timestamp": d.isoformat() + "T10:00:00Z"},
            {"timestamp": d.isoformat() + "T10:02:00Z"},
            {"timestamp": d.isoformat() + "T10:32:00Z"},  # break
            {"timestamp": d.isoformat() + "T10:33:00Z"},
        ])
        rows = dashboard._time_on_task(self.conn, date=d, days=1)
        self.assertEqual(rows[0]["active_minutes"], 3.0)


class TestSessionsAllActiveMinutes(unittest.TestCase):
    """active_minutes must be present on sessions_all entries."""

    def setUp(self):
        self.db_path = _mk_db()
        conn = get_db(self.db_path)
        init_db(conn)
        upsert_sessions(conn, [{
            "session_id": "sess-active", "project_name": "p",
            "first_timestamp": "2026-04-08T09:00:00Z",
            "last_timestamp": "2026-04-08T09:10:00Z",
            "git_branch": "main", "model": "claude-sonnet-4-6",
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cache_read": 0, "total_cache_creation": 0,
            "turn_count": 3,
        }])
        insert_turns(conn, [
            {"session_id": "sess-active", "timestamp": "2026-04-08T09:00:00Z",
             "model": "claude-sonnet-4-6", "input_tokens": 0, "output_tokens": 0,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": None, "cwd": "/"},
            {"session_id": "sess-active", "timestamp": "2026-04-08T09:02:00Z",
             "model": "claude-sonnet-4-6", "input_tokens": 0, "output_tokens": 0,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": None, "cwd": "/"},
            {"session_id": "sess-active", "timestamp": "2026-04-08T09:04:00Z",
             "model": "claude-sonnet-4-6", "input_tokens": 0, "output_tokens": 0,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": None, "cwd": "/"},
        ])
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_sessions_all_includes_active_minutes(self):
        data = dashboard.get_dashboard_data(db_path=self.db_path)
        self.assertEqual(len(data["sessions_all"]), 1)
        s = data["sessions_all"][0]
        self.assertIn("active_minutes", s)
        # Two gaps: 2 min and 2 min -> 4 minutes active
        self.assertEqual(s["active_minutes"], 4.0)
        # Duration is wall-clock 10 minutes
        self.assertEqual(s["duration_min"], 10.0)

    def test_response_includes_time_on_task(self):
        data = dashboard.get_dashboard_data(db_path=self.db_path)
        self.assertIn("time_on_task", data)
        self.assertEqual(len(data["time_on_task"]), 30)


class TestCliTimeCommand(unittest.TestCase):
    def setUp(self):
        self.db_path = _mk_db()
        conn = get_db(self.db_path)
        init_db(conn)
        d = date.today() - timedelta(days=1)
        upsert_sessions(conn, [{
            "session_id": "sess-cli", "project_name": "p",
            "first_timestamp": d.isoformat() + "T10:00:00Z",
            "last_timestamp": d.isoformat() + "T10:05:00Z",
            "git_branch": "main", "model": "claude-sonnet-4-6",
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cache_read": 0, "total_cache_creation": 0,
            "turn_count": 3,
        }])
        insert_turns(conn, [
            {"session_id": "sess-cli", "timestamp": d.isoformat() + "T10:00:00Z",
             "model": "claude-sonnet-4-6", "input_tokens": 0, "output_tokens": 0,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": None, "cwd": "/"},
            {"session_id": "sess-cli", "timestamp": d.isoformat() + "T10:02:00Z",
             "model": "claude-sonnet-4-6", "input_tokens": 0, "output_tokens": 0,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": None, "cwd": "/"},
            {"session_id": "sess-cli", "timestamp": d.isoformat() + "T10:05:00Z",
             "model": "claude-sonnet-4-6", "input_tokens": 0, "output_tokens": 0,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": None, "cwd": "/"},
        ])
        conn.commit()
        conn.close()
        self._orig_db_path = cli.DB_PATH
        cli.DB_PATH = self.db_path

    def tearDown(self):
        cli.DB_PATH = self._orig_db_path
        os.unlink(self.db_path)

    def test_cmd_time_prints_breakdown(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_time()
        out = buf.getvalue()
        self.assertIn("Time on Task", out)
        self.assertIn("Today:", out)
        self.assertIn("30-day avg:", out)
        self.assertIn("30-day total:", out)
        self.assertIn("Per Day:", out)
        # Yesterday\'s gaps: 2 + 3 = 5m active -> should appear
        self.assertIn("5m", out)


if __name__ == "__main__":
    unittest.main()
