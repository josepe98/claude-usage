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
(or HEARTBEAT_SECONDS have passed). scanner.scan() ingests those snapshots
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
        # Keep the raw block so later versions can use buckets we don't parse
        # yet (seven_day_opus, model_scoped, ...).
        "raw": rl,
    }


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


def statusline_main(stdin=sys.stdin, stdout=sys.stdout):
    # Never let a recorder bug blank the status line silently: show a visible
    # marker and log the traceback.
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


COLUMNS = ["ts", "session_id", "subscription_type",
           "five_hour_pct", "five_hour_resets_at",
           "seven_day_pct", "seven_day_resets_at",
           "spend_pct", "spend_used_usd", "spend_limit_usd", "spend_period"]


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
            rows.append(tuple(rec.get(c) for c in COLUMNS))
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO rate_limit_snapshots (%s) VALUES (%s)"
        % (", ".join(COLUMNS), ", ".join("?" * len(COLUMNS))),
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def _window_summary(rows, key, label, now):
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
    same = [r for r in rows if r[reset_col] == resets_at]
    first = same[0]
    dt = latest["ts"] - first["ts"]
    dp = latest[pct_col] - first[pct_col]
    if dt >= 600 and dp > 0:
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
        init_table(conn)
        rows = conn.execute(
            "SELECT * FROM rate_limit_snapshots ORDER BY ts"
        ).fetchall()
    except Exception:
        return None
    if not rows:
        return None
    rows = [dict(zip(COLUMNS, r)) if not hasattr(r, "keys") else dict(r) for r in rows]
    latest = rows[-1]
    windows = []
    for key, label in WINDOWS:
        have = [r for r in rows if r[key + "_pct"] is not None]
        if have:
            windows.append(_window_summary(have, key, label, now))
    spend = None
    if latest["spend_pct"] is not None:
        spend = {k: latest[k] for k in ("spend_pct", "spend_used_usd", "spend_limit_usd", "spend_period")}
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
    }


if __name__ == "__main__":
    statusline_main()
