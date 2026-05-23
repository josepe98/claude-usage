"""Tests for multi-account tracking.

Covers:
- account_for_cwd: rule matching, default fallback, empty/None inputs
- load_account_rules: file parsing, missing file, malformed JSON, underscore-comment skip
- scanner integration: account column persists after a full scan
- migration: _migrate_schema is idempotent and adds `account` to older DBs
- dashboard: payload includes the `accounts` summary and each session carries `account`
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner
import dashboard


def _write_assistant_record(f, session_id, cwd, ts="2026-04-08T10:00:00Z",
                            model="claude-sonnet-4-6", inp=100, out=50, msg_id="m1"):
    rec = {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": ts,
        "cwd": cwd,
        "message": {
            "id": msg_id,
            "model": model,
            "usage": {"input_tokens": inp, "output_tokens": out},
            "content": [],
        },
    }
    f.write(json.dumps(rec) + "\n")


class TestAccountForCwd(unittest.TestCase):
    def test_first_matching_rule_wins(self):
        rules = {"work-": "work", "personal-": "personal"}
        self.assertEqual(scanner.account_for_cwd("/x/work-foo", rules), "work")
        self.assertEqual(scanner.account_for_cwd("/x/personal-bar", rules), "personal")

    def test_no_match_returns_default(self):
        rules = {"work-": "work"}
        self.assertEqual(scanner.account_for_cwd("/x/random", rules), "default")

    def test_empty_cwd_returns_default(self):
        self.assertEqual(scanner.account_for_cwd("", {"x": "y"}), "default")
        self.assertEqual(scanner.account_for_cwd(None, {"x": "y"}), "default")

    def test_no_rules_returns_default(self):
        self.assertEqual(scanner.account_for_cwd("/x/work-foo", {}), "default")
        self.assertEqual(scanner.account_for_cwd("/x/work-foo", None), "default")

    def test_substring_match_anywhere(self):
        # The fragment can sit anywhere in the cwd, not just at the end.
        rules = {"PhpstormProjects/work-": "work"}
        self.assertEqual(
            scanner.account_for_cwd("/Users/me/PhpstormProjects/work-app", rules),
            "work",
        )

    def test_rule_order_determines_winner(self):
        # Both rules match; first-inserted wins (Python dicts preserve order).
        rules = {"oss": "open-source", "/Users": "personal"}
        # cwd contains both "oss" and "/Users"; "oss" was inserted first.
        self.assertEqual(
            scanner.account_for_cwd("/Users/me/oss/thing", rules),
            "open-source",
        )


class TestLoadAccountRules(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(scanner.load_account_rules("/nonexistent/path.json"), {})

    def test_malformed_json_returns_empty(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{not json")
            path = f.name
        try:
            self.assertEqual(scanner.load_account_rules(path), {})
        finally:
            os.unlink(path)

    def test_underscore_keys_are_skipped(self):
        # Lets users keep "_comment" annotations in their config file without
        # them being treated as substring-match rules.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"_comment": "ignore me", "work-": "work"}, f)
            path = f.name
        try:
            rules = scanner.load_account_rules(path)
            self.assertNotIn("_comment", rules)
            self.assertEqual(rules.get("work-"), "work")
        finally:
            os.unlink(path)

    def test_non_dict_returns_empty(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('["array", "not", "dict"]')
            path = f.name
        try:
            self.assertEqual(scanner.load_account_rules(path), {})
        finally:
            os.unlink(path)


class TestMigration(unittest.TestCase):
    def test_migrate_adds_account_to_old_schema(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "old.db"
            # Build a pre-account schema by hand.
            c = sqlite3.connect(db)
            c.execute("""
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
                )
            """)
            c.execute("INSERT INTO sessions (session_id) VALUES ('s1')")
            c.commit()
            scanner._migrate_schema(c)
            c.commit()
            row = c.execute("SELECT account FROM sessions WHERE session_id='s1'").fetchone()
            self.assertEqual(row[0], "default")
            c.close()

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "fresh.db"
            c = scanner.get_db(db)
            scanner.init_db(c)
            # Should be a no-op the second time.
            scanner._migrate_schema(c)
            scanner._migrate_schema(c)
            cols = {r[1] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
            self.assertIn("account", cols)
            c.close()


class TestScannerIntegration(unittest.TestCase):
    def test_account_persisted_after_scan(self):
        with tempfile.TemporaryDirectory() as td:
            projects = Path(td) / "projects" / "myproj"
            projects.mkdir(parents=True)
            log = projects / "log.jsonl"
            with open(log, "w") as f:
                _write_assistant_record(f, "sess-work-1", "/Users/me/work-app", msg_id="w1")
                _write_assistant_record(f, "sess-personal-1", "/Users/me/personal-blog",
                                        ts="2026-04-08T10:01:00Z", msg_id="p1")
                _write_assistant_record(f, "sess-other-1", "/Users/me/random",
                                        ts="2026-04-08T10:02:00Z", msg_id="o1")

            # Write the rule config and point scanner at it.
            config = Path(td) / "accounts.json"
            config.write_text(json.dumps({"work-": "work", "personal-": "personal"}))
            orig = scanner.ACCOUNTS_CONFIG_PATH
            scanner.ACCOUNTS_CONFIG_PATH = config
            try:
                db = Path(td) / "usage.db"
                scanner.scan(projects_dirs=[str(projects.parent)], db_path=db, verbose=False)
                c = sqlite3.connect(db)
                rows = dict(c.execute("SELECT session_id, account FROM sessions").fetchall())
                self.assertEqual(rows.get("sess-work-1"), "work")
                self.assertEqual(rows.get("sess-personal-1"), "personal")
                self.assertEqual(rows.get("sess-other-1"), "default")
                c.close()
            finally:
                scanner.ACCOUNTS_CONFIG_PATH = orig

    def test_aggregate_sessions_accepts_explicit_rules(self):
        # When rules are passed in, the on-disk config must not be consulted —
        # this lets unit tests and integrations stay hermetic.
        session_metas = [{
            "session_id": "s1",
            "project_name": "x",
            "first_timestamp": "2026-04-08T10:00:00Z",
            "last_timestamp": "2026-04-08T10:00:00Z",
            "git_branch": "",
            "model": None,
            "custom_title": None,
            "agent_name": None,
            "cwd": "/clients/acme/repo",
        }]
        out = scanner.aggregate_sessions(
            session_metas, turns=[], account_rules={"clients/acme": "acme"},
        )
        self.assertEqual(out[0]["account"], "acme")


class TestDashboardPayload(unittest.TestCase):
    def test_payload_includes_accounts_and_per_session_account(self):
        with tempfile.TemporaryDirectory() as td:
            projects = Path(td) / "projects" / "p"
            projects.mkdir(parents=True)
            log = projects / "log.jsonl"
            with open(log, "w") as f:
                _write_assistant_record(f, "s-w", "/me/work-app", msg_id="w1")
                _write_assistant_record(f, "s-p", "/me/personal-x",
                                        ts="2026-04-08T10:01:00Z", msg_id="p1")

            config = Path(td) / "accounts.json"
            config.write_text(json.dumps({"work-": "work", "personal-": "personal"}))
            orig = scanner.ACCOUNTS_CONFIG_PATH
            scanner.ACCOUNTS_CONFIG_PATH = config
            try:
                db = Path(td) / "usage.db"
                scanner.scan(projects_dirs=[str(projects.parent)], db_path=db, verbose=False)
                data = dashboard.get_dashboard_data(db)
                # Payload top-level "accounts" summary
                self.assertIn("accounts", data)
                account_names = sorted(a["name"] for a in data["accounts"])
                self.assertEqual(account_names, ["personal", "work"])
                # Every session row has an "account" field
                for s in data["sessions_all"]:
                    self.assertIn("account", s)
                # Counts add up
                for a in data["accounts"]:
                    self.assertEqual(a["sessions"], 1)
            finally:
                scanner.ACCOUNTS_CONFIG_PATH = orig


if __name__ == "__main__":
    unittest.main()
