"""Tests for the git post-commit hook + install-git-hook CLI command."""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import cli
import dashboard


HOOK_SCRIPT = REPO_ROOT / "hooks" / "post-commit"


def _have_git():
    return shutil.which("git") is not None


def _git(repo, *args, env=None):
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, env=e,
    )


def _init_repo(repo):
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Tester")


# ─────────────────────────────────────────────────────────────────────────────
# Bundled hook script
# ─────────────────────────────────────────────────────────────────────────────

class TestHookScript(unittest.TestCase):
    """The shipped hooks/post-commit must exist and be executable."""

    def test_hook_file_exists(self):
        self.assertTrue(HOOK_SCRIPT.exists(),
                        f"missing bundled hook at {HOOK_SCRIPT}")

    def test_hook_is_executable(self):
        mode = HOOK_SCRIPT.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR,
                        "hooks/post-commit must be executable (chmod +x)")

    def test_hook_has_shebang(self):
        first_line = HOOK_SCRIPT.read_text().splitlines()[0]
        self.assertTrue(first_line.startswith("#!"),
                        "post-commit must start with a shebang")


# ─────────────────────────────────────────────────────────────────────────────
# Hook execution against a temp repo
# ─────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(_have_git(), "git not installed")
class TestHookExecution(unittest.TestCase):
    """End-to-end: install hook, commit, assert JSONL record."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.trace_dir = self.tmp / "trace"
        self.trace_dir.mkdir()
        _init_repo(self.repo)
        # Stage the hook under a dedicated dir + point hooksPath at it.
        hooks_dir = self.repo / ".hooks"
        hooks_dir.mkdir()
        shutil.copy2(HOOK_SCRIPT, hooks_dir / "post-commit")
        os.chmod(hooks_dir / "post-commit", 0o755)
        _git(self.repo, "config", "core.hooksPath", str(hooks_dir))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _commit(self, message, env_extra=None):
        (self.repo / "f.txt").write_text(message)
        _git(self.repo, "add", "f.txt")
        env = {"CLAUDE_TRACE_DIR": str(self.trace_dir)}
        if env_extra:
            env.update(env_extra)
        _git(self.repo, "commit", "-q", "-m", message, env=env)

    def test_hook_appends_record_with_env_session_id(self):
        self._commit("first commit", env_extra={"CLAUDE_SESSION_ID": "my-sess-001"})
        trace = self.trace_dir / "git-trace.jsonl"
        self.assertTrue(trace.exists(), "trace file not created")
        lines = [l for l in trace.read_text().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["session_id"], "my-sess-001")
        self.assertEqual(rec["message"], "first commit")
        self.assertEqual(rec["author"], "Tester")
        self.assertEqual(len(rec["sha"]), 40)
        self.assertTrue(rec["timestamp"].endswith("Z"))
        self.assertEqual(rec["repo"], str(self.repo))

    def test_hook_handles_quotes_in_message(self):
        # JSON-escape correctness for tricky commit subjects.
        self._commit('feat: handle "quotes" and \\backslashes',
                     env_extra={"CLAUDE_SESSION_ID": "x"})
        trace = self.trace_dir / "git-trace.jsonl"
        rec = json.loads(trace.read_text().splitlines()[-1])
        self.assertIn('"quotes"', rec["message"])
        self.assertIn("\\backslashes", rec["message"])

    def test_hook_appends_multiple_commits(self):
        self._commit("c1", env_extra={"CLAUDE_SESSION_ID": "s"})
        self._commit("c2", env_extra={"CLAUDE_SESSION_ID": "s"})
        self._commit("c3", env_extra={"CLAUDE_SESSION_ID": "s"})
        trace = self.trace_dir / "git-trace.jsonl"
        lines = [l for l in trace.read_text().splitlines() if l.strip()]
        self.assertEqual(len(lines), 3)
        msgs = [json.loads(l)["message"] for l in lines]
        self.assertEqual(msgs, ["c1", "c2", "c3"])


# ─────────────────────────────────────────────────────────────────────────────
# CLI installer
# ─────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(_have_git(), "git not installed")
class TestInstallGitHookCommand(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        _init_repo(self.repo)
        self._origcwd = os.getcwd()
        os.chdir(self.repo)

    def tearDown(self):
        os.chdir(self._origcwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_install_per_repo_drops_hook_and_sets_config(self):
        with mock.patch.object(sys, "argv", ["cli.py", "install-git-hook"]):
            cli.cmd_install_git_hook()
        installed = self.repo / ".claude-usage-hooks" / "post-commit"
        self.assertTrue(installed.exists())
        self.assertTrue(installed.stat().st_mode & stat.S_IXUSR)
        cfg = _git(self.repo, "config", "--local", "core.hooksPath").stdout.strip()
        self.assertEqual(cfg, str(self.repo / ".claude-usage-hooks"))

    def test_install_outside_repo_exits_nonzero(self):
        # Move out of any git repo. /tmp is fine: not a git working tree.
        os.chdir(self.tmp)  # tmp itself is not a repo
        with mock.patch.object(sys, "argv", ["cli.py", "install-git-hook"]):
            with self.assertRaises(SystemExit) as ctx:
                cli.cmd_install_git_hook()
        self.assertNotEqual(ctx.exception.code, 0)

    def test_install_registered_in_commands(self):
        # Wire-up smoke test: dispatcher must know the command exists.
        self.assertIn("install-git-hook", cli.COMMANDS)
        self.assertIs(cli.COMMANDS["install-git-hook"], cli.cmd_install_git_hook)


# ─────────────────────────────────────────────────────────────────────────────
# _load_git_trace + /api/data integration
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadGitTrace(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.trace = self.tmp / "git-trace.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file_returns_empty_list(self):
        self.assertEqual(dashboard._load_git_trace(path=self.trace), [])

    def test_skips_corrupt_lines(self):
        self.trace.write_text(
            json.dumps({"sha": "a" * 40, "timestamp": "2026-05-22T10:00:00Z"}) + "\n"
            "this-is-not-json\n"
            + json.dumps({"sha": "b" * 40, "timestamp": "2026-05-23T10:00:00Z"}) + "\n"
        )
        out = dashboard._load_git_trace(path=self.trace)
        self.assertEqual(len(out), 2)

    def test_skips_records_missing_sha_or_timestamp(self):
        self.trace.write_text(
            json.dumps({"sha": "a" * 40}) + "\n"  # no timestamp
            + json.dumps({"timestamp": "2026-05-22T10:00:00Z"}) + "\n"  # no sha
            + json.dumps({"sha": "c" * 40, "timestamp": "2026-05-22T10:00:00Z"}) + "\n"
        )
        out = dashboard._load_git_trace(path=self.trace)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["short_sha"], "cccccccc")

    def test_returns_newest_first_and_respects_limit(self):
        lines = []
        for i in range(60):
            lines.append(json.dumps({
                "sha": f"{i:040x}",
                "timestamp": f"2026-05-{(i % 28)+1:02d}T00:00:00Z",
                "session_id": f"sess-{i}",
            }))
        self.trace.write_text("\n".join(lines) + "\n")
        out = dashboard._load_git_trace(path=self.trace, limit=50)
        self.assertEqual(len(out), 50)
        # Must be in reverse-chron order.
        for a, b in zip(out, out[1:]):
            self.assertGreaterEqual(a["timestamp"], b["timestamp"])

    def test_payload_includes_git_trace_recent(self):
        # /api/data must include the field even when the trace file is missing,
        # so downstream JS can rely on `d.git_trace_recent || []`.
        import scanner
        db_file = self.tmp / "u.db"
        conn = scanner.get_db(db_file)
        scanner.init_db(conn)
        conn.commit(); conn.close()

        # Point the loader at our temp trace (which doesn't exist yet) so we
        # don't accidentally read the developer's real ~/.claude/git-trace.jsonl.
        with mock.patch.object(dashboard, "GIT_TRACE_PATH", self.trace):
            data = dashboard.get_dashboard_data(db_path=db_file)
        self.assertIn("git_trace_recent", data)
        self.assertEqual(data["git_trace_recent"], [])

        # Now write a record and re-query.
        self.trace.write_text(
            json.dumps({"sha": "d" * 40, "timestamp": "2026-05-23T10:00:00Z",
                        "session_id": "sess-xyz", "repo": "/r",
                        "message": "feat: x", "author": "me"}) + "\n"
        )
        with mock.patch.object(dashboard, "GIT_TRACE_PATH", self.trace):
            data = dashboard.get_dashboard_data(db_path=db_file)
        self.assertEqual(len(data["git_trace_recent"]), 1)
        self.assertEqual(data["git_trace_recent"][0]["session_id"], "sess-xyz")


if __name__ == "__main__":
    unittest.main()
