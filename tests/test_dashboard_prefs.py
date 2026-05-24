"""Tests for the dashboard customization prefs feature.

Covers:
  - _load_dashboard_prefs / _save_dashboard_prefs file helpers
  - _validate_dashboard_prefs payload validation
  - GET + POST /api/dashboard-prefs endpoints
"""
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


class TestLoadSave(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "prefs.json"

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_returns_empty_when_missing(self):
        self.assertFalse(self.path.exists())
        self.assertEqual(dashboard._load_dashboard_prefs(self.path), {})

    def test_save_then_load_roundtrip(self):
        prefs = {
            "order":  ["stats-row", "chart-daily"],
            "hidden": ["pareto-card"],
        }
        dashboard._save_dashboard_prefs(prefs, self.path)
        self.assertTrue(self.path.exists())
        self.assertEqual(dashboard._load_dashboard_prefs(self.path), prefs)
        # File should be pretty-printed JSON (multi-line).
        self.assertIn("\n", self.path.read_text())

    def test_load_returns_empty_for_invalid_json(self):
        self.path.write_text("not json at all {{")
        self.assertEqual(dashboard._load_dashboard_prefs(self.path), {})


class TestValidate(unittest.TestCase):
    def test_validate_rejects_non_dict(self):
        ok, err = dashboard._validate_dashboard_prefs([])
        self.assertFalse(ok)
        self.assertIn("object", err.lower())

        ok, err = dashboard._validate_dashboard_prefs("nope")
        self.assertFalse(ok)

        ok, err = dashboard._validate_dashboard_prefs(None)
        self.assertFalse(ok)

    def test_validate_rejects_unknown_order_id(self):
        ok, err = dashboard._validate_dashboard_prefs({
            "order": ["stats-row", "definitely-not-a-real-block"],
            "hidden": [],
        })
        self.assertFalse(ok)
        self.assertIn("unknown", err.lower())

    def test_validate_rejects_non_list_order(self):
        ok, err = dashboard._validate_dashboard_prefs({"order": "stats-row", "hidden": []})
        self.assertFalse(ok)

    def test_validate_accepts_empty(self):
        ok, normalized = dashboard._validate_dashboard_prefs({})
        self.assertTrue(ok)
        self.assertEqual(normalized, {"order": [], "hidden": []})

    def test_validate_accepts_full_payload(self):
        payload = {
            "order":  list(dashboard.DASHBOARD_BLOCK_IDS),
            "hidden": ["session-detail-card"],
        }
        ok, normalized = dashboard._validate_dashboard_prefs(payload)
        self.assertTrue(ok)
        self.assertEqual(sorted(normalized["order"]), sorted(payload["order"]))


class TestApiEndpoints(unittest.TestCase):
    """End-to-end check that GET/POST /api/dashboard-prefs work over HTTP."""

    @classmethod
    def setUpClass(cls):
        # Pin the prefs file inside a tmpdir so we don't clobber the user's
        # real ~/.claude/dashboard_prefs.json while tests run.
        cls.tmp = tempfile.mkdtemp()
        cls.prefs_path = Path(cls.tmp) / "dashboard_prefs.json"
        cls._orig_prefs_path = dashboard.DASHBOARD_PREFS_PATH
        dashboard.DASHBOARD_PREFS_PATH = cls.prefs_path
        cls.server = ThreadingHTTPServer(("127.0.0.1", 18298), dashboard.DashboardHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        dashboard.DASHBOARD_PREFS_PATH = cls._orig_prefs_path
        import shutil; shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        # Each test starts with no prefs file.
        if self.prefs_path.exists():
            self.prefs_path.unlink()

    def _get(self):
        with urllib.request.urlopen("http://127.0.0.1:18298/api/dashboard-prefs") as r:
            return r.status, json.loads(r.read())

    def _post(self, body):
        req = urllib.request.Request(
            "http://127.0.0.1:18298/api/dashboard-prefs",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_api_endpoints_work(self):
        # GET with no file -> empty shape.
        status, body = self._get()
        self.assertEqual(status, 200)
        self.assertEqual(body, {"order": [], "hidden": []})

        # POST valid prefs -> ok.
        payload = {
            "order":  ["chart-daily", "stats-row"],
            "hidden": ["pareto-card"],
        }
        status, body = self._post(payload)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(self.prefs_path.exists())

        # GET reflects what we wrote.
        status, body = self._get()
        self.assertEqual(status, 200)
        self.assertEqual(body["order"],  payload["order"])
        self.assertEqual(body["hidden"], payload["hidden"])

        # POST with an unknown block id is rejected with 400 and no file change.
        before = self.prefs_path.read_text()
        status, body = self._post({"order": ["nope"], "hidden": []})
        self.assertEqual(status, 400)
        self.assertFalse(body.get("ok", False))
        self.assertEqual(self.prefs_path.read_text(), before)

        # POST with bad JSON -> 400.
        req = urllib.request.Request(
            "http://127.0.0.1:18298/api/dashboard-prefs",
            data=b"not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as r:
                self.fail("expected HTTPError")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)


if __name__ == "__main__":
    unittest.main()
