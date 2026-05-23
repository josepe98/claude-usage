"""Tests for plan_limits aggregator: 5h + weekly rolling windows, auto-detected
caps from 30-day high-water marks, 48h sparkline."""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dashboard  # noqa: E402
import scanner    # noqa: E402


def _seed(db_path, turns):
    """Insert (timestamp, model, input, output, cache_read, cache_creation)
    rows. Schema-compatible with the scanner's expected `turns` table."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    scanner.init_db(conn); scanner._migrate_schema(conn)
    conn.execute(
        "INSERT INTO sessions(session_id, project_name) VALUES('s', 'p')"
    )
    cur = conn.cursor()
    for ts, model, inp, out, cr, cc in turns:
        cur.execute(
            "INSERT INTO turns(session_id, timestamp, model, "
            "input_tokens, output_tokens, cache_read_tokens, "
            "cache_creation_tokens) VALUES('s', ?, ?, ?, ?, ?, ?)",
            (ts, model, inp, out, cr, cc),
        )
    conn.commit()
    return conn


class TestPlanLimits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "u.db"

    def test_empty_db_returns_empty_models(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        scanner.init_db(conn); scanner._migrate_schema(conn)
        out = dashboard._plan_limits(conn)
        self.assertEqual(out["models"], [])
        self.assertIsNone(out["overall"])

    def test_basic_5h_window(self):
        # 3 turns in the last 5h, 1 turn way back (outside 30d) → drop.
        now = datetime.now(timezone.utc)
        turns = [
            ((now - timedelta(hours=1)).isoformat(), "claude-opus-4-7",  100,  200, 0, 0),
            ((now - timedelta(hours=2)).isoformat(), "claude-opus-4-7",  150,  300, 0, 0),
            ((now - timedelta(hours=4)).isoformat(), "claude-opus-4-7",  200,  400, 0, 0),
            ((now - timedelta(days=60)).isoformat(), "claude-opus-4-7", 9999, 9999, 0, 0),
        ]
        conn = _seed(self.db, turns)
        out = dashboard._plan_limits(conn)
        self.assertEqual(len(out["models"]), 1)
        cur5 = out["overall"]["current_5h"]
        self.assertEqual(cur5["turns"], 3)
        self.assertEqual(cur5["tokens"], 1350)  # 300 + 450 + 600

    def test_max_window_sliding(self):
        # Two clusters; the older one is denser → should be max.
        now = datetime.now(timezone.utc)
        burst = now - timedelta(days=10)
        turns = [
            *[((burst + timedelta(minutes=i)).isoformat(), "m", 1000, 1000, 0, 0) for i in range(20)],
            ((now - timedelta(hours=1)).isoformat(), "m", 100, 100, 0, 0),
        ]
        conn = _seed(self.db, turns)
        out = dashboard._plan_limits(conn)
        self.assertEqual(out["overall"]["max_5h_30d"]["turns"], 20)
        self.assertEqual(out["overall"]["max_5h_30d"]["tokens"], 40000)

    def test_sparkline_length(self):
        now = datetime.now(timezone.utc)
        turns = [((now - timedelta(hours=1)).isoformat(), "m", 10, 10, 0, 0)]
        conn = _seed(self.db, turns)
        out = dashboard._plan_limits(conn)
        spark = out["models"][0]["sparkline_48h"]
        # 48h / 30min step = 96 samples + the final one at "now"
        self.assertGreaterEqual(len(spark), 96)
        self.assertLessEqual(len(spark), 98)

    def test_no_estimated_message_count_caps(self):
        # The function must NEVER hard-code Pro/Max plan thresholds.
        import inspect
        src = inspect.getsource(dashboard._plan_limits)
        for forbidden in ("Pro", "Max plan", "200 messages", "45 messages"):
            self.assertNotIn(forbidden, src, f"plan_limits leaks plan-tier guess: {forbidden!r}")


if __name__ == "__main__":
    unittest.main()
