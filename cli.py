"""
cli.py - Command-line interface for the Claude Code usage dashboard.

Commands:
  scan      - Scan JSONL files and update the database
  today     - Print today's usage summary
  stats     - Print all-time usage statistics
  dashboard - Scan + open browser + start dashboard server
"""

import os
import sys
import json
import sqlite3
from pathlib import Path
from datetime import datetime, date, timedelta

DB_PATH = Path.home() / ".claude" / "usage.db"

from pricing import PRICING, get_pricing, calc_cost


def fmt(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)

def fmt_cost(c):
    return f"${c:.4f}"

def hr(char="-", width=60):
    print(char * width)

def require_db():
    if not DB_PATH.exists():
        print("Database not found. Run: python3 cli.py scan")
        sys.exit(1)
    return sqlite3.connect(DB_PATH)


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_scan(projects_dir=None):
    from scanner import scan
    scan(projects_dir=Path(projects_dir) if projects_dir else None)


def cmd_today():
    conn = require_db()
    conn.row_factory = sqlite3.Row
    today = date.today().isoformat()

    rows = conn.execute("""
        SELECT
            COALESCE(model, 'unknown') as model,
            SUM(input_tokens)          as inp,
            SUM(output_tokens)         as out,
            SUM(cache_read_tokens)     as cr,
            SUM(cache_creation_tokens)    as cc,
            SUM(cache_1h_tokens)          as c1h,
            COUNT(*)                   as turns
        FROM turns
        WHERE substr(timestamp, 1, 10) = ?
        GROUP BY model
        ORDER BY inp + out DESC
    """, (today,)).fetchall()

    sessions = conn.execute("""
        SELECT COUNT(DISTINCT session_id) as cnt
        FROM turns
        WHERE substr(timestamp, 1, 10) = ?
    """, (today,)).fetchone()

    print()
    hr()
    print(f"  Today's Usage  ({today})")
    hr()

    if not rows:
        print("  No usage recorded today.")
        print()
        return

    total_inp = total_out = total_cr = total_cc = total_turns = 0
    total_cost = 0.0

    for r in rows:
        cost = calc_cost(r["model"], r["inp"] or 0, r["out"] or 0, r["cr"] or 0, r["cc"] or 0)
        total_cost += cost
        total_inp += r["inp"] or 0
        total_out += r["out"] or 0
        total_cr  += r["cr"]  or 0
        total_cc  += r["cc"]  or 0
        total_turns += r["turns"]
        print(f"  {r['model']:<30}  turns={r['turns']:<4}  in={fmt(r['inp'] or 0):<8}  out={fmt(r['out'] or 0):<8}  cost={fmt_cost(cost)}")

    hr()
    print(f"  {'TOTAL':<30}  turns={total_turns:<4}  in={fmt(total_inp):<8}  out={fmt(total_out):<8}  cost={fmt_cost(total_cost)}")
    print()
    print(f"  Sessions today:   {sessions['cnt']}")
    print(f"  Cache read:       {fmt(total_cr)}")
    print(f"  Cache creation:   {fmt(total_cc)}")
    hr()
    print()
    conn.close()


def cmd_week():
    conn = require_db()
    conn.row_factory = sqlite3.Row

    today_d = date.today()
    start_d = today_d - timedelta(days=6)
    start = start_d.isoformat()
    end = today_d.isoformat()

    by_day_model = conn.execute("""
        SELECT
            substr(timestamp, 1, 10)   as day,
            COALESCE(model, 'unknown') as model,
            SUM(input_tokens)          as inp,
            SUM(output_tokens)         as out,
            SUM(cache_read_tokens)     as cr,
            SUM(cache_creation_tokens)    as cc,
            SUM(cache_1h_tokens)          as c1h,
            COUNT(*)                   as turns
        FROM turns
        WHERE substr(timestamp, 1, 10) BETWEEN ? AND ?
        GROUP BY day, model
    """, (start, end)).fetchall()

    by_model = conn.execute("""
        SELECT
            COALESCE(model, 'unknown') as model,
            SUM(input_tokens)          as inp,
            SUM(output_tokens)         as out,
            SUM(cache_read_tokens)     as cr,
            SUM(cache_creation_tokens)    as cc,
            SUM(cache_1h_tokens)          as c1h,
            COUNT(*)                   as turns
        FROM turns
        WHERE substr(timestamp, 1, 10) BETWEEN ? AND ?
        GROUP BY model
        ORDER BY inp + out DESC
    """, (start, end)).fetchall()

    sessions = conn.execute("""
        SELECT COUNT(DISTINCT session_id) as cnt
        FROM turns
        WHERE substr(timestamp, 1, 10) BETWEEN ? AND ?
    """, (start, end)).fetchone()

    print()
    hr()
    print(f"  Weekly Usage  ({start} to {end})")
    hr()

    if not by_model:
        print("  No usage recorded in the last 7 days.")
        print()
        conn.close()
        return

    # Aggregate per-day across models (with per-turn cost attribution)
    per_day = {}
    for r in by_day_model:
        d = r["day"]
        bucket = per_day.setdefault(d, {"turns": 0, "inp": 0, "out": 0, "cost": 0.0})
        bucket["turns"] += r["turns"]
        bucket["inp"]   += r["inp"] or 0
        bucket["out"]   += r["out"] or 0
        bucket["cost"]  += calc_cost(r["model"], r["inp"] or 0, r["out"] or 0, r["cr"] or 0, r["cc"] or 0)

    print("  By Day:")
    for i in range(7):
        d = (start_d + timedelta(days=i)).isoformat()
        b = per_day.get(d, {"turns": 0, "inp": 0, "out": 0, "cost": 0.0})
        print(f"    {d}  turns={b['turns']:<4}  in={fmt(b['inp']):<8}  out={fmt(b['out']):<8}  cost={fmt_cost(b['cost'])}")

    hr()
    print("  By Model:")

    total_inp = total_out = total_cr = total_cc = total_turns = 0
    total_cost = 0.0
    for r in by_model:
        cost = calc_cost(r["model"], r["inp"] or 0, r["out"] or 0, r["cr"] or 0, r["cc"] or 0)
        total_cost  += cost
        total_inp   += r["inp"] or 0
        total_out   += r["out"] or 0
        total_cr    += r["cr"]  or 0
        total_cc    += r["cc"]  or 0
        total_turns += r["turns"]
        print(f"    {r['model']:<30}  turns={r['turns']:<4}  in={fmt(r['inp'] or 0):<8}  out={fmt(r['out'] or 0):<8}  cost={fmt_cost(cost)}")

    hr()
    print(f"    {'TOTAL':<30}  turns={total_turns:<4}  in={fmt(total_inp):<8}  out={fmt(total_out):<8}  cost={fmt_cost(total_cost)}")
    print()
    print(f"  Sessions this week:  {sessions['cnt']}")
    print(f"  Cache read:          {fmt(total_cr)}")
    print(f"  Cache creation:      {fmt(total_cc)}")
    hr()
    print()
    conn.close()


