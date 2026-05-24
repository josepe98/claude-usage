"""Tests for currency conversion helpers and /api/fx-rates endpoint.

All network access is mocked - these tests must work offline.
"""
import io
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


FRANKFURTER_SAMPLE = {
    "amount": 1.0,
    "base": "USD",
    "date": "2026-05-23",
    "rates": {"EUR": 0.92, "CZK": 23.4, "GBP": 0.79, "JPY": 156.2},
}


def _mock_urlopen_returning(payload):
    """Build a context-manager fake response whose .read() returns JSON bytes."""
    body = json.dumps(payload).encode("utf-8")

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return body

    return mock.Mock(return_value=_Resp())


def _reset_fx_cache():
    dashboard._FX_CACHE["data"] = None
    dashboard._FX_CACHE["fetched_at"] = 0.0


class TestFetchFxRates(unittest.TestCase):
    def setUp(self):
        _reset_fx_cache()

    def tearDown(self):
        _reset_fx_cache()

    def test_fetch_fx_returns_none_on_network_error(self):
        with mock.patch("dashboard._fx_urlopen",
                        side_effect=urllib.error.URLError("boom")):
            self.assertIsNone(dashboard._fetch_fx_rates())

    def test_fetch_fx_returns_parsed_dict_on_success(self):
        with mock.patch("dashboard._fx_urlopen",
                        _mock_urlopen_returning(FRANKFURTER_SAMPLE)):
            out = dashboard._fetch_fx_rates()
        self.assertIsNotNone(out)
        self.assertEqual(out["base"], "USD")
        self.assertEqual(out["date"], "2026-05-23")
        self.assertIn("as_of", out)
        self.assertEqual(out["rates"]["EUR"], 0.92)
        # Base currency is normalised to 1.0 in the rates dict.
        self.assertEqual(out["rates"]["USD"], 1.0)

    def test_fetch_fx_uses_cache_when_fresh(self):
        mock_open = _mock_urlopen_returning(FRANKFURTER_SAMPLE)
        with mock.patch("dashboard._fx_urlopen", mock_open):
            a = dashboard._fetch_fx_rates()
            b = dashboard._fetch_fx_rates()
        self.assertEqual(mock_open.call_count, 1)
        self.assertIs(a, b)

    def test_fetch_fx_refreshes_when_stale(self):
        mock_open = _mock_urlopen_returning(FRANKFURTER_SAMPLE)
        real_time = time.time
        with mock.patch("dashboard._fx_urlopen", mock_open):
            with mock.patch("dashboard.time.time", side_effect=[1000.0, 1000.0]):
                dashboard._fetch_fx_rates()
            # 7 hours later -> stale, must refetch.
            stale = 1000.0 + (7 * 60 * 60)
            with mock.patch("dashboard.time.time", side_effect=[stale, stale]):
                dashboard._fetch_fx_rates()
        self.assertEqual(mock_open.call_count, 2)

    def test_fetch_fx_respects_target_currencies_filter(self):
        with mock.patch("dashboard._fx_urlopen",
                        _mock_urlopen_returning(FRANKFURTER_SAMPLE)):
            out = dashboard._fetch_fx_rates(target_currencies=["EUR", "CZK"])
        self.assertEqual(set(out["rates"].keys()), {"EUR", "CZK", "USD"})


class TestFxRatesEndpoint(unittest.TestCase):
    PORT = 18098
    server = None

    def setUp(self):
        _reset_fx_cache()
        self.tmp = tempfile.mkdtemp()
        self._orig_db = dashboard.DB_PATH
        dashboard.DB_PATH = Path(self.tmp) / "u.db"
        ThreadingHTTPServer.allow_reuse_address = True
        self.server = ThreadingHTTPServer(("127.0.0.1", self.PORT),
                                          dashboard.DashboardHandler)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        time.sleep(0.1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=2)
        dashboard.DB_PATH = self._orig_db
        _reset_fx_cache()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_api_endpoint_returns_rates(self):
        with mock.patch("dashboard._fx_urlopen",
                        _mock_urlopen_returning(FRANKFURTER_SAMPLE)):
            with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/fx-rates" % self.PORT
            ) as r:
                d = json.loads(r.read())
        self.assertEqual(d["base"], "USD")
        self.assertIn("rates", d)
        self.assertEqual(d["rates"]["EUR"], 0.92)
        self.assertEqual(d["rates"]["USD"], 1.0)
        self.assertIn("as_of", d)

    def test_api_endpoint_returns_fallback_on_fetch_failure(self):
        with mock.patch("dashboard._fx_urlopen",
                        side_effect=urllib.error.URLError("network down")):
            with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/fx-rates" % self.PORT
            ) as r:
                d = json.loads(r.read())
        self.assertTrue(d.get("fallback"))
        self.assertEqual(d.get("error"), "FX fetch failed")
        self.assertEqual(d["rates"]["USD"], 1.0)


if __name__ == "__main__":
    unittest.main()
