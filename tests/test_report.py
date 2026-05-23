"""Tests for `python cli.py report` — Markdown usage report generation."""

import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path

from scanner import get_db, init_db, upsert_sessions, insert_turns
import cli


def _today_iso():
    return date.today().isoformat()


def _days_ago_iso(n):
    return (date.today() - timedelta(days=n)).isoformat()


class TestPeriodBounds(unittest.TestCase):
    """The period -> (start, end) helper."""

    def test_30d_bounds(self):
        ref = date(2026, 5, 23)
        start, end = cli._period_bounds("30d", today=ref)
        # Inclusive 30-day window
        self.assertEqual(end, "2026-05-23")
        self.assertEqual(start, "2026-04-24")

    def test_7d_bounds(self):
        ref = date(2026, 5, 23)
        start, end = cli._period_bounds("7d", today=ref)
        self.assertEqual(end, "2026-05-23")
        self.assertEqual(start, "2026-05-17")

    def test_all_bounds(self):
        ref = date(2026, 5, 23)
        start, end = cli._period_bounds("all", today=ref)
        self.assertIsNone(start)
        self.assertEqual(end, "2026-05-23")

    def test_unknown_period_raises(self):
        with self.assertRaises(ValueError):
            cli._period_bounds("ever")


class TestReportEmptyDB(unittest.TestCase):
    """When the DB doesn't exist, the report still has a valid markdown skeleton."""

    def test_skeleton_for_missing_db(self):
        report = cli.build_report(period="30d", db_path=Path("/nonexistent/path/usage.db"))
        # Required header
        self.assertIn("# Claude Usage Report", report)
        # Required sections — even with no data
        self.assertIn("## Totals", report)
        self.assertIn("## Top Projects", report)
        self.assertIn("## Top Sessions", report)
        self.assertIn("## Breakdown by Model", report)
        self.assertIn("## Forecast", report)
        # Zeroed totals shown
        self.assertIn("Turns: 0", report)
        self.assertIn("Input tokens: 0", report)
        # Forecast explicitly says it can't be computed
        self.assertIn("Not enough data to compute a forecast", report)

    def test_skeleton_for_missing_db_7d(self):
        report = cli.build_report(period="7d", db_path=Path("/nonexistent/path/usage.db"))
        self.assertIn("# Claude Usage Report", report)
        self.assertIn("Last 7 days", report)

    def test_skeleton_for_missing_db_all(self):
        report = cli.build_report(period="all", db_path=Path("/nonexistent/path/usage.db"))
        self.assertIn("# Claude Usage Report", report)
        self.assertIn("All-time", report)