def cmd_tool_usage(range_days=None):
    """Show top tools by turn count across all sessions.

    Surfaces the `tool_name` column that the scanner has been capturing
    since day one but which had no consumer until now.
    """
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}. Run: python cli.py scan")
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    where = ""
    params = []
    if range_days:
        from datetime import datetime as _dt, timedelta as _td
        cutoff = (_dt.utcnow() - _td(days=int(range_days))).strftime("%Y-%m-%d")
        where = "WHERE date(timestamp) >= ?"
        params = [cutoff]
    rows = conn.execute(f"""
        SELECT COALESCE(NULLIF(tool_name, ''), '(no tool / direct turn)') as tool,
               COUNT(*) as turns,
               SUM(input_tokens + output_tokens) as tokens
        FROM turns
        {where}
        GROUP BY tool
        ORDER BY turns DESC
        LIMIT 20
    """, params).fetchall()
    if not rows:
        print("No tool usage recorded.")
        conn.close()
        return
    label = f"last {range_days}d" if range_days else "all time"
    print(f"\nTop tools by turns ({label}):")
    print(f"  {'TOOL':40s}  {'TURNS':>8s}  {'TOKENS':>12s}")
    print(f"  {'-' * 40}  {'-' * 8}  {'-' * 12}")
    for r in rows:
        print(f"  {r['tool'][:40]:40s}  {r['turns']:>8d}  {(r['tokens'] or 0):>12,d}")
    conn.close()


def cmd_forecast():
    """Print burn-rate forecast: 7-day average, 30-day average, month-end projection."""
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}. Run: python cli.py scan")
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Import the helpers from dashboard so the logic stays in one place.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from dashboard import _daily_cost_history, _forecast
    f = _forecast(_daily_cost_history(conn))
    if not f["days_in_data"]:
        print("No spend data yet — run scan after some Claude Code usage.")
        conn.close()
        return
    arrow = "up" if f["trend"] == "up" else "down" if f["trend"] == "down" else "flat"
    print()
    print("Burn-rate forecast")
    print("==================")
    print(f"  7-day avg spend   ${f['avg_7d']:>8.2f}/day  (trend: {arrow})")
    print(f"  30-day avg spend  ${f['avg_30d']:>8.2f}/day")
    print(f"  Month so far      ${f['month_to_date']:>8.2f}")
    print(f"  Days left in month  {f['days_left_in_month']}")
    print(f"  Projected total   ${f['projected_month_end']:>8.2f}  (month-end at current pace)")
    print()
    conn.close()
