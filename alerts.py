"""
alerts.py - Custom alert webhooks for the claude-usage dashboard.

Users define rules in ~/.claude/alerts.json. Each rule pairs a condition
(written in a tiny safe DSL) with an action (shell command or HTTP webhook).
After every scan we evaluate every rule against the current metrics; rules
that fire respect a per-rule cooldown stored in ~/.claude/alerts-state.json.

The condition language is parsed with ast.parse() and walked by a strict
NodeVisitor. We never call eval() or exec(); only the whitelisted node types
below are accepted. Anything else - function calls, attribute access, imports,
arithmetic - is rejected with ConditionError.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

CONFIG_PATH = Path.home() / ".claude" / "alerts.json"
STATE_PATH = Path.home() / ".claude" / "alerts-state.json"
DB_PATH = Path.home() / ".claude" / "usage.db"

DEFAULT_COOLDOWN_MINUTES = 60
SHELL_TIMEOUT_SECONDS = 10
WEBHOOK_TIMEOUT_SECONDS = 10


class AlertError(Exception):
    """Base class for alert configuration / evaluation problems."""


class ConditionError(AlertError):
    """Raised when a condition string is not valid DSL."""


# Allowed comparison operators, mapped to lambdas.
_CMP_OPS: dict[type, Callable[[Any, Any], bool]] = {
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}

# Allowed boolean ops.
_BOOL_OPS: dict[type, Callable[[list], bool]] = {
    ast.And: all,
    ast.Or: any,
}


def _eval_node(node: ast.AST, variables: dict[str, Any]) -> Any:
    """Recursively evaluate an AST node against the variable bag."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, str, bool)) or node.value is None:
            return node.value
        raise ConditionError(f"Unsupported constant type: {type(node.value).__name__}")

    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise ConditionError(f"Unknown variable: {node.id!r}")
        return variables[node.id]

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval_node(node.operand, variables)

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval_node(node.operand, variables)

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, variables)
        for op, comparator in zip(node.ops, node.comparators):
            op_type = type(op)
            if op_type not in _CMP_OPS:
                raise ConditionError(f"Unsupported comparison op: {op_type.__name__}")
            right = _eval_node(comparator, variables)
            if not _CMP_OPS[op_type](left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.BoolOp):
        op_type = type(node.op)
        if op_type not in _BOOL_OPS:
            raise ConditionError(f"Unsupported boolean op: {op_type.__name__}")
        values = [_eval_node(v, variables) for v in node.values]
        return _BOOL_OPS[op_type](values)

    raise ConditionError(f"Disallowed expression node: {type(node).__name__}")


def evaluate_condition(expr: str, variables: dict[str, Any]) -> bool:
    """Parse and evaluate a DSL condition. Returns a bool."""
    if not isinstance(expr, str) or not expr.strip():
        raise ConditionError("Condition must be a non-empty string")
    src = expr
    for keyword in (" AND ", " OR ", " NOT "):
        src = src.replace(keyword, keyword.lower())
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as exc:
        raise ConditionError(f"Invalid syntax: {exc.msg}") from exc
    return bool(_eval_node(tree.body, variables))


@dataclass
class Rule:
    name: str
    condition: str
    action: dict
    cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        if not isinstance(d, dict):
            raise AlertError(f"Rule must be an object, got {type(d).__name__}")
        for key in ("name", "condition", "action"):
            if key not in d:
                raise AlertError(f"Rule missing required key: {key!r}")
        if not isinstance(d["action"], dict) or "type" not in d["action"]:
            raise AlertError("Rule.action must be an object with a 'type' field")
        return cls(
            name=str(d["name"]),
            condition=str(d["condition"]),
            action=dict(d["action"]),
            cooldown_minutes=int(d.get("cooldown_minutes", DEFAULT_COOLDOWN_MINUTES)),
            extras={k: v for k, v in d.items()
                    if k not in {"name", "condition", "action", "cooldown_minutes"}},
        )


