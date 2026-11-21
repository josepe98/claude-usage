"""Scheduled-task sessions (origin_kind) and the dashboard's auto-scan."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard
import scanner

SCHEDULED = {"kind": "task-notification", "subkind": "scheduled-trigger", "fireReason": "scheduled"}
HUMAN = {"kind": "human"}


def records(sid, origin, n_turns=1, start=0):
    out = []
    for i in range(start, start + n_turns):
        user = {"type": "user", "sessionId": sid, "cwd": "/Users/x/proj", "timestamp": f"2026-11-20T10:{i:02d}:00Z",
                "message": {"role": "user", "content": "hi"}}
        if origin is not None and i == 0:
            user["origin"] = origin
        out.append(user)
        out.append({"type": "assistant", "sessionId": sid, "cwd": "/Users/x/proj", "timestamp": f"2026-11-20T10:{i:02d}:30Z",
                    "message": {"id": f"msg_{sid}_{i}", "model": "claude-opus-5-5",
                                "usage": {"input_tokens": 10, "output_tokens": 5}}})
    return out


class OriginTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.proj = self.dir / "projects" / "p"
        self.proj.mkdir(parents=True)
        self.db = self.dir / "usage.db"

    def write(self, name, recs, mode="w"):
        with open(self.proj / name, mode) as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

    def scan(self):
        scanner.scan(projects_dir=self.dir / "projects", db_path=self.db, verbose=False)
        conn = sqlite3.connect(self.db)
        rows = dict(conn.execute("SELECT session_id, origin_kind FROM sessions"))
        conn.close()
        return rows

    def test_extract_origin(self):
        self.assertEqual(scanner.extract_origin({"origin": SCHEDULED}, None), "scheduled")
        self.assertEqual(scanner.extract_origin({"origin": HUMAN}, None), "human")
        self.assertIsNone(scanner.extract_origin({}, None))
        self.assertEqual(scanner.extract_origin({"origin": SCHEDULED}, "human"), "human")

    def test_full_and_incremental_scan(self):
        self.write("a.jsonl", records("sched", SCHEDULED))
        self.write("b.jsonl", records("people", HUMAN))
        self.write("c.jsonl", records("old", None))
        self.assertEqual(self.scan(), {"sched": "scheduled", "people": "human", "old": "unknown"})
        # A later turn in the same session keeps its origin.
        self.write("b.jsonl", records("people", None, start=1), mode="a")
        self.assertEqual(self.scan()["people"], "human")

    def test_backfill_existing_rows(self):
        self.write("a.jsonl", records("sched", SCHEDULED))
        self.scan()
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE sessions SET origin_kind = NULL")
        conn.commit()
        self.assertEqual(scanner.backfill_origin(conn, [str(self.proj / "a.jsonl")]), 1)
        self.assertEqual(conn.execute("SELECT origin_kind FROM sessions").fetchone()[0], "scheduled")
        self.assertEqual(scanner.backfill_origin(conn, [str(self.proj / "a.jsonl")]), 0)
        conn.close()

    def test_dashboard_flags_scheduled_sessions(self):
        self.write("a.jsonl", records("sched", SCHEDULED))
        self.write("b.jsonl", records("people", HUMAN))
        self.scan()
        data = dashboard.get_dashboard_data(db_path=self.db)
        flags = {s["session_id_full"]: s["scheduled"] for s in data["sessions_all"]}
        self.assertEqual(flags, {"sched": True, "people": False})

    def test_dashboard_reads_db_without_origin_column(self):
        conn = sqlite3.connect(self.db)
        scanner.init_db(conn)
        conn.execute("INSERT INTO sessions (session_id, project_name, first_timestamp, last_timestamp, turn_count) "
                     "VALUES ('s1', 'p', '2026-11-20T10:00:00Z', '2026-11-20T10:01:00Z', 1)")
        conn.commit()
        conn.close()
        with mock.patch("scanner._migrate_schema"):
            data = dashboard.get_dashboard_data(db_path=self.db)
        self.assertFalse(data["sessions_all"][0]["scheduled"])

    def test_html_has_scheduled_toggle(self):
        self.assertIn('id="show-scheduled"', dashboard.HTML_TEMPLATE)
        self.assertIn("function _tableSessions", dashboard.HTML_TEMPLATE)


class AutoScanTests(unittest.TestCase):
    def setUp(self):
        dashboard._last_scan = 0.0

    def test_off_by_default(self):
        with mock.patch("scanner.scan") as scan, mock.patch.object(dashboard, "AUTO_SCAN", False):
            dashboard._maybe_scan()
        scan.assert_not_called()

    def test_throttled_when_on(self):
        with mock.patch("scanner.scan") as scan, mock.patch.object(dashboard, "AUTO_SCAN", True):
            dashboard._maybe_scan()
            dashboard._maybe_scan()
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(scan.call_args.kwargs["db_path"], dashboard.DB_PATH)

    def test_scan_error_does_not_raise(self):
        with mock.patch("scanner.scan", side_effect=RuntimeError("boom")), \
             mock.patch.object(dashboard, "AUTO_SCAN", True), mock.patch("traceback.print_exc"):
            dashboard._maybe_scan()


if __name__ == "__main__":
    unittest.main()
