"""Tests for workspace.py and workspace-mode integration in scanner + dashboard."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import workspace
import scanner
from dashboard import get_dashboard_data, HTML_TEMPLATE
from scanner import get_db, init_db, upsert_sessions, insert_turns


class TestWorkspaceConfigDefaults(unittest.TestCase):
    """Missing config file should not break anything -- backward compat."""

    def test_load_config_missing_file_returns_sqlite_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            non_existent = Path(td) / "nope.json"
            cfg = workspace.load_config(path=non_existent)
        self.assertEqual(cfg["backend"], "sqlite")
        self.assertIn("machine_id", cfg)
        self.assertTrue(cfg["machine_id"])  # non-empty (hostname or "local")
        self.assertIsNone(cfg["db_path"])
        self.assertIsNone(cfg["pg_dsn"])

    def test_load_config_reads_postgres_backend(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "workspace.json"
            p.write_text(json.dumps({
                "backend": "postgres",
                "machine_id": "ci-runner",
                "team": "engineering",
                "pg_dsn": "postgres://x@localhost/y",
            }))
            cfg = workspace.load_config(path=p)
        self.assertEqual(cfg["backend"], "postgres")
        self.assertEqual(cfg["machine_id"], "ci-runner")
        self.assertEqual(cfg["team"], "engineering")
        self.assertEqual(cfg["pg_dsn"], "postgres://x@localhost/y")

    def test_env_overrides_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "workspace.json"
            p.write_text(json.dumps({"backend": "sqlite", "machine_id": "from-file"}))
            with mock.patch.dict(os.environ, {
                workspace.ENV_BACKEND: "postgres",
                workspace.ENV_MACHINE: "from-env",
                workspace.ENV_PG_DSN: "postgres://env@host/db",
            }):
                cfg = workspace.load_config(path=p)
        self.assertEqual(cfg["backend"], "postgres")
        self.assertEqual(cfg["machine_id"], "from-env")
        self.assertEqual(cfg["pg_dsn"], "postgres://env@host/db")

    def test_invalid_backend_falls_back_to_sqlite(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "workspace.json"
            p.write_text(json.dumps({"backend": "mysql"}))
            cfg = workspace.load_config(path=p)
        self.assertEqual(cfg["backend"], "sqlite")

    def test_is_team_mode_postgres_true(self):
        cfg = {"backend": "postgres", "machine_id": "x", "team": "", "db_path": None, "pg_dsn": "x"}
        self.assertTrue(workspace.is_team_mode(cfg))

    def test_is_team_mode_local_sqlite_false(self):
        cfg = {"backend": "sqlite", "machine_id": "x", "team": "", "db_path": None, "pg_dsn": None}
        self.assertFalse(workspace.is_team_mode(cfg))

    def test_is_team_mode_shared_sqlite_path_true(self):
        cfg = {"backend": "sqlite", "machine_id": "x", "team": "", "db_path": "/Volumes/team/usage.db", "pg_dsn": None}
        self.assertTrue(workspace.is_team_mode(cfg))


class TestPostgresLazyImport(unittest.TestCase):
    """Postgres support must be optional -- importing workspace must not require psycopg2."""

    def test_workspace_module_imports_without_psycopg2(self):
        # Just exercising the import path; nothing should explode.
        import importlib
        mod = importlib.reload(workspace)
        self.assertTrue(hasattr(mod, "get_postgres_connection"))

    def test_get_postgres_connection_raises_friendly_error_when_driver_missing(self):
        # Simulate a machine without psycopg2 by stubbing the import to fail.
        import sys
        original = sys.modules.get("psycopg2")
        sys.modules["psycopg2"] = None  # makes `import psycopg2` raise ImportError
        try:
            with self.assertRaises(RuntimeError) as ctx:
                workspace.get_postgres_connection(dsn="postgres://x@localhost/y")
            self.assertIn("psycopg2", str(ctx.exception))
        finally:
            if original is None:
                del sys.modules["psycopg2"]
            else:
                sys.modules["psycopg2"] = original

    def test_get_postgres_connection_requires_dsn(self):
        # When psycopg2 _is_ importable but no DSN, we should still get a friendly
        # RuntimeError before reaching the network. Skip if driver isn't installed
        # locally -- the lazy-import test above already covers the missing-driver path.
        try:
            import psycopg2  # noqa: F401
        except ImportError:
            self.skipTest("psycopg2 not installed locally")
        with mock.patch.object(workspace, "load_config", return_value={
            "backend": "postgres", "machine_id": "x", "team": "",
            "db_path": None, "pg_dsn": None,
        }):
            with self.assertRaises(RuntimeError) as ctx:
                workspace.get_postgres_connection()
            self.assertIn("DSN", str(ctx.exception))


class TestMachineIdSchema(unittest.TestCase):
    """machine_id columns must be added to turns + sessions, idempotently."""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpfile.close()
        self.db_path = Path(self.tmpfile.name)

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_fresh_db_has_machine_id_columns(self):
        conn = get_db(self.db_path)
        init_db(conn)
        turn_cols = [r[1] for r in conn.execute("PRAGMA table_info(turns)")]
        sess_cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)")]
        conn.close()
        self.assertIn("machine_id", turn_cols)
        self.assertIn("machine_id", sess_cols)

    def test_migration_is_idempotent(self):
        # Calling _migrate_schema twice on the same DB must not error.
        conn = get_db(self.db_path)
        init_db(conn)
        scanner._migrate_schema(conn)
        scanner._migrate_schema(conn)
        scanner._migrate_schema(conn)
        # Still exactly one machine_id column on each.
        turn_count = sum(1 for r in conn.execute("PRAGMA table_info(turns)") if r[1] == "machine_id")
        sess_count = sum(1 for r in conn.execute("PRAGMA table_info(sessions)") if r[1] == "machine_id")
        conn.close()
        self.assertEqual(turn_count, 1)
        self.assertEqual(sess_count, 1)

    def test_pre_migration_db_gets_machine_id_column(self):
        """A DB created without the machine_id column (pre-feature) should be
        upgraded by _migrate_schema -- simulating an existing user's install."""
        conn = sqlite3.connect(self.db_path)
        # Create older-style schema without machine_id, without cache_1h, without session_name.
        conn.executescript("""
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                project_name TEXT,
                first_timestamp TEXT,
                last_timestamp TEXT,
                git_branch TEXT,
                total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0,
                total_cache_read INTEGER DEFAULT 0,
                total_cache_creation INTEGER DEFAULT 0,
                model TEXT,
                turn_count INTEGER DEFAULT 0
            );
            CREATE TABLE turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, timestamp TEXT, model TEXT,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_creation_tokens INTEGER DEFAULT 0,
                tool_name TEXT, cwd TEXT
            );
        """)
        conn.commit()
        scanner._migrate_schema(conn)
        turn_cols = [r[1] for r in conn.execute("PRAGMA table_info(turns)")]
        sess_cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)")]
        conn.close()
        self.assertIn("machine_id", turn_cols)
        self.assertIn("machine_id", sess_cols)

    def test_insert_turns_stamps_machine_id(self):
        conn = get_db(self.db_path)
        init_db(conn)
        with mock.patch.object(workspace, "load_config", return_value={
            "backend": "sqlite", "machine_id": "test-host",
            "team": "", "db_path": None, "pg_dsn": None,
        }):
            insert_turns(conn, [{
                "session_id": "s1", "timestamp": "2026-05-01T10:00:00Z",
                "model": "claude-sonnet-4-6",
                "input_tokens": 100, "output_tokens": 50,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "cache_1h_tokens": 0,
                "tool_name": None, "cwd": "/tmp",
                "message_id": "msg-1",
            }])
        row = conn.execute("SELECT machine_id FROM turns WHERE message_id='msg-1'").fetchone()
        conn.close()
        self.assertEqual(row[0], "test-host")

    def test_upsert_sessions_stamps_machine_id(self):
        conn = get_db(self.db_path)
        init_db(conn)
        with mock.patch.object(workspace, "load_config", return_value={
            "backend": "sqlite", "machine_id": "test-laptop",
            "team": "", "db_path": None, "pg_dsn": None,
        }):
            upsert_sessions(conn, [{
                "session_id": "sess-x", "project_name": "p", "first_timestamp": "2026-05-01T09:00:00Z",
                "last_timestamp": "2026-05-01T10:00:00Z", "git_branch": "",
                "total_input_tokens": 1, "total_output_tokens": 1,
                "total_cache_read": 0, "total_cache_creation": 0,
                "total_cache_1h": 0, "model": "claude-sonnet-4-6", "turn_count": 1,
                "session_name": None,
            }])
        row = conn.execute("SELECT machine_id FROM sessions WHERE session_id='sess-x'").fetchone()
        conn.close()
        self.assertEqual(row[0], "test-laptop")


