"""Tests for project display-name overrides.

Covers:
  * `_load_project_aliases` / `_save_project_aliases` helpers
  * `sessions_all` carrying a `display_name` field
  * Session detail carrying a `display_name` field
  * `POST /api/project-name` endpoint (set, update, clear, validation)
  * UI surface (helper functions present in the HTML template)
"""

import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch

import dashboard
from dashboard import (
    DashboardHandler,
    HTML_TEMPLATE,
    _load_project_aliases,
    _save_project_aliases,
    get_dashboard_data,
    get_session_detail,
)
from scanner import get_db, init_db, upsert_sessions


class TestAliasFileIO(unittest.TestCase):
    """Round-trip the JSON file through the helpers."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "project-names.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_missing_file_returns_empty(self):
        self.assertEqual(_load_project_aliases(self.path), {})

    def test_load_malformed_json_returns_empty(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(_load_project_aliases(self.path), {})

    def test_load_non_object_returns_empty(self):
        # The spec is "raw_name -> display_name"; a list is meaningless.
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(_load_project_aliases(self.path), {})

    def test_load_strips_non_string_pairs(self):
        # `"bad": 5` is a non-string value; empty key and empty value are also
        # dropped. (JSON forces all keys to strings, so integer-keyed entries
        # in the source object are not representable here.)
        self.path.write_text(
            json.dumps({"good": "ok", "bad": 5, "": "noempty", "x": ""}),
            encoding="utf-8",
        )
        self.assertEqual(_load_project_aliases(self.path), {"good": "ok"})

    def test_load_trims_whitespace(self):
        self.path.write_text(
            json.dumps({"  raw  ": "  pretty  "}), encoding="utf-8"
        )
        self.assertEqual(_load_project_aliases(self.path), {"raw": "pretty"})

    def test_save_creates_parent_dir(self):
        nested = Path(self.tmp.name) / "deep" / "deeper" / "names.json"
        _save_project_aliases({"a": "A"}, nested)
        self.assertTrue(nested.exists())
        self.assertEqual(json.loads(nested.read_text()), {"a": "A"})

    def test_save_drops_empty_display(self):
        # An empty display name *clears* the alias per spec.
        saved = _save_project_aliases({"a": "A", "b": "", "c": "   "}, self.path)
        self.assertEqual(saved, {"a": "A"})
        self.assertEqual(json.loads(self.path.read_text()), {"a": "A"})

    def test_save_then_load_roundtrip(self):
        original = {"PhpstormProjects/PPC freelo": "PPC.cz", "GitHub/jMikroFig4": "mikroFig"}
        _save_project_aliases(original, self.path)
        self.assertEqual(_load_project_aliases(self.path), original)


class TestDashboardDataExposesDisplayName(unittest.TestCase):
    """`sessions_all` must include `display_name` (empty when no alias)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "usage.db"
        self.alias_path = Path(self.tmp.name) / "project-names.json"
        conn = get_db(self.db_path)
        init_db(conn)
        upsert_sessions(conn, [
            {
                "session_id": "sess-aliased", "project_name": "user/raw-proj",
                "first_timestamp": "2026-04-08T09:00:00Z",
                "last_timestamp": "2026-04-08T10:00:00Z",
                "git_branch": "main", "model": "claude-sonnet-4-6",
                "total_input_tokens": 1, "total_output_tokens": 1,
                "total_cache_read": 0, "total_cache_creation": 0,
                "turn_count": 1,
            },
            {
                "session_id": "sess-plain", "project_name": "user/no-alias",
                "first_timestamp": "2026-04-08T09:00:00Z",
                "last_timestamp": "2026-04-08T10:00:00Z",
                "git_branch": "main", "model": "claude-sonnet-4-6",
                "total_input_tokens": 1, "total_output_tokens": 1,
                "total_cache_read": 0, "total_cache_creation": 0,
                "turn_count": 1,
            },
        ])
        conn.commit()
        conn.close()
        self._patch = patch.object(dashboard, "PROJECT_ALIASES_PATH", self.alias_path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()

    def test_display_name_empty_when_no_alias_file(self):
        data = get_dashboard_data(db_path=self.db_path)
        for s in data["sessions_all"]:
            self.assertIn("display_name", s)
            self.assertEqual(s["display_name"], "")

    def test_display_name_populated_when_alias_matches(self):
        self.alias_path.write_text(
            json.dumps({"user/raw-proj": "Pretty Project"}),
            encoding="utf-8",
        )
        data = get_dashboard_data(db_path=self.db_path)
        by_project = {s["project"]: s for s in data["sessions_all"]}
        self.assertEqual(by_project["user/raw-proj"]["display_name"], "Pretty Project")
        # Project name itself stays raw — aggregation keys must not shift.
        self.assertEqual(by_project["user/raw-proj"]["project"], "user/raw-proj")
        # Non-matching projects keep an empty display_name.
        self.assertEqual(by_project["user/no-alias"]["display_name"], "")

    def test_session_detail_includes_display_name(self):
        self.alias_path.write_text(
            json.dumps({"user/raw-proj": "Pretty Project"}),
            encoding="utf-8",
        )
        detail = get_session_detail("sess-aliased", db_path=self.db_path)
        self.assertEqual(detail["project"], "user/raw-proj")
        self.assertEqual(detail["display_name"], "Pretty Project")


class TestProjectNameEndpoint(unittest.TestCase):
    """POST /api/project-name set / update / clear flow."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.alias_path = Path(cls.tmp.name) / "project-names.json"
        cls._patch = patch.object(dashboard, "PROJECT_ALIASES_PATH", cls.alias_path)
        cls._patch.start()
        cls.server = HTTPServer(("127.0.0.1", 0), DashboardHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls._patch.stop()
        cls.tmp.cleanup()

    def setUp(self):
        # Ensure each test starts from an empty alias file.
        if self.alias_path.exists():
            self.alias_path.unlink()

    def _post(self, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/project-name",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_set_new_alias_writes_file(self):
        status, body = self._post({"raw": "user/raw", "display": "Pretty"})
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertEqual(body["aliases"], {"user/raw": "Pretty"})
        self.assertEqual(
            json.loads(self.alias_path.read_text()), {"user/raw": "Pretty"}
        )

    def test_update_existing_alias_overwrites(self):
        self._post({"raw": "p", "display": "First"})
        status, body = self._post({"raw": "p", "display": "Second"})
        self.assertEqual(status, 200)
        self.assertEqual(body["aliases"], {"p": "Second"})

    def test_empty_display_clears_alias(self):
        self._post({"raw": "p", "display": "Will go away"})
        status, body = self._post({"raw": "p", "display": ""})
        self.assertEqual(status, 200)
        self.assertEqual(body["aliases"], {})
        # File should reflect the empty state too.
        self.assertEqual(json.loads(self.alias_path.read_text()), {})

    def test_whitespace_display_clears_alias(self):
        self._post({"raw": "p", "display": "x"})
        status, body = self._post({"raw": "p", "display": "   "})
        self.assertEqual(status, 200)
        self.assertEqual(body["aliases"], {})

    def test_clear_unknown_alias_is_noop(self):
        status, body = self._post({"raw": "never-set", "display": ""})
        self.assertEqual(status, 200)
        self.assertEqual(body["aliases"], {})

    def test_missing_raw_returns_400(self):
        status, body = self._post({"display": "no key"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_empty_raw_returns_400(self):
        status, body = self._post({"raw": "   ", "display": "Anything"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_invalid_json_returns_400(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/project-name",
            data=b"{not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
            self.fail("Expected 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_unknown_method_still_404s(self):
        # Sanity: GET /api/project-name must not leak the aliases.
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/project-name")
            self.fail("Expected 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


class TestUITemplateWiresRenameUI(unittest.TestCase):
    """The HTML template must carry the helpers and the pencil button glue."""

    def test_template_defines_display_helpers(self):
        self.assertIn("function projDisplay(", HTML_TEMPLATE)
        self.assertIn("function projCellHTML(", HTML_TEMPLATE)
        self.assertIn("rebuildProjectDisplayCache", HTML_TEMPLATE)

    def test_template_defines_rename_handler(self):
        self.assertIn("async function renameProject(", HTML_TEMPLATE)
        self.assertIn("/api/project-name", HTML_TEMPLATE)

    def test_template_renders_pencil_button(self):
        self.assertIn("proj-edit-btn", HTML_TEMPLATE)

    def test_sessions_table_uses_proj_cell(self):
        # Old hard-coded `esc(s.project)` should be gone from sessions table.
        self.assertIn("projCellHTML(s.project)", HTML_TEMPLATE)

    def test_project_cost_table_uses_proj_cell(self):
        self.assertIn("projCellHTML(p.project)", HTML_TEMPLATE)
        self.assertIn("projCellHTML(pb.project)", HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
