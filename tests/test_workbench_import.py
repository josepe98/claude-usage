"""Tests for cli.cmd_import_workbench - Anthropic Console/Workbench JSON import."""

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


class WorkbenchImportTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "usage.db"

        # Patch DB_PATH in cli so the importer writes to our temp DB
        import cli
        self._orig_db_path = cli.DB_PATH
        cli.DB_PATH = self.db_path

    def tearDown(self):
        import cli
        cli.DB_PATH = self._orig_db_path

    # ── helpers ─────────────────────────────────────────────────────────────

    def _write_json(self, name, data):
        path = Path(self.tmpdir) / name
        path.write_text(json.dumps(data))
        return path

    def _run_import(self, path):
        from cli import cmd_import_workbench
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_import_workbench(str(path))
        return buf.getvalue()

    def _count_turns(self):
        conn = sqlite3.connect(self.db_path)
        n = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        conn.close()
        return n

    def _all_turns(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM turns").fetchall()
        conn.close()
        return rows

    # ── tests ───────────────────────────────────────────────────────────────

    def test_empty_array_skips_gracefully(self):
        path = self._write_json("empty.json", [])
        out = self._run_import(path)
        self.assertIn("No importable entries", out)
        # DB may not be initialised when nothing imports, so just confirm
        # that either no DB exists, or no turns were written.
        if self.db_path.exists():
            conn = sqlite3.connect(self.db_path)
            try:
                n = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
                self.assertEqual(n, 0)
            except sqlite3.OperationalError:
                pass  # turns table never created — also fine
            finally:
                conn.close()

    def test_imports_valid_records(self):
        path = self._write_json("export.json", [
            {
                "model": "claude-sonnet-4-5-20250929",
                "input_tokens": 1200,
                "output_tokens": 400,
                "timestamp": "2026-04-01T10:00:00Z",
            },
            {
                "model": "claude-opus-4-7",
                "input_tokens": 800,
                "output_tokens": 250,
                "cache_read_input_tokens": 5000,
                "timestamp": "2026-04-01T10:05:00Z",
            },
        ])

        out = self._run_import(path)
        self.assertIn("Imported entries:    2", out)
        self.assertIn("Total tokens:", out)

        turns = self._all_turns()
        self.assertEqual(len(turns), 2)
        # All imported turns should belong to the same wb-* session
        sids = {t["session_id"] for t in turns}
        self.assertEqual(len(sids), 1)
        self.assertTrue(next(iter(sids)).startswith("wb-"))

        # Session row populated with project_name=Workbench
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        sess = conn.execute("SELECT * FROM sessions").fetchone()
        conn.close()
        self.assertEqual(sess["project_name"], "Workbench")
        self.assertEqual(sess["git_branch"], "")
        self.assertEqual(sess["turn_count"], 2)
        self.assertEqual(sess["total_input_tokens"], 2000)
        self.assertEqual(sess["total_output_tokens"], 650)
        self.assertEqual(sess["total_cache_read"], 5000)

    def test_skips_malformed_entries(self):
        path = self._write_json("mixed.json", [
            {"model": "claude-sonnet-4-5", "input_tokens": 100, "output_tokens": 50,
             "timestamp": "2026-04-01T10:00:00Z"},
            "not a dict",                                    # skipped
            {"input_tokens": 100, "output_tokens": 50},      # no model → skipped
            {"model": "claude-haiku-4-5", "input_tokens": 0,
             "output_tokens": 0},                            # zero usage → skipped
            {"model": "claude-opus-4-7", "input_tokens": "abc",  # bad int still ok if other tokens
             "output_tokens": 10, "timestamp": "2026-04-01T10:10:00Z"},
        ])

        out = self._run_import(path)
        self.assertIn("Imported entries:    2", out)
        self.assertIn("Skipped (malformed): 3", out)
        self.assertEqual(self._count_turns(), 2)

    def test_idempotent_reimport(self):
        path = self._write_json("dup.json", [
            {"model": "claude-sonnet-4-5", "input_tokens": 500,
             "output_tokens": 100, "timestamp": "2026-04-01T10:00:00Z"},
            {"model": "claude-opus-4-7", "input_tokens": 300,
             "output_tokens": 80, "timestamp": "2026-04-01T10:01:00Z"},
        ])

        self._run_import(path)
        first_count = self._count_turns()

        out = self._run_import(path)  # re-import same file
        second_count = self._count_turns()

        self.assertEqual(first_count, second_count)
        self.assertEqual(second_count, 2)
        # Second import should report 0 imported, 2 already present
        self.assertIn("Imported entries:    0", out)
        self.assertIn("Already present:     2", out)

        # Session totals should still be correct (not doubled)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        sess = conn.execute("SELECT * FROM sessions").fetchone()
        conn.close()
        self.assertEqual(sess["total_input_tokens"], 800)
        self.assertEqual(sess["total_output_tokens"], 180)
        self.assertEqual(sess["turn_count"], 2)

    def test_summary_includes_counts_and_tokens(self):
        path = self._write_json("summary.json", [
            {"model": "claude-sonnet-4-5", "input_tokens": 1500,
             "output_tokens": 500, "timestamp": "2026-04-01T10:00:00Z"},
        ])
        out = self._run_import(path)
        self.assertIn("Workbench import:", out)
        self.assertIn("Imported entries:", out)
        self.assertIn("Skipped (malformed):", out)
        self.assertIn("Total tokens:", out)
        self.assertIn("Session id:", out)

    def test_accepts_object_wrapper_with_runs_key(self):
        path = self._write_json("wrapped.json", {
            "exported_at": "2026-04-01",
            "runs": [
                {"model": "claude-sonnet-4-5", "input_tokens": 100,
                 "output_tokens": 50, "timestamp": "2026-04-01T10:00:00Z"},
            ],
        })
        self._run_import(path)
        self.assertEqual(self._count_turns(), 1)

    def test_accepts_camel_case_and_nested_usage(self):
        path = self._write_json("camel.json", [
            {
                "model": "claude-sonnet-4-5",
                "usage": {"inputTokens": 200, "outputTokens": 50,
                          "cacheReadInputTokens": 1000},
                "createdAt": "2026-04-01T10:00:00Z",
            },
        ])
        self._run_import(path)
        rows = self._all_turns()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["input_tokens"], 200)
        self.assertEqual(rows[0]["output_tokens"], 50)
        self.assertEqual(rows[0]["cache_read_tokens"], 1000)

    def test_missing_file_exits(self):
        from cli import cmd_import_workbench
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()):
                cmd_import_workbench("/nonexistent/path/nope.json")

    def test_invalid_json_exits(self):
        path = Path(self.tmpdir) / "broken.json"
        path.write_text("{ this is not json")
        from cli import cmd_import_workbench
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()):
                cmd_import_workbench(str(path))


if __name__ == "__main__":
    unittest.main()
