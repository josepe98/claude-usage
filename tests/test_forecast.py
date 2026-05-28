"""Tests for forecast / burn-rate."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner
import dashboard


def _seed_turns(db_path, daily_costs):
    """Insert one turn per day with a model+output count that produces the
    given USD cost at the current haiku rate ($5/MTok output)."""
    conn = scanner.get_db(db_path)
    scanner.init_db(conn)
    scanner.upsert_sessions(conn, [{
        "session_id": "sf", "project_name": "p",
        "first_timestamp": "2026-01-01T00:00:00Z",
        "last_timestamp":  "2026-01-01T00:00:00Z",
        "git_branch": "", "model": "claude-haiku-4-5", "turn_count": 0,
        "total_input_tokens": 0, "total_output_tokens": 0,
        "total_cache_read": 0, "total_cache_creation": 0,
    }])
    turns = []
    for i, cost in enumerate(daily_costs):
        # haiku output = $5/MTok ⇒ tokens = cost / 5 * 1_000_000
        tokens = int(cost / 5 * 1_000_000)
        day = (date.today() - timedelta(days=len(daily_costs) - 1 - i)).strftime("%Y-%m-%d")
        turns.append({
            "session_id": "sf",
            "timestamp": f"{day}T12:00:00Z",
            "model": "claude-haiku-4-5",
            "input_tokens": 0, "output_tokens": tokens,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "tool_name": None, "cwd": "/p", "message_id": f"m{i}",
        })
    scanner.insert_turns(conn, turns)
    conn.commit()
    conn.close()


class TestForecast(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = Path(self.tmpdir) / "u.db"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_db(self):
        scanner.init_db(scanner.get_db(self.db))
        data = dashboard.get_dashboard_data(self.db)
        f = data["forecast"]
        self.assertEqual(f["days_in_data"], 0)
        self.assertEqual(f["avg_7d"], 0.0)

    def test_constant_spend(self):
        # 30 days at $10/day  ⇒  7d avg ≈ $10, 30d avg ≈ $10, trend flat
        _seed_turns(self.db, [10.0] * 30)
        f = dashboard.get_dashboard_data(self.db)["forecast"]
        self.assertAlmostEqual(f["avg_7d"], 10.0, delta=0.5)
        self.assertAlmostEqual(f["avg_30d"], 10.0, delta=0.5)
        self.assertEqual(f["trend"], "flat")

    def test_upward_trend(self):
        # 23 days at $5, then 7 days at $20 → 7d ≫ 30d
        _seed_turns(self.db, [5.0] * 23 + [20.0] * 7)
        f = dashboard.get_dashboard_data(self.db)["forecast"]
        self.assertEqual(f["trend"], "up")
        self.assertGreater(f["avg_7d"], f["avg_30d"])

    def test_downward_trend(self):
        _seed_turns(self.db, [20.0] * 23 + [5.0] * 7)
        f = dashboard.get_dashboard_data(self.db)["forecast"]
        self.assertEqual(f["trend"], "down")

    def test_projected_month_end_positive(self):
        _seed_turns(self.db, [10.0] * 14)
        f = dashboard.get_dashboard_data(self.db)["forecast"]
        # Should be >= money already spent this month
        self.assertGreaterEqual(f["projected_month_end"], f["month_to_date"])


if __name__ == "__main__":
    unittest.main()
