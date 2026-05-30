"""Tests for the `cli.py pricing` command and the `/api/pricing` endpoint.

Both views are thin wrappers over `pricing.PRICING` and `pricing.PRICING_HISTORY`
so the maintainer can spot drift between the two without grepping pricing.py.
"""
import io
import json
import sys
import threading
import time
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli
import dashboard
import pricing
import scanner


# Fake history used to make the "prints history" test deterministic — the real
# PRICING_HISTORY is intentionally empty in upstream/main.
FAKE_HISTORY = [
    {"effective": "2026-04-01", "pricing": {
        "claude-opus-4-7": {
            "input": 4.0, "output": 20.0, "cache_read": 0.4,
            "cache_write": 5.0, "cache_write_5m": 5.0, "cache_write_1h": 8.0,
        }
    }},
    {"effective": "2026-01-15", "pricing": {
        "claude-sonnet-4-6": {
            "input": 2.5, "output": 12.0, "cache_read": 0.25,
            "cache_write": 3.0, "cache_write_5m": 3.0, "cache_write_1h": 5.0,
        }
    }},
]


class TestCliPricing(unittest.TestCase):
    def test_cli_pricing_prints_models(self):
        """Every model key from PRICING shows up in the rendered table."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_pricing()
        out = buf.getvalue()
        for model in pricing.PRICING.keys():
            self.assertIn(model, out, f"Model {model} missing from cli pricing output")

        # Header columns are present and named the way the spec asked for.
        for header in ("input", "output", "cache_read", "cache_creation_5m", "cache_creation_1h"):
            self.assertIn(header, out)

        # Prices are rendered with 6-decimal formatting.
        self.assertIn("5.000000", out)  # opus input
        self.assertIn("25.000000", out)  # opus output

    def test_cli_pricing_prints_history(self):
        """Each PRICING_HISTORY entry's `effective` date appears in the output."""
        with mock.patch.object(cli, "PRICING_HISTORY", FAKE_HISTORY):
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.cmd_pricing()
            out = buf.getvalue()

        for entry in FAKE_HISTORY:
            self.assertIn(entry["effective"], out,
                          f"effective={entry['effective']} missing from output")

        # The "oldest -> newest" heading is rendered.
        self.assertIn("Pricing history (oldest -> newest)", out)

        # Historic opus rate ($4 input, formatted to 6 dp) appears.
        self.assertIn("4.000000", out)


class TestApiPricingEndpoint(unittest.TestCase):
    """End-to-end: spin up DashboardHandler on a real socket, hit /api/pricing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "u.db"
        # Seed a minimal DB so DashboardHandler init code paths don't crash.
        conn = scanner.get_db(self.db)
        scanner.init_db(conn)
        conn.commit()
        conn.close()

        self._orig_db = dashboard.DB_PATH
        dashboard.DB_PATH = self.db
        dashboard.SHARE_TOKEN = None

        # port=0 so we never collide with another test's leaked socket.
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        time.sleep(0.1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        dashboard.DB_PATH = self._orig_db
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_api_pricing_endpoint_returns_payload(self):
        with mock.patch.object(pricing, "PRICING_HISTORY", FAKE_HISTORY):
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/pricing") as r:
                payload = json.loads(r.read())

        self.assertIn("current", payload)
        self.assertIn("history", payload)

        # current == PRICING dict, model-for-model
        self.assertEqual(set(payload["current"].keys()), set(pricing.PRICING.keys()))
        self.assertEqual(payload["current"]["claude-opus-4-7"]["input"], 5.0)

        # history matches the patched value, in order
        self.assertEqual(len(payload["history"]), len(FAKE_HISTORY))
        self.assertEqual(payload["history"][0]["effective"], "2026-04-01")
        self.assertEqual(
            payload["history"][0]["pricing"]["claude-opus-4-7"]["input"], 4.0
        )


if __name__ == "__main__":
    unittest.main()
