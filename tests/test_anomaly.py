"""Tests for anomaly / spike detection."""
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner
import dashboard


def _seed(db, daily_costs):
    """Same helper as test_forecast — one turn per day at $5/MTok haiku
    output for clean cost arithmetic."""
    conn = scanner.get_db(db)
    scanner.init_db(conn)
    scanner.upsert_sessions(conn, [{
        "session_id": "s", "project_name": "p",
        "first_timestamp": "2026-01-01T00:00:00Z",
        "last_timestamp":  "2026-01-01T00:00:00Z",
        "git_branch": "", "model": "claude-haiku-4-5", "turn_count": 0,
        "total_input_tokens": 0, "total_output_tokens": 0,
        "total_cache_read": 0, "total_cache_creation": 0,
    }])
    turns = []
    for i, cost in enumerate(daily_costs):
        tokens = int(cost / 5 * 1_000_000)
        day = (date.today() - timedelta(days=len(daily_costs) - 1 - i)).strftime("%Y-%m-%d")
        turns.append({
            "session_id": "s", "timestamp": f"{day}T12:00:00Z",
            "model": "claude-haiku-4-5", "input_tokens": 0, "output_tokens": tokens,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "tool_name": None, "cwd": "/p", "message_id": f"m{i}",
        })
    scanner.insert_turns(conn, turns)
    conn.commit()
    return conn


class TestAnomaly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "u.db"

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_not_anomalous(self):
        scanner.init_db(scanner.get_db(self.db))
        a = dashboard.get_dashboard_data(self.db)["anomaly"]
        self.assertFalse(a["is_anomalous"])

    def test_constant_spend_not_anomalous(self):
        conn = _seed(self.db, [10.0] * 30 + [10.0])  # 30 days history + today
        conn.close()
        a = dashboard.get_dashboard_data(self.db)["anomaly"]
        self.assertFalse(a["is_anomalous"])

    def test_spike_is_anomalous(self):
        # 30 days at $5, today $50 (10x average)
        conn = _seed(self.db, [5.0] * 30 + [50.0])
        conn.close()
        a = dashboard.get_dashboard_data(self.db)["anomaly"]
        self.assertTrue(a["is_anomalous"])
        self.assertGreater(a["ratio"], 5)

    def test_html_has_banner(self):
        self.assertIn('id="anomaly-banner"', dashboard.HTML_TEMPLATE)
        self.assertIn("renderAnomalyBanner", dashboard.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