class TestDashboardByMachine(unittest.TestCase):
    """Dashboard payload should expose by_machine + all_machines."""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpfile.close()
        self.db_path = Path(self.tmpfile.name)
        conn = get_db(self.db_path)
        init_db(conn)
        # Seed two sessions from two different machines.
        for mid, sid in [("laptop-a", "sess-a"), ("laptop-b", "sess-b")]:
            with mock.patch.object(workspace, "load_config", return_value={
                "backend": "sqlite", "machine_id": mid,
                "team": "", "db_path": None, "pg_dsn": None,
            }):
                upsert_sessions(conn, [{
                    "session_id": sid, "project_name": "team/repo",
                    "first_timestamp": "2026-05-01T09:00:00Z",
                    "last_timestamp": "2026-05-01T10:00:00Z",
                    "git_branch": "main",
                    "total_input_tokens": 1000, "total_output_tokens": 500,
                    "total_cache_read": 0, "total_cache_creation": 0,
                    "total_cache_1h": 0,
                    "model": "claude-sonnet-4-6", "turn_count": 4,
                    "session_name": None,
                }])
                insert_turns(conn, [{
                    "session_id": sid, "timestamp": "2026-05-01T09:30:00Z",
                    "model": "claude-sonnet-4-6",
                    "input_tokens": 1000, "output_tokens": 500,
                    "cache_read_tokens": 0, "cache_creation_tokens": 0,
                    "cache_1h_tokens": 0,
                    "tool_name": None, "cwd": "/tmp",
                    "message_id": f"msg-{sid}",
                }])
        conn.commit()
        conn.close()

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_payload_contains_by_machine(self):
        data = get_dashboard_data(self.db_path)
        self.assertIn("by_machine", data)
        self.assertIn("all_machines", data)
        machines = {m["machine_id"] for m in data["by_machine"]}
        self.assertEqual(machines, {"laptop-a", "laptop-b"})
        self.assertEqual(set(data["all_machines"]), {"laptop-a", "laptop-b"})

    def test_payload_per_machine_totals_correct(self):
        data = get_dashboard_data(self.db_path)
        by_mid = {m["machine_id"]: m for m in data["by_machine"]}
        self.assertEqual(by_mid["laptop-a"]["sessions"], 1)
        self.assertEqual(by_mid["laptop-a"]["input"], 1000)
        self.assertEqual(by_mid["laptop-a"]["output"], 500)
        self.assertEqual(by_mid["laptop-b"]["sessions"], 1)

    def test_sessions_carry_machine_id(self):
        data = get_dashboard_data(self.db_path)
        mids = {s["machine_id"] for s in data["sessions_all"]}
        self.assertEqual(mids, {"laptop-a", "laptop-b"})


class TestDashboardMachineFilterUI(unittest.TestCase):
    """The machine filter UI hooks must be present in the rendered HTML."""

    def test_machine_filter_wrap_present(self):
        self.assertIn("machine-filter-wrap", HTML_TEMPLATE)

    def test_machine_select_present(self):
        self.assertIn('id="machine-select"', HTML_TEMPLATE)

    def test_build_machine_filter_js_present(self):
        self.assertIn("buildMachineFilterUI", HTML_TEMPLATE)

    def test_filter_hidden_by_default(self):
        # Auto-hidden if only one machine (or none) is present -- the wrap starts
        # with display:none and is only toggled on by JS when machines.length > 1.
        self.assertIn('id="machine-filter-wrap"', HTML_TEMPLATE)
        # Ensure the inline default style is display:none.
        idx = HTML_TEMPLATE.find('id="machine-filter-wrap"')
        snippet = HTML_TEMPLATE[idx:idx + 200]
        self.assertIn("display:none", snippet)


if __name__ == "__main__":
    unittest.main()
