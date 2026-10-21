"""
ratelimits.py - Claude.ai plan allowance (5-hour / weekly limit) tracking.

The JSONL transcripts carry no plan-limit data. Claude Code does, however,
pass the server-reported utilization to the status-line command on stdin:

    "rate_limits": {
      "five_hour":   {"used_percentage": 42.0, "resets_at": 1761062400},
      "seven_day":   {"used_percentage": 18.0, "resets_at": 1761494400},
      "spend_limit": {"used_percentage": ..., "resets_at": ...,
                      "used_usd": ..., "limit_usd": ..., "period": "monthly"}
    }

Running this file as the status line (`python3 ratelimits.py`) prints a short
status and appends a snapshot to SNAPSHOT_PATH whenever the readings change
(or HEARTBEAT_SECONDS have passed). The Claude desktop app doesn't run the
status line, so `cli.py record-usage` also accepts the desktop app's plan-usage
JSON (written by a scheduled task) into the same file. scanner.scan() ingests those snapshots
into the `rate_limit_snapshots` table; allowance_summary() turns them into
what the CLI and dashboard show.
"""

import json
import sys
import time
from pathlib import Path

DATA_DIR = Path.home() / ".claude" / "claude-usage"
SNAPSHOT_PATH = DATA_DIR / "rate_limits.jsonl"
STATE_PATH = DATA_DIR / "rate_limits.last.json"
ERROR_LOG_PATH = DATA_DIR / "statusline-errors.log"

# Write a snapshot even when nothing changed, so long gaps are distinguishable
# from "the recorder stopped running".
HEARTBEAT_SECONDS = 600

WINDOWS = [
    ("five_hour", "5-hour session"),
    ("seven_day", "Weekly (all models)"),
]
WINDOW_SECONDS = {"five_hour": 5 * 3600, "seven_day": 7 * 86400}


# ── Recorder (status-line side) ─────────────────────────────────────────────

def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def snapshot_from_payload(payload, now=None):
    """Build a snapshot record from a status-line payload, or None when the
    payload carries no rate_limits (API-key sessions, before the first
    response, or when every window's resets_at has passed)."""
    rl = payload.get("rate_limits")
    if not isinstance(rl, dict) or not rl:
        return None
    five = rl.get("five_hour") or {}
    seven = rl.get("seven_day") or {}
    spend = rl.get("spend_limit") or {}
    return {
        "ts": round(now if now is not None else time.time(), 3),
        "session_id": payload.get("session_id"),
        "subscription_type": payload.get("subscription_type"),
        "five_hour_pct": _num(five.get("used_percentage")),
        "five_hour_resets_at": _num(five.get("resets_at")),
        "seven_day_pct": _num(seven.get("used_percentage")),
        "seven_day_resets_at": _num(seven.get("resets_at")),
        "spend_pct": _num(spend.get("used_percentage")),
        "spend_used_usd": _num(spend.get("used_usd")),
        "spend_limit_usd": _num(spend.get("limit_usd")),
        "spend_period": spend.get("period"),
        "source": "statusline",
        # Keep the raw block so later versions can use buckets we don't parse
        # yet (seven_day_opus, model_scoped, ...).
        "raw": rl,
    }


