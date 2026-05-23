"""Tests for the PII / sensitive-content scanner in dashboard.py.

These verify the SOFT-warning helpers — defaults, user override loading,
match behaviour, and the JS render helper presence. The scanner never
blocks anything; it just flags sessions in the dashboard JSON.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard import (
    HTML_TEMPLATE,
    _default_pii_patterns,
    _load_pii_patterns,
    _pii_check,
    get_dashboard_data,
)
from scanner import get_db, init_db, upsert_sessions, insert_turns


class TestPiiDefaults(unittest.TestCase):
    def test_defaults_present(self):
        """The built-in pattern list must include the headline markers."""
        patterns = _default_pii_patterns()
        self.assertIsInstance(patterns, list)
        self.assertGreater(len(patterns), 0)
        # Sanity-check a few headline patterns the spec calls out.
        joined = " ".join(patterns).lower()
        for needle in ["secret", "credential", "password", "token", ".env"]:
            self.assertIn(needle, joined)


class TestLoadPiiPatterns(unittest.TestCase):
    def test_user_override_loaded(self):
        """A valid JSON list at the override path must replace defaults."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(["mycustompattern", "another"], f)
            override_path = Path(f.name)
        try:
            loaded = _load_pii_patterns(path=override_path)
            self.assertEqual(loaded, ["mycustompattern", "another"])
        finally:
            os.unlink(override_path)

    def test_missing_file_falls_back_to_defaults(self):
        """When the override file does not exist, defaults are returned."""
        loaded = _load_pii_patterns(path=Path("/nonexistent/pii-patterns.json"))
        self.assertEqual(loaded, _default_pii_patterns())

    def test_malformed_file_falls_back_to_defaults(self):
        """Bad JSON or wrong shape must not crash — fall back silently."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("{ not valid json")
            bad_path = Path(f.name)
        try:
            loaded = _load_pii_patterns(path=bad_path)
            self.assertEqual(loaded, _default_pii_patterns())
        finally:
            os.unlink(bad_path)


class TestPiiCheck(unittest.TestCase):
    def test_project_with_credentials_matched(self):
        """A project name containing 'credentials' must be flagged."""
        patterns = _default_pii_patterns()
        matches = _pii_check("acme/credentials-store main", patterns)
        self.assertTrue(any("credential" in m for m in matches))

    def test_project_without_sensitive_content_unmatched(self):
        """A vanilla project name yields an empty match list."""
        patterns = _default_pii_patterns()
        matches = _pii_check("user/myproject main", patterns)
        self.assertEqual(matches, [])

    def test_dotenv_path_matched(self):
        """Branches/paths containing '.env' are flagged."""
        patterns = _default_pii_patterns()
        matches = _pii_check("user/repo feature/.env-loader", patterns)
        self.assertTrue(any(".env" in m for m in matches))

    def test_empty_text_returns_empty(self):
        self.assertEqual(_pii_check("", _default_pii_patterns()), [])
        self.assertEqual(_pii_check(None, _default_pii_patterns()), [])

    def test_bad_regex_skipped(self):
        """Invalid regex patterns must not raise."""
        matches = _pii_check("anything", ["[invalid(", "secret"])
        # Bad pattern silently skipped; valid one still works.
        self.assertNotIn("[invalid(", matches)


class TestSensitiveMatchInDashboardData(unittest.TestCase):
    """End-to-end: get_dashboard_data must attach `sensitive_match`."""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpfile.close()
        self.db_path = Path(self.tmpfile.name)
        conn = get_db(self.db_path)
        init_db(conn)
        sessions = [
            {
                "session_id": "sess-secret-1", "project_name": "acme/credentials",
                "first_timestamp": "2026-04-08T09:00:00Z",
                "last_timestamp": "2026-04-08T10:00:00Z",
                "git_branch": "main", "model": "claude-sonnet-4-6",
                "total_input_tokens": 100, "total_output_tokens": 50,
                "total_cache_read": 0, "total_cache_creation": 0,
                "turn_count": 1,
            },
            {
                "session_id": "sess-clean-1", "project_name": "user/myproject",
                "first_timestamp": "2026-04-08T11:00:00Z",
                "last_timestamp": "2026-04-08T12:00:00Z",
                "git_branch": "main", "model": "claude-sonnet-4-6",
                "total_input_tokens": 100, "total_output_tokens": 50,
                "total_cache_read": 0, "total_cache_creation": 0,
                "turn_count": 1,
            },
        ]
        upsert_sessions(conn, sessions)
        turns = [
            {
                "session_id": "sess-secret-1", "timestamp": "2026-04-08T09:30:00Z",
                "model": "claude-sonnet-4-6", "input_tokens": 100,
                "output_tokens": 50, "cache_read_tokens": 0,
                "cache_creation_tokens": 0, "tool_name": None, "cwd": "/tmp",
            },
            {
                "session_id": "sess-clean-1", "timestamp": "2026-04-08T11:30:00Z",
                "model": "claude-sonnet-4-6", "input_tokens": 100,
                "output_tokens": 50, "cache_read_tokens": 0,
                "cache_creation_tokens": 0, "tool_name": None, "cwd": "/tmp",
            },
        ]
        insert_turns(conn, turns)
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_sensitive_match_attached(self):
        data = get_dashboard_data(db_path=self.db_path)
        by_proj = {s["project"]: s for s in data["sessions_all"]}
        self.assertIn("sensitive_match", by_proj["acme/credentials"])
        self.assertIn("sensitive_match", by_proj["user/myproject"])
        # The sensitive project should have at least one match.
        self.assertGreater(
            len(by_proj["acme/credentials"]["sensitive_match"]), 0
        )
        # The clean project should have none.
        self.assertEqual(by_proj["user/myproject"]["sensitive_match"], [])


class TestJsRenderHelperPresent(unittest.TestCase):
    def test_js_render_helper_present(self):
        """The JS helper _renderSensitiveBadge must be bundled in the page."""
        self.assertIn("_renderSensitiveBadge", HTML_TEMPLATE)
        # And it must be wired into the session cell render.
        self.assertIn("piiBadge", HTML_TEMPLATE)

    def test_badge_emoji_present(self):
        """The warning glyph must be rendered (HTML entity for ⚠)."""
        self.assertIn("&#9888;", HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
