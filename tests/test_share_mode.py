"""Tests for share-link read-only mode."""
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


def _start_server(token=None, port=18091):
    """Spin up the dashboard in a background thread for one test."""
    from http.server import ThreadingHTTPServer
    dashboard.SHARE_TOKEN = token
    server = ThreadingHTTPServer(("127.0.0.1", port), dashboard.DashboardHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.1)
    return server


class TestShareMode(unittest.TestCase):
    def setUp(self):
        dashboard.SHARE_TOKEN = None
        self.server = None

    def tearDown(self):
        dashboard.SHARE_TOKEN = None
        if self.server:
            self.server.shutdown()

    def test_no_token_no_gate(self):
        self.server = _start_server(token=None, port=18091)
        with urllib.request.urlopen("http://127.0.0.1:18091/") as r:
            self.assertEqual(r.status, 200)

    def test_with_token_unauthorized_without_query(self):
        self.server = _start_server(token="secret123", port=18092)
        try:
            urllib.request.urlopen("http://127.0.0.1:18092/")
            self.fail("expected 401")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)

    def test_with_token_query_string_works(self):
        self.server = _start_server(token="secret123", port=18093)
        with urllib.request.urlopen("http://127.0.0.1:18093/?token=secret123") as r:
            self.assertEqual(r.status, 200)

    def test_with_token_header_works(self):
        self.server = _start_server(token="secret123", port=18094)
        req = urllib.request.Request(
            "http://127.0.0.1:18094/",
            headers={"X-Dashboard-Token": "secret123"},
        )
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 200)

    def test_wrong_token_unauthorized(self):
        self.server = _start_server(token="secret123", port=18095)
        try:
            urllib.request.urlopen("http://127.0.0.1:18095/?token=wrong")
            self.fail("expected 401")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)

    def test_share_mode_forbids_rescan(self):
        self.server = _start_server(token="secret123", port=18096)
        req = urllib.request.Request(
            "http://127.0.0.1:18096/api/rescan?token=secret123",
            method="POST",
            data=b"",
        )
        try:
            urllib.request.urlopen(req)
            self.fail("expected 403")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)


if __name__ == "__main__":
    unittest.main()
