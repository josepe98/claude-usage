"""Tests for plan-allowance tracking (status-line recorder + summary)."""
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ratelimits
import dashboard

NOW = 1_761_000_000


def payload(five=None, seven=None, spend=None, five_reset=NOW + 3600, seven_reset=NOW + 86400 * 3):
    rl = {}
    if five is not None:
        rl["five_hour"] = {"used_percentage": five, "resets_at": five_reset}
    if seven is not None:
        rl["seven_day"] = {"used_percentage": seven, "resets_at": seven_reset}
    if spend is not None:
        rl["spend_limit"] = spend
    p = {"session_id": "s1", "model": {"display_name": "Opus"}}
    if rl:
        p["rate_limits"] = rl
    return p


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.snap = self.dir / "snaps.jsonl"
        self.state = self.dir / "state.json"

    def lines(self):
        return self.snap.read_text().splitlines() if self.snap.exists() else []

    def test_no_rate_limits_gives_no_snapshot(self):
        self.assertIsNone(ratelimits.snapshot_from_payload(payload()))

    def test_parses_windows_and_spend(self):
        s = ratelimits.snapshot_from_payload(
            payload(42.5, 18, {"used_percentage": 30, "resets_at": NOW, "used_usd": 15, "limit_usd": 50, "period": "monthly"}),
            now=NOW)
        self.assertEqual(s["five_hour_pct"], 42.5)
        self.assertEqual(s["seven_day_resets_at"], NOW + 86400 * 3)
        self.assertEqual(s["spend_limit_usd"], 50)
        self.assertEqual(s["spend_period"], "monthly")

    def test_dedupes_unchanged_readings_until_heartbeat(self):
        rec = lambda pct, t: ratelimits.record_snapshot(
            ratelimits.snapshot_from_payload(payload(pct, 10), now=t), self.snap, self.state)
        self.assertTrue(rec(40, NOW))
        self.assertFalse(rec(40, NOW + 30))
        self.assertTrue(rec(41, NOW + 60))
        self.assertTrue(rec(41, NOW + 60 + ratelimits.HEARTBEAT_SECONDS))
        self.assertEqual(len(self.lines()), 3)

    def test_statusline_output(self):
        out = io.StringIO()
        with mock.patch.object(ratelimits, "SNAPSHOT_PATH", self.snap), \
             mock.patch.object(ratelimits, "STATE_PATH", self.state):
            ratelimits.statusline_main(io.StringIO(json.dumps(payload(42.4, 18.6))), out)
        self.assertEqual(out.getvalue().strip(), "Opus · 5h 42% · wk 19%")
        self.assertEqual(len(self.lines()), 1)

    def test_statusline_bad_input_is_loud(self):
        out = io.StringIO()
        log = self.dir / "err.log"
        with mock.patch.object(ratelimits, "ERROR_LOG_PATH", log):
            ratelimits.statusline_main(io.StringIO("not json"), out)
        self.assertIn("usage recorder", out.getvalue())
        self.assertIn("JSONDecodeError", log.read_text())


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.snap = Path(tempfile.mkdtemp()) / "snaps.jsonl"

    def write(self, *snaps):
        with open(self.snap, "a") as f:
            for t, p in snaps:
                f.write(json.dumps(ratelimits.snapshot_from_payload(p, now=t)) + "\n")
            f.write("garbage line\n")

    def test_no_snapshots(self):
        self.assertEqual(ratelimits.ingest_snapshots(self.conn, self.snap), 0)
        self.assertIsNone(ratelimits.allowance_summary(self.conn, now=NOW))

    def test_ingest_is_idempotent(self):
        self.write((NOW, payload(10, 5)), (NOW + 60, payload(12, 5)))
        self.assertEqual(ratelimits.ingest_snapshots(self.conn, self.snap), 2)
        self.assertEqual(ratelimits.ingest_snapshots(self.conn, self.snap), 0)

    def test_projection_when_pace_exceeds_window(self):
        # 20% -> 60% in 1h; 40 points left at 40%/h -> full in 1h, reset in 2h.
        reset = NOW + 3 * 3600
        self.write((NOW, payload(20, 5, five_reset=reset)),
                   (NOW + 3600, payload(60, 7, five_reset=reset)))
        ratelimits.ingest_snapshots(self.conn, self.snap)
        a = ratelimits.allowance_summary(self.conn, now=NOW + 3600)
        five = a["windows"][0]
        self.assertEqual(five["pct"], 60)
        self.assertEqual(five["rate_per_hour"], 40.0)
        self.assertAlmostEqual(five["projected_full_at"], NOW + 7200, delta=1)

    def test_no_projection_when_reset_comes_first(self):
        reset = NOW + 3600 + 600
        self.write((NOW, payload(20, 5, five_reset=reset)),
                   (NOW + 3600, payload(30, 5, five_reset=reset)))
        ratelimits.ingest_snapshots(self.conn, self.snap)
        five = ratelimits.allowance_summary(self.conn, now=NOW + 3600)["windows"][0]
        self.assertIsNone(five["projected_full_at"])
        self.assertEqual(five["rate_per_hour"], 10.0)

    def test_pace_ignores_previous_window(self):
        self.write((NOW, payload(90, 5, five_reset=NOW + 100)),
                   (NOW + 200, payload(5, 5, five_reset=NOW + 18000)),
                   (NOW + 1200, payload(6, 5, five_reset=NOW + 18000)))
        ratelimits.ingest_snapshots(self.conn, self.snap)
        five = ratelimits.allowance_summary(self.conn, now=NOW + 1200)["windows"][0]
        self.assertEqual(five["rate_per_hour"], 3.6)

    def test_reset_since_reading(self):
        self.write((NOW, payload(50, 5, five_reset=NOW + 60)))
        ratelimits.ingest_snapshots(self.conn, self.snap)
        five = ratelimits.allowance_summary(self.conn, now=NOW + 120)["windows"][0]
        self.assertTrue(five["reset_since_reading"])


class DashboardIntegrationTests(unittest.TestCase):
    def test_api_data_includes_allowance(self):
        from scanner import get_db, init_db
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = Path(f.name)
        conn = get_db(db)
        init_db(conn)
        conn.close()
        snap = Path(tempfile.mkdtemp()) / "snaps.jsonl"
        snap.write_text(json.dumps(ratelimits.snapshot_from_payload(payload(33, 12))) + "\n")
        with mock.patch.object(ratelimits, "SNAPSHOT_PATH", snap):
            data = dashboard.get_dashboard_data(db_path=db)
        self.assertEqual(data["allowance"]["windows"][0]["pct"], 33)
        self.assertEqual(data["allowance"]["windows"][1]["key"], "seven_day")

    def test_html_has_allowance_card(self):
        self.assertIn('id="allowance-card"', dashboard.HTML_TEMPLATE)
        self.assertIn("function renderAllowance", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
