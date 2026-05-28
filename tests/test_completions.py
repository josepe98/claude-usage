"""Tests for the `completions` subcommand (bash/zsh/fish tab completion)."""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

import cli


class TestCompletions(unittest.TestCase):
    def _run(self, shell):
        """Invoke cmd_completions(shell) and return (stdout, stderr, exit_code)."""
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with redirect_stdout(out), redirect_stderr(err):
            try:
                cli.cmd_completions(shell=shell)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return out.getvalue(), err.getvalue(), code

    # ── bash ──────────────────────────────────────────────────────────────────

    def test_bash_script_contains_all_command_names(self):
        out, _, code = self._run("bash")
        self.assertEqual(code, 0)
        for cmd in cli.COMMANDS:
            self.assertIn(cmd, out, f"bash script missing command '{cmd}'")

    def test_bash_script_registers_complete_function(self):
        out, _, code = self._run("bash")
        self.assertEqual(code, 0)
        self.assertIn("_claude_usage_complete", out)
        self.assertIn("complete -F", out)

    def test_bash_script_lists_known_flags(self):
        out, _, _ = self._run("bash")
        self.assertIn("--projects-dir", out)
        self.assertIn("--host", out)
        self.assertIn("--port", out)

    # ── zsh ───────────────────────────────────────────────────────────────────

    def test_zsh_script_contains_all_command_names(self):
        out, _, code = self._run("zsh")
        self.assertEqual(code, 0)
        for cmd in cli.COMMANDS:
            self.assertIn(cmd, out, f"zsh script missing command '{cmd}'")

    def test_zsh_script_uses_compdef(self):
        out, _, code = self._run("zsh")
        self.assertEqual(code, 0)
        self.assertIn("#compdef", out)
        self.assertIn("compdef _claude_usage", out)

    # ── fish ──────────────────────────────────────────────────────────────────

    def test_fish_script_contains_all_command_names(self):
        out, _, code = self._run("fish")
        self.assertEqual(code, 0)
        for cmd in cli.COMMANDS:
            self.assertIn(f"-a '{cmd}'", out, f"fish script missing command '{cmd}'")

    # ── error paths ───────────────────────────────────────────────────────────

    def test_unknown_shell_prints_helpful_error(self):
        out, err, code = self._run("powershell")
        self.assertNotEqual(code, 0, "unknown shell must exit non-zero")
        self.assertEqual(out, "", "no completion script should be emitted for unknown shell")
        self.assertIn("powershell", err)
        # Mentions every supported shell so the user knows their options.
        self.assertIn("bash", err)
        self.assertIn("zsh", err)
        self.assertIn("fish", err)

    def test_missing_shell_arg_prints_usage(self):
        # With no arg AND no detectable SHELL env var, we should fall through
        # to the usage error.
        with mock.patch.dict(os.environ, {"SHELL": ""}, clear=False):
            out, err, code = self._run(None)
        self.assertNotEqual(code, 0)
        self.assertIn("Usage", err)
        self.assertIn("bash", err)

    # ── auto-detect from $SHELL ───────────────────────────────────────────────

    def test_missing_shell_arg_autodetects_from_env(self):
        # When $SHELL points at a known shell we generate the matching script
        # rather than erroring — improves discoverability per maintainer review.
        for shell_path, marker in (
            ("/bin/bash", "_claude_usage_complete"),
            ("/usr/local/bin/zsh", "#compdef"),
            ("/opt/homebrew/bin/fish", "complete -c claude-usage"),
        ):
            with self.subTest(shell_path=shell_path):
                with mock.patch.dict(os.environ, {"SHELL": shell_path}, clear=False):
                    out, _, code = self._run(None)
                self.assertEqual(code, 0)
                self.assertIn(marker, out)

    def test_missing_shell_arg_unknown_shell_in_env_still_errors(self):
        # $SHELL=/bin/csh (or anything else unsupported) → usage error.
        with mock.patch.dict(os.environ, {"SHELL": "/bin/csh"}, clear=False):
            out, err, code = self._run(None)
        self.assertNotEqual(code, 0)
        self.assertIn("Usage", err)


if __name__ == "__main__":
    unittest.main()