class TestReportWithSeededData(unittest.TestCase):
    """Seed a DB with realistic data and verify report content."""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpfile.close()
        self.db_path = Path(self.tmpfile.name)
        conn = get_db(self.db_path)
        init_db(conn)
        # Two recent sessions on two different projects + two models, with
        # tool_name data on the turns so the by-tool section is exercised too.
        today = _today_iso()
        yesterday = _days_ago_iso(1)
        upsert_sessions(conn, [
            {
                "session_id": "sess-aaaa1111", "project_name": "user/alpha",
                "first_timestamp": f"{yesterday}T09:00:00Z",
                "last_timestamp":  f"{yesterday}T10:00:00Z",
                "git_branch": "main", "model": "claude-sonnet-4-6",
                "total_input_tokens": 5000, "total_output_tokens": 2000,
                "total_cache_read": 500, "total_cache_creation": 200,
                "turn_count": 10,
            },
            {
                "session_id": "sess-bbbb2222", "project_name": "user/beta",
                "first_timestamp": f"{today}T09:00:00Z",
                "last_timestamp":  f"{today}T10:00:00Z",
                "git_branch": "feat", "model": "claude-opus-4-7",
                "total_input_tokens": 8000, "total_output_tokens": 3000,
                "total_cache_read": 0, "total_cache_creation": 0,
                "turn_count": 12,
            },
        ])
        insert_turns(conn, [
            {"session_id": "sess-aaaa1111", "timestamp": f"{yesterday}T09:30:00Z",
             "model": "claude-sonnet-4-6", "input_tokens": 2500, "output_tokens": 1000,
             "cache_read_tokens": 250, "cache_creation_tokens": 100,
             "tool_name": "Read", "cwd": "/tmp", "message_id": "m-aaa-1"},
            {"session_id": "sess-bbbb2222", "timestamp": f"{today}T09:30:00Z",
             "model": "claude-opus-4-7", "input_tokens": 4000, "output_tokens": 1500,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": "Bash", "cwd": "/tmp", "message_id": "m-bbb-1"},
        ])
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_report_has_header_and_required_sections(self):
        report = cli.build_report(period="30d", db_path=self.db_path)
        self.assertIn("# Claude Usage Report", report)
        self.assertIn("## Totals", report)
        self.assertIn("## Top Projects", report)
        self.assertIn("## Top Sessions", report)
        self.assertIn("## Breakdown by Model", report)
        self.assertIn("## Forecast", report)

    def test_report_lists_project_names(self):
        report = cli.build_report(period="30d", db_path=self.db_path)
        self.assertIn("user/alpha", report)
        self.assertIn("user/beta", report)

    def test_report_lists_models(self):
        report = cli.build_report(period="30d", db_path=self.db_path)
        self.assertIn("claude-sonnet-4-6", report)
        self.assertIn("claude-opus-4-7", report)

    def test_report_includes_totals(self):
        report = cli.build_report(period="30d", db_path=self.db_path)
        # 2 sessions, summed input across daily rows = 6,500 input tokens
        self.assertIn("Sessions: 2", report)
        # Comma-formatted numbers
        self.assertIn("6,500", report)   # total input tokens (2500 + 4000)
        self.assertIn("2,500", report)   # total output tokens (1000 + 1500)

    def test_report_includes_tool_breakdown_when_present(self):
        report = cli.build_report(period="30d", db_path=self.db_path)
        self.assertIn("## Breakdown by Tool", report)
        self.assertIn("Read", report)
        self.assertIn("Bash", report)

    def test_report_forecast_computed_with_multi_day_data(self):
        report = cli.build_report(period="30d", db_path=self.db_path)
        # Two distinct turn days -> forecast block should appear with numbers
        self.assertIn("Active days in period: 2", report)
        self.assertIn("Projected 30-day cost:", report)

    def test_period_7d_still_renders_full_skeleton(self):
        report = cli.build_report(period="7d", db_path=self.db_path)
        self.assertIn("# Claude Usage Report", report)
        self.assertIn("Last 7 days", report)
        self.assertIn("user/alpha", report)


class TestReportWritesToFile(unittest.TestCase):
    """`--out FILE` writes the report to disk instead of stdout."""

    def setUp(self):
        self.tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpdb.close()
        self.db_path = Path(self.tmpdb.name)
        conn = get_db(self.db_path)
        init_db(conn)
        today = _today_iso()
        upsert_sessions(conn, [{
            "session_id": "sess-out-test", "project_name": "user/out",
            "first_timestamp": f"{today}T09:00:00Z",
            "last_timestamp":  f"{today}T10:00:00Z",
            "git_branch": "main", "model": "claude-sonnet-4-6",
            "total_input_tokens": 1000, "total_output_tokens": 500,
            "total_cache_read": 0, "total_cache_creation": 0,
            "turn_count": 3,
        }])
        insert_turns(conn, [{
            "session_id": "sess-out-test", "timestamp": f"{today}T09:30:00Z",
            "model": "claude-sonnet-4-6",
            "input_tokens": 1000, "output_tokens": 500,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "tool_name": None, "cwd": "/tmp", "message_id": "m-out-1",
        }])
        conn.commit()
        conn.close()

        self.outfile = tempfile.NamedTemporaryFile(
            suffix=".md", delete=False, mode="w"
        )
        self.outfile.close()
        self.out_path = self.outfile.name

    def tearDown(self):
        os.unlink(self.db_path)
        if os.path.exists(self.out_path):
            os.unlink(self.out_path)

    def test_out_flag_writes_file_with_valid_markdown(self):
        # Point cli.DB_PATH at our temp DB so cmd_report picks it up.
        orig_db = cli.DB_PATH
        cli.DB_PATH = self.db_path
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                cli.cmd_report(period="30d", out=self.out_path)
        finally:
            cli.DB_PATH = orig_db

        # Stdout confirms the write
        self.assertIn("Report written to", buf.getvalue())
        # File exists and contains the report
        self.assertTrue(os.path.exists(self.out_path))
        content = Path(self.out_path).read_text(encoding="utf-8")
        self.assertIn("# Claude Usage Report", content)
        self.assertIn("user/out", content)
        self.assertIn("## Totals", content)

    def test_no_out_flag_prints_to_stdout(self):
        orig_db = cli.DB_PATH
        cli.DB_PATH = self.db_path
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                cli.cmd_report(period="30d", out=None)
        finally:
            cli.DB_PATH = orig_db
        output = buf.getvalue()
        self.assertIn("# Claude Usage Report", output)
        self.assertIn("user/out", output)


class TestReportRegistered(unittest.TestCase):
    """`report` must be wired into COMMANDS and USAGE."""

    def test_in_commands_dict(self):
        self.assertIn("report", cli.COMMANDS)
        self.assertIs(cli.COMMANDS["report"], cli.cmd_report)

    def test_in_usage_string(self):
        self.assertIn("report", cli.USAGE)
        self.assertIn("--period", cli.USAGE)
        self.assertIn("--out", cli.USAGE)


if __name__ == "__main__":
    unittest.main()
