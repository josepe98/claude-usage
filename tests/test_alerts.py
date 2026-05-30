"""Tests for alerts.py - condition evaluator, cooldown, dispatchers."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import alerts


class TestEvaluateCondition(unittest.TestCase):
    """The DSL: comparisons, boolean ops, variable lookup."""

    def test_simple_greater_than(self):
        self.assertTrue(alerts.evaluate_condition("today_cost > 50", {"today_cost": 60}))
        self.assertFalse(alerts.evaluate_condition("today_cost > 50", {"today_cost": 50}))

    def test_all_comparison_ops(self):
        v = {"x": 10}
        self.assertTrue(alerts.evaluate_condition("x > 5", v))
        self.assertTrue(alerts.evaluate_condition("x >= 10", v))
        self.assertTrue(alerts.evaluate_condition("x < 20", v))
        self.assertTrue(alerts.evaluate_condition("x <= 10", v))
        self.assertTrue(alerts.evaluate_condition("x == 10", v))
        self.assertTrue(alerts.evaluate_condition("x != 9", v))

    def test_string_equality(self):
        self.assertTrue(
            alerts.evaluate_condition("project == 'client-X'", {"project": "client-X"})
        )
        self.assertFalse(
            alerts.evaluate_condition("project == 'client-X'", {"project": "other"})
        )

    def test_boolean_and_uppercase(self):
        # SQL-style uppercase AND/OR should be accepted.
        vars_ = {"project": "client-X", "month_to_date": 250}
        self.assertTrue(alerts.evaluate_condition(
            "project == 'client-X' AND month_to_date > 200", vars_,
        ))
        self.assertFalse(alerts.evaluate_condition(
            "project == 'client-X' AND month_to_date > 300", vars_,
        ))

    def test_boolean_or_and_combo(self):
        v = {"a": 1, "b": 0}
        self.assertTrue(alerts.evaluate_condition("a > 0 OR b > 0", v))
        self.assertTrue(alerts.evaluate_condition("a > 0 or b > 0", v))

    def test_unknown_variable_raises(self):
        with self.assertRaises(alerts.ConditionError):
            alerts.evaluate_condition("undefined > 0", {})

    def test_empty_condition_raises(self):
        with self.assertRaises(alerts.ConditionError):
            alerts.evaluate_condition("", {})
        with self.assertRaises(alerts.ConditionError):
            alerts.evaluate_condition("   ", {})


class TestEvaluatorSafety(unittest.TestCase):
    """No arbitrary Python may be executed by the DSL."""

    def test_rejects_function_call(self):
        with self.assertRaises(alerts.ConditionError):
            alerts.evaluate_condition("print('hi')", {})

    def test_rejects_attribute_access(self):
        with self.assertRaises(alerts.ConditionError):
            alerts.evaluate_condition("x.foo > 1", {"x": 1})

    def test_rejects_import_via_call(self):
        # `__import__('os')` would let an attacker reach os.system; reject it.
        with self.assertRaises(alerts.ConditionError):
            alerts.evaluate_condition("__import__('os')", {})

    def test_rejects_subscript(self):
        with self.assertRaises(alerts.ConditionError):
            alerts.evaluate_condition("x[0] > 1", {"x": [1]})

    def test_rejects_arithmetic(self):
        # Arithmetic is out of scope - rules should reference precomputed metrics.
        with self.assertRaises(alerts.ConditionError):
            alerts.evaluate_condition("x + 1 > 2", {"x": 1})

    def test_evaluator_does_not_call_python_eval(self):
        """Regression guard - alerts.py must never call eval() or exec()."""
        import ast as _ast
        tree = _ast.parse(Path(alerts.__file__).read_text())
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name):
                self.assertNotIn(node.func.id, {"eval", "exec", "compile"})


class TestCooldown(unittest.TestCase):

    def _rule(self, name="r", cooldown=60):
        return alerts.Rule(
            name=name, condition="x > 0",
            action={"type": "shell", "cmd": "true"},
            cooldown_minutes=cooldown,
        )

    def test_cooldown_blocks_recent_fire(self):
        rule = self._rule(cooldown=10)
        state = {"r": time.time() - 30}  # fired 30s ago, cooldown is 10 min
        self.assertFalse(alerts._cooldown_ok(rule, state, time.time()))

    def test_cooldown_elapsed(self):
        rule = self._rule(cooldown=1)
        state = {"r": time.time() - 120}
        self.assertTrue(alerts._cooldown_ok(rule, state, time.time()))

    def test_never_fired(self):
        self.assertTrue(alerts._cooldown_ok(self._rule(), {}, time.time()))


class TestLoadRules(unittest.TestCase):

    def test_missing_file_returns_empty(self):
        path = Path(tempfile.mkdtemp()) / "absent.json"
        self.assertEqual(alerts.load_rules(path), [])

    def test_valid_rules_parse(self):
        path = Path(tempfile.mkdtemp()) / "alerts.json"
        path.write_text(json.dumps([
            {"name": "A", "condition": "today_cost > 1",
             "action": {"type": "shell", "cmd": "echo hi"}},
        ]))
        rules = alerts.load_rules(path)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].name, "A")
        self.assertEqual(rules[0].cooldown_minutes, alerts.DEFAULT_COOLDOWN_MINUTES)

    def test_invalid_json_raises(self):
        path = Path(tempfile.mkdtemp()) / "alerts.json"
        path.write_text("{not json")
        with self.assertRaises(alerts.AlertError):
            alerts.load_rules(path)

    def test_non_array_root_raises(self):
        path = Path(tempfile.mkdtemp()) / "alerts.json"
        path.write_text("{}")
        with self.assertRaises(alerts.AlertError):
            alerts.load_rules(path)

    def test_missing_action_type_raises(self):
        path = Path(tempfile.mkdtemp()) / "alerts.json"
        path.write_text(json.dumps([
            {"name": "X", "condition": "x > 0", "action": {}},
        ]))
        with self.assertRaises(alerts.AlertError):
            alerts.load_rules(path)


class TestDispatchers(unittest.TestCase):

    def test_shell_action_invokes_subprocess(self):
        action = {"type": "shell", "cmd": "echo hello"}
        with mock.patch("alerts.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="hello\n", stderr="")
            result = alerts.dispatch_action(action)
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0], "echo hello")
        self.assertTrue(kwargs.get("shell"))
        self.assertEqual(kwargs.get("timeout"), alerts.SHELL_TIMEOUT_SECONDS)
        self.assertTrue(result["ok"])

    def test_shell_action_timeout(self):
        import subprocess as _sp
        action = {"type": "shell", "cmd": "sleep 999"}
        with mock.patch("alerts.subprocess.run",
                        side_effect=_sp.TimeoutExpired("sleep", 1)):
            result = alerts.dispatch_action(action)
        self.assertFalse(result["ok"])
        self.assertIn("timed out", result["error"])

    def test_shell_requires_cmd(self):
        with self.assertRaises(alerts.AlertError):
            alerts.dispatch_action({"type": "shell"})

    def test_webhook_posts_json(self):
        action = {"type": "webhook",
                  "url": "https://example.com/hook",
                  "payload": {"text": "hi"}}
        fake_resp = mock.MagicMock()
        fake_resp.status = 200
        fake_resp.__enter__.return_value = fake_resp
        fake_resp.__exit__.return_value = False
        with mock.patch("alerts.urllib.request.urlopen",
                        return_value=fake_resp) as op:
            result = alerts.dispatch_action(action)
        op.assert_called_once()
        sent_req = op.call_args[0][0]
        self.assertEqual(sent_req.method, "POST")
        self.assertEqual(sent_req.full_url, "https://example.com/hook")
        self.assertEqual(json.loads(sent_req.data.decode()), {"text": "hi"})
        self.assertTrue(result["ok"])

    def test_webhook_requires_url(self):
        with self.assertRaises(alerts.AlertError):
            alerts.dispatch_action({"type": "webhook"})

    def test_unknown_action_type(self):
        with self.assertRaises(alerts.AlertError):
            alerts.dispatch_action({"type": "carrier-pigeon"})


class TestEvaluateAll(unittest.TestCase):

    def _state_file(self):
        return Path(tempfile.mkdtemp()) / "state.json"

    def _rule(self, **kw):
        defaults = dict(name="R", condition="today_cost > 50",
                        action={"type": "shell", "cmd": "true"},
                        cooldown_minutes=60)
        defaults.update(kw)
        return alerts.Rule(**defaults)

    def test_dry_run_does_not_invoke_action(self):
        rule = self._rule()
        with mock.patch("alerts.dispatch_action") as disp:
            results = alerts.evaluate_all(
                rules=[rule], metrics={"today_cost": 100},
                state={}, state_path=self._state_file(), dry_run=True,
            )
        disp.assert_not_called()
        self.assertEqual(results[0]["skipped"], "dry_run")
        self.assertTrue(results[0]["matched"])
        self.assertFalse(results[0]["fired"])

    def test_fires_when_matched_and_cooldown_clear(self):
        rule = self._rule()
        state_path = self._state_file()
        with mock.patch("alerts.dispatch_action",
                        return_value={"ok": True}) as disp:
            results = alerts.evaluate_all(
                rules=[rule], metrics={"today_cost": 100},
                state={}, state_path=state_path,
            )
        disp.assert_called_once_with(rule.action)
        self.assertTrue(results[0]["fired"])
        # state file persisted with timestamp
        saved = json.loads(state_path.read_text())
        self.assertIn("R", saved)

    def test_cooldown_skips_fire(self):
        rule = self._rule(cooldown_minutes=60)
        state = {"R": time.time() - 10}  # fired 10s ago
        with mock.patch("alerts.dispatch_action") as disp:
            results = alerts.evaluate_all(
                rules=[rule], metrics={"today_cost": 100},
                state=state, state_path=self._state_file(),
            )
        disp.assert_not_called()
        self.assertEqual(results[0]["skipped"], "cooldown")
        self.assertFalse(results[0]["fired"])

    def test_condition_error_recorded(self):
        rule = self._rule(condition="bogus > 1")
        results = alerts.evaluate_all(
            rules=[rule], metrics={"today_cost": 100},
            state={}, state_path=self._state_file(),
        )
        self.assertIn("condition", results[0]["error"])
        self.assertFalse(results[0]["fired"])

    def test_no_match_no_fire(self):
        rule = self._rule()
        with mock.patch("alerts.dispatch_action") as disp:
            results = alerts.evaluate_all(
                rules=[rule], metrics={"today_cost": 10},
                state={}, state_path=self._state_file(),
            )
        disp.assert_not_called()
        self.assertFalse(results[0]["matched"])


class TestStateRoundTrip(unittest.TestCase):
    def test_save_then_load(self):
        path = Path(tempfile.mkdtemp()) / "state.json"
        alerts.save_state({"foo": 12345.6}, path)
        self.assertEqual(alerts.load_state(path), {"foo": 12345.6})

    def test_load_missing_file(self):
        self.assertEqual(
            alerts.load_state(Path(tempfile.mkdtemp()) / "absent.json"), {},
        )


class TestFireRule(unittest.TestCase):
    def test_force_fire_unknown_raises(self):
        with mock.patch("alerts.load_rules", return_value=[]):
            with self.assertRaises(alerts.AlertError):
                alerts.fire_rule("nope")

    def test_force_fire_dispatches(self):
        rule = alerts.Rule(name="R", condition="x > 0",
                           action={"type": "shell", "cmd": "true"})
        state_path = Path(tempfile.mkdtemp()) / "state.json"
        with mock.patch("alerts.load_rules", return_value=[rule]), \
             mock.patch("alerts.dispatch_action",
                        return_value={"ok": True}) as disp, \
             mock.patch("alerts.STATE_PATH", state_path):
            result = alerts.fire_rule("R")
        disp.assert_called_once()
        self.assertTrue(result["fired"])
        self.assertTrue(result["forced"])


if __name__ == "__main__":
    unittest.main()