def _epoch(iso):
    from datetime import datetime
    if not isinstance(iso, str):
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _money(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def snapshot_from_desktop(data, now=None):
    """Build a snapshot from the desktop app's plan-usage JSON: either the
    whole get_usage result ({"plan": {...}, ...}) or just its "plan" object.
    Raises ValueError when the figures are missing or unreadable, so the
    caller fails loudly instead of recording nothing."""
    plan = data.get("plan") if isinstance(data, dict) and isinstance(data.get("plan"), dict) else data
    if not isinstance(plan, dict):
        raise ValueError("no plan object in input")
    if plan.get("status") != "ok":
        raise ValueError("plan status is %r: %s" % (plan.get("status"), plan.get("note", "")))
    snap = {c: None for c in COLUMNS}
    snap.update({"ts": round(now if now is not None else time.time(), 3),
                 "subscription_type": plan.get("plan"), "source": "desktop", "raw": plan})
    models = []
    for w in plan.get("windows") or []:
        label = (w.get("label") or "").lower()
        pct, reset = _num(w.get("percentUsed")), _epoch(w.get("resetsAt"))
        if "5-hour" in label:
            snap["five_hour_pct"], snap["five_hour_resets_at"] = pct, reset
        elif label.startswith("weekly") and "all models" in label:
            snap["seven_day_pct"], snap["seven_day_resets_at"] = pct, reset
        elif label.startswith("weekly"):
            name = w["label"].split("·", 1)[-1].strip()
            models.append({"name": name, "pct": pct, "resets_at": reset})
    if snap["seven_day_pct"] is None and snap["five_hour_pct"] is None:
        raise ValueError("no 5-hour or weekly window in plan windows")
    snap["model_weekly"] = models or None
    extra = plan.get("extraUsage") or {}
    if extra.get("enabled"):
        snap["extra_used_usd"] = _money(extra.get("spent"))
        snap["extra_limit_usd"] = _money(extra.get("monthlyLimit"))
    return snap


def record_desktop_main(stdin=None, stdout=None, stderr=None):
    """`cli.py record-usage`: append one desktop snapshot. Every call is
    written (no dedupe), so the file doubles as the sampler's heartbeat.
    Prints RECORDED on success; exits 1 with the reason otherwise."""
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    try:
        snap = snapshot_from_desktop(json.loads(stdin.read()))
        path = Path(SNAPSHOT_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(snap, separators=(",", ":")) + "\n")
    except Exception as e:
        try:
            ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
                f.write("%s record-usage: %s: %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), type(e).__name__, e))
        except OSError:
            pass
        stderr.write("record-usage FAILED: %s: %s\n" % (type(e).__name__, e))
        return 1
    parts = []
    if snap["five_hour_pct"] is not None:
        parts.append("5h %.0f%%" % snap["five_hour_pct"])
    if snap["seven_day_pct"] is not None:
        parts.append("weekly %.0f%%" % snap["seven_day_pct"])
    for m in snap["model_weekly"] or []:
        parts.append("%s %.0f%%" % (m["name"], m["pct"] or 0))
    stdout.write("RECORDED %s\n" % ", ".join(parts))
    return 0


def _fingerprint(snap):
    return json.dumps(snap["raw"], sort_keys=True)


def record_snapshot(snap, snapshot_path=None, state_path=None):
    """Append snap unless it matches the last written one and the heartbeat
    interval hasn't elapsed. Returns True if a line was written."""
    snapshot_path = snapshot_path or SNAPSHOT_PATH
    state_path = state_path or STATE_PATH
    fp = _fingerprint(snap)
    try:
        last = json.loads(Path(state_path).read_text())
    except (OSError, ValueError):
        last = {}
    if last.get("fingerprint") == fp and snap["ts"] - last.get("ts", 0) < HEARTBEAT_SECONDS:
        return False
    Path(snapshot_path).parent.mkdir(parents=True, exist_ok=True)
    with open(snapshot_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(snap, separators=(",", ":")) + "\n")
    Path(state_path).write_text(json.dumps({"fingerprint": fp, "ts": snap["ts"]}))
    return True


def status_text(payload, snap):
    model = (payload.get("model") or {}).get("display_name") or ""
    parts = [model] if model else []
    if snap:
        if snap["five_hour_pct"] is not None:
            parts.append("5h %.0f%%" % snap["five_hour_pct"])
        if snap["seven_day_pct"] is not None:
            parts.append("wk %.0f%%" % snap["seven_day_pct"])
        if snap["spend_used_usd"] is not None and snap["spend_limit_usd"]:
            parts.append("$%.0f/$%.0f" % (snap["spend_used_usd"], snap["spend_limit_usd"]))
        elif snap["spend_pct"] is not None:
            parts.append("spend %.0f%%" % snap["spend_pct"])
    return " · ".join(parts)


def statusline_main(stdin=None, stdout=None):
    # Never let a recorder bug blank the status line silently: show a visible
    # marker and log the traceback.
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        payload = json.loads(stdin.read() or "{}")
        snap = snapshot_from_payload(payload)
        if snap:
            record_snapshot(snap)
        stdout.write(status_text(payload, snap) + "\n")
    except Exception as e:
        import traceback
        try:
            ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), traceback.format_exc()))
        except OSError:
            pass
        stdout.write("⚠ usage recorder: %s\n" % type(e).__name__)