def load_rules(path=None) -> list:
    """Load and validate the rules file. Missing file -> empty list."""
    p = Path(path) if path else CONFIG_PATH
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise AlertError(f"alerts.json is not valid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise AlertError("alerts.json must contain a JSON array of rules")
    return [Rule.from_dict(item) for item in raw]


def load_state(path=None) -> dict:
    """Map of rule-name -> unix timestamp it last fired."""
    p = Path(path) if path else STATE_PATH
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}


def save_state(state: dict, path=None) -> None:
    p = Path(path) if path else STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, sort_keys=True))


def collect_metrics(db_path=None) -> dict:
    """Pull DSL-visible variables out of the usage database."""
    p = Path(db_path) if db_path else DB_PATH
    metrics: dict = {
        "today_cost": 0.0,
        "month_to_date": 0.0,
        "turn_cost": 0.0,
        "model": "",
        "project": "",
        "per_project_today": {},
        "per_project_month": {},
        "per_model_today": {},
    }
    if not p.exists():
        return metrics

    try:
        from pricing import calc_cost
    except ImportError:
        return metrics

    today = date.today().isoformat()
    month_prefix = today[:7]

    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    try:
        today_rows = conn.execute("""
            SELECT COALESCE(model,'') model,
                   SUM(input_tokens) inp, SUM(output_tokens) out,
                   SUM(cache_read_tokens) cr, SUM(cache_creation_tokens) cc
            FROM turns WHERE substr(timestamp,1,10) = ?
            GROUP BY model
        """, (today,)).fetchall()
        for r in today_rows:
            c = calc_cost(r["model"], r["inp"] or 0, r["out"] or 0,
                          r["cr"] or 0, r["cc"] or 0)
            metrics["today_cost"] += c
            metrics["per_model_today"][r["model"]] = c

        month_rows = conn.execute("""
            SELECT COALESCE(t.model,'') model, COALESCE(s.project_name,'') project,
                   SUM(t.input_tokens) inp, SUM(t.output_tokens) out,
                   SUM(t.cache_read_tokens) cr, SUM(t.cache_creation_tokens) cc
            FROM turns t LEFT JOIN sessions s USING(session_id)
            WHERE substr(t.timestamp,1,7) = ?
            GROUP BY t.model, s.project_name
        """, (month_prefix,)).fetchall()
        for r in month_rows:
            c = calc_cost(r["model"], r["inp"] or 0, r["out"] or 0,
                          r["cr"] or 0, r["cc"] or 0)
            metrics["month_to_date"] += c
            if r["project"]:
                metrics["per_project_month"][r["project"]] = (
                    metrics["per_project_month"].get(r["project"], 0.0) + c
                )

        proj_today = conn.execute("""
            SELECT COALESCE(s.project_name,'') project, COALESCE(t.model,'') model,
                   SUM(t.input_tokens) inp, SUM(t.output_tokens) out,
                   SUM(t.cache_read_tokens) cr, SUM(t.cache_creation_tokens) cc
            FROM turns t LEFT JOIN sessions s USING(session_id)
            WHERE substr(t.timestamp,1,10) = ?
            GROUP BY s.project_name, t.model
        """, (today,)).fetchall()
        for r in proj_today:
            if not r["project"]:
                continue
            c = calc_cost(r["model"], r["inp"] or 0, r["out"] or 0,
                          r["cr"] or 0, r["cc"] or 0)
            metrics["per_project_today"][r["project"]] = (
                metrics["per_project_today"].get(r["project"], 0.0) + c
            )

        last = conn.execute("""
            SELECT COALESCE(t.model,'') model, COALESCE(s.project_name,'') project,
                   t.input_tokens inp, t.output_tokens out,
                   t.cache_read_tokens cr, t.cache_creation_tokens cc
            FROM turns t LEFT JOIN sessions s USING(session_id)
            ORDER BY t.timestamp DESC LIMIT 1
        """).fetchone()
        if last:
            metrics["model"] = last["model"]
            metrics["project"] = last["project"]
            metrics["turn_cost"] = calc_cost(
                last["model"], last["inp"] or 0, last["out"] or 0,
                last["cr"] or 0, last["cc"] or 0,
            )
    finally:
        conn.close()

    return metrics