def cmd_export(fmt="json", kind="daily", out=None):
    """Dump usage data for downstream tooling.

    fmt: 'json' (everything) or 'csv' (one of: daily, sessions, projects)
    out: optional file path; defaults to stdout
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from dashboard import get_dashboard_data, _export_raw_turns, _export_csv
    if fmt == "json":
        data = get_dashboard_data()
        data["turns"] = _export_raw_turns()
        body = json.dumps(data, indent=2)
    elif fmt == "csv":
        body = _export_csv(kind)
    else:
        print(f"Unknown format: {fmt}. Use json or csv.")
        return
    if out:
        Path(out).write_text(body)
        print(f"Wrote {len(body)} bytes to {out}")
    else:
        print(body)
def cmd_budget(amount=None, clear=False):
    """Get/set/clear the monthly budget cap."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from dashboard import _load_budget, _save_budget
    cfg = _load_budget()
    if clear:
        cfg.pop("monthly_usd", None)
        _save_budget(cfg)
        print("Budget cleared.")
        return
    if amount is not None:
        cfg["monthly_usd"] = float(amount)
        _save_budget(cfg)
        print(f"Budget set to ${float(amount):.2f}/month.")
        return
    cap = cfg.get("monthly_usd")
    if cap is None:
        print("No budget set. Run: python cli.py budget --set 50")
    else:
        print(f"Current budget: ${cap:.2f}/month")


def cmd_stats():
    conn = require_db()
    conn.row_factory = sqlite3.Row

    # Session-level info (count, date range)
    session_info = conn.execute("""
        SELECT
            COUNT(*)                  as sessions,
            MIN(first_timestamp)      as first,
            MAX(last_timestamp)       as last
        FROM sessions
    """).fetchone()

    # All-time totals from turns (more accurate — per-turn model attribution)
    totals = conn.execute("""
        SELECT
            SUM(input_tokens)             as inp,
            SUM(output_tokens)            as out,
            SUM(cache_read_tokens)        as cr,
            SUM(cache_creation_tokens)    as cc,
            SUM(cache_1h_tokens)          as c1h,
            COUNT(*)                      as turns
        FROM turns
    """).fetchone()

    # By model from turns (each turn has the actual model used)
    by_model = conn.execute("""
        SELECT
            COALESCE(model, 'unknown') as model,
            SUM(input_tokens)          as inp,
            SUM(output_tokens)         as out,
            SUM(cache_read_tokens)     as cr,
            SUM(cache_creation_tokens)    as cc,
            SUM(cache_1h_tokens)          as c1h,
            COUNT(*)                   as turns,
            COUNT(DISTINCT session_id) as sessions
        FROM turns
        GROUP BY model
        ORDER BY inp + out DESC
    """).fetchall()

    # Top 5 projects from turns (join with sessions for project name)
    top_projects = conn.execute("""
        SELECT
            COALESCE(s.project_name, 'unknown') as project_name,
            SUM(t.input_tokens)  as inp,
            SUM(t.output_tokens) as out,
            COUNT(*)             as turns,
            COUNT(DISTINCT t.session_id) as sessions
        FROM turns t
        LEFT JOIN sessions s ON t.session_id = s.session_id
        GROUP BY s.project_name
        ORDER BY inp + out DESC
        LIMIT 5
    """).fetchall()

    # Daily average (last 30 days)
    daily_avg = conn.execute("""
        SELECT
            AVG(daily_inp) as avg_inp,
            AVG(daily_out) as avg_out
        FROM (
            SELECT
                substr(timestamp, 1, 10) as day,
                SUM(input_tokens) as daily_inp,
                SUM(output_tokens) as daily_out
            FROM turns
            WHERE timestamp >= datetime('now', '-30 days')
            GROUP BY day
        )
    """).fetchone()

    # Build total cost across all models
    total_cost = sum(
        calc_cost(r["model"], r["inp"] or 0, r["out"] or 0, r["cr"] or 0, r["cc"] or 0)
        for r in by_model
    )

    print()
    hr("=")
    print("  Claude Code Usage - All-Time Statistics")
    hr("=")

    first_date = (session_info["first"] or "")[:10]
    last_date = (session_info["last"] or "")[:10]
    print(f"  Period:           {first_date} to {last_date}")
    print(f"  Total sessions:   {session_info['sessions'] or 0:,}")
    print(f"  Total turns:      {fmt(totals['turns'] or 0)}")
    print()
    print(f"  Input tokens:     {fmt(totals['inp'] or 0):<12}  (raw prompt tokens)")
    print(f"  Output tokens:    {fmt(totals['out'] or 0):<12}  (generated tokens)")
    print(f"  Cache read:       {fmt(totals['cr'] or 0):<12}  (90% cheaper than input)")
    print(f"  Cache creation:   {fmt(totals['cc'] or 0):<12}  (25% premium on input)")
    print()
    print(f"  Est. total cost:  ${total_cost:.4f}")
    hr()

    print("  By Model:")
    for r in by_model:
        cost = calc_cost(r["model"], r["inp"] or 0, r["out"] or 0, r["cr"] or 0, r["cc"] or 0)
        print(f"    {r['model']:<30}  sessions={r['sessions']:<4}  turns={fmt(r['turns'] or 0):<6}  "
              f"in={fmt(r['inp'] or 0):<8}  out={fmt(r['out'] or 0):<8}  cost={fmt_cost(cost)}")

    hr()
    print("  Top Projects:")
    for r in top_projects:
        print(f"    {(r['project_name'] or 'unknown'):<40}  sessions={r['sessions']:<3}  "
              f"turns={fmt(r['turns'] or 0):<6}  tokens={fmt((r['inp'] or 0)+(r['out'] or 0))}")

    if daily_avg["avg_inp"]:
        hr()
        print("  Daily Average (last 30 days):")
        print(f"    Input:   {fmt(int(daily_avg['avg_inp'] or 0))}")
        print(f"    Output:  {fmt(int(daily_avg['avg_out'] or 0))}")

    hr("=")
    print()
    conn.close()