# ── DB side ─────────────────────────────────────────────────────────────────

def init_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_limit_snapshots (
            ts                  REAL PRIMARY KEY,
            session_id          TEXT,
            subscription_type   TEXT,
            five_hour_pct       REAL,
            five_hour_resets_at REAL,
            seven_day_pct       REAL,
            seven_day_resets_at REAL,
            spend_pct           REAL,
            spend_used_usd      REAL,
            spend_limit_usd     REAL,
            spend_period        TEXT
        )
    """)
    # Columns added for desktop-app snapshots.
    have = {r[1] for r in conn.execute("PRAGMA table_info(rate_limit_snapshots)")}
    for col, typ in (("source", "TEXT"), ("model_weekly", "TEXT"),
                     ("extra_used_usd", "REAL"), ("extra_limit_usd", "REAL")):
        if col not in have:
            conn.execute("ALTER TABLE rate_limit_snapshots ADD COLUMN %s %s" % (col, typ))


COLUMNS = ["ts", "session_id", "subscription_type",
           "five_hour_pct", "five_hour_resets_at",
           "seven_day_pct", "seven_day_resets_at",
           "spend_pct", "spend_used_usd", "spend_limit_usd", "spend_period",
           "source", "model_weekly", "extra_used_usd", "extra_limit_usd"]


def ingest_snapshots(conn, snapshot_path=None):
    """Load snapshots into the DB. The file is small (one line per change),
    so it's re-read in full and deduped on ts. Returns rows inserted."""
    init_table(conn)
    path = Path(snapshot_path or SNAPSHOT_PATH)
    if not path.exists():
        return 0
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or _num(rec.get("ts")) is None:
                continue
            if rec.get("model_weekly") is not None:
                rec["model_weekly"] = json.dumps(rec["model_weekly"])
            rows.append(tuple(rec.get(c) for c in COLUMNS))
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO rate_limit_snapshots (%s) VALUES (%s)"
        % (", ".join(COLUMNS), ", ".join("?" * len(COLUMNS))),
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def _same_window(a, b):
    # Desktop reset times carry fractional seconds, status-line ones don't.
    return a is not None and b is not None and abs(a - b) < 300


def _load_rows(conn):
    init_table(conn)
    rows = [dict(zip(COLUMNS, r)) for r in conn.execute(
        "SELECT %s FROM rate_limit_snapshots ORDER BY ts" % ", ".join(COLUMNS))]
    for r in rows:
        try:
            r["model_weekly"] = json.loads(r["model_weekly"]) if r["model_weekly"] else []
        except ValueError:
            r["model_weekly"] = []
    return rows


def _model_rows(rows):
    """{model name: [{ts, m_pct, m_resets_at}, ...]} from per-model weekly buckets."""
    out = {}
    for r in rows:
        for m in r["model_weekly"]:
            if _num(m.get("pct")) is not None and m.get("name"):
                out.setdefault(m["name"], []).append(
                    {"ts": r["ts"], "m_pct": m["pct"], "m_resets_at": m.get("resets_at")})
    return out


def _window_summary(rows, key, label, now, length):
    """rows: snapshots (oldest first) that have a reading for this window."""
    pct_col, reset_col = key + "_pct", key + "_resets_at"
    latest = rows[-1]
    resets_at = latest[reset_col]
    out = {
        "key": key,
        "label": label,
        "pct": latest[pct_col],
        "resets_at": resets_at,
        "as_of": latest["ts"],
        "reset_since_reading": bool(resets_at and resets_at <= now),
        "rate_per_hour": None,
        "projected_full_at": None,
    }
    if out["reset_since_reading"]:
        return out
    # Average pace across this window: first vs latest reading that share
    # the same resets_at.
    same = [r for r in rows if _same_window(r[reset_col], resets_at)]
    first = same[0]
    dt = latest["ts"] - first["ts"]
    dp = latest[pct_col] - first[pct_col]
    # Percentages arrive as whole numbers, so a short span gives a wildly
    # noisy pace; require at least 5% of the window's length.
    if dt >= 0.05 * length and dp > 0:
        rate = dp / dt
        out["rate_per_hour"] = round(rate * 3600, 2)
        full_at = latest["ts"] + (100 - latest[pct_col]) / rate
        if resets_at and full_at < resets_at:
            out["projected_full_at"] = full_at
    return out


