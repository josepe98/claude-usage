"""Tests for tray.py — the menu-bar / system-tray app.

These tests verify:
  * tray.py imports cleanly with NO tray backend installed (lazy import)
  * cli.py exposes `tray` in its command table and dispatches to it
  * Pure helpers: money formatting, cost colouring, today/month aggregation
  * /api/data polling handles unreachable server / bad payloads gracefully
"""

from __future__ import annotations

import builtins
import importlib
import sys
import unittest
from unittest import mock


# Block rumps / pystray imports for the whole test run so we exercise the
# "neither backend installed" code path even on machines that happen to have
# one of them pip-installed. This must run BEFORE `import tray` so that
# previous test sessions don't leak a cached backend module.
_BLOCKED = {"rumps", "pystray", "PIL", "PIL.Image", "PIL.ImageDraw"}
_real_import = builtins.__import__

def _blocking_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name in _BLOCKED or any(name.startswith(b + ".") for b in _BLOCKED):
        raise ImportError(f"blocked for test: {name}")
    return _real_import(name, globals, locals, fromlist, level)

for mod in list(_BLOCKED):
    sys.modules.pop(mod, None)
sys.modules.pop("tray", None)
builtins.__import__ = _blocking_import
try:
    import tray  # noqa: E402  — import under the import block
finally:
    builtins.__import__ = _real_import


class TestLazyImport(unittest.TestCase):
    """tray.py must import even when no tray backend is installed."""

    def test_module_imports_without_rumps_or_pystray(self):
        # If we got here, `import tray` succeeded under the blocked-import
        # environment set up at module load time. Re-importing must also
        # work and expose the public surface.
        self.assertTrue(hasattr(tray, "run"))
        self.assertTrue(hasattr(tray, "fmt_money"))
        self.assertTrue(hasattr(tray, "cost_color"))
        self.assertTrue(hasattr(tray, "poll_once"))

    def test_pick_backend_returns_none_when_nothing_installed(self):
        with mock.patch.dict(sys.modules, {}, clear=False):
            for name in ("rumps", "pystray", "PIL"):
                sys.modules.pop(name, None)
            with mock.patch("builtins.__import__", side_effect=_blocking_import):
                self.assertIsNone(tray._pick_backend())

    def test_run_prints_install_hint_when_no_backend(self):
        with mock.patch.object(tray, "_pick_backend", return_value=None):
            with mock.patch.object(sys, "stderr") as err:
                rc = tray.run()
        self.assertEqual(rc, 1)
        # stderr.write was called with the install hint
        written = "".join(call.args[0] for call in err.write.call_args_list)
        self.assertIn("rumps", written)
        self.assertIn("pystray", written)


class TestCliTrayCommand(unittest.TestCase):
    """cli.py must expose the tray command and route to tray.run()."""

    def test_tray_command_is_registered(self):
        import cli
        importlib.reload(cli)  # pick up fresh COMMANDS table
        self.assertIn("tray", cli.COMMANDS)
        self.assertTrue(callable(cli.COMMANDS["tray"]))

    def test_cmd_tray_invokes_tray_run(self):
        import cli
        fake_tray = mock.MagicMock()
        fake_tray.run.return_value = 0
        with mock.patch.dict(sys.modules, {"tray": fake_tray}):
            with self.assertRaises(SystemExit) as ctx:
                cli.cmd_tray(url="http://example.test:1234")
        self.assertEqual(ctx.exception.code, 0)
        fake_tray.run.assert_called_once_with("http://example.test:1234")

    def test_tray_command_in_usage_text(self):
        import cli
        self.assertIn("tray", cli.USAGE)