def _run_shell(action: dict) -> dict:
    cmd = action.get("cmd")
    if not cmd or not isinstance(cmd, str):
        raise AlertError("shell action requires a non-empty 'cmd' string")
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=SHELL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "shell command timed out"}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-400:],
        "stderr": proc.stderr[-400:],
    }


def _run_webhook(action: dict) -> dict:
    url = action.get("url")
    if not url or not isinstance(url, str):
        raise AlertError("webhook action requires a 'url' string")
    payload = action.get("payload", {})
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_SECONDS) as resp:
            status = getattr(resp, "status", 200)
            return {"ok": 200 <= status < 300, "status": status}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": str(exc)}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "error": str(exc)}


_DISPATCHERS: dict = {
    "shell": _run_shell,
    "webhook": _run_webhook,
}


def dispatch_action(action: dict) -> dict:
    kind = action.get("type")
    if kind not in _DISPATCHERS:
        raise AlertError(f"Unknown action type: {kind!r}")
    return _DISPATCHERS[kind](action)


def _cooldown_ok(rule: Rule, state: dict, now: float) -> bool:
    last = state.get(rule.name)
    if last is None:
        return True
    return (now - last) >= rule.cooldown_minutes * 60


def _flatten(metrics: dict) -> dict:
    """Strip nested dicts the DSL can't see; keep top-level scalars."""
    return {k: v for k, v in metrics.items() if not isinstance(v, dict)}


def evaluate_all(*, metrics=None, rules=None, state=None,
                 state_path=None, dry_run=False, now=None) -> list:
    """Evaluate every rule and fire those whose condition matched and
    whose cooldown has elapsed. Returns a per-rule result list."""
    if rules is None:
        try:
            rules = load_rules()
        except AlertError as exc:
            return [{"name": "<config>", "fired": False, "error": str(exc)}]
    if metrics is None:
        metrics = collect_metrics()
    if state is None:
        state = load_state(state_path)
    now = now if now is not None else time.time()

    results = []
    mutated = False
    flat = _flatten(metrics)
    for rule in rules:
        entry = {"name": rule.name, "fired": False}
        try:
            matched = evaluate_condition(rule.condition, flat)
        except ConditionError as exc:
            entry["error"] = f"condition: {exc}"
            results.append(entry)
            continue
        entry["matched"] = matched
        if not matched:
            results.append(entry)
            continue
        if not _cooldown_ok(rule, state, now):
            entry["skipped"] = "cooldown"
            results.append(entry)
            continue
        if dry_run:
            entry["skipped"] = "dry_run"
            results.append(entry)
            continue
        try:
            entry["action_result"] = dispatch_action(rule.action)
            entry["fired"] = True
            state[rule.name] = now
            mutated = True
        except AlertError as exc:
            entry["error"] = f"action: {exc}"
        results.append(entry)

    if mutated and not dry_run:
        try:
            save_state(state, state_path)
        except OSError:
            pass
    return results


def fire_rule(name: str, *, state_path=None) -> dict:
    """Force-fire a single rule by name, bypassing condition + cooldown."""
    rules = load_rules()
    for rule in rules:
        if rule.name == name:
            result = {"name": rule.name, "fired": False, "forced": True}
            try:
                result["action_result"] = dispatch_action(rule.action)
                result["fired"] = True
                state = load_state(state_path)
                state[rule.name] = time.time()
                save_state(state, state_path)
            except AlertError as exc:
                result["error"] = str(exc)
            return result
    raise AlertError(f"No rule named {name!r}")
