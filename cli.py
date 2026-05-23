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
    serve(host=host, port=port)


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


# ── Workbench / Console export import ─────────────────────────────────────────

def _normalize_workbench_record(rec, fallback_ts=None):
    """Coerce one Console/Workbench export entry into the turn shape.

    The Anthropic Console export format isn't officially documented; this
    parser accepts the common shapes that appear in real exports:

      Top-level usage fields:
          model | model_name | model_id
          input_tokens | inputTokens | usage.input_tokens
          output_tokens | outputTokens | usage.output_tokens
          cache_read_input_tokens | cacheReadInputTokens
          cache_creation_input_tokens | cacheCreationInputTokens
          timestamp | created_at | createdAt | created  (ISO 8601)
          id | message_id | run_id

      Variants:
          - usage may be nested under `usage` or flat
          - timestamps may be ISO strings or unix epoch seconds/ms
          - extra fields are ignored

    Returns a dict with keys matching scanner.insert_turns input, or
    None if the record can't be parsed (missing model or no token counts).
    """
    if not isinstance(rec, dict):
        return None

    # Some exports nest usage under "usage" sub-object
    usage = rec.get("usage") if isinstance(rec.get("usage"), dict) else {}

    def pick(*keys):
        for k in keys:
            if k in rec and rec[k] is not None:
                return rec[k]
            if k in usage and usage[k] is not None:
                return usage[k]
        return None

    model = pick("model", "model_name", "model_id")
    if not model or not isinstance(model, str):
        return None

    def as_int(v):
        if v is None:
            return 0
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    input_tokens = as_int(pick("input_tokens", "inputTokens", "prompt_tokens"))
    output_tokens = as_int(pick("output_tokens", "outputTokens", "completion_tokens"))
    cache_read = as_int(pick("cache_read_input_tokens", "cacheReadInputTokens"))
    cache_creation = as_int(pick("cache_creation_input_tokens", "cacheCreationInputTokens"))

    if input_tokens + output_tokens + cache_read + cache_creation == 0:
        # No usage to record
        return None

    # Timestamp: accept ISO 8601 or unix epoch (s or ms)
    ts_raw = pick("timestamp", "created_at", "createdAt", "created")
    ts = _coerce_timestamp(ts_raw) or fallback_ts or datetime.utcnow().isoformat()

    return {
        "model": model.split("[", 1)[0],  # strip tier hints like [1m]
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
        "cache_1h_tokens": 0,
        "timestamp": ts,
        "tool_name": None,
    }