def cmd_dashboard(projects_dir=None, host=None, port=None, share=False):
    import webbrowser
    import threading
    import time

    print("Running scan first...")
    cmd_scan(projects_dir=projects_dir)

    print("\nStarting dashboard server...")
    from dashboard import serve

    host = host or os.environ.get("HOST", "localhost")
    port = int(port or os.environ.get("PORT", "8080"))

    def open_browser():
        time.sleep(1.0)
        webbrowser.open(f"http://{host}:{port}")

    t = threading.Thread(target=open_browser, daemon=True)
    t.start()
    share_token = None
    if share:
        import secrets
        share_token = secrets.token_urlsafe(16)
        bind_host = host or "127.0.0.1"
        bind_port = port or "8080"
        print()
        print("Dashboard running in SHARE mode (read-only).")
        print(f"  http://{bind_host}:{bind_port}/?token={share_token}")
        print()
        print("Share this URL with anyone who should see the dashboard.")
        print("Rescan + budget edit are disabled while a token is set.")
        print()
    serve(host=host, port=port, share_token=share_token)


def _period_bounds(period, today=None):
    """Translate a period string ('7d', '30d', 'all') into (start_date, end_date) ISO strings.

    Returns (None, end) for 'all'. end_date is always today.
    """
    today = today or date.today()
    if period == "all":
        return (None, today.isoformat())
    days = {"7d": 7, "30d": 30}.get(period)
    if days is None:
        raise ValueError(f"Unknown period: {period!r}. Use one of: 7d, 30d, all.")
    return ((today - timedelta(days=days - 1)).isoformat(), today.isoformat())


def _in_range(day, start, end):
    if start is None and end is None:
        return True
    if start is not None and day < start:
        return False
    if end is not None and day > end:
        return False
    return True