class TestFmtMoney(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(tray.fmt_money(0), "$0.00")
        self.assertEqual(tray.fmt_money(1), "$1.00")
        self.assertEqual(tray.fmt_money(12.345), "$12.35")
        self.assertEqual(tray.fmt_money(0.001), "$0.00")

    def test_handles_none_and_garbage(self):
        self.assertEqual(tray.fmt_money(None), "$0.00")
        self.assertEqual(tray.fmt_money("not a number"), "$0.00")

    def test_negative_clamped_to_zero(self):
        self.assertEqual(tray.fmt_money(-5), "$0.00")


class TestCostColor(unittest.TestCase):
    def test_green_below_one_dollar(self):
        self.assertEqual(tray.cost_color(0), "green")
        self.assertEqual(tray.cost_color(0.99), "green")

    def test_amber_between_one_and_ten(self):
        self.assertEqual(tray.cost_color(1.0), "amber")
        self.assertEqual(tray.cost_color(5.0), "amber")
        self.assertEqual(tray.cost_color(10.0), "amber")

    def test_red_above_ten(self):
        self.assertEqual(tray.cost_color(10.01), "red")
        self.assertEqual(tray.cost_color(100), "red")

    def test_garbage_treated_as_zero(self):
        self.assertEqual(tray.cost_color(None), "green")
        self.assertEqual(tray.cost_color("bad"), "green")


class TestComputeTodayAndMonth(unittest.TestCase):
    def _row(self, day, model="claude-sonnet-4-6", inp=0, out=0):
        return {
            "day": day, "model": model,
            "input": inp, "output": out,
            "cache_read": 0, "cache_creation": 0, "cache_1h": 0,
        }

    def test_empty_data(self):
        self.assertEqual(tray.compute_today_and_month({}), (0.0, 0.0))
        self.assertEqual(tray.compute_today_and_month(None), (0.0, 0.0))
        self.assertEqual(tray.compute_today_and_month({"daily_by_model": []}), (0.0, 0.0))

    def test_today_and_month_split(self):
        data = {"daily_by_model": [
            self._row("2026-05-23", out=1_000_000),  # today: $15
            self._row("2026-05-10", out=2_000_000),  # same month: $30
            self._row("2026-04-23", out=1_000_000),  # different month: ignored
        ]}
        today, month = tray.compute_today_and_month(data, today_iso="2026-05-23")
        self.assertAlmostEqual(today, 15.00, places=2)
        self.assertAlmostEqual(month, 45.00, places=2)

    def test_unknown_models_cost_zero(self):
        data = {"daily_by_model": [
            self._row("2026-05-23", model="gpt-4o", out=1_000_000),
        ]}
        today, month = tray.compute_today_and_month(data, today_iso="2026-05-23")
        self.assertEqual(today, 0.0)
        self.assertEqual(month, 0.0)


class TestPollOnce(unittest.TestCase):
    def test_returns_offline_when_health_fails(self):
        with mock.patch.object(tray, "fetch_json", return_value=None):
            result = tray.poll_once("http://nope")
        self.assertFalse(result["ok"])
        self.assertEqual(result["today"], 0.0)
        self.assertIn("unreachable", result["error"])

    def test_returns_costs_on_happy_path(self):
        health = {"status": "ok", "sessions": 1, "turns": 1}
        data = {"daily_by_model": [{
            "day": "2026-05-23", "model": "claude-sonnet-4-6",
            "input": 0, "output": 1_000_000,
            "cache_read": 0, "cache_creation": 0, "cache_1h": 0,
        }]}
        with mock.patch.object(tray, "fetch_json", side_effect=[health, data]):
            with mock.patch.object(tray, "compute_today_and_month",
                                   return_value=(7.5, 42.0)):
                result = tray.poll_once("http://x")
        self.assertTrue(result["ok"])
        self.assertEqual(result["today"], 7.5)
        self.assertEqual(result["month"], 42.0)

    def test_handles_error_payload_in_data(self):
        health = {"status": "ok"}
        data = {"error": "Database not found"}
        with mock.patch.object(tray, "fetch_json", side_effect=[health, data]):
            result = tray.poll_once("http://x")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Database not found")


if __name__ == "__main__":
    unittest.main()
