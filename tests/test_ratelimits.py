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

    def test_no_weekly_projection_from_short_span(self):
        # 14% -> 15% in 32 minutes must not project the week as full.
        self.write((NOW, payload(2, 14)), (NOW + 1920, payload(2, 15)))
        ratelimits.ingest_snapshots(self.conn, self.snap)
        week = ratelimits.allowance_summary(self.conn, now=NOW + 1920)["windows"][1]
        self.assertIsNone(week["projected_full_at"])
        self.assertIsNone(week["rate_per_hour"])

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


def desktop(five=4, week=15, fable=25, week_reset="2026-10-23T20:00:00.399Z", status="ok", extra=True):
    plan = {"status": status, "plan": "Max", "windows": [
        {"label": "5-hour limit", "percentUsed": five, "resetsAt": "2026-10-21T14:40:00.399Z"},
        {"label": "Weekly \u00b7 all models", "percentUsed": week, "resetsAt": week_reset},
        {"label": "Weekly \u00b7 Fable", "percentUsed": fable, "resetsAt": week_reset},
    ]}
    if extra:
        plan["extraUsage"] = {"enabled": True, "spent": "12.50", "monthlyLimit": "50.00", "currency": "USD"}
    return {"plan": plan, "context": {}}


WEEK_END = 1792785600  # 2026-10-23T20:00:00Z


class DesktopSnapshotTests(unittest.TestCase):
    def test_parses_get_usage_result(self):
        s = ratelimits.snapshot_from_desktop(desktop(), now=NOW)
        self.assertEqual(s["source"], "desktop")
        self.assertEqual(s["subscription_type"], "Max")
        self.assertEqual(s["seven_day_pct"], 15)
        self.assertAlmostEqual(s["seven_day_resets_at"], WEEK_END, delta=1)
        self.assertEqual(s["model_weekly"][0]["name"], "Fable")
        self.assertEqual(s["extra_used_usd"], 12.5)

    def test_accepts_bare_plan_object(self):
        s = ratelimits.snapshot_from_desktop(desktop()["plan"], now=NOW)
        self.assertEqual(s["five_hour_pct"], 4)

    def test_unavailable_raises(self):
        with self.assertRaises(ValueError):
            ratelimits.snapshot_from_desktop(desktop(status="unavailable"))

    def test_no_windows_raises(self):
        with self.assertRaises(ValueError):
            ratelimits.snapshot_from_desktop({"plan": {"status": "ok", "windows": []}})

    def test_record_main_writes_every_call_and_fails_loud(self):
        d = Path(tempfile.mkdtemp())
        with mock.patch.object(ratelimits, "SNAPSHOT_PATH", d / "s.jsonl"), \
             mock.patch.object(ratelimits, "ERROR_LOG_PATH", d / "e.log"):
            out = io.StringIO()
            self.assertEqual(ratelimits.record_desktop_main(io.StringIO(json.dumps(desktop())), out, io.StringIO()), 0)
            self.assertEqual(ratelimits.record_desktop_main(io.StringIO(json.dumps(desktop())), out, io.StringIO()), 0)
            self.assertIn("RECORDED 5h 4%, weekly 15%, Fable 25%", out.getvalue())
            err = io.StringIO()
            self.assertEqual(ratelimits.record_desktop_main(io.StringIO("{}"), io.StringIO(), err), 1)
            self.assertIn("FAILED", err.getvalue())
            self.assertEqual(len((d / "s.jsonl").read_text().splitlines()), 2)
            self.assertIn("record-usage", (d / "e.log").read_text())


class WeeklyHistoryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.snap = Path(tempfile.mkdtemp()) / "snaps.jsonl"

    def add(self, snap):
        with open(self.snap, "a") as f:
            f.write(json.dumps(snap) + "\n")

    def test_groups_by_week_mixing_sources(self):
        prev_end = "2026-10-16T20:00:00.399Z"
        self.add(ratelimits.snapshot_from_desktop(desktop(week=60, fable=70, week_reset=prev_end), now=WEEK_END - 9 * 86400))
        self.add(ratelimits.snapshot_from_desktop(desktop(week=82, fable=90, week_reset=prev_end), now=WEEK_END - 7 * 86400 - 3 * 3600))
        # status-line reading for the current week: integer reset time
        self.add(ratelimits.snapshot_from_payload(payload(2, 14, seven_reset=WEEK_END), now=WEEK_END - 2 * 86400))
        self.add(ratelimits.snapshot_from_desktop(desktop(week=15, fable=25), now=WEEK_END - 2 * 86400 + 600))
        ratelimits.ingest_snapshots(self.conn, self.snap)
        a = ratelimits.allowance_summary(self.conn, now=WEEK_END - 2 * 86400 + 700)
        weeks = a["weekly_history"]
        self.assertEqual(len(weeks), 2)
        cur, prev = weeks
        self.assertFalse(cur["complete"])
        self.assertEqual((cur["pct"], cur["readings"], cur["models"]), (15, 2, {"Fable": 25}))
        self.assertTrue(prev["complete"])
        self.assertEqual((prev["pct"], prev["models"]["Fable"]), (82, 90))
        self.assertEqual(prev["gap_hours"], 3.0)

    def test_summary_includes_model_window_and_extra_usage(self):
        self.add(ratelimits.snapshot_from_desktop(desktop(), now=NOW))
        ratelimits.ingest_snapshots(self.conn, self.snap)
        a = ratelimits.allowance_summary(self.conn, now=NOW)
        self.assertEqual([w["key"] for w in a["windows"]], ["five_hour", "seven_day", "model:Fable"])
        self.assertEqual(a["spend"]["label"], "Extra usage")
        self.assertEqual(a["spend"]["spend_pct"], 25.0)

    def test_old_table_gets_new_columns(self):
        self.conn.execute("CREATE TABLE rate_limit_snapshots (ts REAL PRIMARY KEY, session_id TEXT, subscription_type TEXT, five_hour_pct REAL, five_hour_resets_at REAL, seven_day_pct REAL, seven_day_resets_at REAL, spend_pct REAL, spend_used_usd REAL, spend_limit_usd REAL, spend_period TEXT)")
        self.add(ratelimits.snapshot_from_desktop(desktop(), now=NOW))
        self.assertEqual(ratelimits.ingest_snapshots(self.conn, self.snap), 1)


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