def build_report(period="30d", db_path=None):
    """Build a Markdown report for the given period.

    Returns a string. Uses get_dashboard_data() for project/session/model data
    and queries the DB directly for the by-tool breakdown (which the dashboard
    payload doesn't expose).

    If the DB doesn't exist or returns an error, returns a valid markdown
    skeleton with zeroed totals — never raises.
    """
    db_path = db_path if db_path is not None else DB_PATH
    start, end = _period_bounds(period)
    period_label = {"7d": "Last 7 days", "30d": "Last 30 days", "all": "All-time"}[period]
    period_range = f"{start} to {end}" if start else f"up to {end}"

    lines = []
    lines.append("# Claude Usage Report")
    lines.append("")
    lines.append(f"- **Period:** {period_label} ({period_range})")
    lines.append(f"- **Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # Defer import so tests that monkey-patch dashboard.DB_PATH still work.
    from dashboard import get_dashboard_data

    data = get_dashboard_data(db_path=db_path)
    if "error" in data:
        lines.append("## Totals")
        lines.append("")
        lines.append("_No usage data available — database not found._")
        lines.append("")
        lines.append("- Sessions: 0")
        lines.append("- Turns: 0")
        lines.append("- Input tokens: 0")
        lines.append("- Output tokens: 0")
        lines.append("- Estimated cost: $0.0000")
        lines.append("")
        lines.append("## Top Projects")
        lines.append("")
        lines.append("_No data._")
        lines.append("")
        lines.append("## Top Sessions")
        lines.append("")
        lines.append("_No data._")
        lines.append("")
        lines.append("## Breakdown by Model")
        lines.append("")
        lines.append("_No data._")
        lines.append("")
        lines.append("## Forecast")
        lines.append("")
        lines.append("_Not enough data to compute a forecast._")
        lines.append("")
        return "\n".join(lines)

    # ── Aggregate daily_by_model in-range ───────────────────────────────────
    daily = [d for d in data.get("daily_by_model", []) if _in_range(d["day"], start, end)]
    total_inp = sum(d["input"] for d in daily)
    total_out = sum(d["output"] for d in daily)
    total_cr  = sum(d["cache_read"] for d in daily)
    total_cc  = sum(d["cache_creation"] for d in daily)
    total_turns = sum(d["turns"] for d in daily)
    total_cost = sum(
        calc_cost(d["model"], d["input"], d["output"], d["cache_read"], d["cache_creation"])
        for d in daily
    )

    # ── Sessions in-range (filter by last_date which is YYYY-MM-DD) ─────────
    sessions = [
        s for s in data.get("sessions_all", [])
        if _in_range(s.get("last_date", ""), start, end)
    ]
    total_sessions = len(sessions)

    lines.append("## Totals")
    lines.append("")
    lines.append(f"- Sessions: {total_sessions}")
    lines.append(f"- Turns: {total_turns:,}")
    lines.append(f"- Input tokens: {total_inp:,}")
    lines.append(f"- Output tokens: {total_out:,}")
    lines.append(f"- Cache read: {total_cr:,}")
    lines.append(f"- Cache creation: {total_cc:,}")
    lines.append(f"- Estimated cost: ${total_cost:.4f}")
    lines.append("")

    # ── Top Projects ────────────────────────────────────────────────────────
    proj_agg = {}
    for s in sessions:
        p = proj_agg.setdefault(s.get("project") or "unknown",
                                 {"sessions": 0, "turns": 0, "input": 0, "output": 0,
                                  "cache_read": 0, "cache_creation": 0, "model_seen": None})
        p["sessions"] += 1
        p["turns"]   += s.get("turns", 0)
        p["input"]   += s.get("input", 0)
        p["output"]  += s.get("output", 0)
        p["cache_read"]     += s.get("cache_read", 0)
        p["cache_creation"] += s.get("cache_creation", 0)
        # Use the session's primary model as a rough cost basis
        p["model_seen"] = p["model_seen"] or s.get("model")

    top_projects = sorted(
        proj_agg.items(),
        key=lambda kv: kv[1]["input"] + kv[1]["output"],
        reverse=True,
    )[:10]

    lines.append("## Top Projects")
    lines.append("")
    if top_projects:
        lines.append("| Project | Sessions | Turns | Input | Output | Est. cost |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for name, p in top_projects:
            cost = calc_cost(p["model_seen"], p["input"], p["output"],
                             p["cache_read"], p["cache_creation"])
            lines.append(
                f"| {name} | {p['sessions']} | {p['turns']:,} | "
                f"{p['input']:,} | {p['output']:,} | ${cost:.4f} |"
            )
    else:
        lines.append("_No projects in this period._")
    lines.append("")

    # ── Top Sessions ────────────────────────────────────────────────────────
    top_sessions = sorted(
        sessions, key=lambda s: s.get("input", 0) + s.get("output", 0), reverse=True,
    )[:10]

    lines.append("## Top Sessions")
    lines.append("")
    if top_sessions:
        lines.append("| Session | Project | Model | Turns | Input | Output | Est. cost |")
        lines.append("|---|---|---|---:|---:|---:|---:|")
        for s in top_sessions:
            cost = calc_cost(s.get("model"), s.get("input", 0), s.get("output", 0),
                             s.get("cache_read", 0), s.get("cache_creation", 0))
            label = s.get("session_name") or s.get("session_id", "")
            lines.append(
                f"| {label} | {s.get('project', 'unknown')} | "
                f"{s.get('model', 'unknown')} | {s.get('turns', 0):,} | "
                f"{s.get('input', 0):,} | {s.get('output', 0):,} | ${cost:.4f} |"
            )
    else:
        lines.append("_No sessions in this period._")
    lines.append("")

    # ── Breakdown by Model ──────────────────────────────────────────────────
    model_agg = {}
    for d in daily:
        m = model_agg.setdefault(d["model"],
                                 {"turns": 0, "input": 0, "output": 0,
                                  "cache_read": 0, "cache_creation": 0})
        m["turns"]  += d["turns"]
        m["input"]  += d["input"]
        m["output"] += d["output"]
        m["cache_read"]     += d["cache_read"]
        m["cache_creation"] += d["cache_creation"]

    lines.append("## Breakdown by Model")
    lines.append("")
    if model_agg:
        lines.append("| Model | Turns | Input | Output | Cache read | Cache creation | Est. cost |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for model, m in sorted(model_agg.items(),
                               key=lambda kv: kv[1]["input"] + kv[1]["output"],
                               reverse=True):
            cost = calc_cost(model, m["input"], m["output"],
                             m["cache_read"], m["cache_creation"])
            lines.append(
                f"| {model} | {m['turns']:,} | {m['input']:,} | {m['output']:,} | "
                f"{m['cache_read']:,} | {m['cache_creation']:,} | ${cost:.4f} |"
            )
    else:
        lines.append("_No model usage in this period._")
    lines.append("")

    # ── Breakdown by Tool (optional — only if any tool_name rows exist) ─────
    tool_rows = []
    try:
        if Path(db_path).exists():
            tconn = sqlite3.connect(db_path)
            tconn.row_factory = sqlite3.Row
            if start is None:
                tool_rows = tconn.execute("""
                    SELECT tool_name, COUNT(*) as uses
                    FROM turns
                    WHERE tool_name IS NOT NULL AND tool_name != ''
                    GROUP BY tool_name
                    ORDER BY uses DESC
                    LIMIT 20
                """).fetchall()
            else:
                tool_rows = tconn.execute("""
                    SELECT tool_name, COUNT(*) as uses
                    FROM turns
                    WHERE tool_name IS NOT NULL AND tool_name != ''
                      AND substr(timestamp, 1, 10) BETWEEN ? AND ?
                    GROUP BY tool_name
                    ORDER BY uses DESC
                    LIMIT 20
                """, (start, end)).fetchall()
            tconn.close()
    except sqlite3.OperationalError:
        tool_rows = []

    if tool_rows:
        lines.append("## Breakdown by Tool")
        lines.append("")
        lines.append("| Tool | Uses |")
        lines.append("|---|---:|")
        for r in tool_rows:
            lines.append(f"| {r['tool_name']} | {r['uses']:,} |")
        lines.append("")

    # ── Forecast ────────────────────────────────────────────────────────────
    # Project per-day average over active days out to a 30-day total.
    unique_days = {d["day"] for d in daily}
    n_days = len(unique_days)
    lines.append("## Forecast")
    lines.append("")
    if n_days >= 2 and (total_inp + total_out) > 0:
        avg_cost_per_day = total_cost / n_days
        avg_turns_per_day = total_turns / n_days
        avg_inp_per_day = total_inp / n_days
        avg_out_per_day = total_out / n_days
        projected_cost_30d = avg_cost_per_day * 30
        lines.append(f"- Active days in period: {n_days}")
        lines.append(f"- Avg turns / day: {avg_turns_per_day:,.1f}")
        lines.append(f"- Avg input tokens / day: {avg_inp_per_day:,.0f}")
        lines.append(f"- Avg output tokens / day: {avg_out_per_day:,.0f}")
        lines.append(f"- Avg cost / day: ${avg_cost_per_day:.4f}")
        lines.append(f"- Projected 30-day cost: ${projected_cost_30d:.4f}")
    else:
        lines.append("_Not enough data to compute a forecast._")
    lines.append("")

    return "\n".join(lines)


def cmd_report(period="30d", out=None):
    """Generate a Markdown usage report.

    Args:
        period: '7d', '30d', or 'all'.
        out: file path to write the report to. If None, prints to stdout.
    """
    try:
        report = build_report(period=period)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(2)

    if out:
        Path(out).write_text(report, encoding="utf-8")
        print(f"Report written to {out}")
    else:
        print(report)


def cmd_dashboard(projects_dir=None, host=None, port=None):
    import webbrowser
    import threading
    import time

    print("Running scan first...")
    cmd_scan(projects_dir=projects_dir)

    print("\nStarting dashboard server...")
    from dashboard import serve

    host = host or os.environ.get("HOST", "localhost")
    port = int(port or os.environ.get("PORT", "8080"))

    def open_browser():
        time.sleep(1.0)
        webbrowser.open(f"http://{host}:{port}")

    t = threading.Thread(target=open_browser, daemon=True)
    t.start()
    share_token = None
    if share:
        import secrets
        share_token = secrets.token_urlsafe(16)
        bind_host = host or "127.0.0.1"
        bind_port = port or "8080"
        print()
        print("Dashboard running in SHARE mode (read-only).")
        print(f"  http://{bind_host}:{bind_port}/?token={share_token}")
        print()
        print("Share this URL with anyone who should see the dashboard.")
        print("Rescan + budget edit are disabled while a token is set.")
        print()
    serve(host=host, port=port, share_token=share_token)


# ── Theme command ──────────────────────────────────────────────────────────────

def cmd_theme():
    import urllib.request
    import urllib.error
    from dashboard import BUNDLED_THEMES, AWESOME_CATALOG, THEMES_DIR

    sub = sys.argv[2] if len(sys.argv) > 2 else None

    if sub == "list":
        installed = {t["id"] for t in BUNDLED_THEMES}
        THEMES_DIR.mkdir(parents=True, exist_ok=True)
        for f in THEMES_DIR.glob("*.json"):
            try:
                t = json.load(open(f))
                if "id" in t:
                    installed.add(t["id"])
            except Exception:
                pass
        all_ids = {c["id"] for c in AWESOME_CATALOG} | installed
        print(f"\n{'ID':<20} {'NAME':<22} {'CATEGORY':<28} STATUS")
        print("-" * 80)
        for entry in sorted(AWESOME_CATALOG + [t for t in BUNDLED_THEMES if t["id"] not in {c["id"] for c in AWESOME_CATALOG}], key=lambda x: x["name"]):
            status = "installed" if entry["id"] in installed else "available"
            print(f"{entry['id']:<20} {entry['name']:<22} {entry['category']:<28} {status}")
        print()

    elif sub == "add":
        theme_id = sys.argv[3] if len(sys.argv) > 3 else None
        if not theme_id:
            print("Usage: python3 cli.py theme add <id>")
            print("Run 'python3 cli.py theme list' to see available theme IDs.")
            sys.exit(1)

        # Check it's in the catalog
        catalog_entry = next((c for c in AWESOME_CATALOG if c["id"] == theme_id), None)
        if not catalog_entry:
            print(f"Unknown theme '{theme_id}'. Run 'python3 cli.py theme list' to see valid IDs.")
            sys.exit(1)

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("'theme add' generates a new theme using the Claude API and requires an API key.")
            print("This is separate from your Claude Code subscription — you need an Anthropic API key.")
            print("Get one at https://console.anthropic.com, then:")
            print("  export ANTHROPIC_API_KEY=sk-ant-...")
            print("  python3 cli.py theme add <id>")
            print()
            print("The built-in themes (apple, linear, vercel, notion, stripe) work without an API key:")
            print("  python3 cli.py theme list")
            sys.exit(1)

        # Fetch the DESIGN.md from awesome-design-md
        design_url = f"https://raw.githubusercontent.com/VoltAgent/awesome-design-md/main/design-md/{theme_id}/README.md"
        print(f"Fetching design system for '{theme_id}'...")
        try:
            with urllib.request.urlopen(design_url, timeout=15) as r:
                design_md = r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            print(f"Could not fetch design file (HTTP {e.code}). The theme may have a different path in the repo.")
            sys.exit(1)
        except Exception as e:
            print(f"Network error: {e}")
            sys.exit(1)

        print("Generating CSS with Claude API...")
        prompt = f"""You are converting a design system description into CSS custom properties for a data dashboard.

Given the DESIGN.md below, produce a CSS :root {{ }} block with EXACTLY these variables:
  --bg            page background color
  --card          card / surface background
  --border        border color (prefer rgba with low opacity)
  --text          primary text color
  --muted         secondary / muted text color (prefer rgba)
  --accent        primary interactive / accent color
  --green         positive number color (for financial figures)
  --shadow        box-shadow value for cards
  --chart-label   color for chart axis labels (must be legible on --bg)
  --chart-grid    color for chart grid lines (subtle, low opacity)

Output ONLY the :root {{ }} block — no explanation, no markdown fences, no other text.

DESIGN.md:
{design_md[:8000]}"""

        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 512,
            "messages": [{"role": "user", "content": prompt}]
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.loads(r.read())
        except Exception as e:
            print(f"API error: {e}")
            sys.exit(1)

        css = result["content"][0]["text"].strip()
        if not css.startswith(":root"):
            # Try to extract :root block if wrapped in markdown
            import re
            m = re.search(r":root\s*\{[^}]+\}", css, re.DOTALL)
            css = m.group(0) if m else css

        # Extract preview colors from the CSS
        import re
        def extract_var(css_text, var):
            m = re.search(rf"--{var}\s*:\s*([^;]+);", css_text)
            return m.group(1).strip() if m else "#888888"

        preview = {
            "bg":     extract_var(css, "bg"),
            "card":   extract_var(css, "card"),
            "text":   extract_var(css, "text"),
            "accent": extract_var(css, "accent"),
            "muted":  extract_var(css, "border"),
        }

        theme = {
            "id":       theme_id,
            "name":     catalog_entry["name"],
            "category": catalog_entry["category"],
            "dark":     False,
            "bundled":  False,
            "preview":  preview,
            "css":      css,
        }

        THEMES_DIR.mkdir(parents=True, exist_ok=True)
        out = THEMES_DIR / f"{theme_id}.json"
        out.write_text(json.dumps(theme, indent=2))
        print(f"✓ Theme '{catalog_entry['name']}' installed to {out}")
        print("  Reload the dashboard to see it in Appearance.")

    elif sub == "remove":
        theme_id = sys.argv[3] if len(sys.argv) > 3 else None
        if not theme_id:
            print("Usage: python3 cli.py theme remove <id>")
            sys.exit(1)
        f = THEMES_DIR / f"{theme_id}.json"
        if f.exists():
            f.unlink()
            print(f"Removed theme '{theme_id}'.")
        else:
            print(f"Theme '{theme_id}' is not installed (or is a bundled theme and cannot be removed).")

    else:
        print("""
Theme management:

  python3 cli.py theme list               List all installed and available themes
  python3 cli.py theme add <id>           Generate and install a theme (requires ANTHROPIC_API_KEY)
  python3 cli.py theme remove <id>        Remove a user-installed theme

Example:
  python3 cli.py theme add spotify
  python3 cli.py theme add tesla
""")


# ── Shell completions ─────────────────────────────────────────────────────────

# Known long-flag set, kept here so completions stay in sync with the parser.
# Adding a new flag? Add it here too.
COMPLETION_FLAGS = [
    "--projects-dir",
    "--host",
    "--port",
]


def _bash_completion_script(commands, flags):
    cmds = " ".join(commands)
    fls = " ".join(flags)
    return f"""# bash completion for claude-usage CLI
# Install:
#   python3 cli.py completions bash > ~/.claude-usage-completion.bash
#   echo 'source ~/.claude-usage-completion.bash' >> ~/.bashrc
_claude_usage_complete() {{
    local cur prev words cword
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"
    local commands="{cmds}"
    local flags="{fls}"

    # Completing the subcommand (first positional after the script).
    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=( $(compgen -W "$commands" -- "$cur") )
        return 0
    fi

    # Flags that take a path value -- defer to filename completion.
    case "$prev" in
        --projects-dir)
            COMPREPLY=( $(compgen -f -- "$cur") )
            return 0
            ;;
    esac

    # Otherwise offer flags when the user starts typing a dash.
    if [[ "$cur" == -* ]]; then
        COMPREPLY=( $(compgen -W "$flags" -- "$cur") )
        return 0
    fi

    return 0
}}
complete -F _claude_usage_complete claude-usage
complete -F _claude_usage_complete cli.py
"""


def _zsh_completion_script(commands, flags):
    cmds = " ".join(commands)
    fls = " ".join(flags)
    return f"""#compdef claude-usage cli.py
# zsh completion for claude-usage CLI
# Install:
#   python3 cli.py completions zsh > ~/.zsh/completions/_claude-usage
#   # ensure ~/.zsh/completions is on $fpath, then: autoload -U compinit && compinit
_claude_usage() {{
    local -a commands flags
    commands=({cmds})
    flags=({fls})

    if (( CURRENT == 2 )); then
        _describe 'command' commands
        return
    fi

    case "${{words[CURRENT-1]}}" in
        --projects-dir)
            _files
            return
            ;;
    esac

    if [[ "${{words[CURRENT]}}" == -* ]]; then
        compadd -- $flags
    fi
}}
compdef _claude_usage claude-usage cli.py
"""


def _fish_completion_script(commands, flags):
    lines = ["# fish completion for claude-usage CLI",
             "# Install:",
             "#   python3 cli.py completions fish > ~/.config/fish/completions/claude-usage.fish",
             "complete -c claude-usage -f"]
    for c in commands:
        lines.append(f"complete -c claude-usage -n '__fish_use_subcommand' -a '{c}'")
    # Flags that take a file argument: enable file completion after them.
    file_flags = {"--projects-dir"}
    for f in flags:
        flag = f.lstrip("-")
        if f in file_flags:
            lines.append(f"complete -c claude-usage -l {flag} -r -F")
        else:
            lines.append(f"complete -c claude-usage -l {flag}")
    return "\n".join(lines) + "\n"


_COMPLETIONS_SENTINEL = object()


def cmd_completions(shell=_COMPLETIONS_SENTINEL):
    """Print a tab-completion script for the requested shell to stdout.

    Pass an explicit ``shell`` (string or ``None``) when calling
    programmatically; the default sentinel falls back to ``sys.argv[2]`` for
    the bare ``COMMANDS[command]()`` dispatch case.
    """
    if shell is _COMPLETIONS_SENTINEL:
        shell = sys.argv[2] if len(sys.argv) > 2 else None

    if not shell:
        print("Usage: python3 cli.py completions <bash|zsh|fish>", file=sys.stderr)
        sys.exit(2)

    shell = shell.lower()
    commands = sorted(COMMANDS.keys())
    flags = list(COMPLETION_FLAGS)

    if shell == "bash":
        sys.stdout.write(_bash_completion_script(commands, flags))
    elif shell == "zsh":
        sys.stdout.write(_zsh_completion_script(commands, flags))
    elif shell == "fish":
        sys.stdout.write(_fish_completion_script(commands, flags))
    else:
        print(
            f"Unknown shell '{shell}'. Supported shells: bash, zsh, fish.",
            file=sys.stderr,
        )
        sys.exit(2)


# ── Entry point ───────────────────────────────────────────────────────────────

USAGE = """
Claude Code Usage Dashboard

Usage:
  python3 cli.py scan [--projects-dir PATH]       Scan JSONL files and update database
  python3 cli.py today                            Show today's usage summary
  python3 cli.py week                             Show last 7 days (per-day + by-model)
  python3 cli.py stats                            Show all-time statistics
  python3 cli.py dashboard [--projects-dir PATH] [--host HOST] [--port PORT]
                                                 Scan + start dashboard
  python3 cli.py report [--period 7d|30d|all] [--out FILE]
                                                 Generate a Markdown usage report
  python3 cli.py theme <list|add|remove>          Manage UI themes
  python3 cli.py completions <bash|zsh|fish>      Print shell tab-completion script
"""

COMMANDS = {
    "scan": cmd_scan,
    "today": cmd_today,
    "week": cmd_week,
    "stats": cmd_stats,
    "tool-usage": cmd_tool_usage,
    "forecast": cmd_forecast,
    "export": cmd_export,
    "budget": cmd_budget,
    "dashboard": cmd_dashboard,
    "report": cmd_report,
    "theme": cmd_theme,
    "completions": cmd_completions,
}

def parse_named_arg(args, flag):
    """Extract a --flag VALUE pair from an argument list."""
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
    return None

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(USAGE)
        sys.exit(0)

    command = sys.argv[1]
    rest = sys.argv[2:]
    projects_dir = parse_named_arg(rest, "--projects-dir")

    if command == "theme":
        cmd_theme()
    elif command == "dashboard":
        cmd_dashboard(
            projects_dir=projects_dir,
            host=parse_named_arg(rest, "--host"),
            port=parse_named_arg(rest, "--port"),
            share="--share" in rest,
        )
    elif command == "scan" and projects_dir:
        cmd_scan(projects_dir=projects_dir)
    elif command == "completions":
        cmd_completions(shell=rest[0] if rest else None)
    elif command == "tool-usage":
        cmd_tool_usage(range_days=parse_named_arg(rest, "--range"))
    elif command == "export":
        cmd_export(
            fmt=parse_named_arg(rest, "--format") or "json",
            kind=parse_named_arg(rest, "--type") or "daily",
            out=parse_named_arg(rest, "--out"),
        )
    elif command == "budget":
        amt = parse_named_arg(rest, "--set")
        clear = "--clear" in rest
        cmd_budget(amount=amt, clear=clear)
    elif command == "report":
        cmd_report(
            period=parse_named_arg(rest, "--period") or "30d",
            out=parse_named_arg(rest, "--out"),
        )
    else:
        COMMANDS[command]()
