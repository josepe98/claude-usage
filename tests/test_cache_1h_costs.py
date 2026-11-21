"""1-hour cache writes must count in every cost figure (dashboard data, CLI,
plan card). Claude Code writes mostly to the 1h cache, so leaving it out
understated costs by roughly half."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli
import dashboard
import scanner

MODEL = "claude-opus-5-5"   # 1h cache write: $8 / MTok
ONE_H = 1_000_000           # → $8.00


def make_db():
    """A DB with one session whose only cost is 1M tokens of 1h cache writes."""
    root = Path(tempfile.mkdtemp())
    proj = root / "projects" / "p"
    proj.mkdir(parents=True)
    ts = date.today().isoformat() + "T10:00:00Z"
    recs = [
        {"type": "user", "sessionId": "s1", "cwd": "/x/proj", "timestamp": ts,
         "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "sessionId": "s1", "cwd": "/x/proj", "timestamp": ts,
         "message": {"id": "msg_1", "model": MODEL, "usage": {
             "input_tokens": 0, "output_tokens": 0,
             "cache_creation_input_tokens": ONE_H,
             "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": ONE_H}}}},
    ]
    (proj / "s1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    db = root / "usage.db"
    scanner.scan(projects_dir=root / "projects", db_path=db, verbose=False)
    return db


class Cache1hCostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = make_db()

    def test_scanner_records_1h_tokens(self):
        import sqlite3
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT SUM(cache_1h_tokens) FROM turns").fetchone()[0], ONE_H)
        conn.close()

    def test_dashboard_sessions_carry_1h_tokens(self):
        data = dashboard.get_dashboard_data(db_path=self.db)
        self.assertEqual(data["sessions_all"][0]["cache_1h"], ONE_H)
        self.assertEqual(sum(r["cache_1h"] for r in data["daily_by_model"]), ONE_H)

    def test_plan_card_month_to_date_includes_1h(self):
        data = dashboard.get_dashboard_data(db_path=self.db)
        self.assertAlmostEqual(data["plan_recommendation"]["month_to_date"], 8.00, places=2)

    def test_js_aggregations_sum_1h(self):
        # Est. Cost and the model table are built from these per-model / per-day maps.
        self.assertIn("m.cache_1h       += r.cache_1h || 0;", dashboard.HTML_TEMPLATE)
        self.assertIn("d.cache_1h       += r.cache_1h || 0;", dashboard.HTML_TEMPLATE)

    def test_cli_today_includes_1h(self):
        out = io.StringIO()
        with mock.patch.object(cli, "DB_PATH", self.db), redirect_stdout(out):
            cli.cmd_today()
        self.assertIn("$8.0000", out.getvalue())


if __name__ == "__main__":
    unittest.main()
