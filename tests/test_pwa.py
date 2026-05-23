"""Tests for PWA installability — manifest, service worker, icon, HTML hooks."""
import json
import sys
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


PORT = 18099
BASE = f"http://127.0.0.1:{PORT}"


class TestPWA(unittest.TestCase):
    """End-to-end checks that the dashboard exposes everything Chrome needs
    to show the install dialog and run as a standalone PWA."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", PORT), dashboard.DashboardHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    # ── /manifest.json ─────────────────────────────────────────────────────

    def test_manifest_returns_200_and_valid_json(self):
        with urllib.request.urlopen(f"{BASE}/manifest.json") as r:
            self.assertEqual(r.status, 200)
            ctype = r.headers.get("Content-Type", "")
            self.assertIn("manifest+json", ctype, f"unexpected ct: {ctype!r}")
            data = json.loads(r.read())
        # All fields Chrome's install-criteria check requires.
        for key in ("name", "short_name", "start_url", "display",
                    "theme_color", "background_color", "icons"):
            self.assertIn(key, data, f"manifest missing key: {key}")
        self.assertEqual(data["display"], "standalone")
        self.assertEqual(data["start_url"], "/")
        self.assertIsInstance(data["icons"], list)
        self.assertGreaterEqual(len(data["icons"]), 1)
        # Each icon entry must declare src + sizes + type.
        for icon in data["icons"]:
            self.assertIn("src", icon)
            self.assertIn("sizes", icon)
            self.assertIn("type", icon)
        # Theme + background colors should be hex so Chrome can parse them.
        self.assertTrue(data["theme_color"].startswith("#"))
        self.assertTrue(data["background_color"].startswith("#"))

    # ── /sw.js ─────────────────────────────────────────────────────────────

    def test_sw_js_returns_200_with_js_content_type(self):
        with urllib.request.urlopen(f"{BASE}/sw.js") as r:
            self.assertEqual(r.status, 200)
            ctype = r.headers.get("Content-Type", "")
            self.assertIn("javascript", ctype, f"unexpected ct: {ctype!r}")
            # Allow SW to register at root scope.
            self.assertEqual(r.headers.get("Service-Worker-Allowed"), "/")
            body = r.read().decode("utf-8")
        # Spot-check that it's actually a service worker.
        self.assertIn("addEventListener", body)
        self.assertIn("install", body)
        self.assertIn("fetch", body)
        self.assertIn("clients.claim", body)

    # ── /icon.svg ──────────────────────────────────────────────────────────

    def test_icon_svg_returns_200_with_svg_content_type(self):
        with urllib.request.urlopen(f"{BASE}/icon.svg") as r:
            self.assertEqual(r.status, 200)
            ctype = r.headers.get("Content-Type", "")
            self.assertIn("image/svg+xml", ctype, f"unexpected ct: {ctype!r}")
            body = r.read().decode("utf-8")
        self.assertTrue(body.lstrip().startswith("<svg"), "body is not an SVG element")
        self.assertIn("</svg>", body)

    # ── HTML hooks ─────────────────────────────────────────────────────────

    def test_index_html_links_manifest_and_theme_color(self):
        with urllib.request.urlopen(f"{BASE}/") as r:
            html = r.read().decode("utf-8")
        self.assertIn('rel="manifest"', html)
        self.assertIn('href="/manifest.json"', html)
        self.assertIn('name="theme-color"', html)
        # Icon links so Chrome's tab + install dialog have artwork.
        self.assertIn('href="/icon.svg"', html)

    def test_index_html_registers_service_worker(self):
        with urllib.request.urlopen(f"{BASE}/") as r:
            html = r.read().decode("utf-8")
        # Must feature-detect AND point at /sw.js.
        self.assertIn("serviceWorker", html)
        self.assertIn('navigator.serviceWorker.register("/sw.js"', html)


if __name__ == "__main__":
    unittest.main()
