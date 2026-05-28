"""Tests for subagent vs main-thread split."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cowork
import scanner
import dashboard


class TestCoworkSubagentFlag(unittest.TestCase):
    def test_subagent_flagged_via_parent_tool_use_id(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
        # One main-thread result (no parent_tool_use_id) + one sub-agent result.
        f.write(json.dumps({
            "type": "result", "session_id": "sid1",
            "_audit_timestamp": "2026-04-01T10:00:00Z",
            "modelUsage": {"claude-opus-4-7": {"inputTokens": 100, "outputTokens": 50,
                                               "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}},
        }) + "\n")
        f.write(json.dumps({
            "type": "result", "session_id": "sid1",
            "_audit_timestamp": "2026-04-01T10:01:00Z",
            "parent_tool_use_id": "Task_abc",
            "modelUsage": {"claude-haiku-4-5": {"inputTokens": 1000, "outputTokens": 200,
                                                "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}},
        }) + "\n")
        f.close()
        _, turns, _ = cowork.parse_audit_file(f.name)
        import os; os.unlink(f.name)
        flags = {t["model"]: t["tool_name"] for t in turns}
        self.assertIsNone(flags["claude-opus-4-7"])
        self.assertEqual(flags["claude-haiku-4-5"], "subagent")


class TestSubagentSplit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "u.db"
        conn = scanner.get_db(self.db)
        scanner.init_db(conn)
        scanner.upsert_sessions(conn, [{
            "session_id": "s", "project_name": "p",
            "first_timestamp": "2026-04-01T10:00:00Z",
            "last_timestamp":  "2026-04-01T10:00:00Z",
            "git_branch": "", "model": "claude-opus-4-7", "turn_count": 2,
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cache_read": 0, "total_cache_creation": 0,
        }])
        # opus 1M output @ $25/MTok -> $25 (main)
        # haiku 5M output @ $5/MTok -> $25 (subagent)
        scanner.insert_turns(conn, [
            {"session_id": "s", "timestamp": "2026-04-01T10:00:00Z",
             "model": "claude-opus-4-7", "input_tokens": 0, "output_tokens": 1_000_000,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": None, "cwd": "/p", "message_id": "m1"},
            {"session_id": "s", "timestamp": "2026-04-01T10:30:00Z",
             "model": "claude-haiku-4-5", "input_tokens": 0, "output_tokens": 5_000_000,
             "cache_read_tokens": 0, "cache_creation_tokens": 0,
             "tool_name": "subagent", "cwd": "/p", "message_id": "m2"},
        ])
        conn.commit()
        self.conn = conn

    def tearDown(self):
        self.conn.close()
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_split_50_50(self):
        s = dashboard._subagent_split(self.conn)
        self.assertAlmostEqual(s["main"], 25.0, places=1)
        self.assertAlmostEqual(s["subagent"], 25.0, places=1)
        self.assertEqual(s["main_pct"], 50.0)


if __name__ == "__main__":
    unittest.main()