def _coerce_timestamp(value):
    """Accept ISO strings, unix seconds, unix ms, or datetime. Return ISO string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float)):
        # Heuristic: ms vs seconds
        v = float(value)
        if v > 10_000_000_000:  # > year 2286 in seconds → must be ms
            v = v / 1000.0
        try:
            return datetime.utcfromtimestamp(v).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        # Trust ISO-looking strings as-is; otherwise try to parse
        s = value.strip()
        if not s:
            return None
        # Quick sanity: must start with a digit (year)
        if not s[0].isdigit():
            return None
        return s
    return None


def cmd_import_workbench(path):
    """Import an Anthropic Console / Workbench JSON export into the DB.

    Console export format (best-effort — Anthropic hasn't published a stable
    schema). The importer accepts a JSON file containing one of:

      - A top-level array of run/message objects
      - An object with a "runs", "messages", or "data" array
      - A single run/message object

    Each record is normalized into a `turns` row with:
      project_name = "Workbench"
      session_id   = "wb-<sha1(file)[:12]>"
      git_branch   = ""
      message_id   = "wb-<filename>-<line_index>"  (idempotency key)

    Re-importing the same file is a no-op because turns are deduped by
    message_id via the unique partial index on `turns.message_id`.
    """
    import json
    import hashlib
    from scanner import get_db, init_db, upsert_sessions, insert_turns

    file_path = Path(path)
    if not file_path.exists():
        print(f"Error: file not found: {path}")
        sys.exit(1)

    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: {path} is not valid JSON ({e})")
        sys.exit(1)

    # Coerce into a flat list of records, regardless of wrapper shape
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        for key in ("runs", "messages", "data", "entries", "items"):
            if isinstance(data.get(key), list):
                records = data[key]
                break
        else:
            # Treat the whole object as a single record
            records = [data]
    else:
        print(f"Error: unsupported JSON structure in {path} (expected array or object)")
        sys.exit(1)

    # Stable session id derived from file contents — re-importing the same
    # exact file maps to the same session row.
    file_hash = hashlib.sha1(file_path.read_bytes()).hexdigest()[:12]
    session_id = f"wb-{file_hash}"
    file_stem = file_path.stem

    turns = []
    skipped = 0
    for idx, rec in enumerate(records):
        normalized = _normalize_workbench_record(rec)
        if normalized is None:
            skipped += 1
            continue
        normalized["session_id"] = session_id
        normalized["cwd"] = "Workbench"
        normalized["message_id"] = f"wb-{file_stem}-{idx}"
        turns.append(normalized)

    if not turns:
        print(f"No importable entries found in {path} ({skipped} skipped).")
        return

    # Build a single session row spanning all imported turns
    timestamps = [t["timestamp"] for t in turns if t.get("timestamp")]
    first_ts = min(timestamps) if timestamps else ""
    last_ts = max(timestamps) if timestamps else ""

    # Pick the most common model as the session-level model label
    from collections import Counter
    model_counts = Counter(t["model"] for t in turns if t["model"])
    session_model = model_counts.most_common(1)[0][0] if model_counts else None

    session_row = {
        "session_id": session_id,
        "project_name": "Workbench",
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
        "git_branch": "",
        "model": session_model,
        "total_input_tokens": sum(t["input_tokens"] for t in turns),
        "total_output_tokens": sum(t["output_tokens"] for t in turns),
        "total_cache_read": sum(t["cache_read_tokens"] for t in turns),
        "total_cache_creation": sum(t["cache_creation_tokens"] for t in turns),
        "total_cache_1h": 0,
        "turn_count": len(turns),
        "session_name": f"Workbench {file_stem}",
    }

    conn = get_db(DB_PATH)
    init_db(conn)

    # Detect how many of these message_ids are already in the DB so we can
    # report "imported" vs "already present" accurately.
    msg_ids = [t["message_id"] for t in turns]
    placeholders = ",".join("?" * len(msg_ids))
    existing_rows = conn.execute(
        f"SELECT message_id FROM turns WHERE message_id IN ({placeholders})",
        msg_ids,
    ).fetchall()
    already_present = {row[0] for row in existing_rows}
    new_turn_count = sum(1 for mid in msg_ids if mid not in already_present)

    upsert_sessions(conn, [session_row])
    insert_turns(conn, turns)

    # Recompute session totals from actual turns so totals stay accurate
    # whether this is the first import or a re-import.
    conn.execute("""
        UPDATE sessions SET
            total_input_tokens   = COALESCE((SELECT SUM(input_tokens)         FROM turns WHERE turns.session_id = sessions.session_id), 0),
            total_output_tokens  = COALESCE((SELECT SUM(output_tokens)        FROM turns WHERE turns.session_id = sessions.session_id), 0),
            total_cache_read     = COALESCE((SELECT SUM(cache_read_tokens)    FROM turns WHERE turns.session_id = sessions.session_id), 0),
            total_cache_creation = COALESCE((SELECT SUM(cache_creation_tokens)FROM turns WHERE turns.session_id = sessions.session_id), 0),
            total_cache_1h       = COALESCE((SELECT SUM(cache_1h_tokens)      FROM turns WHERE turns.session_id = sessions.session_id), 0),
            turn_count           = COALESCE((SELECT COUNT(*)                  FROM turns WHERE turns.session_id = sessions.session_id), 0)
        WHERE session_id = ?
    """, (session_id,))
    conn.commit()
    conn.close()

    total_tokens = sum(
        t["input_tokens"] + t["output_tokens"]
        + t["cache_read_tokens"] + t["cache_creation_tokens"]
        for t in turns
    )

    print()
    print(f"  Workbench import: {file_path.name}")
    hr()
    print(f"  Imported entries:    {new_turn_count}")
    print(f"  Already present:     {len(already_present)}")
    print(f"  Skipped (malformed): {skipped}")
    print(f"  Total tokens:        {fmt(total_tokens)}")
    print(f"  Session id:          {session_id}")
    hr()
    print()


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
  python3 cli.py theme <list|add|remove>          Manage UI themes
  python3 cli.py import-workbench <PATH>          Import an Anthropic Console/Workbench JSON export
"""

COMMANDS = {
    "scan": cmd_scan,
    "today": cmd_today,
    "week": cmd_week,
    "stats": cmd_stats,
    "dashboard": cmd_dashboard,
    "theme": cmd_theme,
    "import-workbench": cmd_import_workbench,
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
        )
    elif command == "scan" and projects_dir:
        cmd_scan(projects_dir=projects_dir)
    elif command == "import-workbench":
        if not rest:
            print("Usage: python3 cli.py import-workbench <path/to/export.json>")
            sys.exit(1)
        cmd_import_workbench(rest[0])
    else:
        COMMANDS[command]()