def allowance_summary(conn, now=None, history_days=7):
    """Latest plan-allowance readings plus history, or None if no snapshot
    has ever been recorded."""
    now = now if now is not None else time.time()
    try:
        rows = _load_rows(conn)
    except Exception:
        return None
    if not rows:
        return None
    latest = rows[-1]
    windows = []
    for key, label in WINDOWS:
        have = [r for r in rows if r[key + "_pct"] is not None]
        if have:
            windows.append(_window_summary(have, key, label, now, WINDOW_SECONDS[key]))
    for name, have in sorted(_model_rows(rows).items()):
        w = _window_summary(have, "m", "Weekly (%s)" % name, now, WINDOW_SECONDS["seven_day"])
        w["key"] = "model:" + name
        windows.append(w)
    spend = None
    if latest["spend_pct"] is not None:
        spend = {k: latest[k] for k in ("spend_pct", "spend_used_usd", "spend_limit_usd", "spend_period")}
        spend["label"] = "Spend limit"
    else:
        extra = [r for r in rows if r["extra_limit_usd"]]
        if extra:
            e = extra[-1]
            spend = {"label": "Extra usage", "spend_period": "monthly",
                     "spend_used_usd": e["extra_used_usd"] or 0.0,
                     "spend_limit_usd": e["extra_limit_usd"],
                     "spend_pct": 100.0 * (e["extra_used_usd"] or 0.0) / e["extra_limit_usd"]}
    cutoff = now - history_days * 86400
    history = [[r["ts"], r["five_hour_pct"], r["seven_day_pct"]] for r in rows if r["ts"] >= cutoff]
    return {
        "latest_ts": latest["ts"],
        "age_seconds": round(now - latest["ts"]),
        "subscription_type": latest["subscription_type"],
        "windows": windows,
        "spend": spend,
        "history": history,
        "snapshot_count": len(rows),
        "weekly_history": weekly_history(rows, now),
    }


def weekly_history(rows, now=None):
    """One entry per weekly window seen, newest first. A weekly figure only
    rises until its reset, so the window's highest reading is its total so
    far; `gap_hours` says how long before the reset that reading was taken
    (the true final figure can be higher by whatever was used in the gap)."""
    now = now if now is not None else time.time()
    weeks = []
    for r in rows:
        end = r["seven_day_resets_at"]
        if r["seven_day_pct"] is None or end is None:
            continue
        wk = next((w for w in weeks if _same_window(w["resets_at"], end)), None)
        if wk is None:
            wk = {"resets_at": end, "pct": r["seven_day_pct"], "last_ts": r["ts"],
                  "readings": 0, "models": {}}
            weeks.append(wk)
        wk["pct"] = max(wk["pct"], r["seven_day_pct"])
        wk["last_ts"] = max(wk["last_ts"], r["ts"])
        wk["readings"] += 1
        for m in r["model_weekly"]:
            if _num(m.get("pct")) is not None and _same_window(m.get("resets_at"), end):
                wk["models"][m["name"]] = max(wk["models"].get(m["name"], 0), m["pct"])
    for wk in weeks:
        wk["starts_at"] = wk["resets_at"] - 7 * 86400
        wk["complete"] = wk["resets_at"] <= now
        wk["gap_hours"] = round((wk["resets_at"] - wk["last_ts"]) / 3600, 1) if wk["complete"] else None
    weeks.sort(key=lambda w: w["resets_at"], reverse=True)
    return weeks


if __name__ == "__main__":
    statusline_main()
