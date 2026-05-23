"""Tests for `cli.py timeline <session_id>`.

Covers:
  - writes markdown to stdout when no --out given
  - writes markdown to file when --out FILE is given
  - prints a clear message for an unknown session id (and exits non-zero)
  - includes the tool histogram section
"""

import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import cli
from scanner import get_db, init_db, upsert_sessions, insert_turns


def _build_fixture_db(db_path: Path) -> str:
    """Create a small DB with one session + a few turns. Returns full session id."""
    conn = get_db(db_path)
    init_db(conn)
    full_id = "deadbeef-cafe-4f00-9abc-1234567890ab"
    upsert_sessions(conn, [{
        "session_id": full_id,
        "project_name": "user/myproject",
        "first_timestamp": "2026-05-23T10:15:23Z",
        "last_timestamp":  "2026-05-23T10:16:00Z",
        "git_branch": "main",
        "model": "claude-opus-4-7",
        "total_input_tokens": 1200,
        "total_output_tokens": 800,
        "total_cache_read": 0,
        "total_cache_creation": 0,
        "turn_count": 3,
    }])
    insert_turns(conn, [
        {
            "session_id": full_id,
            "timestamp": "2026-05-23T10:15:23Z",
            "model": "claude-opus-4-7",
            "input_tokens": 500, "output_tokens": 300,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "tool_name": "Bash", "cwd": "/tmp",
        },
        {
            "session_id": full_id,
            "timestamp": "2026-05-23T10:15:45Z",
            "model": "claude-opus-4-7",
            "input_tokens": 400, "output_tokens": 250,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "tool_name": "Bash", "cwd": "/tmp",
        },
        {
            "session_id": full_id,
            "timestamp": "2026-05-23T10:16:00Z",
            "model": "claude-opus-4-7",
            "input_tokens": 300, "output_tokens": 250,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "tool_name": None, "cwd": "/tmp",
        },
    ])
    conn.commit()
    conn.close()
    return full_id


class TimelineTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpfile.close()
        self.db_path = Path(self.tmpfile.name)
        self.full_id = _build_fixture_db(self.db_path)
        self._db_patch = mock.patch.object(cli, "DB_PATH", self.db_path)
        self._db_patch.start()

    def tearDown(self):
        self._db_patch.stop()
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass


class TestTimelineStdout(TimelineTestBase):
    def test_writes_markdown_to_stdout(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_timeline(self.full_id[:8])
        out = buf.getvalue()

        # H1 with session short id and project
        self.assertIn(f"# Session `{self.full_id[:8]}`", out)
        self.assertIn("user/myproject", out)
        # Per-turn rows: time, model, tool
        self.assertIn("10:15:23", out)
        self.assertIn("claude-opus-4-7", out)
        self.assertIn("Bash", out)
        self.assertIn("(no tool)", out)
        # Totals block + cost rendering
        self.assertIn("Total cost:", out)
        self.assertRegex(out, r"\$\d+\.\d{4}")


class TestTimelineFileOutput(TimelineTestBase):
    def test_writes_to_file_when_out_given(self):
        out_dir = tempfile.mkdtemp()
        out_path = Path(out_dir) / "timeline.md"
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.cmd_timeline(self.full_id[:8], out=str(out_path))

            self.assertTrue(out_path.exists())
            content = out_path.read_text(encoding="utf-8")
            self.assertIn(f"# Session `{self.full_id[:8]}`", content)
            self.assertIn("## Turns", content)

            # Stdout only contains the confirmation, not the markdown body
            stdout = buf.getvalue()
            self.assertIn(str(out_path), stdout)
            self.assertNotIn("## Turns", stdout)
        finally:
            if out_path.exists():
                out_path.unlink()
            os.rmdir(out_dir)


class TestTimelineUnknownSession(TimelineTestBase):
    def test_unknown_session_prints_message_and_exits(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as cm:
                cli.cmd_timeline("00000000")
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn("Unknown session id", buf.getvalue())


class TestTimelineToolHistogram(TimelineTestBase):
    def test_histogram_section_present_with_counts(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_timeline(self.full_id[:8])
        out = buf.getvalue()

        self.assertIn("## Tool histogram", out)
        # Two Bash turns + one untool turn
        self.assertRegex(out, r"`Bash`:\s*2")
        self.assertRegex(out, r"`\(no tool\)`:\s*1")


if __name__ == "__main__":
    unittest.main()
