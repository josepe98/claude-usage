"""
dashboard.py - Local web dashboard served on localhost:8080.
"""

import json
import os
import re
import sqlite3
import time
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from pathlib import Path

from pricing import PRICING, get_pricing
from datetime import datetime, date, timedelta

DB_PATH               = Path.home() / ".claude" / "usage.db"
THEMES_DIR            = Path.home() / ".claude" / "claude-usage" / "themes"
PROJECT_ALIASES_PATH  = Path.home() / ".claude" / "project-names.json"


# ── Project display name overrides ────────────────────────────────────────────
def _load_project_aliases(path=None):
    """Load raw_name -> display_name mapping from ~/.claude/project-names.json.

    Returns an empty dict if the file is missing, malformed, or not a plain
    string-to-string object. Never raises — alias support is a soft feature
    and a corrupt file must not break the dashboard.
    """
    p = Path(path) if path is not None else PROJECT_ALIASES_PATH
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, str):
            k_s, v_s = k.strip(), v.strip()
            if k_s and v_s:
                out[k_s] = v_s
    return out


def _save_project_aliases(aliases, path=None):
    """Atomically persist the alias mapping. Creates the parent dir if needed.

    Entries with empty/whitespace-only display names are dropped — the spec
    says empty display_name clears the alias.
    """
    p = Path(path) if path is not None else PROJECT_ALIASES_PATH
    cleaned = {}
    for k, v in (aliases or {}).items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        k_s, v_s = k.strip(), v.strip()
        if k_s and v_s:
            cleaned[k_s] = v_s
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cleaned, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, p)
    return cleaned
from pricing import PRICING
from datetime import datetime, timedelta, date
from pricing import PRICING, calc_cost, get_pricing
from datetime import datetime
from datetime import datetime, timedelta, date as _date_cls
from pricing import PRICING, calc_cost
from datetime import datetime, timedelta, timezone

DB_PATH    = Path.home() / ".claude" / "usage.db"
THEMES_DIR = Path.home() / ".claude" / "claude-usage" / "themes"
PII_PATTERNS_PATH = Path.home() / ".claude" / "pii-patterns.json"


# ── PII / sensitive-content scanner ────────────────────────────────────────────
# SOFT warning only — flags sessions whose project name or branch contains
# common secret/credential markers. Patterns are configurable via
# ~/.claude/pii-patterns.json (a JSON list of regex strings); fallback below.

def _default_pii_patterns():
    """Return the built-in list of regex patterns for sensitive markers.
    Each pattern is matched case-insensitively against project + branch text.
    """
    return [
        r"secret",
        r"credential",
        r"password",
        r"api[_-]?key",
        r"token",
        r"\.env",
        r"\.aws/credentials",
        r"\bSSN",
        r"private[_-]?key",
        r"\.ssh/",
    ]


def _load_pii_patterns(path=PII_PATTERNS_PATH):
    """Load user-overridden PII patterns from ~/.claude/pii-patterns.json,
    or fall back to the defaults if the file is missing or malformed.
    The override file must contain a JSON list of regex strings.
    """
    try:
        if path and Path(path).exists():
            data = json.loads(Path(path).read_text())
            if isinstance(data, list) and all(isinstance(p, str) for p in data):
                return data
    except Exception:
        pass
    return _default_pii_patterns()


def _pii_check(text, patterns):
    """Return a list of pattern strings that matched the given text
    (case-insensitive). Empty list = no sensitive markers found.
    Never raises on a bad regex — invalid patterns are silently skipped.
    """
    if not text:
        return []
    matches = []
    for pat in patterns:
        try:
            if re.search(pat, text, re.IGNORECASE):
                matches.append(pat)
        except re.error:
            continue
    return matches

DB_PATH        = Path.home() / ".claude" / "usage.db"
THEMES_DIR     = Path.home() / ".claude" / "claude-usage" / "themes"
GIT_TRACE_PATH = Path.home() / ".claude" / "git-trace.jsonl"


def _load_git_trace(path=None, limit=50):
    """Load the last `limit` commit records from the git-trace JSONL.

    Each line is `{repo, sha, message, author, timestamp, session_id}`,
    written by the bundled `post-commit` hook. Bad lines (corrupt JSON,
    missing fields) are skipped silently — the trace is informational
    and must never break /api/data.

    Returns commits in reverse-chronological order (newest first).
    """
    path = Path(path) if path else GIT_TRACE_PATH
    if not path.exists():
        return []

    records = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                # Minimum-viable record: must at least have sha + timestamp.
                if not rec.get("sha") or not rec.get("timestamp"):
                    continue
                records.append({
                    "repo":       rec.get("repo", ""),
                    "sha":        rec["sha"],
                    "short_sha":  rec["sha"][:8],
                    "message":    rec.get("message", ""),
                    "author":     rec.get("author", ""),
                    "timestamp":  rec["timestamp"],
                    "session_id": rec.get("session_id") or "",
                })
    except OSError:
        return []

    # Sort newest first by timestamp (lexicographic on ISO8601 works), then
    # slice. We sort defensively because the file is append-only but a
    # parallel commit could in theory land out-of-order across processes.
    records.sort(key=lambda r: r["timestamp"], reverse=True)
    return records[:limit]

# Inbound webhook log + config. Module-level so tests can monkey-patch
# these paths to point at a tempdir without writing to the user's home.
INBOUND_LOG    = Path.home() / ".claude" / "inbound.jsonl"
INBOUND_CONFIG = Path.home() / ".claude" / "inbound-config.json"
INBOUND_MAX_BYTES = 10 * 1024 * 1024  # rotate at 10 MB


def _load_inbound_secret():
    """Read the optional shared-secret from ~/.claude/inbound-config.json.
    Returns None if the file is missing, malformed, or has no 'secret' key —
    in which case the endpoint accepts unauthenticated POSTs."""
    try:
        with open(INBOUND_CONFIG, "r") as f:
            cfg = json.load(f)
        secret = cfg.get("secret")
        return secret if isinstance(secret, str) and secret else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _rotate_inbound_log():
    """If the active log is >= INBOUND_MAX_BYTES, move it to .jsonl.1 and
    drop the previous .jsonl.1 (single-generation rotation)."""
    try:
        if not INBOUND_LOG.exists():
            return
        if INBOUND_LOG.stat().st_size < INBOUND_MAX_BYTES:
            return
        rotated = INBOUND_LOG.with_suffix(INBOUND_LOG.suffix + ".1")
        if rotated.exists():
            rotated.unlink()
        INBOUND_LOG.rename(rotated)
    except OSError:
        # Rotation is best-effort; never block an append on a stat failure.
        pass


def append_inbound_event(event_type, payload, source_ip):
    """Wrap an inbound payload with envelope metadata and append it as a
    single JSONL line. Rotates the log first if it has hit the cap."""
    _rotate_inbound_log()
    INBOUND_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "type":        event_type,
        "received_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_ip":   source_ip,
        "payload":     payload,
    }
    with open(INBOUND_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def read_inbound_events(limit=100):
    """Return up to `limit` most recent events (newest first). Skips lines
    that fail to parse so a single bad write doesn't poison the endpoint."""
    if not INBOUND_LOG.exists():
        return []
    try:
        with open(INBOUND_LOG, "r") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out


DASHBOARD_PREFS_PATH = Path.home() / ".claude" / "dashboard_prefs.json"

# Canonical set of top-level dashboard block IDs.  Used by validation so a
# malicious or stale client can't smuggle arbitrary strings into the
# server-side prefs file.  Keep this in sync with the data-block-id values
# in HTML_TEMPLATE.  Mega-merge adds many sections beyond the PR's original
# eight; they're all enumerated here so the validator accepts the full set.
DASHBOARD_BLOCK_IDS = frozenset({
    "stats-row",
    "plan-limits-card",
    "pareto-card",
    "budget-bar",
    "anomaly-banner",
    "plan-card",
    "downgrade-card",
    "cache-hit-card",
    "git-trace-card",
    "inbound-card",
    "time-on-task-card",
    "charts-grid-main",
    "charts-grid-tools",
    "cost-by-model-table",
    "recent-sessions-table",
    "session-detail-card",
    "cost-by-project-table",
    "cost-by-project-branch-table",
    "cost-by-branch-card",
})


def _load_dashboard_prefs(path=None):
    """Read dashboard prefs JSON.  Returns {} when the file is missing or
    unreadable so callers can rely on a dict shape."""
    p = Path(path) if path is not None else DASHBOARD_PREFS_PATH
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text() or "{}")
    except Exception:
        return {}


def _save_dashboard_prefs(prefs, path=None):
    """Persist prefs as pretty-printed JSON.  Creates parent dirs as needed."""
    p = Path(path) if path is not None else DASHBOARD_PREFS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prefs, indent=2, sort_keys=True))


def _validate_dashboard_prefs(body):
    """Validate prefs payload.  Returns (ok, error_or_normalized)."""
    if not isinstance(body, dict):
        return False, "prefs must be a JSON object"
    order = body.get("order", [])
    hidden = body.get("hidden", [])
    if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
        return False, "order must be a list of strings"
    if not isinstance(hidden, list) or not all(isinstance(x, str) for x in hidden):
        return False, "hidden must be a list of strings"
    for bid in order:
        if bid not in DASHBOARD_BLOCK_IDS:
            return False, "unknown block id in order: " + bid
    for bid in hidden:
        if bid not in DASHBOARD_BLOCK_IDS:
            return False, "unknown block id in hidden: " + bid
    return True, {"order": order, "hidden": hidden}

# ── Bundled themes ─────────────────────────────────────────────────────────────
BUNDLED_THEMES = [
    {
        "id": "apple", "name": "Apple", "category": "Enterprise & Consumer",
        "dark": False, "bundled": True,
        "preview": {"bg": "#f5f5f7", "card": "#ffffff", "text": "#1d1d1f", "accent": "#0071e3", "muted": "rgba(0,0,0,0.10)"},
        "css": """:root {
  --bg: #f5f5f7; --card: #ffffff; --border: rgba(0,0,0,0.08);
  --text: #1d1d1f; --muted: rgba(0,0,0,0.48); --accent: #0071e3;
  --green: #1c7a3a; --shadow: 0px 2px 12px rgba(0,0,0,0.08);
  --card-radius: 14px; --card-border: none;
  --chart-label: rgba(0,0,0,0.48); --chart-grid: rgba(0,0,0,0.06);
  --chart-1: rgba(0,113,227,0.8); --chart-2: rgba(88,86,214,0.8);
  --chart-3: rgba(52,199,89,0.8); --chart-4: rgba(255,159,10,0.75);
}"""
    },
    {
        "id": "linear", "name": "Linear", "category": "Developer Tools",
        "dark": True, "bundled": True,
        "preview": {"bg": "#0f0f10", "card": "#1a1a1b", "text": "#e8e8e8", "accent": "#5e6ad2", "muted": "rgba(255,255,255,0.12)"},
        "css": """:root {
  --bg: #0f0f10; --card: #1a1a1b; --border: rgba(255,255,255,0.08);
  --text: #e8e8e8; --muted: rgba(255,255,255,0.4); --accent: #5e6ad2;
  --green: #4ade80; --shadow: none;
  --card-radius: 8px; --card-border: 1px solid rgba(255,255,255,0.08);
  --chart-label: rgba(255,255,255,0.4); --chart-grid: rgba(255,255,255,0.07);
  --chart-1: rgba(94,106,210,0.9); --chart-2: rgba(139,92,246,0.85);
  --chart-3: rgba(74,222,128,0.85); --chart-4: rgba(251,191,36,0.85);
}"""
    },
    {
        "id": "vercel", "name": "Vercel", "category": "Developer Tools",
        "dark": True, "bundled": True,
        "preview": {"bg": "#000000", "card": "#111111", "text": "#ffffff", "accent": "#ffffff", "muted": "rgba(255,255,255,0.12)"},
        "css": """:root {
  --bg: #000000; --card: #111111; --border: rgba(255,255,255,0.1);
  --text: #ffffff; --muted: rgba(255,255,255,0.4); --accent: #ffffff;
  --green: #50e3c2; --shadow: none;
  --card-radius: 4px; --card-border: 1px solid rgba(255,255,255,0.12);
  --chart-label: rgba(255,255,255,0.4); --chart-grid: rgba(255,255,255,0.08);
  --chart-1: rgba(255,255,255,0.85); --chart-2: rgba(160,160,160,0.75);
  --chart-3: rgba(80,227,194,0.85); --chart-4: rgba(200,200,200,0.6);
}"""
    },
    {
        "id": "notion", "name": "Notion", "category": "Design & Productivity",
        "dark": False, "bundled": True,
        "preview": {"bg": "#ffffff", "card": "#f7f7f5", "text": "#37352f", "accent": "#2eaadc", "muted": "rgba(55,53,47,0.10)"},
        "css": """:root {
  --bg: #ffffff; --card: #f7f7f5; --border: rgba(55,53,47,0.09);
  --text: #37352f; --muted: rgba(55,53,47,0.5); --accent: #2eaadc;
  --green: #0f7b6c; --shadow: none;
  --card-radius: 6px; --card-border: 1px solid rgba(55,53,47,0.12);
  --chart-label: rgba(55,53,47,0.5); --chart-grid: rgba(55,53,47,0.08);
  --chart-1: rgba(46,170,220,0.85); --chart-2: rgba(103,195,140,0.85);
  --chart-3: rgba(15,123,108,0.85); --chart-4: rgba(235,168,69,0.85);
}"""
    },
    {
        "id": "stripe", "name": "Stripe", "category": "Infrastructure & Cloud",
        "dark": False, "bundled": True,
        "preview": {"bg": "#f6f9fc", "card": "#ffffff", "text": "#0a2540", "accent": "#635bff", "muted": "rgba(10,37,64,0.10)"},
        "css": """:root {
  --bg: #f6f9fc; --card: #ffffff; --border: rgba(0,0,0,0.1);
  --text: #0a2540; --muted: rgba(10,37,64,0.5); --accent: #635bff;
  --green: #09825d; --shadow: 0px 2px 5px rgba(0,0,0,0.08), 0px 1px 1px rgba(0,0,0,0.05);
  --card-radius: 8px; --card-border: 1px solid rgba(10,37,64,0.1);
  --chart-label: rgba(10,37,64,0.5); --chart-grid: rgba(10,37,64,0.07);
  --chart-1: rgba(99,91,255,0.85); --chart-2: rgba(0,122,255,0.8);
  --chart-3: rgba(9,130,93,0.85); --chart-4: rgba(255,149,0,0.8);
}"""
    },
]

# ── Catalog of all themes available from awesome-design-md ─────────────────────
AWESOME_CATALOG = [
    # AI & ML
    {"id": "claude",      "name": "Claude",       "category": "AI & ML"},
    {"id": "cohere",      "name": "Cohere",        "category": "AI & ML"},
    {"id": "elevenlabs",  "name": "ElevenLabs",    "category": "AI & ML"},
    {"id": "minimax",     "name": "Minimax",       "category": "AI & ML"},
    {"id": "mistral",     "name": "Mistral AI",    "category": "AI & ML"},
    {"id": "ollama",      "name": "Ollama",        "category": "AI & ML"},
    {"id": "replicate",   "name": "Replicate",     "category": "AI & ML"},
    {"id": "runwayml",    "name": "RunwayML",      "category": "AI & ML"},
    {"id": "together",    "name": "Together AI",   "category": "AI & ML"},
    # Developer Tools
    {"id": "cursor",      "name": "Cursor",        "category": "Developer Tools"},
    {"id": "expo",        "name": "Expo",          "category": "Developer Tools"},
    {"id": "lovable",     "name": "Lovable",       "category": "Developer Tools"},
    {"id": "mintlify",    "name": "Mintlify",      "category": "Developer Tools"},
    {"id": "posthog",     "name": "PostHog",       "category": "Developer Tools"},
    {"id": "raycast",     "name": "Raycast",       "category": "Developer Tools"},
    {"id": "resend",      "name": "Resend",        "category": "Developer Tools"},
    {"id": "sentry",      "name": "Sentry",        "category": "Developer Tools"},
    {"id": "supabase",    "name": "Supabase",      "category": "Developer Tools"},
    {"id": "superhuman",  "name": "Superhuman",    "category": "Developer Tools"},
    {"id": "warp",        "name": "Warp",          "category": "Developer Tools"},
    {"id": "zapier",      "name": "Zapier",        "category": "Developer Tools"},
    # Infrastructure & Cloud
    {"id": "clickhouse",  "name": "ClickHouse",    "category": "Infrastructure & Cloud"},
    {"id": "composio",    "name": "Composio",      "category": "Infrastructure & Cloud"},
    {"id": "hashicorp",   "name": "HashiCorp",     "category": "Infrastructure & Cloud"},
    {"id": "mongodb",     "name": "MongoDB",       "category": "Infrastructure & Cloud"},
    {"id": "sanity",      "name": "Sanity",        "category": "Infrastructure & Cloud"},
    # Design & Productivity
    {"id": "airtable",    "name": "Airtable",      "category": "Design & Productivity"},
    {"id": "cal",         "name": "Cal.com",       "category": "Design & Productivity"},
    {"id": "clay",        "name": "Clay",          "category": "Design & Productivity"},
    {"id": "figma",       "name": "Figma",         "category": "Design & Productivity"},
    {"id": "framer",      "name": "Framer",        "category": "Design & Productivity"},
    {"id": "intercom",    "name": "Intercom",      "category": "Design & Productivity"},
    {"id": "miro",        "name": "Miro",          "category": "Design & Productivity"},
    {"id": "pinterest",   "name": "Pinterest",     "category": "Design & Productivity"},
    {"id": "webflow",     "name": "Webflow",       "category": "Design & Productivity"},
    # Fintech & Crypto
    {"id": "coinbase",    "name": "Coinbase",      "category": "Fintech & Crypto"},
    {"id": "kraken",      "name": "Kraken",        "category": "Fintech & Crypto"},
    {"id": "revolut",     "name": "Revolut",       "category": "Fintech & Crypto"},
    {"id": "wise",        "name": "Wise",          "category": "Fintech & Crypto"},
    # Enterprise & Consumer
    {"id": "airbnb",      "name": "Airbnb",        "category": "Enterprise & Consumer"},
    {"id": "ibm",         "name": "IBM",           "category": "Enterprise & Consumer"},
    {"id": "nvidia",      "name": "NVIDIA",        "category": "Enterprise & Consumer"},
    {"id": "spacex",      "name": "SpaceX",        "category": "Enterprise & Consumer"},
    {"id": "spotify",     "name": "Spotify",       "category": "Enterprise & Consumer"},
    {"id": "uber",        "name": "Uber",          "category": "Enterprise & Consumer"},
    # Car Brands
    {"id": "bmw",         "name": "BMW",           "category": "Car Brands"},
    {"id": "ferrari",     "name": "Ferrari",       "category": "Car Brands"},
    {"id": "lamborghini", "name": "Lamborghini",   "category": "Car Brands"},
    {"id": "renault",     "name": "Renault",       "category": "Car Brands"},
    {"id": "tesla",       "name": "Tesla",         "category": "Car Brands"},
]


def get_themes():
    """Return installed themes: bundled first, then user-generated from THEMES_DIR."""
    themes = {t["id"]: dict(t) for t in BUNDLED_THEMES}
    THEMES_DIR.mkdir(parents=True, exist_ok=True)
    for f in sorted(THEMES_DIR.glob("*.json")):
        try:
            t = json.loads(f.read_text())
            if "id" in t and "css" in t:
                t.setdefault("bundled", False)
                themes[t["id"]] = t
        except Exception:
            pass
    return list(themes.values())



def _cost_by_branch(sessions):
    """Aggregate sessions by (project, branch) tuple.

    Mirrors the JS ``_costByBranch`` helper so the same aggregation is
    available server-side for tests and downstream consumers.

    Each input row must have: ``project``, ``branch``, ``input``,
    ``output``, ``cache_read``, ``cache_creation``, ``turns``, ``cost``.

    Empty/missing branch values are normalised to the literal string
    ``"(default)"`` (matches the UI label for un-branched activity).

    Returns a list sorted descending by ``cost``.
    """
    DEFAULT = "(default)"
    by_key = {}
    for s in sessions or []:
        project = s.get("project") or ""
        branch = (s.get("branch") or "").strip() or DEFAULT
        key = (project, branch)
        row = by_key.get(key)
        if row is None:
            row = {
                "project": project,
                "branch": branch,
                "sessions": 0,
                "turns": 0,
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_creation": 0,
                "cost": 0.0,
            }
            by_key[key] = row
        row["sessions"] += 1
        row["turns"] += s.get("turns", 0) or 0
        row["input"] += s.get("input", 0) or 0
        row["output"] += s.get("output", 0) or 0
        row["cache_read"] += s.get("cache_read", 0) or 0
        row["cache_creation"] += s.get("cache_creation", 0) or 0
        row["cost"] += float(s.get("cost", 0) or 0)
    return sorted(by_key.values(), key=lambda r: -r["cost"])
def _cache_hit_ratio(input_tokens, cache_read_tokens):
    """Ratio of cache_read over (input + cache_read). 0.0 when both are zero.

    A ratio of 0% means no cache hits at all; 100% means every token of
    "fresh" prompt context was actually served from cache (rare, but possible
    on long warm sessions).
    """
    denom = (input_tokens or 0) + (cache_read_tokens or 0)
    if denom <= 0:
        return 0.0
    return (cache_read_tokens or 0) / denom


def _cache_hit_category(ratio):
    """Bucket a ratio into 'low' / 'medium' / 'high'."""
    if ratio < 0.30:
        return "low"
    if ratio <= 0.70:
        return "medium"
    return "high"


def _cache_hit_analysis(conn, input_threshold=50_000):
    """Per-session cache hit ratios and a global summary.

    Returns a dict shaped like::

        {
          "per_session": {session_id_full: {"ratio": float, "category": str,
                                            "input": int, "cache_read": int,
                                            "underusing": bool}, ...},
          "summary": {
              "avg_ratio": float,                # 0..1, mean across sessions
              "avg_ratio_pct": float,            # convenience, 0..100, 1dp
              "sessions_total": int,
              "sessions_with_cache": int,        # ratio > 0
              "sessions_underusing": int,        # ratio<0.3 AND input>threshold
              "input_threshold": int,
              "by_category": {"low": int, "medium": int, "high": int},
          },
        }

    "Underusing" flags sessions paying full price for repeat content: they
    have high raw input but rarely (<30%) hit the prompt cache.
    """
    rows = conn.execute("""
        SELECT
            session_id,
            COALESCE(total_input_tokens, 0)  AS input,
            COALESCE(total_cache_read, 0)    AS cache_read
        FROM sessions
    """).fetchall()

    per_session = {}
    ratios = []
    by_category = {"low": 0, "medium": 0, "high": 0}
    with_cache = 0
    underusing = 0

    for r in rows:
        sid = r["session_id"]
        inp = r["input"] or 0
        cr  = r["cache_read"] or 0
        ratio = _cache_hit_ratio(inp, cr)
        cat = _cache_hit_category(ratio)
        is_under = (ratio < 0.30) and (inp > input_threshold)
        per_session[sid] = {
            "ratio": round(ratio, 4),
            "category": cat,
            "input": inp,
            "cache_read": cr,
            "underusing": is_under,
        }
        ratios.append(ratio)
        by_category[cat] += 1
        if ratio > 0:
            with_cache += 1
        if is_under:
            underusing += 1

    avg = (sum(ratios) / len(ratios)) if ratios else 0.0
    summary = {
        "avg_ratio":           round(avg, 4),
        "avg_ratio_pct":       round(avg * 100, 1),
        "sessions_total":      len(ratios),
        "sessions_with_cache": with_cache,
        "sessions_underusing": underusing,
        "input_threshold":     input_threshold,
        "by_category":         by_category,
    }
    return {"per_session": per_session, "summary": summary}
# ── 1-hour cache opportunity hints ────────────────────────────────────────────
# Anthropic offers two prompt-cache tiers:
#   * 5-minute (default): cache_write costs 1.25x base input, but the cache
#     expires every 5 min so a long session keeps rewriting it.
#   * 1-hour: cache_write costs 1.6x the 5-minute rate (= 2.0x base input)
#     but only needs to be paid once per session.
# For long sessions with heavy cache writes, switching to the 1-hour tier
# typically saves 30-50 % of the cache-creation cost.

CACHE_1H_MIN_DURATION_MIN = 30
CACHE_1H_MIN_CACHE_CREATION = 100_000
# Mid-band of the 30-50 % heuristic savings range.
CACHE_1H_SAVINGS_FRACTION = 0.40


def _cache_1h_opportunities(conn):
    """Return sessions that would have benefited from the 1-hour cache tier.

    Criteria: duration > 30 minutes AND cache_creation > 100 000 tokens.
    Returns the top 10 candidates sorted by estimated savings (desc):

        {session_id, duration_min, cache_creation_tokens,
         current_cost, estimated_savings_with_1h}

    Estimated savings are projected at ~40 % of the current cache-creation
    cost (mid-band of the 30-50 % heuristic range). Sessions on unknown /
    non-billable models are skipped so we never invent a savings figure.
    """
    rows = conn.execute("""
        SELECT session_id, first_timestamp, last_timestamp,
               total_cache_creation, model
        FROM sessions
        WHERE total_cache_creation > ?
    """, (CACHE_1H_MIN_CACHE_CREATION,)).fetchall()

    opportunities = []
    for r in rows:
        try:
            t1 = datetime.fromisoformat((r["first_timestamp"] or "").replace("Z", "+00:00"))
            t2 = datetime.fromisoformat((r["last_timestamp"] or "").replace("Z", "+00:00"))
            duration_min = round((t2 - t1).total_seconds() / 60, 1)
        except Exception:
            continue
        if duration_min <= CACHE_1H_MIN_DURATION_MIN:
            continue

        p = get_pricing(r["model"])
        if p is None:
            continue  # unknown / non-billable model — don't guess

        cache_creation = r["total_cache_creation"] or 0
        # Current cost reflects the 5-minute tier (the default cache_write).
        current_cost = cache_creation * p["cache_write_5m"] / 1_000_000
        estimated_savings = round(current_cost * CACHE_1H_SAVINGS_FRACTION, 4)

        opportunities.append({
            "session_id":                r["session_id"][:8],
            "duration_min":              duration_min,
            "cache_creation_tokens":     cache_creation,
            "current_cost":              round(current_cost, 4),
            "estimated_savings_with_1h": estimated_savings,
        })

    opportunities.sort(key=lambda o: o["estimated_savings_with_1h"], reverse=True)
    return opportunities[:10]
def _session_active_minutes(timestamps, gap_threshold_min=5):
    """Sum of gaps (in minutes) between consecutive turn timestamps where
    gap < gap_threshold_min. Gaps >= threshold are treated as breaks and
    excluded. Single-turn sessions yield 0.

    `timestamps` is an iterable of ISO-8601 strings (UTC) for one session's
    turns. They do not have to be sorted; the helper sorts a copy.
    """
    if not timestamps:
        return 0.0
    parsed = []
    for ts in timestamps:
        if not ts:
            continue
        try:
            parsed.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
        except Exception:
            continue
    if len(parsed) < 2:
        return 0.0
    parsed.sort()
    threshold = gap_threshold_min * 60.0
    total = 0.0
    for a, b in zip(parsed, parsed[1:]):
        gap = (b - a).total_seconds()
        if 0 <= gap < threshold:
            total += gap
    return round(total / 60.0, 2)


def _time_on_task(conn, date=None, gap_threshold_min=5, days=30):
    """Return per-day active minutes for the trailing `days` days ending on
    `date` (default: today).

    Active minutes for a day = sum of inter-turn gaps within each session that
    fall on that day, where the gap is strictly less than `gap_threshold_min`.
    Gaps that span midnight are credited to the day of the earlier timestamp.

    Returns a list of dicts: [{"day": "YYYY-MM-DD", "active_minutes": float}, ...]
    in chronological order, with every day present (zero-filled).
    """
    end_d = date if isinstance(date, _date_cls) else _date_cls.today()
    start_d = end_d - timedelta(days=days - 1)
    start_iso = start_d.isoformat()
    end_iso = end_d.isoformat()

    # Fetch all turns whose date is in the window, grouped by session, ordered
    # by timestamp. We need every turn (not aggregates) to compute inter-turn
    # gaps. Limit query by date prefix so we don't scan history.
    rows = conn.execute(
        """
        SELECT session_id, timestamp
        FROM turns
        WHERE timestamp IS NOT NULL
          AND substr(timestamp, 1, 10) BETWEEN ? AND ?
        ORDER BY session_id, timestamp
        """,
        (start_iso, end_iso),
    ).fetchall()

    per_day = {(start_d + timedelta(days=i)).isoformat(): 0.0 for i in range(days)}
    threshold = gap_threshold_min * 60.0

    # Walk session by session, summing intra-session gaps to the day of the
    # earlier timestamp.
    current_sid = None
    prev_ts = None
    for r in rows:
        sid = r[0] if not hasattr(r, "keys") else r["session_id"]
        ts  = r[1] if not hasattr(r, "keys") else r["timestamp"]
        if sid != current_sid:
            current_sid = sid
            prev_ts = ts
            continue
        try:
            t1 = datetime.fromisoformat(prev_ts.replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            prev_ts = ts
            continue
        gap = (t2 - t1).total_seconds()
        if 0 <= gap < threshold:
            day_key = prev_ts[:10]
            if day_key in per_day:
                per_day[day_key] += gap
        prev_ts = ts

    return [
        {"day": d, "active_minutes": round(per_day[d] / 60.0, 2)}
        for d in sorted(per_day.keys())
    ]


# ── Currency / FX rates ────────────────────────────────────────────────────
# Frankfurter (https://www.frankfurter.app) is an open, no-key, ECB-sourced
# daily FX rates API. We cache results in-process for 6 hours; the frontend
# also caches in localStorage for 24 hours.
FX_CACHE_TTL_SECONDS = 6 * 60 * 60
_FX_CACHE = {"data": None, "fetched_at": 0.0}


def _fx_urlopen(url, timeout=5):
    """Thin wrapper around urllib.request.urlopen so tests can patch a single symbol."""
    return urllib.request.urlopen(url, timeout=timeout)


def _fetch_fx_rates(base="USD", target_currencies=None):
    """Fetch latest FX rates from frankfurter.app.

    Returns a dict like {"base": "USD", "date": "2026-05-23",
    "rates": {"EUR": 0.92, ...}, "as_of": <iso ts>}, or None on failure.
    Uses an in-process cache for FX_CACHE_TTL_SECONDS (default 6h).
    """
    now = time.time()
    cached = _FX_CACHE.get("data")
    if cached and (now - _FX_CACHE.get("fetched_at", 0)) < FX_CACHE_TTL_SECONDS:
        return cached
    url = "https://api.frankfurter.app/latest?from=" + base
    try:
        with _fx_urlopen(url, timeout=5) as resp:
            raw = resp.read()
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001 - network/JSON failures both -> None
        return None
    rates = parsed.get("rates") or {}
    # Always make sure the base currency is present at 1.0 so the frontend
    # can iterate a single dict.
    rates[base] = 1.0
    if target_currencies:
        rates = {k: v for k, v in rates.items() if k in set(target_currencies) | {base}}
    out = {
        "base": base,
        "date": parsed.get("date"),
        "rates": rates,
        "as_of": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    _FX_CACHE["data"] = out
    _FX_CACHE["fetched_at"] = now
    return out


def _cost_concentration(sessions_with_cost, top_n=5):
    """Compute Pareto-style concentration: top-N sessions' cost as % of total."""
    if not sessions_with_cost:
        return None
    total = sum(s["cost"] for s in sessions_with_cost)
    if total <= 0:
        return None
    ranked = sorted(sessions_with_cost, key=lambda s: -s["cost"])[:top_n]
    top_cost = sum(s["cost"] for s in ranked)
    return {
        "top_n": top_n,
        "top_cost": round(top_cost, 2),
        "total_cost": round(total, 2),
        "pct": round(top_cost / total * 100, 1),
        "top_sessions": [
            {"session_id": s["session_id"], "project": s.get("project", ""),
             "model": s.get("model", ""), "cost": round(s["cost"], 2)}
            for s in ranked
        ],
    }


def _year_calendar(conn, today=None):
    """Return a list of 365 dicts (oldest day first) covering the trailing
    365 days ending at ``today`` (UTC date by default). Each dict has the
    shape ``{"date": "YYYY-MM-DD", "cost": float, "turns": int}``. Days with
    no recorded turns are included with ``cost=0`` and ``turns=0`` so the
    front-end can draw a contiguous 53x7 grid without gaps.

    Cost is calculated server-side per (day, model) using the canonical
    PRICING table from ``pricing.py``; turns from non-billable models still
    count toward the day's turn total but contribute 0 cost — matching the
    behaviour of the JS ``calcCost`` helper.
    """
    today = today or date.today()
    start = today - timedelta(days=364)  # inclusive window of 365 days
    start_iso = start.isoformat()

    rows = conn.execute("""
        SELECT
            substr(timestamp, 1, 10)                  as day,
            COALESCE(NULLIF(model, ''), 'unknown')    as model,
            SUM(input_tokens)          as input,
            SUM(output_tokens)         as output,
            SUM(cache_read_tokens)     as cache_read,
            SUM(cache_creation_tokens) as cache_creation,
            COUNT(*)                   as turns
        FROM turns
        WHERE substr(timestamp, 1, 10) >= ?
        GROUP BY day, model
    """, (start_iso,)).fetchall()

    by_day = {}
    for r in rows:
        day = r["day"]
        if not day:
            continue
        bucket = by_day.setdefault(day, {"cost": 0.0, "turns": 0})
        bucket["turns"] += r["turns"] or 0
        p = get_pricing(r["model"])
        if p:
            bucket["cost"] += (
                (r["input"] or 0)          * p["input"]       / 1e6 +
                (r["output"] or 0)         * p["output"]      / 1e6 +
                (r["cache_read"] or 0)     * p["cache_read"]  / 1e6 +
                (r["cache_creation"] or 0) * p["cache_write"] / 1e6
            )

    out = []
    for i in range(365):
        d = (start + timedelta(days=i)).isoformat()
        b = by_day.get(d, {"cost": 0.0, "turns": 0})
        out.append({"date": d, "cost": round(b["cost"], 6), "turns": b["turns"]})
    return out


def _daily_cost_history(conn, days_back=60):
    """Return [(day, cost_usd, tokens_in, tokens_out)] for the last N days.
    Cost uses the current PRICING table; this is intentionally an estimate
    (Anthropic's actual billing follows historical pricing) but matches what
    the dashboard otherwise shows."""
    from pricing import get_pricing
    from datetime import datetime as _dt, timedelta as _td
    cutoff = (_dt.utcnow() - _td(days=days_back)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT date(timestamp) as day, model,
               SUM(input_tokens)          as inp,
               SUM(output_tokens)         as out,
               SUM(cache_read_tokens)     as cr,
               SUM(cache_creation_tokens) as cw
        FROM turns
        WHERE timestamp IS NOT NULL AND date(timestamp) >= ?
        GROUP BY day, model
        ORDER BY day ASC
    """, (cutoff,)).fetchall()
    by_day = {}
    for r in rows:
        p = get_pricing(r["model"])
        if not p:
            continue
        c = ((r["inp"] or 0) * p["input"]
             + (r["out"] or 0) * p["output"]
             + (r["cr"] or 0) * p["cache_read"]
             + (r["cw"] or 0) * p["cache_write"]) / 1_000_000
        d = by_day.setdefault(r["day"], {"day": r["day"], "cost": 0.0, "tokens": 0})
        d["cost"] += c
        d["tokens"] += (r["inp"] or 0) + (r["out"] or 0)
    return sorted(by_day.values(), key=lambda x: x["day"])


def _anomaly_check(conn, threshold_sigma=2.0):
    """Detect spend spikes. Returns dict with today/avg/sigma/is_anomalous."""
    from pricing import get_pricing
    from datetime import date as _date, timedelta as _td
    today = _date.today().strftime("%Y-%m-%d")
    cutoff = (_date.today() - _td(days=30)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT date(timestamp) as day, model,
               SUM(input_tokens) as inp, SUM(output_tokens) as out,
               SUM(cache_read_tokens) as cr, SUM(cache_creation_tokens) as cw
        FROM turns
        WHERE timestamp IS NOT NULL AND date(timestamp) >= ?
        GROUP BY day, model
    """, (cutoff,)).fetchall()
    by_day = {}
    for r in rows:
        p = get_pricing(r["model"])
        if not p:
            continue
        c = ((r["inp"] or 0) * p["input"]
             + (r["out"] or 0) * p["output"]
             + (r["cr"] or 0) * p["cache_read"]
             + (r["cw"] or 0) * p["cache_write"]) / 1_000_000
        by_day[r["day"]] = by_day.get(r["day"], 0.0) + c
    if not by_day:
        return {"is_anomalous": False, "reason": "no data"}
    today_spend = by_day.get(today, 0.0)
    history = [v for d, v in by_day.items() if d != today]
    if len(history) < 7:
        return {"is_anomalous": False, "reason": "not enough history",
                "today": round(today_spend, 2)}
    mean = sum(history) / len(history)
    var = sum((v - mean) ** 2 for v in history) / len(history)
    sigma = var ** 0.5
    is_anomalous = today_spend > mean + threshold_sigma * sigma and today_spend > 2 * mean
    return {
        "is_anomalous": is_anomalous,
        "today": round(today_spend, 2),
        "mean": round(mean, 2),
        "sigma": round(sigma, 2),
        "ratio": round(today_spend / mean, 2) if mean > 0 else None,
        "threshold_sigma": threshold_sigma,
    }


def _forecast(history):
    """Simple lagging-average forecast. Returns dict with avg_7d, avg_30d,
    projected_month_end, trend ('up'|'down'|'flat')."""
    if not history:
        return {"avg_7d": 0.0, "avg_30d": 0.0,
                "projected_month_end": 0.0, "trend": "flat",
                "days_in_data": 0}
    last7  = history[-7:]
    last30 = history[-30:]
    avg7   = sum(d["cost"] for d in last7)  / max(len(last7), 1)
    avg30  = sum(d["cost"] for d in last30) / max(len(last30), 1)
    # Project to end of the current calendar month using avg7.
    from datetime import date as _date, timedelta as _td
    today = _date.today()
    # Last day of current month
    if today.month == 12:
        next_month = _date(today.year + 1, 1, 1)
    else:
        next_month = _date(today.year, today.month + 1, 1)
    days_left = (next_month - today).days
    month_so_far = sum(
        d["cost"] for d in history
        if d["day"][:7] == today.strftime("%Y-%m")
    )
    projected = month_so_far + avg7 * days_left
    if avg30 > 0:
        ratio = avg7 / avg30
        trend = "up" if ratio > 1.1 else "down" if ratio < 0.9 else "flat"
    else:
        trend = "flat"
    return {
        "avg_7d":  round(avg7, 2),
        "avg_30d": round(avg30, 2),
        "month_to_date": round(month_so_far, 2),
        "projected_month_end": round(projected, 2),
        "days_left_in_month": days_left,
        "trend": trend,
        "days_in_data": len(history),
    }


BUDGET_CONFIG_PATH = Path.home() / ".claude" / "budget.json"


def _load_budget():
    """Read ~/.claude/budget.json. Schema:
       {"monthly_usd": 50.0, "warn_at": [0.8, 1.0], "last_alerted": {...}}
    Returns {} if missing or unreadable so callers can use .get(...)."""
    try:
        return json.loads(BUDGET_CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


TAGS_CONFIG_PATH = Path.home() / ".claude" / "tags.json"


def _load_tags():
    """Read user-defined session tags. Returns {} if unreadable."""
    try:
        return json.loads(TAGS_CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_budget(cfg):
    BUDGET_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    BUDGET_CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def _budget_status(month_to_date_usd, cfg=None):
    """Compute current % consumed + threshold crossed (if any)."""
    cfg = cfg or _load_budget()
    cap = cfg.get("monthly_usd")
    if not cap or cap <= 0:
        return {"configured": False}
    pct = month_to_date_usd / cap
    thresholds = cfg.get("warn_at", [0.8, 1.0])
    crossed = None
    for t in sorted(thresholds, reverse=True):
        if pct >= t:
            crossed = t
            break
    return {
        "configured": True,
        "monthly_usd": cap,
        "month_to_date": round(month_to_date_usd, 2),
        "pct": round(pct, 4),
        "crossed_threshold": crossed,
    }
def _save_tags(tags):
    TAGS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    TAGS_CONFIG_PATH.write_text(json.dumps(tags, indent=2, sort_keys=True))
def _dow_hour_heatmap(conn):
    """Return a 7x24 grid: weekday (0=Mon) x hour (0-23) -> {turns, tokens}.

    Uses sqlite's strftime to extract dow + hour from the UTC timestamp.
    Caller's UI can shift the timezone if needed; we keep UTC for the
    server-side aggregation."""
    rows = conn.execute("""
        SELECT
            CAST(strftime('%w', timestamp) as INTEGER) as dow,
            CAST(strftime('%H', timestamp) as INTEGER) as hour,
            COUNT(*) as turns,
            SUM(input_tokens + output_tokens) as tokens
        FROM turns
        WHERE timestamp IS NOT NULL
        GROUP BY dow, hour
    """).fetchall()
    grid = [[{"turns": 0, "tokens": 0} for _ in range(24)] for _ in range(7)]
    # sqlite %w is 0=Sun .. 6=Sat; we re-index to 0=Mon..6=Sun so weekdays
    # are contiguous in the UI.
    remap = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
    for r in rows:
        d = remap.get(r["dow"], 0)
        h = r["hour"]
        grid[d][h]["turns"] += r["turns"] or 0
        grid[d][h]["tokens"] += r["tokens"] or 0
    return grid
def _cost_per_turn_stats(conn):
    """Compute per-turn cost distribution: p50/p95/p99/max + 12 log-spaced buckets."""
    from pricing import get_pricing
    rows = conn.execute("""
        SELECT model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens
        FROM turns
    """).fetchall()
    costs = []
    for r in rows:
        p = get_pricing(r["model"])
        if not p:
            continue
        c = ((r["input_tokens"] or 0) * p["input"]
             + (r["output_tokens"] or 0) * p["output"]
             + (r["cache_read_tokens"] or 0) * p["cache_read"]
             + (r["cache_creation_tokens"] or 0) * p["cache_write"]) / 1_000_000
        if c > 0:
            costs.append(c)
    if not costs:
        return None
    costs.sort()
    def pct(p): return costs[min(int(len(costs) * p), len(costs) - 1)]
    # 12 log-spaced buckets from $0.0001 to costs[-1]
    import math
    max_c = max(costs[-1], 1e-4)
    edges = [10 ** (math.log10(1e-4) + i * (math.log10(max_c) - math.log10(1e-4)) / 12) for i in range(13)]
    buckets = [0] * 12
    for c in costs:
        for i in range(12):
            if c <= edges[i + 1]:
                buckets[i] += 1
                break
        else:
            buckets[-1] += 1
    return {
        "n": len(costs),
        "p50": round(pct(0.5), 4),
        "p95": round(pct(0.95), 4),
        "p99": round(pct(0.99), 4),
        "max": round(costs[-1], 4),
        "mean": round(sum(costs) / len(costs), 4),
        "buckets": buckets,
        "edges": [round(e, 5) for e in edges],
    }


# Subscription plan pricing (USD/month). Anthropic's published numbers, May 2026.
PLANS = {
    "Free":       {"price": 0,    "included": "~ $5/mo equivalent"},
    "Pro":        {"price": 20,   "included": "~ $100/mo equivalent"},
    "Max-5x":     {"price": 100,  "included": "~ $500/mo equivalent"},
    "Max-20x":    {"price": 200,  "included": "~ $1000/mo equivalent"},
}


def _plan_comparison(month_to_date_usd):
    """Given API-equivalent spend so far this month, surface which plan would
    have been cheapest. Heuristic — these mappings are approximate (Pro caps
    are usage-based not strict)."""
    rec = None
    for name, p in PLANS.items():
        if p["price"] == 0:
            continue
        # Pick the cheapest plan whose included usage exceeds month-to-date.
        # We approximate "included" as 5x the price (Pro: $20 -> $100 worth,
        # Max-5x: $100 -> $500). Matches Anthropic's public marketing copy.
        included = p["price"] * 5
        if month_to_date_usd <= included:
            rec = name
            break
    rec = rec or "Max-20x"
    return {
        "month_to_date": round(month_to_date_usd, 2),
        "recommended": rec,
        "plans": PLANS,
    }


PROJECT_BUDGETS_PATH = Path.home() / ".claude" / "project-budgets.json"


def _load_project_budgets():
    try:
        return json.loads(PROJECT_BUDGETS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_project_budgets(cfg):
    PROJECT_BUDGETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROJECT_BUDGETS_PATH.write_text(json.dumps(cfg, indent=2, sort_keys=True))


def _project_budget_status(per_project_mtd, cfg=None):
    """Given {project: month_to_date_usd}, return {project: {cap, mtd, pct, state}}."""
    cfg = cfg or _load_project_budgets()
    out = {}
    for proj, cap in cfg.items():
        if not cap or cap <= 0:
            continue
        mtd = per_project_mtd.get(proj, 0)
        pct = mtd / cap if cap > 0 else 0
        state = "over" if pct >= 1 else "warn" if pct >= 0.8 else "ok"
        out[proj] = {"cap": cap, "mtd": round(mtd, 2), "pct": round(pct, 4), "state": state}
    return out


def _session_detail(session_id_prefix):
    """Return per-turn timeline + tools breakdown for one session.
    `session_id_prefix` is the 8-char id shown in the dashboard."""
    if not DB_PATH.exists():
        return {"error": "no DB"}
    from pricing import get_pricing
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Find the full session_id by prefix match.
    row = conn.execute("""
        SELECT session_id, project_name, git_branch, model, turn_count,
               first_timestamp, last_timestamp,
               total_input_tokens, total_output_tokens,
               total_cache_read, total_cache_creation
        FROM sessions
        WHERE substr(session_id, 1, 8) = ?
        LIMIT 1
    """, (session_id_prefix,)).fetchone()
    if not row:
        conn.close()
        return {"error": "session not found"}
    sid = row["session_id"]
    turns = conn.execute("""
        SELECT timestamp, model, input_tokens, output_tokens,
               cache_read_tokens, cache_creation_tokens, tool_name
        FROM turns
        WHERE session_id = ?
        ORDER BY timestamp ASC
    """, (sid,)).fetchall()
    # Compute cumulative cost timeline
    cum = 0.0
    timeline = []
    tools = {}
    for t in turns:
        p = get_pricing(t["model"])
        c = 0.0
        if p:
            c = ((t["input_tokens"] or 0) * p["input"]
                 + (t["output_tokens"] or 0) * p["output"]
                 + (t["cache_read_tokens"] or 0) * p["cache_read"]
                 + (t["cache_creation_tokens"] or 0) * p["cache_write"]) / 1_000_000
        cum += c
        timeline.append({
            "timestamp": t["timestamp"],
            "model":     t["model"],
            "tokens":    (t["input_tokens"] or 0) + (t["output_tokens"] or 0),
            "cost":      round(c, 4),
            "cum_cost":  round(cum, 4),
            "tool":      t["tool_name"] or None,
        })
        tn = t["tool_name"] or "(direct)"
        tools[tn] = tools.get(tn, 0) + 1
    conn.close()
    return {
        "session": dict(row),
        "turn_count_actual": len(timeline),
        "timeline": timeline,
        "tools_breakdown": [{"tool": k, "count": v} for k, v in
                            sorted(tools.items(), key=lambda x: -x[1])],
        "total_cost": round(cum, 4),
    }


def _subagent_split(conn):
    """Return {main: cost, subagent: cost, main_pct} from current PRICING.
    Uses the convention that turns with tool_name='subagent' are delegated
    (Cowork Task-spawn); everything else is main-thread."""
    from pricing import get_pricing
    rows = conn.execute("""
        SELECT tool_name, model,
               SUM(input_tokens) as inp, SUM(output_tokens) as out,
               SUM(cache_read_tokens) as cr, SUM(cache_creation_tokens) as cw
        FROM turns
        GROUP BY tool_name, model
    """).fetchall()
    main = 0.0
    sub = 0.0
    for r in rows:
        p = get_pricing(r["model"])
        if not p:
            continue
        c = ((r["inp"] or 0) * p["input"] + (r["out"] or 0) * p["output"]
             + (r["cr"] or 0) * p["cache_read"] + (r["cw"] or 0) * p["cache_write"]) / 1_000_000
        if r["tool_name"] == "subagent":
            sub += c
        else:
            main += c
    total = main + sub
    return {
        "main": round(main, 2),
        "subagent": round(sub, 2),
        "main_pct": round((main / total * 100) if total > 0 else 0, 1),
        "subagent_pct": round((sub / total * 100) if total > 0 else 0, 1),
    }
def _session_sparklines(conn, session_ids):
    """For each session id, return a 30-bin sparkline (turns per equal-width
    time bucket) spanning the session's first_timestamp -> last_timestamp.
    Returns {session_id: [int, int, ...]}."""
    if not session_ids:
        return {}
    out = {}
    placeholders = ",".join("?" * len(session_ids))
    rows = conn.execute(f"""
        SELECT t.session_id, t.timestamp
        FROM turns t
        WHERE t.session_id IN ({placeholders}) AND t.timestamp IS NOT NULL
        ORDER BY t.session_id, t.timestamp ASC
    """, tuple(session_ids)).fetchall()
    # Group timestamps by session
    by_sid = {}
    for r in rows:
        by_sid.setdefault(r["session_id"], []).append(r["timestamp"])
    BINS = 30
    for sid, ts in by_sid.items():
        if len(ts) < 2:
            out[sid] = [len(ts)]
            continue
        # Convert to epoch-ish ordinal for bucketing (lexicographic timestamps work too).
        first, last = ts[0], ts[-1]
        # We just use ordinal position over total — coarse but enough for sparkline.
        bins = [0] * BINS
        first_dt = first
        last_dt = last
        if first_dt == last_dt:
            bins[BINS - 1] = len(ts)
            out[sid] = bins
            continue
        # Map each ts to a bucket by ratio of (ts - first) / (last - first).
        # Use ISO-8601 string comparison; it works monotonically for our data
        # and avoids parsing. For the bin index we still need a numeric ratio
        # — convert via datetime when feasible.
        from datetime import datetime as _dt
        try:
            fdt = _dt.fromisoformat(first.replace("Z", "+00:00"))
            ldt = _dt.fromisoformat(last.replace("Z", "+00:00"))
            span = (ldt - fdt).total_seconds()
        except Exception:
            span = 0
        if span <= 0:
            bins[BINS - 1] = len(ts)
            out[sid] = bins
            continue
        for t in ts:
            try:
                tdt = _dt.fromisoformat(t.replace("Z", "+00:00"))
                ratio = (tdt - fdt).total_seconds() / span
            except Exception:
                ratio = 1.0
            idx = min(int(ratio * BINS), BINS - 1)
            bins[idx] += 1
        out[sid] = bins
    return out
def _compute_streak(conn, today=None):
    """Return the number of consecutive calendar days (UTC) ending at
    ``today`` on which the user had >=1 assistant turn. Future-dated rows are
    ignored so a bad clock can't inflate the streak. Returns 0 when there's
    no activity today (the streak only counts unbroken runs anchored at
    today). ``today`` is injectable for tests; defaults to UTC date now."""
    today = today or datetime.utcnow().date()
    today_iso = today.isoformat()
    rows = conn.execute(
        """
        SELECT DISTINCT substr(timestamp, 1, 10) AS day
        FROM turns
        WHERE timestamp IS NOT NULL
          AND length(timestamp) >= 10
          AND substr(timestamp, 1, 10) <= ?
        """,
        (today_iso,),
    ).fetchall()
    days = {r["day"] if isinstance(r, sqlite3.Row) else r[0] for r in rows}
    streak = 0
    cursor = today
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak
# ── Model downgrade suggestions ──────────────────────────────────────────────
# Tools that a cheaper model (haiku) can handle just as well as opus/sonnet.
# Reading files, simple edits, running commands, searching — these don't need
# the deepest reasoning model. Task (subagent dispatch), WebSearch, WebFetch
# and any multi-step "think hard" reply turn does.
_DOWNGRADE_SIMPLE_TOOLS = {
    "Read", "Edit", "Bash", "Grep", "Glob", "LS",
    "MultiEdit", "Write", "NotebookRead",
}
# Tools whose presence vetoes a downgrade outright — they're signals the
# session leaned on agentic / web-research capabilities of the larger model.
_DOWNGRADE_VETO_TOOLS = {
    "Task", "WebSearch", "WebFetch",
}
# Threshold parameters — kept as module-level constants so tests can monkey-patch.
_DOWNGRADE_SMALL_INPUT_AVG = 5000
_DOWNGRADE_SMALL_OUTPUT_AVG = 2000
_DOWNGRADE_CACHE_CREATION_RATIO = 0.20  # cache_creation tokens / total < this
_DOWNGRADE_SIMPLE_TOOL_RATIO = 0.70     # of identified tool turns
_DOWNGRADE_MIN_SCORE = 3                # out of 4 signals
_DOWNGRADE_MIN_TURNS = 5                # ignore tiny one-shot sessions
_DOWNGRADE_MIN_SAVINGS = 0.01           # don't surface sub-cent savings
_DOWNGRADE_TOP_N = 20


def _is_downgradable_model(model):
    """True iff model is opus or sonnet (not haiku, not unknown)."""
    if not model:
        return False
    m = model.lower()
    return ("opus" in m) or ("sonnet" in m)


def _suggest_target_model(current_model):
    """Pick a haiku model from PRICING that matches the family era when
    possible, falling back to the newest haiku."""
    # Prefer same generation (e.g. opus-4-7 -> haiku-4-7). The PRICING dict is
    # ordered newest-first, so iterate it directly.
    if current_model:
        # Extract the trailing -X-Y version suffix, if any
        m = re.search(r"-(\d+-\d+)$", current_model)
        if m:
            suffix = m.group(1)
            candidate = f"claude-haiku-{suffix}"
            if candidate in PRICING:
                return candidate
    # Fallback: newest haiku in the table
    for key in PRICING:
        if "haiku" in key:
            return key
    return "claude-haiku-4-5"


def _downgrade_suggestions(conn):
    """Find sessions where a cheaper model could plausibly have done the work.

    Heuristic per opus/sonnet session (haiku is never suggested for downgrade):
      1. avg input < _DOWNGRADE_SMALL_INPUT_AVG AND avg output <
         _DOWNGRADE_SMALL_OUTPUT_AVG — small per-turn footprint.
      2. cache_creation / total_tokens < _DOWNGRADE_CACHE_CREATION_RATIO —
         not a sprawling long-context session.
      3. Of turns with an identifiable tool, share of "simple" tools
         (Read/Edit/Bash/Grep/...) >= _DOWNGRADE_SIMPLE_TOOL_RATIO.
      4. No vetoed tools (Task, WebSearch, WebFetch) anywhere in the session.

    Each satisfied check is one point; sessions scoring >= _DOWNGRADE_MIN_SCORE
    are flagged. Cost is re-priced against the suggested haiku model, and we
    return the top _DOWNGRADE_TOP_N by absolute savings.
    """
    try:
        session_rows = conn.execute("""
            SELECT
                session_id, model, project_name,
                total_input_tokens, total_output_tokens,
                total_cache_read, total_cache_creation, total_cache_1h,
                turn_count
            FROM sessions
            WHERE turn_count >= ?
        """, (_DOWNGRADE_MIN_TURNS,)).fetchall()
    except sqlite3.OperationalError:
        # total_cache_1h column missing on very old DBs
        session_rows = conn.execute("""
            SELECT
                session_id, model, project_name,
                total_input_tokens, total_output_tokens,
                total_cache_read, total_cache_creation,
                0 AS total_cache_1h,
                turn_count
            FROM sessions
            WHERE turn_count >= ?
        """, (_DOWNGRADE_MIN_TURNS,)).fetchall()

    suggestions = []

    for r in session_rows:
        model = r["model"] or ""
        if not _is_downgradable_model(model):
            continue
        turns = r["turn_count"] or 0
        if turns <= 0:
            continue

        inp = r["total_input_tokens"] or 0
        out = r["total_output_tokens"] or 0
        cr = r["total_cache_read"] or 0
        cc = r["total_cache_creation"] or 0
        cc1h = r["total_cache_1h"] or 0
        total_tokens = inp + out + cr + cc + cc1h
        if total_tokens <= 0:
            continue

        avg_in = inp / turns
        avg_out = out / turns
        cc_ratio = (cc + cc1h) / total_tokens if total_tokens else 0.0

        # Tool distribution — also veto check
        tool_rows = conn.execute("""
            SELECT COALESCE(tool_name, '') AS tool, COUNT(*) AS n
            FROM turns
            WHERE session_id = ?
            GROUP BY tool
        """, (r["session_id"],)).fetchall()

        vetoed = False
        simple_tool_turns = 0
        identified_tool_turns = 0
        for tr in tool_rows:
            t = tr["tool"]
            n = tr["n"] or 0
            if not t:
                continue  # empty tool means reply / no tool call
            if t in _DOWNGRADE_VETO_TOOLS:
                vetoed = True
                break
            identified_tool_turns += n
            if t in _DOWNGRADE_SIMPLE_TOOLS:
                simple_tool_turns += n

        if vetoed:
            continue

        simple_ratio = (
            simple_tool_turns / identified_tool_turns
            if identified_tool_turns > 0
            else 1.0  # no tool calls at all = trivially "simple"
        )

        score = 0
        if avg_in < _DOWNGRADE_SMALL_INPUT_AVG and avg_out < _DOWNGRADE_SMALL_OUTPUT_AVG:
            score += 1
        if cc_ratio < _DOWNGRADE_CACHE_CREATION_RATIO:
            score += 1
        if simple_ratio >= _DOWNGRADE_SIMPLE_TOOL_RATIO:
            score += 1
        # 4th signal: a session with literally zero tool turns and short
        # turns is the textbook "chat about syntax" case haiku eats for breakfast.
        if identified_tool_turns == 0 and avg_in < _DOWNGRADE_SMALL_INPUT_AVG:
            score += 1
        elif identified_tool_turns > 0 and not vetoed:
            # Or, having tool turns at all with no veto is itself a signal
            # the session was hands-on engineering rather than ideation.
            score += 1

        if score < _DOWNGRADE_MIN_SCORE:
            continue

        current_cost = calc_cost(model, inp, out, cr, cc, cache_1h=cc1h)
        target_model = _suggest_target_model(model)
        projected_cost = calc_cost(target_model, inp, out, cr, cc, cache_1h=cc1h)
        savings = current_cost - projected_cost
        if savings < _DOWNGRADE_MIN_SAVINGS:
            continue

        suggestions.append({
            "session_id":      r["session_id"][:8],
            "session_id_full": r["session_id"],
            "project":         r["project_name"] or "unknown",
            "current_model":   model,
            "suggested_model": target_model,
            "current_cost":    round(current_cost, 4),
            "projected_cost":  round(projected_cost, 4),
            "savings_usd":     round(savings, 4),
            "score":           score,
            "turns":           turns,
            "avg_input":       round(avg_in, 1),
            "avg_output":      round(avg_out, 1),
        })

    suggestions.sort(key=lambda s: s["savings_usd"], reverse=True)
    return suggestions[:_DOWNGRADE_TOP_N]


def _plan_limits(conn):
    """Plan-utilization metrics: current 5h + weekly rolling windows per model,
    auto-detected caps from 30-day high-water marks, and a 48-hour 5h-sparkline.

    Caps are observational (max-ever-seen in the last 30 days), not Anthropic
    plan guesses — the user explicitly didn't want estimated message-count caps.
    All values are real: turns, billable tokens, USD cost equivalent."""
    from datetime import datetime, timedelta, timezone
    try:
        import pricing
        calc_cost = pricing.calc_cost
    except Exception:  # pricing module is optional in some forks
        calc_cost = lambda *a, **kw: 0.0  # noqa: E731

    rows = conn.execute("""
        SELECT timestamp,
               COALESCE(NULLIF(model, ''), 'unknown') AS model,
               COALESCE(input_tokens, 0)              AS input,
               COALESCE(output_tokens, 0)             AS output,
               COALESCE(cache_read_tokens, 0)         AS cache_read,
               COALESCE(cache_creation_tokens, 0)     AS cache_creation
        FROM turns
        WHERE timestamp IS NOT NULL AND length(timestamp) >= 19
        ORDER BY timestamp ASC
    """).fetchall()

    now = datetime.now(timezone.utc)
    horizon = now - timedelta(days=30)

    parsed = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < horizon:
            continue
        tokens = ((r["input"] or 0) + (r["output"] or 0)
                  + (r["cache_read"] or 0) + (r["cache_creation"] or 0))
        try:
            cost = calc_cost(r["model"], r["input"] or 0, r["output"] or 0,
                             r["cache_read"] or 0, r["cache_creation"] or 0)
        except Exception:
            cost = 0.0
        parsed.append((ts, r["model"], tokens, cost))

    if not parsed:
        return {"models": [], "overall": None, "computed_at": now.isoformat()}

    by_model = {}
    for ts, model, tokens, cost in parsed:
        by_model.setdefault(model, []).append((ts, tokens, cost))

    def window_stats(turns_list, start, end):
        in_w = [t for t in turns_list if start <= t[0] <= end]
        return {
            "turns":  len(in_w),
            "tokens": sum(t[1] for t in in_w),
            "cost":   round(sum(t[2] for t in in_w), 4),
        }

    def max_window(turns_list, hours):
        """Two-pointer sweep: highest billable-token sum in any `hours`-wide
        window across the chronological turns_list. Returns the window stats
        + when its rightmost turn occurred."""
        if not turns_list:
            return None
        w = timedelta(hours=hours)
        best = {"turns": 0, "tokens": 0, "cost": 0.0, "ends_at": None}
        left = 0
        run_turns = run_tokens = 0
        run_cost = 0.0
        for right in range(len(turns_list)):
            run_turns  += 1
            run_tokens += turns_list[right][1]
            run_cost   += turns_list[right][2]
            while turns_list[right][0] - turns_list[left][0] > w:
                run_turns  -= 1
                run_tokens -= turns_list[left][1]
                run_cost   -= turns_list[left][2]
                left += 1
            if run_tokens > best["tokens"]:
                best = {
                    "turns":   run_turns,
                    "tokens":  run_tokens,
                    "cost":    round(run_cost, 4),
                    "ends_at": turns_list[right][0].isoformat(),
                }
        return best

    models_out = []
    for model in sorted(by_model.keys()):
        turns_list = by_model[model]
        cur_5h = window_stats(turns_list, now - timedelta(hours=5), now)
        cur_7d = window_stats(turns_list, now - timedelta(days=7),  now)
        max_5h = max_window(turns_list, hours=5)
        max_7d = max_window(turns_list, hours=24 * 7)

        # 48h sparkline of 5h-rolling token totals, sampled every 30 minutes.
        spark = []
        step = timedelta(minutes=30)
        win  = timedelta(hours=5)
        cursor = now - timedelta(hours=48)
        while cursor <= now:
            ws = window_stats(turns_list, cursor - win, cursor)
            spark.append({
                "t":      cursor.isoformat(),
                "turns":  ws["turns"],
                "tokens": ws["tokens"],
                "cost":   ws["cost"],
            })
            cursor += step

        models_out.append({
            "model":         model,
            "current_5h":    cur_5h,
            "current_7d":    cur_7d,
            "max_5h_30d":    max_5h,
            "max_7d_30d":    max_7d,
            "sparkline_48h": spark,
        })

    all_turns = [(ts, tk, c) for ts, _m, tk, c in parsed]
    all_turns.sort(key=lambda x: x[0])
    overall = {
        "model":      "ALL",
        "current_5h": window_stats(all_turns, now - timedelta(hours=5), now),
        "current_7d": window_stats(all_turns, now - timedelta(days=7),  now),
        "max_5h_30d": max_window(all_turns, hours=5),
        "max_7d_30d": max_window(all_turns, hours=24 * 7),
    }

    return {
        "computed_at": now.isoformat(),
        "overall":     overall,
        "models":      models_out,
        "note":        "Caps auto-detected from your 30-day high-water marks (real data, not estimated plan limits).",
    }


def get_dashboard_data(db_path=DB_PATH):
    if not db_path.exists():
        return {"error": "Database not found. Run: python cli.py scan"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Make sure tier-aware columns exist even if scan hasn't run yet on this DB.
    import scanner
    scanner._migrate_schema(conn)

    # ── All models (for filter UI) ────────────────────────────────────────────
    model_rows = conn.execute("""
        SELECT COALESCE(NULLIF(model, ''), 'unknown') as model
        FROM turns
        GROUP BY model
        ORDER BY SUM(input_tokens + output_tokens) DESC
    """).fetchall()
    all_models = [r["model"] for r in model_rows]

    # ── Daily per-model, ALL history (client filters by range) ────────────────
    daily_rows = conn.execute("""
        SELECT
            substr(timestamp, 1, 10)                  as day,
            COALESCE(NULLIF(model, ''), 'unknown')     as model,
            SUM(input_tokens)          as input,
            SUM(output_tokens)         as output,
            SUM(cache_read_tokens)     as cache_read,
            SUM(cache_creation_tokens) as cache_creation,
            SUM(cache_1h_tokens) as cache_1h,
            COUNT(*)                   as turns
        FROM turns
        GROUP BY day, model
        ORDER BY day, model
    """).fetchall()

    daily_by_model = [{
        "day":            r["day"],
        "model":          r["model"],
        "input":          r["input"] or 0,
        "output":         r["output"] or 0,
        "cache_read":     r["cache_read"] or 0,
        "cache_creation": r["cache_creation"] or 0,
        "cache_1h":       r["cache_1h"] or 0,
        "turns":          r["turns"] or 0,
    } for r in daily_rows]

    # ── Hourly per-day per-model (client filters by range + TZ-shifts) ────────
    # Timestamps are ISO8601 UTC (e.g. "2026-04-08T09:30:00Z"); chars 12-13 = hour.
    hourly_rows = conn.execute("""
        SELECT
            substr(timestamp, 1, 10)                  as day,
            CAST(substr(timestamp, 12, 2) AS INTEGER) as hour,
            COALESCE(NULLIF(model, ''), 'unknown')    as model,
            SUM(output_tokens)                        as output,
            COUNT(*)                                  as turns
        FROM turns
        WHERE timestamp IS NOT NULL AND length(timestamp) >= 13
        GROUP BY day, hour, model
        ORDER BY day, hour, model
    """).fetchall()

    hourly_by_model = [{
        "day":    r["day"],
        "hour":   r["hour"] if r["hour"] is not None else 0,
        "model":  r["model"],
        "output": r["output"] or 0,
        "turns":  r["turns"] or 0,
    } for r in hourly_rows]

    # ── All sessions (client filters by range, model, and account) ────────────
    # session_name / account / machine_id may be missing on older DB schemas; fall back gracefully.
    try:
        session_rows = conn.execute("""
            SELECT
                session_id, project_name, first_timestamp, last_timestamp,
                total_input_tokens, total_output_tokens,
                total_cache_read, total_cache_creation, model, turn_count,
                git_branch, session_name,
                COALESCE(NULLIF(account, ''), 'default') AS account,
                machine_id
            FROM sessions
            ORDER BY last_timestamp DESC
        """).fetchall()
    except sqlite3.OperationalError:
        try:
            session_rows = conn.execute("""
                SELECT
                    session_id, project_name, first_timestamp, last_timestamp,
                    total_input_tokens, total_output_tokens,
                    total_cache_read, total_cache_creation, model, turn_count,
                    git_branch, session_name,
                    'default' AS account,
                    NULL AS machine_id
                FROM sessions
                ORDER BY last_timestamp DESC
            """).fetchall()
        except sqlite3.OperationalError:
            session_rows = conn.execute("""
                SELECT
                    session_id, project_name, first_timestamp, last_timestamp,
                    total_input_tokens, total_output_tokens,
                    total_cache_read, total_cache_creation, model, turn_count,
                    git_branch, NULL AS session_name,
                    'default' AS account,
                    NULL AS machine_id
                FROM sessions
                ORDER BY last_timestamp DESC
            """).fetchall()

    aliases = _load_project_aliases()

    # ── Tools (turns + tokens) per tool_name across all history ───────────────
    tool_rows = conn.execute("""
        SELECT
            COALESCE(NULLIF(tool_name, ''), '(no tool / direct turn)') as tool,
            timestamp,
            COUNT(*) as turns,
            SUM(input_tokens + output_tokens) as tokens
        FROM turns
        WHERE timestamp IS NOT NULL
        GROUP BY DATE(timestamp), tool
        ORDER BY timestamp ASC
    """).fetchall()
    tools_daily = [
        {"day": (r["timestamp"] or "")[:10], "tool": r["tool"],
         "turns": r["turns"] or 0, "tokens": r["tokens"] or 0}
        for r in tool_rows
    ]
    cache_hit = _cache_hit_analysis(conn)
    cache_hit_per_session = cache_hit["per_session"]

    # Pre-fetch per-session turn timestamps so we can compute active_minutes
    # (sum of inter-turn gaps under the break threshold) without N round-trips.
    ts_rows = conn.execute(
        """SELECT session_id, timestamp FROM turns
           WHERE timestamp IS NOT NULL
           ORDER BY session_id, timestamp"""
    ).fetchall()
    session_ts = {}
    for r in ts_rows:
        session_ts.setdefault(r["session_id"], []).append(r["timestamp"])

    sessions_all = []
    for r in session_rows:
        try:
            t1 = datetime.fromisoformat(r["first_timestamp"].replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(r["last_timestamp"].replace("Z", "+00:00"))
            duration_min = round((t2 - t1).total_seconds() / 60, 1)
        except Exception:
            duration_min = 0
        raw_project = r["project_name"] or "unknown"
        ch = cache_hit_per_session.get(r["session_id"], {})
        active_minutes = _session_active_minutes(session_ts.get(r["session_id"], []))
        sessions_all.append({
            "session_id":      r["session_id"][:8],
            "session_id_full": r["session_id"],
            "session_name":    r["session_name"] or "",
            "project":         raw_project,
            "display_name":    aliases.get(raw_project, ""),
            "branch":          r["git_branch"] or "",
            "first":           (r["first_timestamp"] or "")[:16].replace("T", " "),
            "last":            (r["last_timestamp"] or "")[:16].replace("T", " "),
            "last_date":     (r["last_timestamp"] or "")[:10],
            "duration_min":  duration_min,
            "active_minutes": active_minutes,
            "model":         r["model"] or "unknown",
            "turns":         r["turn_count"] or 0,
            "input":         r["total_input_tokens"] or 0,
            "output":        r["total_output_tokens"] or 0,
            "cache_read":    r["total_cache_read"] or 0,
            "cache_creation": r["total_cache_creation"] or 0,
            "cache_hit_ratio":     ch.get("ratio", 0.0),
            "cache_hit_category":  ch.get("category", "low"),
            "cache_underusing":    ch.get("underusing", False),
            "account":       r["account"] or "default",
            "machine_id":    (r["machine_id"] if "machine_id" in r.keys() else None) or "local",
        })

    year_calendar = _year_calendar(conn)
    forecast = _forecast(_daily_cost_history(conn))
    # Month-to-date cost for budget watchdog. Pulls current calendar month's
    # spend using current PRICING (estimate; matches what the dashboard shows).
    from pricing import get_pricing as _gp
    from datetime import date as _date
    month_prefix = _date.today().strftime("%Y-%m")
    mtd_rows = conn.execute("""
        SELECT model,
               SUM(input_tokens) as inp, SUM(output_tokens) as out,
               SUM(cache_read_tokens) as cr, SUM(cache_creation_tokens) as cw
        FROM turns
        WHERE substr(timestamp, 1, 7) = ?
        GROUP BY model
    """, (month_prefix,)).fetchall()
    month_to_date_usd = 0.0
    for r in mtd_rows:
        p = _gp(r["model"])
        if not p:
            continue
        month_to_date_usd += ((r["inp"] or 0) * p["input"]
                              + (r["out"] or 0) * p["output"]
                              + (r["cr"] or 0) * p["cache_read"]
                              + (r["cw"] or 0) * p["cache_write"]) / 1_000_000
    budget_status_data = _budget_status(month_to_date_usd)

    anomaly = _anomaly_check(conn)
    _tags_map = _load_tags()
    for s in sessions_all:
        s["tags"] = _tags_map.get(s["session_id"], [])
    dow_hour = _dow_hour_heatmap(conn)
    cost_histogram = _cost_per_turn_stats(conn)

    # Plan comparison — reuse mtd computation for plan recommendation.
    plan_recommendation = _plan_comparison(month_to_date_usd)
    # Per-project month-to-date for budget alerts
    from pricing import get_pricing as _gp_pb
    from datetime import date as _dt_pb
    _mp_pb = _dt_pb.today().strftime("%Y-%m")
    _per_proj = {}
    for r in conn.execute("""
        SELECT s.project_name as project, t.model,
               SUM(t.input_tokens) as inp, SUM(t.output_tokens) as out,
               SUM(t.cache_read_tokens) as cr, SUM(t.cache_creation_tokens) as cw
        FROM turns t JOIN sessions s ON s.session_id = t.session_id
        WHERE substr(t.timestamp, 1, 7) = ?
        GROUP BY s.project_name, t.model
    """, (_mp_pb,)).fetchall():
        _p = _gp_pb(r["model"])
        if not _p:
            continue
        _c = ((r["inp"] or 0) * _p["input"] + (r["out"] or 0) * _p["output"]
              + (r["cr"] or 0) * _p["cache_read"] + (r["cw"] or 0) * _p["cache_write"]) / 1_000_000
        _per_proj[r["project"]] = _per_proj.get(r["project"], 0) + _c
    project_budgets = _project_budget_status(_per_proj)
    subagent_split = _subagent_split(conn)
    # Sparkline data per session (small turn-rate histogram for the UI).
    _sparkline_data = _session_sparklines(
        conn, [s["session_id"] for s in sessions_all]
    )
    for s in sessions_all:
        s["sparkline"] = _sparkline_data.get(s["session_id"], [])
    streak = _compute_streak(conn)
    # Sensitive-content scan: SOFT warning, runs locally, no data leaves the box.
    _pii_patterns = _load_pii_patterns()
    for s in sessions_all:
        haystack = f"{s.get('project', '')} {s.get('branch', '')}"
        s["sensitive_match"] = _pii_check(haystack, _pii_patterns)
    downgrade_suggestions = _downgrade_suggestions(conn)
    downgrade_total_savings = round(
        sum(s["savings_usd"] for s in downgrade_suggestions), 2
    )
    cache_1h_opportunities = _cache_1h_opportunities(conn)

    # ── By-machine aggregation (workspace / team mode) ────────────────────────
    # When every session was scanned on a single laptop this is a one-row list
    # and the dashboard auto-hides the filter UI. In a shared-DB setup it gives
    # a per-machine token / cost rollup.
    machine_agg = {}
    for s in sessions_all:
        mid = s.get("machine_id") or "local"
        m = machine_agg.setdefault(mid, {
            "machine_id": mid, "sessions": 0, "turns": 0,
            "input": 0, "output": 0,
            "cache_read": 0, "cache_creation": 0,
        })
        m["sessions"] += 1
        m["turns"] += s["turns"] or 0
        m["input"] += s["input"] or 0
        m["output"] += s["output"] or 0
        m["cache_read"] += s["cache_read"] or 0
        m["cache_creation"] += s["cache_creation"] or 0
    by_machine = sorted(
        machine_agg.values(),
        key=lambda x: (-(x["input"] + x["output"]), x["machine_id"]),
    )
    all_machines = [m["machine_id"] for m in by_machine]

    time_on_task = _time_on_task(conn)
    plan_limits = _plan_limits(conn)
    conn.close()

    # ── Account summary (sessions + tokens per account, all-time) ─────────────
    # Cost can't be computed here without duplicating the JS pricing table; the
    # client recomputes per-account cost after filtering. We include token totals
    # so an integration consuming the JSON has something useful out of the box.
    accounts_map = {}
    for s in sessions_all:
        acct = s.get("account") or "default"
        a = accounts_map.setdefault(acct, {"name": acct, "sessions": 0, "tokens": 0})
        a["sessions"] += 1
        a["tokens"] += (s["input"] + s["output"] + s["cache_read"] + s["cache_creation"])
    accounts = sorted(accounts_map.values(), key=lambda x: (-x["sessions"], x["name"]))

    return {
        "all_models":      all_models,
        "all_machines":    all_machines,
        "daily_by_model":  daily_by_model,
        "hourly_by_model": hourly_by_model,
        "sessions_all":    sessions_all,
        "year_calendar":   year_calendar,
        "tools_daily":     tools_daily,
        "forecast":        forecast,
        "budget":          budget_status_data,
        "anomaly":         anomaly,
        "dow_hour":        dow_hour,
        "cost_histogram":  cost_histogram,
        "project_budgets": project_budgets,
        "subagent_split":  subagent_split,
        "streak":          streak,
        "downgrade_suggestions":   downgrade_suggestions,
        "downgrade_total_savings": downgrade_total_savings,
        "accounts":        accounts,
        "by_machine":      by_machine,
        "time_on_task":    time_on_task,
        "plan_limits":     plan_limits,
        "generated_at":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "plan_recommendation": plan_recommendation,
        "generated_at":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "all_models":        all_models,
        "daily_by_model":    daily_by_model,
        "hourly_by_model":   hourly_by_model,
        "sessions_all":      sessions_all,
        "cache_hit_summary": cache_hit["summary"],
        "generated_at":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "all_models":             all_models,
        "daily_by_model":         daily_by_model,
        "hourly_by_model":        hourly_by_model,
        "sessions_all":           sessions_all,
        "cache_1h_opportunities": cache_1h_opportunities,
        "generated_at":           datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "all_models":       all_models,
        "daily_by_model":   daily_by_model,
        "hourly_by_model":  hourly_by_model,
        "sessions_all":     sessions_all,
        "git_trace_recent": _load_git_trace(limit=50),
        "generated_at":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def get_text_stats(db_path=None, now=None):
    """Return a flat dict of single-number stats for the AppleScript / text endpoints.

    Keys: today_cost, month_cost, active_sessions, budget_pct.
    All numeric, no nesting — kept boring so /api/text/* can stringify directly.

    - Costs are in USD and computed per-model with ``calc_cost``.
    - "active_sessions" = sessions whose last turn was within the last 5h
      (Claude Code's rate-limit window — what "active" means in the UI).
    - "budget_pct" reads ``CLAUDE_USAGE_MONTHLY_BUDGET`` (USD) from env; if unset
      or 0, returns 0 so callers can render "n/a" without special-casing.
    """
    db_path = db_path or DB_PATH
    now = now or datetime.now(timezone.utc)
    today_iso = now.strftime("%Y-%m-%d")
    month_iso = now.strftime("%Y-%m")
    cutoff_iso = (now - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    result = {"today_cost": 0.0, "month_cost": 0.0, "active_sessions": 0, "budget_pct": 0}
    if not db_path.exists():
        return result

    conn = sqlite3.connect(db_path)
    try:
        # Per-model token sums for today + month-to-date. Doing the cost math in
        # Python (not SQL) keeps the single source of truth in pricing.calc_cost.
        for day_filter, key in ((today_iso, "today_cost"), (month_iso, "month_cost")):
            rows = conn.execute("""
                SELECT COALESCE(NULLIF(model, ''), 'unknown') AS model,
                       SUM(input_tokens)          AS inp,
                       SUM(output_tokens)         AS out,
                       SUM(cache_read_tokens)     AS cr,
                       SUM(cache_creation_tokens) AS cc,
                       SUM(COALESCE(cache_1h_tokens, 0)) AS c1h
                FROM turns
                WHERE substr(timestamp, 1, ?) = ?
                GROUP BY model
            """, (len(day_filter), day_filter)).fetchall()
            total = 0.0
            for model, inp, out, cr, cc, c1h in rows:
                total += calc_cost(model, inp or 0, out or 0, cr or 0, cc or 0, cache_1h=c1h or 0)
            result[key] = round(total, 2)

        # Active = a session touched in the last 5h (Claude Code's rate window).
        row = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE last_timestamp >= ?",
            (cutoff_iso,),
        ).fetchone()
        result["active_sessions"] = int(row[0] or 0)
    finally:
        conn.close()

    try:
        budget = float(os.environ.get("CLAUDE_USAGE_MONTHLY_BUDGET", "0") or 0)
    except ValueError:
        budget = 0.0
    if budget > 0:
        result["budget_pct"] = int(round(result["month_cost"] / budget * 100))

    return result


def get_session_detail(session_id, db_path=DB_PATH):
    if not db_path.exists():
        return {"error": "Database not found. Run: python3 cli.py scan"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    session = conn.execute("""
        SELECT
            session_id, project_name, first_timestamp, last_timestamp, git_branch,
            total_input_tokens, total_output_tokens,
            total_cache_read, total_cache_creation, model, turn_count
        FROM sessions
        WHERE session_id = ?
    """, (session_id,)).fetchone()

    if session is None:
        conn.close()
        return {"error": "Session not found"}

    turn_rows = conn.execute("""
        SELECT
            timestamp, model, input_tokens, output_tokens,
            cache_read_tokens, cache_creation_tokens, cache_1h_tokens, tool_name, cwd
        FROM turns
        WHERE session_id = ?
        ORDER BY timestamp ASC, id ASC
    """, (session_id,)).fetchall()

    turns = []
    tool_usage = {}
    cwd_counts = {}

    for r in turn_rows:
        tool_name = r["tool_name"] or "reply"
        cwd = r["cwd"] or "unknown"
        total_tokens = (
            (r["input_tokens"] or 0) +
            (r["output_tokens"] or 0) +
            (r["cache_read_tokens"] or 0) +
            (r["cache_creation_tokens"] or 0) +
            (r["cache_1h_tokens"] or 0)
        )
        turns.append({
            "timestamp":       r["timestamp"] or "",
            "timestamp_short": (r["timestamp"] or "")[:16].replace("T", " "),
            "model":           r["model"] or "unknown",
            "tool_name":       tool_name,
            "cwd":             cwd,
            "input":           r["input_tokens"] or 0,
            "output":          r["output_tokens"] or 0,
            "cache_read":      r["cache_read_tokens"] or 0,
            "cache_creation":  r["cache_creation_tokens"] or 0,
            "cache_1h":        r["cache_1h_tokens"] or 0,
            "total":           total_tokens,
        })

        stats = tool_usage.setdefault(tool_name, {"tool_name": tool_name, "turns": 0, "tokens": 0})
        stats["turns"] += 1
        stats["tokens"] += total_tokens
        cwd_counts[cwd] = cwd_counts.get(cwd, 0) + 1

    conn.close()

    try:
        t1 = datetime.fromisoformat((session["first_timestamp"] or "").replace("Z", "+00:00"))
        t2 = datetime.fromisoformat((session["last_timestamp"] or "").replace("Z", "+00:00"))
        duration_min = round((t2 - t1).total_seconds() / 60, 1)
    except Exception:
        duration_min = 0

    raw_project = session["project_name"] or "unknown"
    aliases = _load_project_aliases()
    return {
        "session_id":     session["session_id"],
        "project":        raw_project,
        "display_name":   aliases.get(raw_project, ""),
        "branch":         session["git_branch"] or "",
        "first":          (session["first_timestamp"] or "")[:19].replace("T", " "),
        "last":           (session["last_timestamp"] or "")[:19].replace("T", " "),
        "duration_min":   duration_min,
        "model":          session["model"] or "unknown",
        "turns":          session["turn_count"] or 0,
        "input":          session["total_input_tokens"] or 0,
        "output":         session["total_output_tokens"] or 0,
        "cache_read":     session["total_cache_read"] or 0,
        "cache_creation": session["total_cache_creation"] or 0,
        "tool_usage":     sorted(tool_usage.values(), key=lambda item: (-item["tokens"], item["tool_name"])),
        "cwd_usage":      sorted(
            [{"cwd": c, "turns": n} for c, n in cwd_counts.items()],
            key=lambda item: (-item["turns"], item["cwd"])
        ),
        "turn_history":   turns,
    }


GALLERY_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Appearance — Claude Usage Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #f5f5f7; font-family: -apple-system, "SF Pro Text", "Helvetica Neue", Helvetica, Arial, sans-serif; font-size: 14px; color: #1d1d1f; }

  .g-header { position: sticky; top: 0; z-index: 100; background: rgba(255,255,255,0.85); backdrop-filter: saturate(180%) blur(20px); -webkit-backdrop-filter: saturate(180%) blur(20px); border-bottom: 1px solid rgba(0,0,0,0.08); padding: 0 32px; height: 52px; display: flex; align-items: center; gap: 16px; }
  .g-back { background: none; border: none; cursor: pointer; font-size: 17px; color: #0071e3; padding: 0 8px 0 0; letter-spacing: -0.374px; }
  .g-back:hover { text-decoration: underline; }
  .g-title { font-size: 17px; font-weight: 600; letter-spacing: -0.374px; flex: 1; }
  .g-search { background: rgba(0,0,0,0.06); border: none; border-radius: 8px; padding: 6px 12px; font-size: 13px; width: 220px; color: #1d1d1f; outline: none; letter-spacing: -0.12px; }
  .g-search:focus { background: rgba(0,0,0,0.09); }

  .g-modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 1000; align-items: center; justify-content: center; }
  .g-modal-overlay.visible { display: flex; }
  .g-modal { background: #fff; border-radius: 16px; padding: 32px 40px; text-align: center; box-shadow: 0 20px 60px rgba(0,0,0,0.2); min-width: 280px; }
  .g-modal-icon { font-size: 40px; margin-bottom: 12px; }
  .g-modal-title { font-size: 17px; font-weight: 600; letter-spacing: -0.374px; margin-bottom: 8px; }
  .g-modal-sub { font-size: 13px; color: rgba(0,0,0,0.48); letter-spacing: -0.12px; }

  .g-body { max-width: 1200px; margin: 0 auto; padding: 40px 32px 64px; }
  .g-section-header { display: flex; align-items: baseline; gap: 12px; margin-bottom: 20px; }
  .g-section-title { font-size: 21px; font-weight: 600; letter-spacing: -0.28px; }
  .g-section-hint { font-size: 13px; color: rgba(0,0,0,0.48); letter-spacing: -0.12px; }
  .g-divider { margin: 48px 0 32px; border: none; border-top: 1px solid rgba(0,0,0,0.08); }

  .g-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 20px; }

  .theme-card { background: #ffffff; border-radius: 14px; overflow: hidden; box-shadow: 0px 2px 12px rgba(0,0,0,0.08); transition: transform 0.18s, box-shadow 0.18s; display: flex; flex-direction: column; }
  .theme-card:hover { transform: translateY(-3px); box-shadow: 0px 8px 28px rgba(0,0,0,0.13); }
  .theme-card.is-active { box-shadow: 0px 0px 0px 2px #0071e3, 0px 8px 28px rgba(0,113,227,0.18); }

  .theme-preview { height: 152px; padding: 10px; overflow: hidden; position: relative; }
  .theme-preview.unavailable { filter: grayscale(1); opacity: 0.45; }
  .prev-shell { height: 100%; border-radius: 8px; overflow: hidden; display: flex; flex-direction: column; }
  .prev-topbar { height: 18px; display: flex; align-items: center; padding: 0 8px; gap: 5px; flex-shrink: 0; }
  .prev-topbar-title { height: 5px; width: 48px; border-radius: 3px; opacity: 0.5; }
  .prev-topbar-dot { width: 12px; height: 12px; border-radius: 50%; margin-left: auto; }
  .prev-filterbar { height: 14px; display: flex; align-items: center; padding: 0 8px; gap: 3px; flex-shrink: 0; }
  .prev-pill { height: 7px; border-radius: 4px; }
  .prev-body { flex: 1; padding: 6px 8px; display: flex; flex-direction: column; gap: 5px; overflow: hidden; }
  .prev-stats { display: flex; gap: 4px; }
  .prev-stat { flex: 1; border-radius: 5px; height: 22px; }
  .prev-chart { flex: 1; border-radius: 5px; padding: 5px 6px; display: flex; align-items: flex-end; gap: 2px; }
  .prev-bar { flex: 1; border-radius: 2px; }

  .theme-info { padding: 14px 16px 16px; border-top: 1px solid rgba(0,0,0,0.06); display: flex; flex-direction: column; gap: 8px; }
  .theme-name { font-size: 14px; font-weight: 600; letter-spacing: -0.224px; }
  .theme-meta { display: flex; align-items: center; gap: 6px; }
  .theme-category { font-size: 11px; color: rgba(0,0,0,0.48); letter-spacing: -0.08px; }
  .theme-badge { font-size: 10px; padding: 1px 7px; border-radius: 980px; letter-spacing: -0.08px; }
  .badge-active { background: rgba(0,113,227,0.1); color: #0071e3; }
  .badge-bundled { background: rgba(0,0,0,0.06); color: rgba(0,0,0,0.48); }
  .btn-apply { background: #0071e3; color: #fff; border: none; border-radius: 6px; padding: 6px 16px; font-size: 13px; font-weight: 500; cursor: pointer; letter-spacing: -0.12px; transition: background 0.15s; }
  .btn-apply:hover { background: #0077ed; }
  .btn-applied { background: rgba(0,113,227,0.1); color: #0071e3; border: none; border-radius: 6px; padding: 6px 16px; font-size: 13px; font-weight: 500; cursor: default; letter-spacing: -0.12px; }
  .btn-generate { background: transparent; color: rgba(0,0,0,0.4); border: 1px solid rgba(0,0,0,0.15); border-radius: 6px; padding: 6px 16px; font-size: 13px; cursor: default; letter-spacing: -0.12px; }
  .theme-cmd { font-family: "SF Mono", ui-monospace, monospace; font-size: 10px; color: rgba(0,0,0,0.35); margin-top: 2px; }

  .g-empty { color: rgba(0,0,0,0.4); font-size: 14px; padding: 24px 0; }

  @media (max-width: 600px) { .g-body { padding: 24px 16px; } .g-header { padding: 0 16px; } }
</style>
</head>
<body>


<div class="g-header">
  <button class="g-back" onclick="window.close()">← Back</button>
  <div class="g-title">Appearance</div>
  <input class="g-search" type="search" placeholder="Search themes…" oninput="filterThemes(this.value)">
</div>
<div class="g-modal-overlay" id="applied-modal">
  <div class="g-modal">
    <div class="g-modal-icon">✓</div>
    <div class="g-modal-title" id="modal-theme-name">Theme applied</div>
    <div class="g-modal-sub" id="modal-countdown">Closing in 3…</div>
  </div>
</div>
<div class="g-body">
  <div class="g-section-header">
    <div class="g-section-title">Installed</div>
  </div>
  <div class="g-grid" id="installed-grid"><div class="g-empty">Loading…</div></div>

  <hr class="g-divider">

  <div class="g-section-header">
    <div class="g-section-title">Available</div>
    <div class="g-section-hint">Run <code>python cli.py theme add &lt;id&gt;</code> to generate and install</div>
  </div>
  <div class="g-grid" id="available-grid"></div>
</div>

<script>
const CATALOG = __CATALOG_JSON__;
let allInstalled = [];
let activeThemeId = localStorage.getItem('dashboard-theme-id') || 'apple';

async function init() {
  const resp = await fetch('/api/themes');
  allInstalled = await resp.json();
  render('');
}

function render(query) {
  const q = query.toLowerCase();
  const installedIds = new Set(allInstalled.map(t => t.id));

  const matchInstalled = allInstalled.filter(t =>
    !q || t.name.toLowerCase().includes(q) || t.category.toLowerCase().includes(q)
  );
  const matchAvailable = CATALOG.filter(t =>
    !installedIds.has(t.id) &&
    (!q || t.name.toLowerCase().includes(q) || t.category.toLowerCase().includes(q))
  );

  document.getElementById('installed-grid').innerHTML =
    matchInstalled.length ? matchInstalled.map(t => cardHTML(t, true)).join('') : '<div class="g-empty">No installed themes match.</div>';
  document.getElementById('available-grid').innerHTML =
    matchAvailable.length ? matchAvailable.map(t => cardHTML(t, false)).join('') : '<div class="g-empty">No available themes match.</div>';
}

function filterThemes(q) { render(q); }

function cardHTML(t, installed) {
  const isActive = t.id === activeThemeId;
  const preview = t.preview ? previewHTML(t) : unavailablePreviewHTML(t);
  const badge = isActive
    ? '<span class="theme-badge badge-active">Active</span>'
    : (t.bundled ? '<span class="theme-badge badge-bundled">Bundled</span>' : '');
  const btn = !installed
    ? `<button class="btn-generate" disabled>Generate</button><div class="theme-cmd">python cli.py theme add ${t.id}</div>`
    : isActive
    ? `<button class="btn-applied">Applied ✓</button>`
    : `<button class="btn-apply" onclick="applyTheme('${t.id}')">Apply</button>`;

  return `<div class="theme-card${isActive ? ' is-active' : ''}" id="card-${t.id}">
    <div class="theme-preview${!installed ? ' unavailable' : ''}">${preview}</div>
    <div class="theme-info">
      <div>
        <div class="theme-name">${t.name}</div>
        <div class="theme-meta"><span class="theme-category">${t.category}</span>${badge}</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:4px">${btn}</div>
    </div>
  </div>`;
}

function previewHTML(t) {
  const p = t.preview;
  const bars = [40, 70, 55, 85, 60, 90, 75].map(h =>
    `<div class="prev-bar" style="height:${h}%;background:${p.accent};opacity:0.75"></div>`
  ).join('');
  return `<div class="prev-shell" style="background:${p.bg}">
    <div class="prev-topbar" style="background:${p.card}">
      <div class="prev-topbar-title" style="background:${p.text}"></div>
      <div class="prev-topbar-dot" style="background:${p.accent}"></div>
    </div>
    <div class="prev-filterbar" style="background:${p.card};border-bottom:1px solid ${p.muted}">
      <div class="prev-pill" style="width:28px;background:${p.accent}"></div>
      <div class="prev-pill" style="width:20px;background:${p.muted}"></div>
      <div class="prev-pill" style="width:20px;background:${p.muted}"></div>
    </div>
    <div class="prev-body" style="background:${p.bg}">
      <div class="prev-stats">
        <div class="prev-stat" style="background:${p.card}"></div>
        <div class="prev-stat" style="background:${p.card}"></div>
        <div class="prev-stat" style="background:${p.card}"></div>
      </div>
      <div class="prev-chart" style="background:${p.card}">${bars}</div>
    </div>
  </div>`;
}

function unavailablePreviewHTML(t) {
  return `<div class="prev-shell" style="background:#e8e8e8">
    <div class="prev-topbar" style="background:#d0d0d0"></div>
    <div class="prev-body" style="background:#e8e8e8">
      <div class="prev-stats">
        <div class="prev-stat" style="background:#d0d0d0"></div>
        <div class="prev-stat" style="background:#d0d0d0"></div>
        <div class="prev-stat" style="background:#d0d0d0"></div>
      </div>
      <div class="prev-chart" style="background:#d0d0d0;height:60px"></div>
    </div>
  </div>`;
}

function applyTheme(id) {
  const t = allInstalled.find(x => x.id === id);
  if (!t) return;
  localStorage.setItem('dashboard-theme-id', id);
  localStorage.setItem('dashboard-theme-css', t.css);
  activeThemeId = id;
  if (window.opener && !window.opener.closed) {
    try { window.opener.setTheme(t.css, id); } catch(e) {}
  }
  render(document.querySelector('.g-search').value);

  // Show confirmation modal then auto-close
  const modal = document.getElementById('applied-modal');
  const countdownEl = document.getElementById('modal-countdown');
  document.getElementById('modal-theme-name').textContent = t.name + ' applied';
  modal.classList.add('visible');
  let secs = 3;
  countdownEl.textContent = 'Closing in ' + secs + '\u2026';
  const iv = setInterval(() => {
    secs--;
    if (secs > 0) {
      countdownEl.textContent = 'Closing in ' + secs + '\u2026';
    } else {
      clearInterval(iv);
      window.close();
    }
  }, 1000);
}

init();
</script>
</body>
</html>
"""

# ── PWA assets ─────────────────────────────────────────────────────────────────
# Bar-chart glyph in the Apple-theme accent blue. Used as the manifest icon and
# as both <link rel="icon"> and apple-touch-icon. SVG is fine for PWA installs
# in modern Chrome/Edge/Safari — no separate PNG raster is needed.
APP_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="96" fill="#0071e3"/>
  <rect x="112" y="288" width="64" height="128" rx="12" fill="#ffffff"/>
  <rect x="208" y="208" width="64" height="208" rx="12" fill="#ffffff"/>
  <rect x="304" y="144" width="64" height="272" rx="12" fill="#ffffff"/>
  <rect x="96"  y="416" width="320" height="16" rx="8" fill="rgba(255,255,255,0.7)"/>
</svg>"""

# Minimal service worker. The only "smart" behavior is a stale-while-revalidate
# cache for the Chart.js CDN bundle so a flaky network doesn't break dashboard
# boot. All other requests (including /api/*) go straight to the network.
SERVICE_WORKER_JS = """// Claude Code Usage Dashboard — minimal PWA service worker.
const CACHE_NAME = "claude-usage-static-v1";
const STATIC_URLS = [
  "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_URLS).catch(() => {}))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  // Stale-while-revalidate for the Chart.js CDN bundle.
  if (STATIC_URLS.includes(url.href)) {
    event.respondWith(
      caches.open(CACHE_NAME).then(async (cache) => {
        const cached = await cache.match(req);
        const network = fetch(req).then((res) => {
          if (res && res.status === 200) cache.put(req, res.clone());
          return res;
        }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }

  // Everything else: plain network passthrough.
  event.respondWith(fetch(req).catch(() => caches.match(req)));
});
"""


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Code Usage Dashboard</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0071e3">
<link rel="icon" type="image/svg+xml" href="/icon.svg">
<link rel="apple-touch-icon" href="/icon.svg">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Claude Usage">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js" integrity="sha384-e6nUZLBkQ86NJ6TVVKAeSaK8jWa3NhkYWZFomE39AvDbQWeie9PlQqM3pmYW5d1g" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<style id="active-theme">:root {
    --bg: #f5f5f7; --card: #ffffff; --border: rgba(0,0,0,0.08);
    --text: #1d1d1f; --muted: rgba(0,0,0,0.48); --accent: #0071e3;
    --green: #1c7a3a; --shadow: 0px 2px 12px rgba(0,0,0,0.08);
    --card-radius: 14px; --card-border: none;
    --chart-label: rgba(0,0,0,0.48); --chart-grid: rgba(0,0,0,0.06);
    --chart-1: rgba(0,113,227,0.8); --chart-2: rgba(88,86,214,0.8);
    --chart-3: rgba(52,199,89,0.8); --chart-4: rgba(255,159,10,0.75);
  }</style>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, "SF Pro Text", "Helvetica Neue", Helvetica, Arial, sans-serif; font-size: 14px; letter-spacing: -0.224px; }

  header { position: sticky; top: 0; z-index: 100; background: rgba(255,255,255,0.85); backdrop-filter: saturate(180%) blur(20px); -webkit-backdrop-filter: saturate(180%) blur(20px); border-bottom: 1px solid var(--border); padding: 0 24px; height: 48px; display: flex; align-items: center; justify-content: space-between; }
  header h1 { font-size: 17px; font-weight: 600; color: var(--text); letter-spacing: -0.374px; }
  header .meta { color: var(--muted); font-size: 12px; letter-spacing: -0.12px; }
  .streak-badge { display: inline-flex; align-items: center; gap: 4px; background: rgba(255,159,10,0.12); color: #d97706; border: 1px solid rgba(255,159,10,0.25); border-radius: 980px; padding: 2px 10px; font-size: 12px; font-weight: 600; letter-spacing: -0.12px; line-height: 1.4; white-space: nowrap; }
  .streak-badge[hidden] { display: none; }
  .appearance-btn { background: transparent; border: 1px solid var(--border); border-radius: 6px; color: var(--muted); font-size: 12px; padding: 4px 12px; cursor: pointer; letter-spacing: -0.12px; transition: all 0.15s; white-space: nowrap; }
  .appearance-btn:hover { border-color: var(--accent); color: var(--accent); }
  select.appearance-btn { font-family: inherit; appearance: none; -webkit-appearance: none; padding-right: 22px; background-image: linear-gradient(45deg, transparent 50%, var(--muted) 50%), linear-gradient(135deg, var(--muted) 50%, transparent 50%); background-position: calc(100% - 12px) 50%, calc(100% - 7px) 50%; background-size: 5px 5px, 5px 5px; background-repeat: no-repeat; }
  select.appearance-btn:hover { color: var(--accent); }
  .link-btn { background: transparent; border: none; color: var(--muted); cursor: pointer; font-size: 11px; padding: 4px 8px; }
  .link-btn:hover { color: var(--text); text-decoration: underline; }
  #rescan-btn { background: var(--card); border: 1px solid var(--border); color: var(--muted); padding: 4px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; }
  #rescan-btn:hover { color: var(--text); border-color: var(--accent); }
  #rescan-btn:disabled { opacity: 0.5; cursor: not-allowed; }

  #filter-bar { background: rgba(255,255,255,0.85); backdrop-filter: saturate(180%) blur(20px); -webkit-backdrop-filter: saturate(180%) blur(20px); border-bottom: 1px solid var(--border); padding: 10px 24px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .filter-label { font-size: 12px; font-weight: 600; letter-spacing: -0.12px; color: var(--muted); white-space: nowrap; }
  .filter-sep { width: 1px; height: 22px; background: rgba(0,0,0,0.12); flex-shrink: 0; }
  #model-checkboxes { display: flex; flex-wrap: wrap; gap: 6px; }
  .model-cb-label { display: flex; align-items: center; gap: 5px; padding: 4px 12px; border-radius: 980px; border: 1px solid rgba(0,0,0,0.12); cursor: pointer; font-size: 12px; color: var(--muted); letter-spacing: -0.12px; transition: all 0.15s; user-select: none; }
  .model-cb-label:hover { border-color: var(--accent); color: var(--accent); }
  .model-cb-label.checked { background: rgba(0,113,227,0.08); border-color: var(--accent); color: var(--accent); font-weight: 500; }
  .model-cb-label input { display: none; }
  .filter-btn { padding: 4px 12px; border-radius: 980px; border: 1px solid rgba(0,0,0,0.12); background: transparent; color: var(--muted); font-size: 12px; cursor: pointer; white-space: nowrap; letter-spacing: -0.12px; transition: all 0.15s; }
  .filter-btn:hover { border-color: var(--accent); color: var(--accent); }
  .range-group { display: flex; border: 1px solid rgba(0,0,0,0.12); border-radius: 8px; overflow: hidden; flex-shrink: 0; background: var(--card); }
  .range-btn { padding: 5px 14px; background: transparent; border: none; border-right: 1px solid rgba(0,0,0,0.08); color: var(--muted); font-size: 12px; cursor: pointer; letter-spacing: -0.12px; transition: background 0.15s, color 0.15s; }
  .range-btn:last-child { border-right: none; }
  .range-btn:hover { background: rgba(0,113,227,0.05); color: var(--text); }
  .range-btn.active { background: var(--accent); color: #ffffff; font-weight: 500; }
  #custom-range { display: none; align-items: center; gap: 6px; }
  #custom-range.visible { display: flex; }
  #custom-range input[type="date"] { background: var(--card); border: 1px solid rgba(0,0,0,0.12); border-radius: 8px; color: var(--text); font-size: 12px; padding: 4px 10px; cursor: pointer; letter-spacing: -0.12px; font-family: -apple-system, sans-serif; }
  #custom-range input[type="date"]:focus { outline: 2px solid var(--accent); border-color: transparent; }
  #custom-range .range-sep { color: var(--muted); font-size: 12px; }

  .container { max-width: 1200px; margin: 0 auto; padding: 32px 24px; }
  .stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 24px; }
  /* Plan utilization (5h + weekly) */
  .plan-limits-card { background: var(--card); border-radius: var(--card-radius); border: var(--card-border); padding: 20px; margin-bottom: 24px; box-shadow: var(--shadow); }
  .pl-header { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 16px; gap: 12px; flex-wrap: wrap; }
  .pl-header h2 { font-size: 15px; font-weight: 600; letter-spacing: -0.24px; color: var(--text); margin: 0; }
  .pl-note { color: var(--muted); font-size: 11px; }
  .pl-rows { display: flex; flex-direction: column; gap: 14px; margin-bottom: 18px; }
  .pl-row { display: grid; grid-template-columns: minmax(180px, 1fr) repeat(2, minmax(0, 2fr)); gap: 16px; align-items: center; }
  .pl-row-label { font-size: 13px; font-weight: 500; color: var(--text); }
  .pl-row-label .pl-sub { color: var(--muted); font-size: 11px; font-weight: 400; margin-top: 2px; display: block; }
  .pl-bar-wrap { position: relative; }
  .pl-bar-meta { display: flex; justify-content: space-between; font-size: 11px; color: var(--muted); margin-bottom: 4px; }
  .pl-bar { height: 8px; background: var(--chart-grid); border-radius: 4px; overflow: hidden; }
  .pl-bar-fill { height: 100%; border-radius: 4px; transition: width 0.3s ease; }
  .pl-fill-ok    { background: #4ade80; }
  .pl-fill-warn  { background: #fbbf24; }
  .pl-fill-hot   { background: #f87171; }
  .pl-charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .pl-chart-wrap { background: rgba(0,0,0,0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
  .pl-chart-title { font-size: 12px; font-weight: 500; color: var(--muted); margin-bottom: 8px; }
  .pl-chart-wrap .chart-wrap { height: 140px; }
  @media (max-width: 768px) {
    .pl-row { grid-template-columns: 1fr; gap: 6px; }
    .pl-charts { grid-template-columns: 1fr; }
  }

  .stat-card { background: var(--card); border-radius: var(--card-radius); border: var(--card-border); padding: 20px; box-shadow: var(--shadow); }
  .stat-card .label { color: var(--muted); font-size: 12px; letter-spacing: -0.12px; margin-bottom: 8px; font-weight: 500; }
  .stat-card .value { font-size: 24px; font-weight: 600; letter-spacing: -0.28px; color: var(--text); }
  .stat-card .sub { color: var(--muted); font-size: 11px; margin-top: 4px; letter-spacing: -0.08px; }
  .delta { display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 10px; font-size: 10px; font-weight: 600; vertical-align: middle; }
  .delta-up   { background: rgba(248, 113, 113, 0.15); color: #f87171; }
  .delta-down { background: rgba(74, 222, 128, 0.15);  color: #4ade80; }
  /* A/B compare mode */
  .compare-toggle { padding: 5px 12px; border-radius: 8px; border: 1px solid rgba(0,0,0,0.12); background: var(--card); color: var(--muted); font-size: 12px; cursor: pointer; letter-spacing: -0.12px; transition: all 0.15s; white-space: nowrap; }
  .compare-toggle.active { background: var(--accent); color: #ffffff; border-color: var(--accent); font-weight: 500; }
  .compare-row { display: none; align-items: center; gap: 10px; flex-wrap: wrap; width: 100%; padding: 8px 24px; border-bottom: 1px solid var(--border); background: rgba(245, 158, 11, 0.04); }
  .compare-row.visible { display: flex; }
  .ab-tag { display: inline-block; min-width: 14px; padding: 1px 6px; border-radius: 4px; font-size: 10px; font-weight: 700; letter-spacing: 0.4px; vertical-align: middle; margin-right: 4px; text-align: center; }
  .ab-tag-a { background: rgba(0, 113, 227, 0.18); color: #0071e3; }
  .ab-tag-b { background: rgba(245, 158, 11, 0.20); color: #d97706; }
  .ab-stat { display: flex; flex-direction: column; gap: 4px; margin-top: 6px; font-size: 11px; }
  .ab-stat-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
  .ab-stat-row .ab-cell { color: var(--text); font-weight: 600; font-variant-numeric: tabular-nums; }
  .ab-stat-row .ab-cell.muted { color: var(--muted); font-weight: 500; }
  .session-row.period-a td:first-child { box-shadow: inset 3px 0 0 #0071e3; }
  .session-row.period-b td:first-child { box-shadow: inset 3px 0 0 #d97706; }
  .session-row.period-ab td:first-child { box-shadow: inset 3px 0 0 #8b5cf6; }

  .charts-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  .chart-card { background: var(--card); border-radius: var(--card-radius); border: var(--card-border); padding: 20px; box-shadow: var(--shadow); }
  .chart-card.wide { grid-column: 1 / -1; }
  .chart-card h2 { font-size: 13px; font-weight: 600; color: var(--text); letter-spacing: -0.12px; margin-bottom: 16px; }
  .chart-wrap { position: relative; height: 240px; }
  .chart-wrap.tall { height: 300px; }
  .chart-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }
  .chart-header h2 { margin-bottom: 0; }
  .chart-header-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .chart-day-count { font-size: 11px; color: var(--muted); }
  .tz-group { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  .tz-btn { padding: 3px 10px; background: transparent; border: none; border-right: 1px solid var(--border); color: var(--muted); font-size: 11px; cursor: pointer; transition: background 0.15s, color 0.15s; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }
  .tz-btn:last-child { border-right: none; }
  .tz-btn:hover { background: rgba(255,255,255,0.04); color: var(--text); }
  .tz-btn.active { background: rgba(217,119,87,0.15); color: var(--accent); }
  .peak-legend { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: var(--muted); }
  .peak-swatch { width: 10px; height: 10px; background: rgba(248,113,113,0.8); border-radius: 2px; display: inline-block; }

  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 8px 12px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); border-bottom: 1px solid var(--border); white-space: nowrap; }
  th.sortable { cursor: pointer; user-select: none; }
  th.sortable:hover { color: var(--text); }
  .sort-icon { font-size: 9px; opacity: 0.8; }
  td { padding: 10px 12px; border-bottom: 1px solid var(--border); font-size: 13px; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(0,113,227,0.03); }
  .model-tag { display: inline-block; padding: 2px 8px; border-radius: 980px; font-size: 11px; background: rgba(0,113,227,0.08); color: var(--accent); letter-spacing: -0.08px; }
    .cache-warn-badge { display:inline-block; margin-left:6px; padding:1px 6px; border-radius:10px; background:rgba(217,119,87,0.18); color:var(--text); font-size:10px; font-weight:600; letter-spacing:0.02em; vertical-align:middle; cursor:help; }
  .session-name { color: var(--text); font-weight: 600; }
  .proj-edit-btn { background: transparent; border: 0; color: var(--muted); padding: 0 2px; margin-left: 2px; cursor: pointer; font-size: 11px; line-height: 1; opacity: 0; transition: opacity 0.1s; }
  tr:hover .proj-edit-btn, td:hover .proj-edit-btn { opacity: 0.7; }
  .proj-edit-btn:hover { color: var(--accent); opacity: 1; }
  .cost { color: var(--green); font-family: "SF Mono", ui-monospace, monospace; font-size: 12px; }
  .cost-na { color: var(--muted); font-family: "SF Mono", ui-monospace, monospace; font-size: 11px; }
  .num { font-family: "SF Mono", ui-monospace, monospace; font-size: 12px; }
  .muted { color: var(--muted); }
  .section-title { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px; }
  .section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .section-header .section-title { margin-bottom: 0; }
  .export-btn { background: var(--card); border: 1px solid var(--border); color: var(--muted); padding: 3px 10px; border-radius: 5px; cursor: pointer; font-size: 11px; }
  .export-btn:hover { color: var(--text); border-color: var(--accent); }
  .md-btn { background: var(--card); border: 1px solid var(--border); color: var(--muted); padding: 3px 10px; border-radius: 5px; cursor: pointer; font-size: 11px; margin-right: 6px; }
  .md-btn:hover { color: var(--text); border-color: var(--accent); }
  .section-actions { display: inline-flex; align-items: center; gap: 0; }
  .md-toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%) translateY(20px); background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; font-size: 13px; box-shadow: var(--shadow); opacity: 0; pointer-events: none; transition: opacity 200ms ease, transform 200ms ease; z-index: 9999; }
  .md-toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
  .table-card { background: var(--card); border-radius: var(--card-radius); border: var(--card-border); padding: 20px; margin-bottom: 24px; overflow-x: auto; box-shadow: var(--shadow); }

  footer { border-top: 1px solid var(--border); padding: 20px 24px; margin-top: 8px; }
  .footer-content { max-width: 1200px; margin: 0 auto; }
  .footer-content p { color: var(--muted); font-size: 12px; line-height: 1.7; margin-bottom: 4px; letter-spacing: -0.12px; }
  .footer-content p:last-child { margin-bottom: 0; }
  .footer-content a { color: var(--accent); text-decoration: none; }
  .footer-content a:hover { text-decoration: underline; }

  /* Year calendar heatmap (GitHub-style contribution grid) — centered */
  .yc-wrap { overflow-x: auto; padding: 4px 2px 8px; text-align: center; }
  .yc-grid { display: inline-grid; grid-template-columns: repeat(53, 13px); grid-template-rows: repeat(7, 13px); grid-auto-flow: column; gap: 3px; margin: 0 auto; }
  .yc-cell { width: 13px; height: 13px; border-radius: 2px; background: var(--chart-grid); cursor: pointer; }
  .yc-cell.empty { background: transparent; cursor: default; }
  .yc-cell:hover { outline: 1px solid var(--accent); transform: scale(1.15); transition: transform 0.08s; }
  .yc-legend { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--muted); margin-top: 8px; justify-content: center; }
  .yc-legend-swatch { width: 13px; height: 13px; border-radius: 2px; }

  /* DOW × Hour heatmap — match year-calendar width + centered, clickable */
  .dow-hour-wrap { display: flex; justify-content: center; padding: 4px 2px 8px; }
  /* Width matches year-calendar: 53 cols × 13px + 52 × 3px gap = 845px */
  #dow-hour-grid { display: grid !important; grid-template-columns: 30px repeat(24, 1fr) !important; gap: 3px !important; max-width: 845px; width: 100%; }
  #dow-hour-grid > div { cursor: pointer; transition: transform 0.08s; }
  #dow-hour-grid > div:hover { outline: 1px solid var(--accent); transform: scale(1.05); }

  tr.session-row { cursor: pointer; }
  tr.session-row.selected td { background: rgba(0,113,227,0.06); }
  .detail-grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 0.8fr); gap: 16px; }
  .detail-card { background: rgba(0,0,0,0.02); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
  .detail-card h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin-bottom: 12px; }
  .detail-meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 16px; }
  .detail-meta .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; }
  .detail-meta .value { font-size: 13px; }
  .pill-list { display: flex; flex-wrap: wrap; gap: 8px; }
  .pill { border: 1px solid var(--border); border-radius: 999px; padding: 5px 10px; font-size: 12px; color: var(--text); background: rgba(0,0,0,0.02); }
  .detail-table-wrap { max-height: 360px; overflow: auto; border: 1px solid var(--border); border-radius: 8px; }
  .detail-table-wrap table th { position: sticky; top: 0; background: var(--card); }
  .detail-table-wrap td, .detail-table-wrap th { white-space: nowrap; }
  .hint { color: var(--muted); font-size: 12px; }

  @media (max-width: 768px) {
    .charts-grid { grid-template-columns: 1fr; }
    .chart-card.wide { grid-column: 1; }
    .detail-grid { grid-template-columns: 1fr; }
  }

  @media (max-width: 640px) {
    .container { padding: 16px 12px; }
    .header-bar { flex-direction: column; align-items: flex-start; gap: 8px; }
    .header-bar > div, .header-bar > h1 { width: 100%; }
    .filter-bar { flex-direction: column; align-items: stretch; gap: 12px; }
    .range-group { overflow-x: auto; -webkit-overflow-scrolling: touch; white-space: nowrap; }
    .range-btn { flex-shrink: 0; padding: 6px 10px; }
    #stats-row { grid-template-columns: repeat(2, 1fr) !important; gap: 8px; }
    .stat-card { padding: 12px; }
    .stat-card .value { font-size: 20px; }
    .table-card { overflow-x: auto; }
    .table-card table { min-width: 600px; }
    .table-card th.hide-mobile,
    .table-card td.hide-mobile { display: none; }
    .chart-wrap { height: 240px; }
  }
  /* ── Dashboard customization (edit mode) ─────────────────────────────── */
  .appearance-btn.editing { background: var(--accent); color: #ffffff; border-color: var(--accent); }
  body.edit-mode [data-block-id] { position: relative; outline: 2px dashed var(--accent); outline-offset: 4px; transition: opacity 0.15s; }
  body.edit-mode [data-block-id].dragging { opacity: 0.4; }
  body.edit-mode [data-block-id].drop-target { outline-color: var(--green); outline-style: solid; }
  .block-edit-overlay { display: none; }
  body.edit-mode .block-edit-overlay { display: flex; position: absolute; top: -14px; left: 0; right: 0; height: 24px; align-items: center; justify-content: space-between; padding: 0 6px; pointer-events: none; z-index: 5; }
  body.edit-mode .block-edit-overlay > * { pointer-events: auto; }
  .block-drag-handle { display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; background: var(--card); border: 1px solid var(--border); border-radius: 4px; color: var(--muted); cursor: grab; font-size: 14px; line-height: 1; user-select: none; box-shadow: var(--shadow); }
  .block-drag-handle:active { cursor: grabbing; }
  .block-hide-label { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--text); background: var(--card); border: 1px solid var(--border); border-radius: 4px; padding: 2px 8px; cursor: pointer; box-shadow: var(--shadow); user-select: none; }
  .block-hide-label input { margin: 0; cursor: pointer; }
  /* In edit mode, hidden blocks stay visible (faded) so the user can unhide them. */
  body.edit-mode [data-block-id].block-hidden { opacity: 0.4; }
  body.edit-mode [data-block-id].block-hidden::after { content: "Hidden"; position: absolute; top: 4px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.6); color: #fff; font-size: 10px; padding: 2px 8px; border-radius: 4px; pointer-events: none; }
  /* Outside edit mode: actually hide blocks the user has hidden. */
  body:not(.edit-mode) [data-block-id].block-hidden { display: none !important; }
  #edit-mode-toolbar { display: none; position: sticky; top: 48px; z-index: 90; background: rgba(255,159,10,0.10); border-bottom: 1px solid var(--border); padding: 8px 24px; align-items: center; gap: 12px; font-size: 12px; color: var(--text); }
  body.edit-mode #edit-mode-toolbar { display: flex; }
  #edit-mode-toolbar .toolbar-hint { color: var(--muted); flex: 1; }
  #edit-mode-toolbar button { background: var(--card); border: 1px solid var(--border); color: var(--text); border-radius: 6px; padding: 4px 12px; cursor: pointer; font-size: 12px; }
  #edit-mode-toolbar button:hover { border-color: var(--accent); color: var(--accent); }
</style>
</head>
<body>
<div id="session-modal" style="display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.65); z-index:1000; align-items:center; justify-content:center;" onclick="if(event.target===this) _closeSessionModal()">
  <div style="background:var(--card); border-radius:12px; padding:24px; max-width:900px; width:90%; max-height:80vh; overflow-y:auto; color:var(--text);">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
      <h2 id="session-modal-title" style="margin:0;">Session</h2>
      <button onclick="_closeSessionModal()" style="background:none; border:none; color:var(--muted); font-size:24px; cursor:pointer;">&times;</button>
    </div>
    <div id="session-modal-body"></div>
  </div>
</div>
<header>
  <h1>Claude Code Usage Dashboard</h1>
  <div style="display:flex;align-items:center;gap:12px">
    <span id="streak-badge" class="streak-badge" hidden title="Consecutive days (UTC) with at least one assistant turn"></span>
    <div class="meta" id="meta">Loading...</div>
    <button class="link-btn" onclick="_resetPrefs()" title="Clear saved range / model / theme preferences and reload">Reset prefs</button>
      <button id="rescan-btn" onclick="triggerRescan()" title="Rebuild the database from scratch by re-scanning all JSONL files. Use if data looks stale or costs seem wrong.">&#x21bb; Rescan</button>
    <div id="live-widget" style="display:none; padding:6px 10px; margin-right:10px; background:rgba(34,197,94,0.12); color:#22c55e; border-radius:6px; font-size:11px; font-weight:600; cursor:default;" title=""></div>
    <button id="reset-btn" onclick="_confirmReset()" title="Delete usage.db entirely and re-create empty schema. Run scan afterwards to repopulate." style="background: var(--card); border: 1px solid var(--border); color: var(--muted); padding: 4px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; margin-left: 6px;">&#x1f5d1; Reset DB</button>
    <button id="rescan-btn" onclick="triggerRescan()" title="Rebuild the database from scratch by re-scanning all JSONL files. Use if data looks stale or costs seem wrong.">&#x21bb; Rescan</button>
    <select id="theme-quick" onchange="_onThemeQuickChange(this.value)" title="Quick-switch theme" style="background: var(--card); border: 1px solid var(--border); border-radius: 6px; color: var(--text); padding: 4px 8px; font-size: 12px; margin-right: 6px;"></select>
    <select id="currency-select" class="appearance-btn" title="Display currency (FX rates from frankfurter.app)" onchange="onCurrencyChange(this.value)">
      <option value="USD">USD $</option>
    </select>
      <button class="appearance-btn" onclick="window.open('/themes','_blank')">Appearance</button>
      <button class="appearance-btn" id="customize-btn" onclick="toggleEditMode()" title="Rearrange or hide dashboard sections">Customize</button>
  </div>
</header>

<div id="edit-mode-toolbar">
  <span class="toolbar-hint">Edit mode · drag the ⋮ handles to reorder, check Hide to remove a section. Changes save automatically when you exit.</span>
  <button onclick="resetDashboardPrefs()" title="Restore the default order and unhide everything">Reset to defaults</button>
  <button onclick="toggleEditMode()">Done</button>
</div>

<div id="filter-bar">
  <div id="machine-filter-wrap" data-machine-filter style="display:none; align-items:center; gap:10px;">
    <div class="filter-label">Machine</div>
    <select id="machine-select" onchange="onMachineChange(this.value)" style="background:var(--card);border:1px solid var(--border);border-radius:8px;padding:5px 10px;font-size:12px;color:var(--text);letter-spacing:-0.12px;cursor:pointer;">
      <option value="">All machines</option>
    </select>
    <div class="filter-sep"></div>
  </div>
  <div class="filter-label">Models</div>
  <div id="model-checkboxes"></div>
  <button class="filter-btn" onclick="selectAllModels()">All</button>
  <button class="filter-btn" onclick="clearAllModels()">None</button>
  <div class="filter-sep" id="accounts-sep" style="display:none"></div>
  <div class="filter-label" id="accounts-label" style="display:none">Account</div>
  <select id="account-select" onchange="onAccountChange(this.value)" style="display:none; padding: 4px 10px; border-radius: 980px; border: 1px solid rgba(0,0,0,0.12); background: transparent; color: var(--text); font-size: 12px; cursor: pointer; letter-spacing: -0.12px;"></select>
  <div class="filter-sep"></div>
  <div class="filter-label">Range</div>
  <div class="range-group">
    <button class="range-btn" data-range="today" onclick="setRange('today')">Today</button>
    <button class="range-btn" data-range="week" onclick="setRange('week')">This Week</button>
    <button class="range-btn" data-range="month" onclick="setRange('month')">This Month</button>
    <button class="range-btn" data-range="prev-month" onclick="setRange('prev-month')">Prev Month</button>
    <button class="range-btn" data-range="7d"  onclick="setRange('7d')">7d</button>
    <button class="range-btn" data-range="30d" onclick="setRange('30d')">30d</button>
    <button class="range-btn" data-range="90d" onclick="setRange('90d')">90d</button>
    <button class="range-btn" data-range="all" onclick="setRange('all')">All</button>
  </div>
  <div class="filter-sep"></div>
  <div class="filter-label">Compare</div>
  <button id="compare-toggle" class="compare-toggle" onclick="toggleCompareMode()" title="Compare two periods side-by-side">off</button>
</div>

<div id="compare-row" class="compare-row">
  <div class="filter-label"><span class="ab-tag ab-tag-a">A</span> Period A</div>
  <div class="range-group" id="range-group-a">
    <button class="range-btn" data-rangea="today" onclick="setRangeA('today')">Today</button>
    <button class="range-btn" data-rangea="week" onclick="setRangeA('week')">This Week</button>
    <button class="range-btn" data-rangea="month" onclick="setRangeA('month')">This Month</button>
    <button class="range-btn" data-rangea="prev-month" onclick="setRangeA('prev-month')">Prev Month</button>
    <button class="range-btn" data-rangea="7d"  onclick="setRangeA('7d')">7d</button>
    <button class="range-btn" data-rangea="30d" onclick="setRangeA('30d')">30d</button>
    <button class="range-btn" data-rangea="90d" onclick="setRangeA('90d')">90d</button>
    <button class="range-btn" data-rangea="all" onclick="setRangeA('all')">All</button>
  </div>
  <div class="filter-sep"></div>
  <div class="filter-label"><span class="ab-tag ab-tag-b">B</span> Period B</div>
  <div class="range-group" id="range-group-b">
    <button class="range-btn" data-rangeb="today" onclick="setRangeB('today')">Today</button>
    <button class="range-btn" data-rangeb="week" onclick="setRangeB('week')">This Week</button>
    <button class="range-btn" data-rangeb="month" onclick="setRangeB('month')">This Month</button>
    <button class="range-btn" data-rangeb="prev-month" onclick="setRangeB('prev-month')">Prev Month</button>
    <button class="range-btn" data-rangeb="7d"  onclick="setRangeB('7d')">7d</button>
    <button class="range-btn" data-rangeb="30d" onclick="setRangeB('30d')">30d</button>
    <button class="range-btn" data-rangeb="90d" onclick="setRangeB('90d')">90d</button>
    <button class="range-btn" data-rangeb="all" onclick="setRangeB('all')">All</button>
  </div>
</div>

<div class="container">
  <div class="stats-row" id="stats-row" data-block-id="stats-row"></div>
  <div class="plan-limits-card" id="plan-limits-card" data-block-id="plan-limits-card" style="display:none;">
    <div class="pl-header">
      <h2>Plan Utilization</h2>
      <span class="pl-note" id="pl-note"></span>
    </div>
    <div class="pl-rows" id="pl-rows"></div>
    <div class="pl-charts">
      <div class="pl-chart-wrap">
        <div class="pl-chart-title">5-hour rolling window (last 48h)</div>
        <div class="chart-wrap"><canvas id="chart-plan-spark"></canvas></div>
      </div>
      <div class="pl-chart-wrap">
        <div class="pl-chart-title">Weekly tokens per day (current week)</div>
        <div class="chart-wrap"><canvas id="chart-plan-weekly"></canvas></div>
      </div>
    </div>
  </div>

    <div id="pareto-card" data-block-id="pareto-card" style="display:none; margin: -8px 0 16px 0; padding: 10px 14px; background: rgba(217,119,87,0.08); border-radius: 8px; font-size: 12px; color: var(--text);"></div>
  <div id="budget-bar" data-block-id="budget-bar" style="display:none; margin:0 0 16px 0;"><div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:6px;"><div style="font-size:12px; color:var(--muted);">Monthly budget <span id="budget-label"></span></div><div style="font-size:11px; color:var(--muted);"><a href="#" onclick="_editBudget(); return false;" style="color:var(--muted); text-decoration:underline;">edit</a></div></div><div id="budget-track" style="height:6px; background:rgba(255,255,255,0.08); border-radius:3px; overflow:hidden;"><div id="budget-fill" style="height:100%; background:#4ade80; transition: width 0.3s, background-color 0.3s;"></div></div></div>
  <div id="anomaly-banner" data-block-id="anomaly-banner" style="display:none; padding:10px 14px; margin: 0 0 12px 0; background:rgba(248,113,113,0.12); border-left:3px solid #f87171; border-radius:6px; color:#f87171; font-size:13px;"></div>
    <div id="plan-card" data-block-id="plan-card" style="display:none; margin:0 0 16px 0; padding:10px 14px; background:rgba(74,222,128,0.08); border-radius:8px; font-size:12px; color:var(--text);"></div>
    <div id="downgrade-card" data-block-id="downgrade-card" style="display:none; margin: -8px 0 16px 0; padding: 10px 14px; background: rgba(74,222,128,0.10); border-radius: 8px; font-size: 12px; color: var(--text);"></div>
    <div id="cache-hit-card" data-block-id="cache-hit-card" style="display:none; margin: -4px 0 16px 0; padding: 10px 14px; background: rgba(94,106,210,0.08); border-radius: 8px; font-size: 12px; color: var(--text);"></div>
    <div id="git-trace-card" data-block-id="git-trace-card" style="display:none; margin: -8px 0 16px 0; padding: 12px 16px; background: rgba(76,175,80,0.08); border: 1px solid rgba(76,175,80,0.18); border-radius: 8px; font-size: 13px; color: var(--text);"></div>
    <details id="inbound-card" data-block-id="inbound-card" style="display:none; margin: -8px 0 16px 0; padding: 10px 14px; background: rgba(94,106,210,0.08); border-radius: 8px; font-size: 12px; color: var(--text);"><summary style="cursor:pointer; user-select:none;"><strong>Recent inbound events</strong> <span id="inbound-count" style="color:var(--muted);"></span></summary><div id="inbound-list" style="margin-top:8px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; line-height: 1.6;"></div></details>
    <div class="chart-card" id="time-on-task-card" data-block-id="time-on-task-card" style="margin-bottom: 16px;">
      <div style="display:flex; align-items:flex-start; justify-content:space-between; gap:16px; flex-wrap:wrap;">
        <div>
          <h2 style="margin-bottom:4px;">Time on Task</h2>
          <div class="hint" id="tot-sub">Active coding minutes today &mdash; sum of intra-session gaps under 5 minutes</div>
          <div style="display:flex; align-items:baseline; gap:16px; margin-top:10px;">
            <div>
              <div style="font-size:32px; font-weight:600; letter-spacing:-0.4px; color:var(--text);" id="tot-today">&mdash;</div>
              <div class="muted" style="font-size:11px;">today</div>
            </div>
            <div>
              <div style="font-size:18px; font-weight:500; color:var(--muted);" id="tot-avg">&mdash;</div>
              <div class="muted" style="font-size:11px;">30d avg</div>
            </div>
            <div>
              <div style="font-size:18px; font-weight:500; color:var(--muted);" id="tot-total">&mdash;</div>
              <div class="muted" style="font-size:11px;">30d total</div>
            </div>
          </div>
        </div>
        <div style="flex:1; min-width:200px; max-width:520px; height:80px;">
          <canvas id="chart-time-on-task"></canvas>
        </div>
      </div>
    </div>
  <div class="charts-grid" data-block-id="charts-grid-main">
    <div class="chart-card wide">
      <h2 id="daily-chart-title">Daily Token Usage</h2>
      <div class="chart-wrap tall"><canvas id="chart-daily"></canvas></div>
    </div>
    <div class="chart-card wide">
      <h2>Activity by Day-of-Week × Hour (UTC)</h2>
      <div class="dow-hour-wrap"><div id="dow-hour-grid" style="font-size: 10px; color: var(--muted);"></div></div>
    </div>

    <div class="chart-card wide">
      <div class="chart-header">
        <h2>Activity (last 365 days)</h2>
        <div class="chart-header-right">
          <span class="chart-day-count" id="year-calendar-total"></span>
        </div>
      </div>
      <div class="yc-wrap"><div id="year-calendar" class="yc-grid"></div></div>
      <div class="yc-legend">
        <span>Less</span>
        <span class="yc-legend-swatch" style="background:var(--chart-grid)"></span>
        <span class="yc-legend-swatch" id="yc-leg-1"></span>
        <span class="yc-legend-swatch" id="yc-leg-2"></span>
        <span class="yc-legend-swatch" id="yc-leg-3"></span>
        <span class="yc-legend-swatch" id="yc-leg-4"></span>
        <span>More</span>
      </div>
    </div>
    <div class="chart-card wide">
      <div class="chart-header">
        <h2 id="hourly-chart-title">Average Hourly Distribution</h2>
        <div class="chart-header-right">
          <span class="peak-legend" title="Mon–Fri 05:00–11:00 PT — Anthropic peak-hour throttling window"><span class="peak-swatch"></span>Peak hours (PT)</span>
          <span class="chart-day-count" id="hourly-day-count"></span>
          <div class="tz-group">
            <button class="tz-btn" data-tz="local" onclick="setHourlyTZ('local')">Local</button>
            <button class="tz-btn" data-tz="utc"   onclick="setHourlyTZ('utc')">UTC</button>
          </div>
        </div>
      </div>
      <div class="chart-wrap"><canvas id="chart-hourly"></canvas></div>
    </div>
    <div class="chart-card">
      <h2>Cost per Turn Distribution</h2>
      <div id="histo-stats" style="font-size: 11px; color: var(--muted); margin-bottom: 8px;"></div>
      <div class="chart-wrap"><canvas id="chart-histo"></canvas></div>
    </div>

    <div class="chart-card">
      <h2>By Model</h2>
      <div class="chart-wrap"><canvas id="chart-model"></canvas></div>
    </div>
    <div class="chart-card">
      <h2>Top Projects by Tokens</h2>
      <div class="chart-wrap"><canvas id="chart-project"></canvas></div>
    </div>
  </div>

  <div class="charts-grid" data-block-id="charts-grid-tools" style="grid-template-columns: 1fr;">
    <div class="chart-card">
      <h2>Top Tools by Turns</h2>
      <div id="tools-chart-empty" style="display:none; color:var(--muted); padding:24px 0; text-align:center;">No tool_name data yet — older transcripts may not have tool info.</div>
      <div class="chart-wrap"><canvas id="chart-tools"></canvas></div>
    </div>
  </div>
  <div class="table-card" data-block-id="cost-by-model-table">
    <div class="section-header"><div class="section-title">Cost by Model</div><div class="section-actions"><button class="md-btn" onclick="copyModelMD()" title="Copy table as markdown">&#x1f4cb; MD</button></div></div>
    <table id="model-cost-table">
      <thead><tr>
        <th>Model</th>
        <th class="sortable" onclick="setModelSort('turns')">Turns <span class="sort-icon" id="msort-turns"></span></th>
        <th class="sortable" onclick="setModelSort('input')">Input <span class="sort-icon" id="msort-input"></span></th>
        <th class="sortable" onclick="setModelSort('output')">Output <span class="sort-icon" id="msort-output"></span></th>
        <th class="sortable hide-mobile" onclick="setModelSort('cache_read')">Cache Read <span class="sort-icon" id="msort-cache_read"></span></th>
        <th class="sortable hide-mobile" onclick="setModelSort('cache_creation')">Cache Creation <span class="sort-icon" id="msort-cache_creation"></span></th>
        <th class="sortable" onclick="setModelSort('cost')">Est. Cost <span class="sort-icon" id="msort-cost"></span></th>
      </tr></thead>
      <tbody id="model-cost-body"></tbody>
    </table>
  </div>
  <div class="table-card" data-block-id="recent-sessions-table">
    <div class="section-header">
      <div class="section-title">Recent Sessions</div>
      <div class="section-actions">
        <input id="sessions-search" type="search" placeholder="Search project / branch / session…" oninput="_onSearchInput(this.value)" style="width:240px; padding:6px 10px; background: var(--card); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 12px;">
        <button class="md-btn" onclick="copySessionsMD()" title="Copy table as markdown">&#x1f4cb; MD</button>
        <button class="export-btn" onclick="exportSessionsCSV()" title="Export all filtered sessions to CSV">&#x2913; CSV</button>
      </div>
    </div>
    <div class="hint" style="margin-bottom:12px;">Click a session row for branch, tool, cwd, and turn history detail.</div>
    <table id="sessions-table">
      <thead><tr>
        <th>Session</th>
        <th>Project</th>
        <th class="sortable" onclick="setSessionSort('last')">Last Active <span class="sort-icon" id="sort-icon-last"></span></th>
        <th class="sortable" onclick="setSessionSort('duration_min')">Duration <span class="sort-icon" id="sort-icon-duration_min"></span></th>
        <th>Model</th>
        <th class="sortable" onclick="setSessionSort('turns')">Turns <span class="sort-icon" id="sort-icon-turns"></span></th>
        <th class="sortable" onclick="setSessionSort('input')">Input <span class="sort-icon" id="sort-icon-input"></span></th>
        <th class="sortable" onclick="setSessionSort('output')">Output <span class="sort-icon" id="sort-icon-output"></span></th>
        <th class="sortable" onclick="setSessionSort('cost')">Est. Cost <span class="sort-icon" id="sort-icon-cost"></span></th>
      </tr></thead>
      <tbody id="sessions-body"></tbody>
    </table>
  </div>
  <div class="table-card" id="session-detail-card" data-block-id="session-detail-card" style="display:none;">
    <div class="section-title">Session Detail</div>
    <div id="session-detail"></div>
  </div>
  <div class="table-card" data-block-id="cost-by-project-table">
    <div class="section-header"><div class="section-title">Cost by Project</div><div class="section-actions"><button class="md-btn" onclick="copyProjectsMD()" title="Copy table as markdown">&#x1f4cb; MD</button><button class="export-btn" onclick="exportProjectsCSV()" title="Export all projects to CSV">&#x2913; CSV</button></div></div>
    <table id="project-cost-table">
      <thead><tr>
        <th>Project</th>
        <th class="sortable" onclick="setProjectSort('sessions')">Sessions <span class="sort-icon" id="psort-sessions"></span></th>
        <th class="sortable" onclick="setProjectSort('turns')">Turns <span class="sort-icon" id="psort-turns"></span></th>
        <th class="sortable" onclick="setProjectSort('input')">Input <span class="sort-icon" id="psort-input"></span></th>
        <th class="sortable" onclick="setProjectSort('output')">Output <span class="sort-icon" id="psort-output"></span></th>
        <th class="sortable" onclick="setProjectSort('cost')">Est. Cost <span class="sort-icon" id="psort-cost"></span></th>
      </tr></thead>
      <tbody id="project-cost-body"></tbody>
    </table>
  </div>
  <div class="table-card" data-block-id="cost-by-project-branch-table">
    <div class="section-header"><div class="section-title">Cost by Project &amp; Branch</div><div class="section-actions"><button class="md-btn" onclick="copyProjectBranchMD()" title="Copy table as markdown">&#x1f4cb; MD</button><button class="export-btn" onclick="exportProjectBranchCSV()" title="Export project+branch breakdown to CSV">&#x2913; CSV</button></div></div>
    <table id="project-branch-cost-table">
      <thead><tr>
        <th>Project</th>
        <th>Branch</th>
        <th class="sortable" onclick="setProjectBranchSort('sessions')">Sessions <span class="sort-icon" id="pbsort-sessions"></span></th>
        <th class="sortable" onclick="setProjectBranchSort('turns')">Turns <span class="sort-icon" id="pbsort-turns"></span></th>
        <th class="sortable" onclick="setProjectBranchSort('input')">Input <span class="sort-icon" id="pbsort-input"></span></th>
        <th class="sortable" onclick="setProjectBranchSort('output')">Output <span class="sort-icon" id="pbsort-output"></span></th>
        <th class="sortable" onclick="setProjectBranchSort('cost')">Est. Cost <span class="sort-icon" id="pbsort-cost"></span></th>
      </tr></thead>
      <tbody id="project-branch-cost-body"></tbody>
    </table>
  </div>
  <div class="table-card" id="cost-by-branch-card" data-block-id="cost-by-branch-card">
    <div class="section-header"><div class="section-title">Cost by Branch</div><button class="export-btn" onclick="exportBranchCSV()" title="Export branch breakdown to CSV">&#x2913; CSV</button></div>
    <table>
      <thead><tr>
        <th class="sortable" onclick="setBranchOnlySort('project')">Project <span class="sort-icon" id="cbsort-project"></span></th>
        <th class="sortable" onclick="setBranchOnlySort('branch')">Branch <span class="sort-icon" id="cbsort-branch"></span></th>
        <th class="sortable" onclick="setBranchOnlySort('sessions')">Sessions <span class="sort-icon" id="cbsort-sessions"></span></th>
        <th class="sortable" onclick="setBranchOnlySort('turns')">Turns <span class="sort-icon" id="cbsort-turns"></span></th>
        <th class="sortable" onclick="setBranchOnlySort('input')">Input <span class="sort-icon" id="cbsort-input"></span></th>
        <th class="sortable" onclick="setBranchOnlySort('output')">Output <span class="sort-icon" id="cbsort-output"></span></th>
        <th class="sortable" onclick="setBranchOnlySort('cache_read')">Cache Read <span class="sort-icon" id="cbsort-cache_read"></span></th>
        <th class="sortable" onclick="setBranchOnlySort('cache_creation')">Cache Creation <span class="sort-icon" id="cbsort-cache_creation"></span></th>
        <th class="sortable" onclick="setBranchOnlySort('cost')">Est. Cost <span class="sort-icon" id="cbsort-cost"></span></th>
      </tr></thead>
      <tbody id="branch-only-cost-body"></tbody>
  <div class="table-card" id="cache-1h-card">
    <div class="section-title">1-Hour Cache Opportunities</div>
    <p class="muted" style="font-size:12px;margin-bottom:12px;line-height:1.5;">
      Sessions longer than 30 minutes with &gt; 100k cache-creation tokens. On Anthropic&#39;s default 5-minute cache tier the cache expires and gets rewritten roughly every 5 min; the 1-hour tier costs 1.6&times; per write but only needs to be paid once per session. Switching these candidates would save ~40% of the current cache-creation cost. See the <a href="https://docs.claude.com/en/docs/build-with-claude/prompt-caching" target="_blank" style="color:var(--accent)">prompt caching docs</a>.
    </p>
    <table>
      <thead><tr>
        <th>Session</th>
        <th>Duration</th>
        <th>Cache Creation</th>
        <th>Current Cost</th>
        <th>Est. Savings (1h tier)</th>
      </tr></thead>
      <tbody id="cache-1h-body"></tbody>
    </table>
  </div>
</div>

<div id="md-toast" class="md-toast" role="status" aria-live="polite">Copied as markdown!</div>

<footer>
  <div class="footer-content">
    <p>Cost estimates based on Anthropic API pricing (<a href="https://claude.com/pricing#api" target="_blank">claude.com/pricing#api</a>) as of April 2026. Only models containing <em>opus</em>, <em>sonnet</em>, or <em>haiku</em> in the name are included in cost calculations. Actual costs for Max/Pro subscribers differ from API pricing.</p>
    <p>
      GitHub: <a href="https://github.com/phuryn/claude-usage" target="_blank">https://github.com/phuryn/claude-usage</a>
      &nbsp;&middot;&nbsp;
      Created by: <a href="https://www.productcompass.pm" target="_blank">The Product Compass Newsletter</a>
      &nbsp;&middot;&nbsp;
      License: MIT
    </p>
    <p id="fx-footer" style="display:none">FX rates from <a href="https://www.frankfurter.app" target="_blank">frankfurter.app</a> &middot; updated <span id="fx-asof">?</span></p>
  </div>
</footer>

<script>
// ── Theme switching ────────────────────────────────────────────────────────
function setTheme(css, id) {
  document.getElementById('active-theme').textContent = css;
  if (id)  localStorage.setItem('dashboard-theme-id',  id);
  if (css) localStorage.setItem('dashboard-theme-css', css);
  if (rawData) applyFilter();
}

(function restoreTheme() {
  const css = localStorage.getItem('dashboard-theme-css');
  if (css) document.getElementById('active-theme').textContent = css;
})();

async function _populateThemeDropdown() {  // eslint-disable-line no-unused-vars
  try {
    const sel = document.getElementById("theme-quick");
    if (!sel) return;
    const r = await fetch("/api/themes");
    const themes = await r.json();
    const current = localStorage.getItem("dashboard-theme-id") || "apple";
    sel.innerHTML = themes.map(t =>
      `<option value="${t.id}" ${t.id === current ? "selected" : ""}>${t.name}</option>`
    ).join("");
    // Cache themes for instant switching without another fetch
    window._cachedThemes = themes;
  } catch (e) { /* gallery / themes API not present — fine */ }
}

function _onThemeQuickChange(id) {  // eslint-disable-line no-unused-vars
  const themes = window._cachedThemes || [];
  const t = themes.find(x => x.id === id);
  if (!t) return;
  setTheme(t.css, t.id);
}
function _renderTags(s) {  // eslint-disable-line no-unused-vars
  if (!s.tags || !s.tags.length) return "";
  return s.tags.map(t => `<span style="display:inline-block;margin-left:4px;padding:1px 6px;border-radius:8px;font-size:10px;background:rgba(217,119,87,0.18);color:var(--accent);">${t}</span>`).join("");
}

function _promptTags(sid) {  // eslint-disable-line no-unused-vars
  const s = (rawData && rawData.sessions_all || []).find(x => x.session_id === sid);
  const cur = (s && s.tags || []).join(", ");
  const v = prompt("Tags for " + sid + " (comma-separated, empty to clear):", cur);
  if (v === null) return;
  const tags = v.split(",").map(t => t.trim()).filter(Boolean);
  fetch("/api/tags", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({session_id: sid, tags: tags}),
  }).then(() => loadData());
}

function chartColors() {
  const s = getComputedStyle(document.documentElement);
  return {
    label: s.getPropertyValue('--chart-label').trim() || 'rgba(0,0,0,0.48)',
    grid:  s.getPropertyValue('--chart-grid').trim()  || 'rgba(0,0,0,0.06)',
    c1:    s.getPropertyValue('--chart-1').trim()     || 'rgba(0,113,227,0.8)',
    c2:    s.getPropertyValue('--chart-2').trim()     || 'rgba(88,86,214,0.8)',
    c3:    s.getPropertyValue('--chart-3').trim()     || 'rgba(52,199,89,0.8)',
    c4:    s.getPropertyValue('--chart-4').trim()     || 'rgba(255,159,10,0.75)',
  };
}

// ── Helpers ────────────────────────────────────────────────────────────────
function esc(s) {
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

// ── Project display-name overrides ────────────────────────────────────────
// rawData.sessions_all carries both `project` (raw, used for filtering and
// aggregation keys) and `display_name` (user-supplied pretty label). Every
// table/chart that renders a project name should funnel through these.
const _projDisplayCache = {};
function rebuildProjectDisplayCache() {
  for (const k in _projDisplayCache) delete _projDisplayCache[k];
  if (!rawData || !rawData.sessions_all) return;
  for (const s of rawData.sessions_all) {
    if (s.display_name) _projDisplayCache[s.project] = s.display_name;
  }
}
function projDisplay(rawProject) {
  if (!rawProject) return '';
  return _projDisplayCache[rawProject] || rawProject;
}
function projCellHTML(rawProject) {
  // <project label> + tiny pencil button. Click → prompt for new name.
  const display = projDisplay(rawProject);
  const renamed = display !== rawProject;
  const title = renamed
    ? 'Rename project (raw: ' + rawProject + ')'
    : 'Set a display name';
  return esc(display)
    + ' <button type="button" class="proj-edit-btn" title="' + esc(title)
    + '" onclick="renameProject(event, ' + JSON.stringify(rawProject) + ')">&#9998;</button>';
}
async function renameProject(ev, rawProject) {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  const current = projDisplay(rawProject);
  const suggested = current === rawProject ? '' : current;
  const next = window.prompt(
    'Display name for "' + rawProject + '"\n(blank to clear)',
    suggested,
  );
  if (next === null) return;  // user hit Cancel
  try {
    const resp = await fetch('/api/project-name', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ raw: rawProject, display: next }),
    });
    if (!resp.ok) {
      alert('Could not save project name (HTTP ' + resp.status + ').');
      return;
    }
    await loadData();
  } catch (e) {
    alert('Could not save project name: ' + e);
  }
}

// ── State ──────────────────────────────────────────────────────────────────
let rawData = null;
let selectedModels = new Set();
const LS_KEY = 'claude-usage-prefs/v1';
function _loadPrefs() {
  try { return JSON.parse(localStorage.getItem(LS_KEY) || '{}') || {}; }
  catch (e) { return {}; }
}
function _savePrefs(patch) {
  try {
    const cur = _loadPrefs();
    const next = Object.assign({}, cur, patch);
    localStorage.setItem(LS_KEY, JSON.stringify(next));
  } catch (e) { /* private mode / quota / SSR */ }
}
function _resetPrefs() {
  try { localStorage.removeItem(LS_KEY); } catch (e) {}
  window.location.search = '';  // clears URL params + reloads
}

let selectedRange = (_loadPrefs().range) || '30d';
let selectedAccount = (_loadPrefs().account) || 'all';  // 'all' or an account name
// A/B compare mode state. In compare mode, `selectedRange` plays the role of
// Period A (so existing single-period URL/prefs stay compatible), and the
// new `selectedRangeB` adds the second window.
let compareMode = !!_loadPrefs().compareMode;
let selectedRangeB = (_loadPrefs().rangeB) || 'prev-month';
let selectedSessionId = null;
let charts = {};
let sessionSortCol = 'last';
let modelSortCol = 'cost';
let modelSortDir = 'desc';
let projectSortCol = 'cost';
let projectSortDir = 'desc';
let branchSortCol = 'cost';
let branchSortDir = 'desc';
let cbSortCol = 'cost';
let cbSortDir = 'desc';
let lastFilteredSessions = [];
let lastByProject = [];
let lastByProjectBranch = [];
let lastByBranch = [];
let lastByModel = [];
let sessionSortDir = 'desc';
let hourlyTZ = (_loadPrefs().hourlyTZ) || 'local';  // 'local' or 'utc'
let selectedMachine = '';  // empty string = "all machines"

function buildMachineFilterUI(machines) {
  const wrap = document.getElementById('machine-filter-wrap');
  const sel = document.getElementById('machine-select');
  if (!wrap || !sel) return;
  // Auto-hide when only one machine has reported in -- single-laptop install.
  if (!machines || machines.length <= 1) {
    wrap.style.display = 'none';
    selectedMachine = '';
    return;
  }
  wrap.style.display = 'flex';
  const opts = ['<option value="">All machines</option>']
    .concat(machines.map(m => `<option value="${esc(m)}">${esc(m)}</option>`));
  sel.innerHTML = opts.join('');
  sel.value = selectedMachine || '';
}

function onMachineChange(val) {
  selectedMachine = val || '';
  applyFilter();
}

// ── Peak-hour config ───────────────────────────────────────────────────────
// Anthropic throttles Mon–Fri 05:00–11:00 PT. We approximate as fixed UTC hours
// 12–17 (matches PDT; during PST the window shifts by 1h — accepted simplification).
const PEAK_HOURS_UTC = new Set([12, 13, 14, 15, 16, 17]);

// Local-timezone offset in hours (signed). Fractional offsets (e.g. India UTC+5:30)
// are rounded to the nearest hour for bucket alignment.
function localOffsetHours() {
  return Math.round(-new Date().getTimezoneOffset() / 60);
}

// Return the UTC hour (0–23) corresponding to a displayed-hour bucket.
function displayHourToUTC(displayHour, tzMode) {
  if (tzMode === 'utc') return displayHour;
  return ((displayHour - localOffsetHours()) % 24 + 24) % 24;
}

// Return the displayed-hour bucket for a UTC hour.
function utcHourToDisplay(utcHour, tzMode) {
  if (tzMode === 'utc') return utcHour;
  return ((utcHour + localOffsetHours()) % 24 + 24) % 24;
}

function isPeakHour(displayHour, tzMode) {
  return PEAK_HOURS_UTC.has(displayHourToUTC(displayHour, tzMode));
}

function formatHourLabel(h) {
  return String(h).padStart(2, '0') + ':00';
}

function tzDisplayName(tzMode) {
  if (tzMode === 'utc') return 'UTC';
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Local';
  } catch(e) {
    return 'Local';
  }
}

// ── Pricing (Anthropic API, April 2026) ────────────────────────────────────
const PRICING = /*__PRICING_JSON__*/;

function isBillable(model) {
  if (!model) return false;
  const m = model.toLowerCase();
  return m.includes('opus') || m.includes('sonnet') || m.includes('haiku');
}

function getPricing(model) {
  if (!model) return null;
  if (PRICING[model]) return PRICING[model];
  for (const key of Object.keys(PRICING)) {
    if (model.startsWith(key)) return PRICING[key];
  }
  const m = model.toLowerCase();
  if (m.includes('opus'))   return PRICING['claude-opus-4-7'];
  if (m.includes('sonnet')) return PRICING['claude-sonnet-4-6'];
  if (m.includes('haiku'))  return PRICING['claude-haiku-4-5'];
  return null;
}

function calcCost(model, inp, out, cacheRead, cacheCreation, cache1h = 0) {
  if (!isBillable(model)) return 0;
  const p = getPricing(model);
  if (!p) return 0;
  return (
    inp           * p.input       / 1e6 +
    out           * p.output      / 1e6 +
    cacheRead     * p.cache_read  / 1e6 +
    (cacheCreation || 0) * (p.cache_write_5m || p.cache_write) / 1e6
    + (cache1h || 0) * (p.cache_write_1h || (p.cache_write * 1.6)) / 1e6
  );
}

// ── Formatting ─────────────────────────────────────────────────────────────
function fmt(n) {
  if (n >= 1e9) return (n/1e9).toFixed(2)+'B';
  if (n >= 1e6) return (n/1e6).toFixed(2)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'K';
  return n.toLocaleString();
}
// ── Currency / FX (USD-based; rates come from /api/fx-rates) ──────────────
const FX_LS_KEY            = 'fx_rates';
const FX_CURRENCY_LS_KEY   = 'fx_currency';
const FX_CLIENT_TTL_MS     = 24 * 60 * 60 * 1000;  // 24h
const CURRENCY_SYMBOLS = {
  USD: '$', EUR: '€', GBP: '£', JPY: '¥', CNY: '¥',
  CHF: 'CHF ', CAD: 'C$', AUD: 'A$', NZD: 'NZ$', SGD: 'S$', HKD: 'HK$',
  CZK: ' Kč', PLN: ' zł', SEK: ' kr', NOK: ' kr',
  DKK: ' kr', HUF: ' Ft', RON: ' lei', BGN: ' lv',
  ISK: ' kr', INR: '₹', KRW: '₩', BRL: 'R$', MXN: 'Mex$',
  ZAR: 'R', TRY: '₺', ILS: '₪', THB: '฿', PHP: '₱',
  IDR: 'Rp', MYR: 'RM',
};
// Symbols that follow the number (with a leading thin space).
const SYMBOL_SUFFIX = new Set(['CZK','PLN','SEK','NOK','DKK','HUF','RON','BGN','ISK','CHF']);
let fxState = {
  base: 'USD',
  rates: { USD: 1.0 },
  asOf: null,
  currency: (localStorage.getItem(FX_CURRENCY_LS_KEY) || 'USD'),
};

function _symbolFor(cur) { return CURRENCY_SYMBOLS[cur] || (cur + ' '); }
function convertCost(usdAmount) {
  if (typeof usdAmount !== 'number' || !isFinite(usdAmount)) return 0;
  const r = (fxState.rates && fxState.rates[fxState.currency]);
  return (typeof r === 'number') ? usdAmount * r : usdAmount;
}
function _fmtAmount(value, digits) {
  const cur = fxState.currency;
  // Currencies with no minor units / where decimals are noisy.
  if (cur === 'JPY' || cur === 'KRW' || cur === 'HUF' || cur === 'IDR') {
    digits = (digits >= 2) ? 0 : 0;
  }
  return value.toFixed(digits);
}
function fmtCostCurrency(usdAmount, digits) {
  if (typeof digits !== 'number') digits = 2;
  const value = convertCost(usdAmount);
  const sym = _symbolFor(fxState.currency);
  const formatted = _fmtAmount(value, digits);
  return SYMBOL_SUFFIX.has(fxState.currency) ? (formatted + sym) : (sym + formatted);
}
function fmtCost(c)    { return fmtCostCurrency(c, 4); }
function fmtCostBig(c) { return fmtCostCurrency(c, 2); }

async function _loadFxRates() {
  // Try localStorage cache first.
  try {
    const raw = localStorage.getItem(FX_LS_KEY);
    if (raw) {
      const cached = JSON.parse(raw);
      if (cached && cached.cached_at && (Date.now() - cached.cached_at) < FX_CLIENT_TTL_MS
          && cached.rates && typeof cached.rates === 'object') {
        fxState.rates = cached.rates;
        fxState.asOf  = cached.as_of || null;
        fxState.base  = cached.base || 'USD';
        return;
      }
    }
  } catch (e) { /* ignore */ }
  try {
    const resp = await fetch('/api/fx-rates');
    const d = await resp.json();
    if (d && d.rates) {
      fxState.rates = d.rates;
      fxState.asOf  = d.as_of || null;
      fxState.base  = d.base || 'USD';
      try {
        localStorage.setItem(FX_LS_KEY, JSON.stringify({
          rates: d.rates, as_of: d.as_of, base: d.base || 'USD',
          cached_at: Date.now(), fallback: !!d.fallback,
        }));
      } catch (e) { /* ignore quota */ }
    }
  } catch (e) { console.error('FX fetch failed', e); }
}

function _populateCurrencyOptions() {
  const sel = document.getElementById('currency-select');
  if (!sel) return;
  const codes = Object.keys(fxState.rates || {});
  // Make sure USD is always present and first.
  if (codes.indexOf('USD') === -1) codes.unshift('USD');
  codes.sort((a, b) => (a === 'USD' ? -1 : b === 'USD' ? 1 : a.localeCompare(b)));
  sel.innerHTML = codes.map(c =>
    `<option value="${c}"${c === fxState.currency ? ' selected' : ''}>${c} ${_symbolFor(c).trim() || ''}</option>`
  ).join('');
  if (!fxState.rates[fxState.currency]) {
    fxState.currency = 'USD';
    sel.value = 'USD';
  }
  if (fxState.asOf) {
    const footer = document.getElementById('fx-footer');
    const asof   = document.getElementById('fx-asof');
    if (asof) asof.textContent = fxState.asOf;
    if (footer) footer.style.display = '';
  }
}

function onCurrencyChange(code) {
  fxState.currency = code || 'USD';
  try { localStorage.setItem(FX_CURRENCY_LS_KEY, fxState.currency); } catch (e) {}
  if (typeof applyFilter === 'function' && typeof rawData !== 'undefined' && rawData) {
    applyFilter();
  }
}

// Kick off FX load early. When done, populate dropdown and re-render if data is ready.
_loadFxRates().then(() => {
  _populateCurrencyOptions();
  if (typeof rawData !== 'undefined' && rawData && typeof applyFilter === 'function') {
    applyFilter();
  }
});

// ── Chart colors ───────────────────────────────────────────────────────────
function tokenColors() {
  const c = chartColors();
  return { input: c.c1, output: c.c2, cache_read: c.c3, cache_creation: c.c4 };
}
function modelColors() {
  const c = chartColors();
  return [c.c1, c.c2, c.c3, c.c4, '#ff3b30', '#ff2d55', '#64d2ff', '#30d158'];
}

// ── Time range ─────────────────────────────────────────────────────────────
const RANGE_LABELS = { 'today': 'Today', 'week': 'This Week', 'month': 'This Month', 'prev-month': 'Previous Month', '7d': 'Last 7 Days', '30d': 'Last 30 Days', '90d': 'Last 90 Days', 'all': 'All Time' };
const RANGE_TICKS  = { 'today': 1, 'week': 7, 'month': 15, 'prev-month': 15, '7d': 7, '30d': 15, '90d': 13, 'all': 12 };
const VALID_RANGES = Object.keys(RANGE_LABELS);

function rangeIncludesToday(range) {
  if (range === 'all') return true;
  const { start, end } = getRangeBounds(range);
  const today = new Date().toISOString().slice(0, 10);
  if (start && today < start) return false;
  if (end && today > end) return false;
  return true;
}

function getRangeBounds(range) {
  if (range === 'all') return { start: null, end: null };
  const today = new Date();
  const iso = d => d.toISOString().slice(0, 10);
  if (range === 'today') {
    const t = iso(today);
    return { start: t, end: t };
  }
  if (range === 'week') {
    const day = today.getDay();
    const diffToMon = day === 0 ? 6 : day - 1;
    const mon = new Date(today); mon.setDate(today.getDate() - diffToMon);
    const sun = new Date(mon); sun.setDate(mon.getDate() + 6);
    return { start: iso(mon), end: iso(sun) };
  }
  if (range === 'month') {
    const start = new Date(today.getFullYear(), today.getMonth(), 1);
    const end = new Date(today.getFullYear(), today.getMonth() + 1, 0);
    return { start: iso(start), end: iso(end) };
  }
  if (range === 'prev-month') {
    const start = new Date(today.getFullYear(), today.getMonth() - 1, 1);
    const end = new Date(today.getFullYear(), today.getMonth(), 0);
    return { start: iso(start), end: iso(end) };
  }
  const days = range === '7d' ? 7 : range === '30d' ? 30 : 90;
  const d = new Date();
  d.setDate(d.getDate() - days);
  return { start: iso(d), end: iso(today) };
}

function readURLRange() {
  const p = new URLSearchParams(window.location.search).get('range');
  return VALID_RANGES.includes(p) ? p : '30d';
}

function setRange(range) {
  _savePrefs({ range: range });
  selectedRange = range;
  document.querySelectorAll('[data-range]').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.range === range)
  );
  // Keep Period A row in sync when user picks from the main pills.
  document.querySelectorAll('[data-rangea]').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.rangea === range)
  );
  updateURL();
  applyFilter();
  scheduleAutoRefresh();
}

function setHourlyTZ(mode) {
  _savePrefs({ hourlyTZ: tz });
  hourlyTZ = mode;
  document.querySelectorAll('.tz-btn').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.tz === mode)
  );
  applyFilter();
}

// ── Account filter ─────────────────────────────────────────────────────────
function readURLAccount() {
  const p = new URLSearchParams(window.location.search).get('account');
  return p || 'all';
}

function buildAccountUI(accounts) {
  // Hide the dropdown entirely when the user only has one account — it would
  // be a no-op control otherwise. The "all" option is implicit in that case.
  const sel = document.getElementById('account-select');
  const lbl = document.getElementById('accounts-label');
  const sep = document.getElementById('accounts-sep');
  if (!accounts || accounts.length <= 1) {
    sel.style.display = 'none';
    lbl.style.display = 'none';
    sep.style.display = 'none';
    selectedAccount = 'all';
    return;
  }
  sel.style.display = '';
  lbl.style.display = '';
  sep.style.display = '';
  const opts = ['<option value="all">All accounts</option>']
    .concat(accounts.map(a => `<option value="${esc(a.name)}">${esc(a.name)} (${a.sessions})</option>`));
  sel.innerHTML = opts.join('');
  // Restore saved/URL selection, falling back to "all" if it no longer exists.
  const wanted = readURLAccount();
  const valid = wanted === 'all' || accounts.some(a => a.name === wanted);
  selectedAccount = valid ? wanted : 'all';
  sel.value = selectedAccount;
}

function onAccountChange(value) {
  selectedAccount = value || 'all';
  _savePrefs({ account: selectedAccount });
  updateURL();
  applyFilter();
}
// ── A/B compare mode ───────────────────────────────────────────────────────
function toggleCompareMode() {
  compareMode = !compareMode;
  _savePrefs({ compareMode: compareMode });
  updateCompareUI();
  applyFilter();
}

function setRangeA(range) {
  // Period A is the existing `selectedRange` — keep them in sync.
  setRange(range);
}

function setRangeB(range) {
  _savePrefs({ rangeB: range });
  selectedRangeB = range;
  document.querySelectorAll('[data-rangeb]').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.rangeb === range)
  );
  applyFilter();
}

function updateCompareUI() {
  const btn = document.getElementById('compare-toggle');
  const row = document.getElementById('compare-row');
  if (btn) {
    btn.textContent = compareMode ? 'on' : 'off';
    btn.classList.toggle('active', compareMode);
  }
  if (row) row.classList.toggle('visible', compareMode);
  // Mirror the main range pills onto Period A buttons.
  document.querySelectorAll('[data-rangea]').forEach(b =>
    b.classList.toggle('active', b.dataset.rangea === selectedRange)
  );
  document.querySelectorAll('[data-rangeb]').forEach(b =>
    b.classList.toggle('active', b.dataset.rangeb === selectedRangeB)
  );
}

// ── Model filter ───────────────────────────────────────────────────────────
function modelPriority(m) {
  const ml = m.toLowerCase();
  if (ml.includes('opus'))   return 0;
  if (ml.includes('sonnet')) return 1;
  if (ml.includes('haiku'))  return 2;
  return 3;
}

function readURLModels(allModels) {
  const param = new URLSearchParams(window.location.search).get('models');
  if (!param) {
    const billable = allModels.filter(m => isBillable(m));
    return new Set(billable.length > 0 ? billable : allModels);
  }
  const fromURL = new Set(param.split(',').map(s => s.trim()).filter(Boolean));
  return new Set(allModels.filter(m => fromURL.has(m)));
}

function isDefaultModelSelection(allModels) {
  const billable = allModels.filter(m => isBillable(m));
  if (selectedModels.size !== billable.length) return false;
  return billable.every(m => selectedModels.has(m));
}

function buildFilterUI(allModels) {
  const sorted = [...allModels].sort((a, b) => {
    const pa = modelPriority(a), pb = modelPriority(b);
    return pa !== pb ? pa - pb : a.localeCompare(b);
  });
  selectedModels = readURLModels(allModels);
  const container = document.getElementById('model-checkboxes');
  container.innerHTML = sorted.map(m => {
    const checked = selectedModels.has(m);
    return `<label class="model-cb-label ${checked ? 'checked' : ''}" data-model="${esc(m)}">
      <input type="checkbox" value="${esc(m)}" ${checked ? 'checked' : ''} onchange="onModelToggle(this)">
      ${esc(m)}
    </label>`;
  }).join('');
}

function onModelToggle(cb) {
  _savePrefs({ models: Array.from(selectedModels) });
  const label = cb.closest('label');
  if (cb.checked) { selectedModels.add(cb.value);    label.classList.add('checked'); }
  else            { selectedModels.delete(cb.value); label.classList.remove('checked'); }
  updateURL();
  applyFilter();
}

function selectAllModels() {
  _savePrefs({ models: Array.from(selectedModels) });
  document.querySelectorAll('#model-checkboxes input').forEach(cb => {
    cb.checked = true; selectedModels.add(cb.value); cb.closest('label').classList.add('checked');
  });
  updateURL(); applyFilter();
}

function clearAllModels() {
  document.querySelectorAll('#model-checkboxes input').forEach(cb => {
    cb.checked = false; selectedModels.delete(cb.value); cb.closest('label').classList.remove('checked');
  });
  updateURL(); applyFilter();
}

// ── URL persistence ────────────────────────────────────────────────────────
function updateURL() {
  const allModels = Array.from(document.querySelectorAll('#model-checkboxes input')).map(cb => cb.value);
  const params = new URLSearchParams();
  if (selectedRange !== '30d') params.set('range', selectedRange);
  if (!isDefaultModelSelection(allModels)) params.set('models', Array.from(selectedModels).join(','));
  if (selectedAccount && selectedAccount !== 'all') params.set('account', selectedAccount);
  const search = params.toString() ? '?' + params.toString() : '';
  history.replaceState(null, '', window.location.pathname + search);
}

// ── Session sort ───────────────────────────────────────────────────────────
function setSessionSort(col) {
  if (sessionSortCol === col) {
    sessionSortDir = sessionSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    sessionSortCol = col;
    sessionSortDir = 'desc';
  }
  updateSortIcons();
  applyFilter();
}

function updateSortIcons() {
  document.querySelectorAll('.sort-icon').forEach(el => el.textContent = '');
  const icon = document.getElementById('sort-icon-' + sessionSortCol);
  if (icon) icon.textContent = sessionSortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function sortSessions(sessions) {
  return [...sessions].sort((a, b) => {
    let av, bv;
    if (sessionSortCol === 'cost') {
      av = calcCost(a.model, a.input, a.output, a.cache_read, a.cache_creation, a.cache_1h);
      bv = calcCost(b.model, b.input, b.output, b.cache_read, b.cache_creation, b.cache_1h);
    } else if (sessionSortCol === 'duration_min') {
      av = parseFloat(a.duration_min) || 0;
      bv = parseFloat(b.duration_min) || 0;
    } else {
      av = a[sessionSortCol] ?? 0;
      bv = b[sessionSortCol] ?? 0;
    }
    if (av < bv) return sessionSortDir === 'desc' ? 1 : -1;
    if (av > bv) return sessionSortDir === 'desc' ? -1 : 1;
    return 0;
  });
}

// ── Aggregation & filtering ────────────────────────────────────────────────
// computePeriod(range) — pure aggregation for one window. Used once in the
// default case and twice in A/B compare-mode (one call per period). The
// returned object carries totals, sorted breakdowns, daily series, and the
// filtered session list so callers can pipe each piece to its renderer.
function computePeriod(range) {
  const { start, end } = getRangeBounds(range);

  const filteredDaily = rawData.daily_by_model.filter(r =>
    selectedModels.has(r.model) && (!start || r.day >= start) && (!end || r.day <= end)
  );

  // Previous-period totals (same-length window ending the day before `start`)
  // so each stat card can show a delta vs. the equivalent prior window.
  let prevTotals = null;
  if (start && end) {
    const [prevStart, prevEnd] = _prevWindow(start, end);
    const prevDaily = rawData.daily_by_model.filter(r =>
      selectedModels.has(r.model) && r.day >= prevStart && r.day <= prevEnd
    );
    prevTotals = { input: 0, output: 0, cache_read: 0, cache_creation: 0, turns: 0, cost: 0 };
    const prevSessIds = new Set();
    for (const r of prevDaily) {
      prevTotals.input  += r.input;
      prevTotals.output += r.output;
      prevTotals.cache_read     += r.cache_read;
      prevTotals.cache_creation += r.cache_creation;
      prevTotals.turns  += r.turns;
    }
    // Sessions: count sessions whose last_date is in the previous window
    const inAccountPrev = (s) => selectedAccount === 'all' || (s.account || 'default') === selectedAccount;
    for (const s of rawData.sessions_all) {
      if (!selectedModels.has(s.model)) continue;
      if (!inAccountPrev(s)) continue;
      if (s.last_date >= prevStart && s.last_date <= prevEnd) prevSessIds.add(s.session_id);
    }
    prevTotals.sessions = prevSessIds.size;
    prevTotals.cost = prevDaily.reduce(
      (acc, r) => acc + calcCost(r.model, r.input, r.output, r.cache_read, r.cache_creation),
      0,
    );
  }

  // Daily chart: aggregate by day
  // Daily chart series
  const dailyMap = {};
  for (const r of filteredDaily) {
    if (!dailyMap[r.day]) dailyMap[r.day] = { day: r.day, input: 0, output: 0, cache_read: 0, cache_creation: 0 };
    const d = dailyMap[r.day];
    d.input          += r.input;
    d.output         += r.output;
    d.cache_read     += r.cache_read;
    d.cache_creation += r.cache_creation;
  }
  const daily = Object.values(dailyMap).sort((a, b) => a.day.localeCompare(b.day));

  // By model
  const modelMap = {};
  for (const r of filteredDaily) {
    if (!modelMap[r.model]) modelMap[r.model] = { model: r.model, input: 0, output: 0, cache_read: 0, cache_creation: 0, turns: 0, sessions: 0 };
    const m = modelMap[r.model];
    m.input          += r.input;
    m.output         += r.output;
    m.cache_read     += r.cache_read;
    m.cache_creation += r.cache_creation;
    m.turns          += r.turns;
  }

  // Filter sessions by model + date range + account + machine + search
  const inAccount = (s) => selectedAccount === 'all' || (s.account || 'default') === selectedAccount;
  const filteredSessions = rawData.sessions_all.filter(s => _matchesSearch(s) &&
    selectedModels.has(s.model)
    && (!start || s.last_date >= start)
    && (!end || s.last_date <= end)
    && inAccount(s)
    && (!selectedMachine || s.machine_id === selectedMachine)
  );
  for (const s of filteredSessions) {
    if (modelMap[s.model]) modelMap[s.model].sessions++;
  }
  const byModel = Object.values(modelMap).sort((a, b) => (b.input + b.output) - (a.input + a.output));

  // By project
  const projMap = {};
  for (const s of filteredSessions) {
    if (!projMap[s.project]) projMap[s.project] = { project: s.project, input: 0, output: 0, cache_read: 0, cache_creation: 0, turns: 0, sessions: 0, cost: 0 };
    const p = projMap[s.project];
    p.input          += s.input;
    p.output         += s.output;
    p.cache_read     += s.cache_read;
    p.cache_creation += s.cache_creation;
    p.turns          += s.turns;
    p.sessions++;
    p.cost += calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation, s.cache_1h);
  }
  const byProject = Object.values(projMap).sort((a, b) => (b.input + b.output) - (a.input + a.output));

  // By project+branch
  const projBranchMap = {};
  for (const s of filteredSessions) {
    const key = s.project + '\x00' + (s.branch || '');
    if (!projBranchMap[key]) projBranchMap[key] = { project: s.project, branch: s.branch || '', input: 0, output: 0, cache_read: 0, cache_creation: 0, turns: 0, sessions: 0, cost: 0 };
    const pb = projBranchMap[key];
    pb.input          += s.input;
    pb.output         += s.output;
    pb.cache_read     += s.cache_read;
    pb.cache_creation += s.cache_creation;
    pb.turns          += s.turns;
    pb.sessions++;
    pb.cost += calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation, s.cache_1h);
  }
  const byProjectBranch = Object.values(projBranchMap).sort((a, b) => b.cost - a.cost);

  // By branch (project + branch tuple, with cache columns surfaced separately
  // from the existing Project & Branch card so users can see cache mix per branch)
  const byBranch = _costByBranch(filteredSessions);

  // Totals
  // Count distinct models that have token usage in the filtered window but
  // aren't priced (e.g. router proxies, custom finetunes). The Est. Cost
  // card aggregates only billable models; this lets us tell the user how
  // many models we silently dropped.
  const nonBillableModels = new Set();
  for (const r of filteredDaily) {
    if (!isBillable(r.model) && (r.input + r.output) > 0) {
      nonBillableModels.add(r.model);
    }
  }

  const totals = {
    sessions:       filteredSessions.length,
    turns:          byModel.reduce((s, m) => s + m.turns, 0),
    input:          byModel.reduce((s, m) => s + m.input, 0),
    output:         byModel.reduce((s, m) => s + m.output, 0),
    cache_read:     byModel.reduce((s, m) => s + m.cache_read, 0),
    cache_creation: byModel.reduce((s, m) => s + m.cache_creation, 0),
    cost:           byModel.reduce((s, m) => s + calcCost(m.model, m.input, m.output, m.cache_read, m.cache_creation, m.cache_1h), 0),
    nonBillableCount: nonBillableModels.size,
    nonBillableModels: Array.from(nonBillableModels),
  };

  const hourlySrc = (rawData.hourly_by_model || []).filter(r =>
    selectedModels.has(r.model) && (!start || r.day >= start) && (!end || r.day <= end)
  );
  const hourlyAgg = aggregateHourly(hourlySrc, hourlyTZ);

  return {
    range, start, end,
    totals, prevTotals,
    daily, byModel, byProject, byProjectBranch,
    filteredSessions, hourlyAgg,
  };
}
// applyFilter — single entry point used by every onChange in the UI. In the
// default case it computes one period (selectedRange). In compare-mode it
// computes both periods and hands the diff data to the dual renderers.
function applyFilter() {
  if (!rawData) return;

  const pa = computePeriod(selectedRange);
  const pb = compareMode ? computePeriod(selectedRangeB) : null;

  // Titles include both labels in compare mode.
  const titleSuffix = compareMode
    ? RANGE_LABELS[selectedRange] + ' vs ' + RANGE_LABELS[selectedRangeB]
    : RANGE_LABELS[selectedRange];
  document.getElementById('daily-chart-title').textContent  = 'Daily Token Usage — ' + titleSuffix;
  document.getElementById('hourly-chart-title').textContent = 'Average Hourly Distribution — ' + RANGE_LABELS[selectedRange];

  // Period-specific renderers: differ between single-view and A/B compare.
  if (compareMode && pb) {
    renderStatsAB(pa.totals, pb.totals);
    renderDailyChartAB(pa.daily, pb.daily);
    renderModelCostTableAB(pa.byModel, pb.byModel);
    renderProjectCostTableAB(pa.byProject, pb.byProject);
    // Sessions: union, colour-coded by period membership.
    const ids = new Map();
    for (const s of pa.filteredSessions) ids.set(s.session_id_full, 'period-a');
    for (const s of pb.filteredSessions) {
      ids.set(s.session_id_full, ids.has(s.session_id_full) ? 'period-ab' : 'period-b');
    }
    const seen = new Set();
    const union = [];
    for (const s of pa.filteredSessions.concat(pb.filteredSessions)) {
      if (seen.has(s.session_id_full)) continue;
      seen.add(s.session_id_full);
      union.push(Object.assign({}, s, { _abClass: ids.get(s.session_id_full) }));
    }
    lastFilteredSessions = sortSessions(union);
    lastByProject = sortProjects(pa.byProject);
    lastByProjectBranch = sortProjectBranch(pa.byProjectBranch);
    renderSessionsTable(lastFilteredSessions.slice(0, 20));
  } else {
    renderStats(pa.totals, pa.prevTotals);
    renderDailyChart(pa.daily);
    renderPareto(lastFilteredSessions || pa.filteredSessions);
    lastFilteredSessions = sortSessions(pa.filteredSessions);
    lastByProject = sortProjects(pa.byProject);
    lastByProjectBranch = sortProjectBranch(pa.byProjectBranch);
    renderSessionsTable(lastFilteredSessions.slice(0, 20));
    renderModelCostTable(pa.byModel);
    renderProjectCostTable(lastByProject.slice(0, 20));
  }

  // Shared renderers: run in BOTH single-view and compare mode. Period A is
  // used as the reference period in compare mode (avoids leaving half the
  // dashboard blank when the toggle is on).
  renderModelChart(pa.byModel);
  renderProjectChart(pa.byProject);
  renderHourlyChart(pa.hourlyAgg);
  renderProjectBranchCostTable(sortProjectBranch(pa.byProjectBranch).slice(0, 20));
  const filteredTools = (rawData.tools_daily || []).filter(r => (!pa.start || r.day >= pa.start) && (!pa.end || r.day <= pa.end));
  renderBudget();
  renderYearCalendar(rawData.year_calendar || []);
  renderToolsChart(filteredTools);
  renderAnomalyBanner();
  renderDowHourHeatmap();
  renderHistogram();
  renderPlanCard();
  renderDowngradeSuggestions();
  renderCacheHit(rawData ? rawData.cache_hit_summary : null);
  renderInbound();
  renderTimeOnTask();
  renderBranchOnlyCostTable(lastByBranch.slice(0, 20));
  renderCache1hOpportunities(rawData.cache_1h_opportunities || []);
  renderPlanLimits();

  const visibleSessions = lastFilteredSessions.slice(0, 20);
  if (!visibleSessions.length) {
    selectedSessionId = null;
    document.getElementById('session-detail-card').style.display = 'none';
    return;
  }
  if (!selectedSessionId || !visibleSessions.some(s => s.session_id_full === selectedSessionId)) {
    selectedSessionId = visibleSessions[0].session_id_full;
  }
  selectSession(selectedSessionId);
}

function selectSession(sessionId) {
  selectedSessionId = sessionId;
  document.querySelectorAll('tr.session-row').forEach(row =>
    row.classList.toggle('selected', row.dataset.sessionId === sessionId)
  );
  loadSessionDetail(sessionId);
}

async function loadSessionDetail(sessionId) {
  if (!sessionId) return;
  try {
    const resp = await fetch('/api/session?session_id=' + encodeURIComponent(sessionId));
    if (selectedSessionId !== sessionId) return;
    const detail = await resp.json();
    if (detail.error) return;
    renderSessionDetail(detail);
  } catch (e) {
    console.error(e);
  }
}

function renderSessionDetail(detail) {
  const detailCard = document.getElementById('session-detail-card');
  detailCard.style.display = '';

  const toolPills = detail.tool_usage.length
    ? detail.tool_usage.map(t => `<span class="pill">${esc(t.tool_name)} · ${fmt(t.tokens)} tokens · ${fmt(t.turns)} turns</span>`).join('')
    : '<div class="hint">No tool usage recorded.</div>';

  const cwdPills = detail.cwd_usage.length
    ? detail.cwd_usage.map(c => `<span class="pill">${esc(c.cwd)} · ${fmt(c.turns)} turns</span>`).join('')
    : '<div class="hint">No working directory recorded.</div>';

  document.getElementById('session-detail').innerHTML = `
    <div class="detail-meta">
      <div><div class="label">Session</div><div class="value" style="font-family:monospace">${esc(detail.session_id)}</div></div>
      <div><div class="label">Project</div><div class="value">${projCellHTML(detail.project)}</div></div>
      <div><div class="label">Branch</div><div class="value">${esc(detail.branch || 'n/a')}</div></div>
      <div><div class="label">Model</div><div class="value">${esc(detail.model)}</div></div>
      <div><div class="label">First Seen</div><div class="value">${esc(detail.first)}</div></div>
      <div><div class="label">Last Seen</div><div class="value">${esc(detail.last)}</div></div>
      <div><div class="label">Duration</div><div class="value">${esc(String(detail.duration_min))}m</div></div>
      <div><div class="label">Tokens</div><div class="value">${fmt(detail.input + detail.output + detail.cache_read + detail.cache_creation)}</div></div>
    </div>
    <div class="detail-grid">
      <div class="detail-card">
        <h3>Turn History</h3>
        <div class="detail-table-wrap">
          <table>
            <thead><tr>
              <th>Time</th><th>Tool</th><th>Model</th><th>Input</th><th>Output</th><th>Cache Read</th><th>Cache Write</th><th>Total</th>
            </tr></thead>
            <tbody>${detail.turn_history.map(t => `
              <tr>
                <td class="muted">${esc(t.timestamp_short)}</td>
                <td>${esc(t.tool_name)}</td>
                <td>${esc(t.model)}</td>
                <td class="num">${fmt(t.input)}</td>
                <td class="num">${fmt(t.output)}</td>
                <td class="num">${fmt(t.cache_read)}</td>
                <td class="num">${fmt((t.cache_creation || 0) + (t.cache_1h || 0))}</td>
                <td class="num">${fmt(t.total)}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>
      </div>
      <div>
        <div class="detail-card" style="margin-bottom:16px;">
          <h3>Tool Usage</h3>
          <div class="pill-list">${toolPills}</div>
        </div>
        <div class="detail-card">
          <h3>Working Directories</h3>
          <div class="pill-list">${cwdPills}</div>
        </div>
      </div>
    </div>`;
}

// ── Renderers ────────────────────────────────────────────────────────────────────────────
function _costSub(t) {
  const base = 'API pricing, Apr 2026';
  if (!t || !t.nonBillableCount) return base;
  const n = t.nonBillableCount;
  return base + ' • ' + n + ' model' + (n === 1 ? '' : 's') + ' excluded';
}
function _costTitle(t) {
  if (!t || !t.nonBillableCount) return '';
  return 'Excluded from cost (not in PRICING table): ' +
    (t.nonBillableModels || []).slice(0, 5).join(', ') +
    (t.nonBillableModels.length > 5 ? ', ...' : '');
}

function _prevWindow(start, end) {
  // Return [prevStart, prevEnd] — a same-length window ending the day before
  // `start` (inclusive). Dates are YYYY-MM-DD strings, manipulated via Date.
  const sd = new Date(start + 'T00:00:00Z');
  const ed = new Date(end + 'T00:00:00Z');
  const lenMs = ed - sd;
  const prevEnd = new Date(sd.getTime() - 24 * 3600 * 1000);
  const prevStart = new Date(prevEnd.getTime() - lenMs);
  const fmtD = d => d.toISOString().slice(0, 10);
  return [fmtD(prevStart), fmtD(prevEnd)];
}

function _deltaBadge(curr, prev) {
  if (prev == null || prev === undefined) return '';
  if (prev === 0) return curr > 0 ? ' <span class="delta delta-up">new</span>' : '';
  const pct = ((curr - prev) / prev) * 100;
  if (Math.abs(pct) < 1) return '';  // ignore noise
  const sign = pct > 0 ? '+' : '';
  const cls = pct > 0 ? 'delta-up' : 'delta-down';
  return ` <span class="delta ${cls}">${sign}${pct.toFixed(0)}%</span>`;
}

// ── Forecast helpers ──────────────────────────────────────────
function _f() { return (rawData && rawData.forecast) || null; }
function forecastValue() {
  const f = _f();
  if (!f || !f.days_in_data) return 'n/a';
  return '$' + (f.projected_month_end || 0).toFixed(2);
}
function forecastSub() {
  const f = _f();
  if (!f || !f.days_in_data) return 'no spend data yet';
  const arrow = f.trend === 'up' ? '\u2191' : f.trend === 'down' ? '\u2193' : '\u2192';
  return arrow + ' $' + f.avg_7d.toFixed(2) + '/day (7d) \u2022 ' + f.days_left_in_month + 'd left in month';
}
function forecastColor() {
  const f = _f();
  if (!f) return '';
  return f.trend === 'up' ? '#f87171' : f.trend === 'down' ? '#4ade80' : '';
}
// ── A/B compare renderers ───────────────────────────────────────────────
// Tiny helpers — keep delta math in one place so stats cards, model table,
// and project table all format the same way.
function _abPct(a, b) {
  if (b == null) return null;
  if (b === 0) return a > 0 ? Infinity : 0;
  return ((a - b) / b) * 100;
}
function _abPctLabel(a, b) {
  const p = _abPct(a, b);
  if (p == null) return '';
  if (p === Infinity) return ' <span class="delta delta-up">new</span>';
  if (Math.abs(p) < 1) return ' <span class="delta">~</span>';
  const cls = p > 0 ? 'delta-up' : 'delta-down';
  const sign = p > 0 ? '+' : '';
  return ' <span class="delta ' + cls + '">' + sign + p.toFixed(0) + '%</span>';
}

function renderStatsAB(a, b) {
  const labelA = RANGE_LABELS[selectedRange];
  const labelB = RANGE_LABELS[selectedRangeB];
  const cards = [
    { label: 'Sessions',       fmt: v => v.toLocaleString(), aVal: a.sessions,       bVal: b.sessions },
    { label: 'Turns',          fmt: fmt,                     aVal: a.turns,          bVal: b.turns },
    { label: 'Input Tokens',   fmt: fmt,                     aVal: a.input,          bVal: b.input },
    { label: 'Output Tokens',  fmt: fmt,                     aVal: a.output,         bVal: b.output },
    { label: 'Cache Read',     fmt: fmt,                     aVal: a.cache_read,     bVal: b.cache_read },
    { label: 'Cache Creation', fmt: fmt,                     aVal: a.cache_creation, bVal: b.cache_creation },
    { label: 'Est. Cost',      fmt: fmtCostBig,              aVal: a.cost,           bVal: b.cost, color: '#4ade80' },
  ];
  document.getElementById('stats-row').innerHTML = cards.map(c => `
    <div class="stat-card">
      <div class="label">${c.label}${_abPctLabel(c.aVal, c.bVal)}</div>
      <div class="ab-stat">
        <div class="ab-stat-row"><span><span class="ab-tag ab-tag-a">A</span>${esc(labelA)}</span><span class="ab-cell" style="${c.color ? 'color:' + c.color : ''}">${esc(c.fmt(c.aVal))}</span></div>
        <div class="ab-stat-row"><span><span class="ab-tag ab-tag-b">B</span>${esc(labelB)}</span><span class="ab-cell muted">${esc(c.fmt(c.bVal))}</span></div>
      </div>
    </div>
  `).join('');
}

// Daily chart in compare mode — overlay both periods on the same x axis as
// "Day 1 of period", "Day 2 of period", etc. so windows of different lengths
// can still be visually compared.
function renderDailyChartAB(dailyA, dailyB) {
  const ctx = document.getElementById('chart-daily').getContext('2d');
  if (charts.daily) charts.daily.destroy();
  const len = Math.max(dailyA.length, dailyB.length);
  const labels = [];
  for (let i = 0; i < len; i++) labels.push('Day ' + (i + 1));
  const sumTokens = d => d.input + d.output + d.cache_read + d.cache_creation;
  const dataA = dailyA.map(sumTokens);
  const dataB = dailyB.map(sumTokens);
  while (dataA.length < len) dataA.push(0);
  while (dataB.length < len) dataB.push(0);
  charts.daily = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [
        { label: 'A · ' + RANGE_LABELS[selectedRange],  data: dataA, backgroundColor: 'rgba(0,113,227,0.75)' },
        { label: 'B · ' + RANGE_LABELS[selectedRangeB], data: dataB, backgroundColor: 'rgba(245,158,11,0.75)' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: chartColors().label, boxWidth: 12 } } },
      scales: {
        x: { ticks: { color: chartColors().label, maxTicksLimit: 15 }, grid: { color: chartColors().grid } },
        y: { ticks: { color: chartColors().label, callback: v => fmt(v) }, grid: { color: chartColors().grid } },
      }
    }
  });
}

function renderModelCostTableAB(byModelA, byModelB) {
  const bIndex = new Map();
  for (const m of byModelB) bIndex.set(m.model, m);
  const allKeys = new Set([...byModelA.map(m => m.model), ...byModelB.map(m => m.model)]);
  const rows = [];
  for (const key of allKeys) {
    const a = byModelA.find(m => m.model === key) || { model: key, input: 0, output: 0, cache_read: 0, cache_creation: 0, turns: 0, cache_1h: 0 };
    const b = bIndex.get(key)                    || { model: key, input: 0, output: 0, cache_read: 0, cache_creation: 0, turns: 0, cache_1h: 0 };
    const ca = calcCost(a.model, a.input, a.output, a.cache_read, a.cache_creation, a.cache_1h);
    const cb = calcCost(b.model, b.input, b.output, b.cache_read, b.cache_creation, b.cache_1h);
    rows.push({ model: key, a, b, ca, cb });
  }
  rows.sort((x, y) => (y.ca + y.cb) - (x.ca + x.cb));
  document.getElementById('model-cost-body').innerHTML = rows.map(r => {
    const costCellA = isBillable(r.model) ? fmtCost(r.ca) : '<span class="muted">n/a</span>';
    const costCellB = isBillable(r.model) ? fmtCost(r.cb) : '<span class="muted">n/a</span>';
    return `<tr>
      <td><span class="model-tag">${esc(r.model)}</span></td>
      <td class="num"><span class="ab-tag ab-tag-a">A</span>${fmt(r.a.turns)}<br><span class="ab-tag ab-tag-b">B</span>${fmt(r.b.turns)}</td>
      <td class="num"><span class="ab-tag ab-tag-a">A</span>${fmt(r.a.input)}<br><span class="ab-tag ab-tag-b">B</span>${fmt(r.b.input)}</td>
      <td class="num"><span class="ab-tag ab-tag-a">A</span>${fmt(r.a.output)}<br><span class="ab-tag ab-tag-b">B</span>${fmt(r.b.output)}</td>
      <td class="num hide-mobile"><span class="ab-tag ab-tag-a">A</span>${fmt(r.a.cache_read)}<br><span class="ab-tag ab-tag-b">B</span>${fmt(r.b.cache_read)}</td>
      <td class="num hide-mobile"><span class="ab-tag ab-tag-a">A</span>${fmt(r.a.cache_creation)}<br><span class="ab-tag ab-tag-b">B</span>${fmt(r.b.cache_creation)}</td>
      <td class="cost"><span class="ab-tag ab-tag-a">A</span>${costCellA}<br><span class="ab-tag ab-tag-b">B</span>${costCellB}${_abPctLabel(r.ca, r.cb)}</td>
    </tr>`;
  }).join('');
}

function renderProjectCostTableAB(byProjectA, byProjectB) {
  const aIdx = new Map();  for (const p of byProjectA) aIdx.set(p.project, p);
  const bIdx = new Map();  for (const p of byProjectB) bIdx.set(p.project, p);
  const keys = new Set([...aIdx.keys(), ...bIdx.keys()]);
  const rows = [];
  for (const k of keys) {
    const a = aIdx.get(k) || { project: k, sessions: 0, turns: 0, input: 0, output: 0, cost: 0 };
    const b = bIdx.get(k) || { project: k, sessions: 0, turns: 0, input: 0, output: 0, cost: 0 };
    rows.push({ project: k, a, b });
  }
  rows.sort((x, y) => (y.a.cost + y.b.cost) - (x.a.cost + x.b.cost));
  document.getElementById('project-cost-body').innerHTML = rows.slice(0, 20).map(r => `
    <tr>
      <td>${esc(r.project)}</td>
      <td class="num"><span class="ab-tag ab-tag-a">A</span>${r.a.sessions}<br><span class="ab-tag ab-tag-b">B</span>${r.b.sessions}</td>
      <td class="num"><span class="ab-tag ab-tag-a">A</span>${fmt(r.a.turns)}<br><span class="ab-tag ab-tag-b">B</span>${fmt(r.b.turns)}</td>
      <td class="num"><span class="ab-tag ab-tag-a">A</span>${fmt(r.a.input)}<br><span class="ab-tag ab-tag-b">B</span>${fmt(r.b.input)}</td>
      <td class="num"><span class="ab-tag ab-tag-a">A</span>${fmt(r.a.output)}<br><span class="ab-tag ab-tag-b">B</span>${fmt(r.b.output)}</td>
      <td class="cost"><span class="ab-tag ab-tag-a">A</span>${fmtCost(r.a.cost)}<br><span class="ab-tag ab-tag-b">B</span>${fmtCost(r.b.cost)}${_abPctLabel(r.a.cost, r.b.cost)}</td>
    </tr>
  `).join('');
}

// ── Renderers ──────────────────────────────────────────────────────────────
function renderBudget() {  // eslint-disable-line no-unused-vars
  const b = rawData && rawData.budget;
  const bar = document.getElementById('budget-bar');
  const fill = document.getElementById('budget-fill');
  const label = document.getElementById('budget-label');
  if (!bar || !fill || !label) return;
  if (!b || !b.configured) {
    bar.style.display = 'none';
    return;
  }
  bar.style.display = '';
  const pct = Math.min(b.pct, 1.5);  // cap bar visual at 150% for readability
  fill.style.width = (Math.min(pct, 1) * 100).toFixed(1) + '%';
  // Colour: green <80%, amber 80-100%, red >=100%
  fill.style.backgroundColor = pct >= 1 ? '#f87171' : pct >= 0.8 ? '#fbbf24' : '#4ade80';
  const overflow = pct > 1 ? ` (${((pct - 1) * 100).toFixed(0)}% over)` : '';
  label.textContent = '$' + b.month_to_date.toFixed(2) + ' / $' + b.monthly_usd.toFixed(2) + overflow;
}

function _editBudget() {
  const cur = (rawData && rawData.budget && rawData.budget.monthly_usd) || '';
  const v = prompt('Monthly budget (USD) — leave empty to disable:', cur);
  if (v === null) return;
  const val = v.trim() === '' ? null : parseFloat(v);
  fetch('/api/budget', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ monthly_usd: val }),
  }).then(() => loadData());
}

function renderStats(t, prev) {
  const rangeLabel = RANGE_LABELS[selectedRange].toLowerCase();
  const stats = [
    { label: 'Sessions',       value: t.sessions.toLocaleString(), sub: rangeLabel,               delta: prev && _deltaBadge(t.sessions, prev.sessions) },
    { label: 'Turns',          value: fmt(t.turns),                sub: rangeLabel,               delta: prev && _deltaBadge(t.turns, prev.turns) },
    { label: 'Input Tokens',   value: fmt(t.input),                sub: rangeLabel,               delta: prev && _deltaBadge(t.input, prev.input) },
    { label: 'Output Tokens',  value: fmt(t.output),               sub: rangeLabel,               delta: prev && _deltaBadge(t.output, prev.output) },
    { label: 'Cache Read',     value: fmt(t.cache_read),           sub: 'from prompt cache',      delta: prev && _deltaBadge(t.cache_read, prev.cache_read) },
    { label: 'Cache Creation', value: fmt(t.cache_creation),       sub: 'writes to prompt cache', delta: prev && _deltaBadge(t.cache_creation, prev.cache_creation) },
    { label: 'Est. Cost',      value: fmtCostBig(t.cost),          sub: _costSub(t), color: '#4ade80', title: _costTitle(t), delta: prev && _deltaBadge(t.cost, prev.cost) },
    { label: 'Forecast',       value: forecastValue(),             sub: forecastSub(),            color: forecastColor() },
  ];
  document.getElementById('stats-row').innerHTML = stats.map(s => `
    <div class="stat-card" title="${s.title ? esc(s.title) : ''}">
      <div class="label">${s.label}${s.delta || ''}</div>
      <div class="value" style="${s.color ? 'color:' + s.color : ''}">${esc(s.value)}</div>
      ${s.sub ? `<div class="sub">${esc(s.sub)}</div>` : ''}
    </div>
  `).join('');
}

// ── Year calendar (GitHub-style heatmap) ──────────────────────────────────
// Always renders the trailing 365 days regardless of range/model filters —
// the value is the "year view" itself; mixing in filters would defeat it.
function renderYearCalendar(rows) {
  const container = document.getElementById('year-calendar');
  if (!container || !rows || !rows.length) {
    if (container) container.innerHTML = '';
    return;
  }
  const maxCost = rows.reduce((m, r) => r.cost > m ? r.cost : m, 0);
  const totalCost = rows.reduce((s, r) => s + (r.cost || 0), 0);

  // Layout: 53 columns x 7 rows, oldest-first. We pad the first column so
  // each row corresponds to a weekday (Sun at the top, matching GitHub).
  // Cells before the first day stay invisible.
  const firstDate = new Date(rows[0].date + 'T00:00:00Z');
  const firstWeekday = firstDate.getUTCDay(); // 0=Sun..6=Sat

  // Build a flat array of cells in column-major order (top-to-bottom, then
  // left-to-right) — the CSS grid uses grid-auto-flow: column to match.
  const cells = [];
  for (let i = 0; i < firstWeekday; i++) cells.push({ empty: true });
  for (const r of rows) cells.push(r);
  while (cells.length < 53 * 7) cells.push({ empty: true });

  const accent = (getComputedStyle(document.documentElement)
                   .getPropertyValue('--accent') || '#0071e3').trim();

  function cellColor(hex, alpha) {
    // Accept rgb(...) / rgba(...) / #rrggbb / #rgb forms.
    const trimmed = hex.trim();
    if (trimmed.startsWith('rgb')) {
      const nums = trimmed.match(/[\d.]+/g) || [];
      const [r, g, b] = nums.map(Number);
      return `rgba(${r|0}, ${g|0}, ${b|0}, ${alpha})`;
    }
    let h = trimmed.replace('#', '');
    if (h.length === 3) h = h.split('').map(c => c + c).join('');
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  function intensityColor(cost) {
    if (!cost || maxCost <= 0) return '';
    const t = Math.min(1, cost / maxCost);
    // 4 discrete buckets keep the gradient legible.
    const bucket = t >= 0.75 ? 1.0 : t >= 0.5 ? 0.75 : t >= 0.25 ? 0.5 : 0.25;
    return cellColor(accent, bucket);
  }

  container.innerHTML = cells.map(c => {
    if (c.empty) return '<div class="yc-cell empty"></div>';
    const color = intensityColor(c.cost);
    const style = color ? ` style="background:${color}"` : '';
    const title = `${c.date} — ${fmtCost(c.cost)} — ${c.turns} turn${c.turns === 1 ? '' : 's'}. Click to filter sessions to this day.`;
    return `<div class="yc-cell"${style} title="${esc(title)}" onclick="_ycClick('${c.date}')"></div>`;
  }).join('');

  // Legend swatches use the same 4 intensity buckets.
  ['1','2','3','4'].forEach((n, i) => {
    const el = document.getElementById('yc-leg-' + n);
    if (el) el.style.background = cellColor(accent, 0.25 * (i + 1));
  });

  const totalEl = document.getElementById('year-calendar-total');
  if (totalEl) totalEl.textContent = fmtCostBig(totalCost) + ' over 365 days';
}

// ── Plan limits: 5h + weekly rolling utilization ─────────────────────────
let planSparkChart = null;
let planWeeklyChart = null;
function _plFmtTokens(n) { if (n >= 1e9) return (n/1e9).toFixed(2) + 'B'; if (n >= 1e6) return (n/1e6).toFixed(1) + 'M'; if (n >= 1e3) return (n/1e3).toFixed(1) + 'K'; return String(n|0); }
function _plFmtCost(n) { return '$' + (n || 0).toFixed(2); }
function _plRow(label, sub, cur, max, fmt) {
  const pct = max && max > 0 ? Math.min(1, cur / max) : 0;
  const cls = pct >= 0.9 ? 'pl-fill-hot' : pct >= 0.7 ? 'pl-fill-warn' : 'pl-fill-ok';
  const pctTxt = max && max > 0 ? (pct * 100).toFixed(0) + '%' : '—';
  return '<div class="pl-bar-wrap"><div class="pl-bar-meta"><span>' + fmt(cur) + ' of ' + (max ? fmt(max) : '—') + '</span><span>' + pctTxt + '</span></div><div class="pl-bar"><div class="pl-bar-fill ' + cls + '" style="width:' + (pct * 100).toFixed(1) + '%"></div></div></div>';
}
function renderPlanLimits() {  // eslint-disable-line no-unused-vars
  const card = document.getElementById('plan-limits-card');
  const pl = rawData && rawData.plan_limits;
  if (!card || !pl || !pl.models || !pl.models.length) {
    if (card) card.style.display = 'none';
    return;
  }
  card.style.display = '';
  document.getElementById('pl-note').textContent = pl.note || '';
  const rows = document.getElementById('pl-rows');
  const list = [pl.overall].concat(pl.models);
  rows.innerHTML = list.map(m => {
    const lbl = m.model === 'ALL' ? 'All models combined' : m.model;
    return '<div class="pl-row"><div class="pl-row-label">' + lbl + '<span class="pl-sub">5h rolling · weekly rolling</span></div>' +
           _plRow('5h', '', m.current_5h.tokens, m.max_5h_30d ? m.max_5h_30d.tokens : 0, _plFmtTokens) +
           _plRow('7d', '', m.current_7d.tokens, m.max_7d_30d ? m.max_7d_30d.tokens : 0, _plFmtTokens) +
           '</div>';
  }).join('');

  // 5h sparkline — overlay the biggest 3 models (or first 3 alphabetically)
  const top = pl.models.slice().sort((a, b) => (b.max_5h_30d ? b.max_5h_30d.tokens : 0) - (a.max_5h_30d ? a.max_5h_30d.tokens : 0)).slice(0, 3);
  const labels = (top[0] && top[0].sparkline_48h) ? top[0].sparkline_48h.map(s => s.t.slice(11, 16)) : [];
  const colors = ['rgba(0,113,227,0.85)', 'rgba(217,119,87,0.85)', 'rgba(74,222,128,0.85)'];
  const datasets = top.map((m, i) => ({
    label: m.model,
    data: (m.sparkline_48h || []).map(s => s.tokens),
    borderColor: colors[i],
    backgroundColor: colors[i].replace('0.85', '0.15'),
    fill: true,
    pointRadius: 0,
    tension: 0.3,
  }));
  const sCtx = document.getElementById('chart-plan-spark');
  if (sCtx && labels.length) {
    if (planSparkChart) planSparkChart.destroy();
    planSparkChart = new Chart(sCtx, {
      type: 'line',
      data: { labels: labels, datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { labels: { color: 'var(--muted)', boxWidth: 10, font: { size: 10 } } } },
        scales: {
          x: { ticks: { color: 'var(--muted)', font: { size: 9 }, maxTicksLimit: 10 } },
          y: { ticks: { color: 'var(--muted)', font: { size: 9 }, callback: v => _plFmtTokens(v) }, beginAtZero: true },
        },
      },
    });
  }

  // Weekly tokens-per-day bar chart (last 7 days)
  const allTurnsBy = {};
  for (const r of (rawData.daily_by_model || [])) {
    if (!allTurnsBy[r.day]) allTurnsBy[r.day] = 0;
    allTurnsBy[r.day] += (r.input || 0) + (r.output || 0) + (r.cache_read || 0) + (r.cache_creation || 0);
  }
  const days = Object.keys(allTurnsBy).sort().slice(-7);
  const wCtx = document.getElementById('chart-plan-weekly');
  if (wCtx && days.length) {
    if (planWeeklyChart) planWeeklyChart.destroy();
    const data = days.map(d => allTurnsBy[d]);
    const cap = pl.overall && pl.overall.max_7d_30d ? pl.overall.max_7d_30d.tokens / 7 : 0;
    planWeeklyChart = new Chart(wCtx, {
      type: 'bar',
      data: {
        labels: days.map(d => d.slice(5)),
        datasets: [
          { label: 'Daily tokens', data: data, backgroundColor: 'rgba(0,113,227,0.7)' },
          cap > 0 ? { label: 'Avg cap (max7d/7)', data: days.map(() => cap), type: 'line', borderColor: '#fbbf24', borderDash: [4, 4], pointRadius: 0, fill: false } : null,
        ].filter(Boolean),
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { labels: { color: 'var(--muted)', boxWidth: 10, font: { size: 10 } } } },
        scales: {
          x: { ticks: { color: 'var(--muted)', font: { size: 9 } } },
          y: { ticks: { color: 'var(--muted)', font: { size: 9 }, callback: v => _plFmtTokens(v) }, beginAtZero: true },
        },
      },
    });
  }
}


// Bucket rows into 24 hours (display-TZ), summing turns + output, and count
// the unique days in the input so the caller can compute per-day averages.
function aggregateHourly(rows, tzMode) {
  const byHour = {};
  for (let h = 0; h < 24; h++) byHour[h] = { turns: 0, output: 0 };
  const days = new Set();
  for (const r of rows) {
    const displayHour = utcHourToDisplay(r.hour, tzMode);
    byHour[displayHour].turns  += r.turns  || 0;
    byHour[displayHour].output += r.output || 0;
    if (r.day) days.add(r.day);
  }
  const dayCount = days.size;
  const hours = [];
  for (let h = 0; h < 24; h++) {
    hours.push({
      hour:       h,
      avgTurns:   dayCount ? byHour[h].turns  / dayCount : 0,
      avgOutput:  dayCount ? byHour[h].output / dayCount : 0,
      totalTurns: byHour[h].turns,
      peak:       isPeakHour(h, tzMode),
    });
  }
  return { hours, dayCount };
}

function renderHourlyChart(agg) {
  const dayCountEl = document.getElementById('hourly-day-count');
  dayCountEl.textContent = agg.dayCount
    ? agg.dayCount + ' day' + (agg.dayCount === 1 ? '' : 's') + ' averaged · ' + tzDisplayName(hourlyTZ)
    : 'No data · ' + tzDisplayName(hourlyTZ);

  const ctx = document.getElementById('chart-hourly').getContext('2d');
  if (charts.hourly) charts.hourly.destroy();

  const labels = agg.hours.map(h => (h.peak ? '⚡ ' : '') + formatHourLabel(h.hour));
  const turns  = agg.hours.map(h => h.avgTurns);
  const output = agg.hours.map(h => h.avgOutput);
  const barColors = agg.hours.map(h => h.peak ? 'rgba(248,113,113,0.8)' : tokenColors().input);

  charts.hourly = new Chart(ctx, {
    data: {
      labels: labels,
      datasets: [
        {
          type: 'bar',
          label: 'Avg turns / hour',
          data: turns,
          backgroundColor: barColors,
          yAxisID: 'y',
          order: 2,
        },
        {
          type: 'line',
          label: 'Avg output tokens / hour',
          data: output,
          borderColor: tokenColors().output,
          backgroundColor: 'rgba(167,139,250,0.15)',
          borderWidth: 2,
          pointRadius: 2,
          tension: 0.3,
          yAxisID: 'y1',
          order: 1,
        },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: '#8892a4', boxWidth: 12 } },
        tooltip: {
          callbacks: {
            title: (items) => {
              if (!items.length) return '';
              const idx = items[0].dataIndex;
              const h = agg.hours[idx];
              const base = formatHourLabel(h.hour) + ' ' + tzDisplayName(hourlyTZ);
              return h.peak ? base + ' · Peak — Anthropic US hours' : base;
            },
            label: (item) => {
              if (item.dataset.label && item.dataset.label.indexOf('turns') !== -1) {
                return ' Avg turns: ' + item.parsed.y.toFixed(2);
              }
              return ' Avg output: ' + fmt(item.parsed.y);
            },
          }
        },
      },
      scales: {
        x: { ticks: { color: '#8892a4', maxRotation: 0, autoSkip: false, font: { size: 10 } }, grid: { color: '#2a2d3a' } },
        y:  { position: 'left',  beginAtZero: true, ticks: { color: '#8892a4', callback: v => v.toFixed(1) },     grid: { color: '#2a2d3a' }, title: { display: true, text: 'Avg turns / hour',         color: '#8892a4', font: { size: 11 } } },
        y1: { position: 'right', beginAtZero: true, ticks: { color: '#8892a4', callback: v => fmt(v) }, grid: { drawOnChartArea: false },   title: { display: true, text: 'Avg output tokens / hour', color: '#8892a4', font: { size: 11 } } },
      }
    }
  });
}

function renderDailyChart(daily) {
  const ctx = document.getElementById('chart-daily').getContext('2d');
  if (charts.daily) charts.daily.destroy();
  charts.daily = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: daily.map(d => d.day),
      datasets: [
        { label: 'Input',          data: daily.map(d => d.input),          backgroundColor: tokenColors().input,          stack: 'tokens' },
        { label: 'Output',         data: daily.map(d => d.output),         backgroundColor: tokenColors().output,         stack: 'tokens' },
        { label: 'Cache Read',     data: daily.map(d => d.cache_read),     backgroundColor: tokenColors().cache_read,     stack: 'tokens' },
        { label: 'Cache Creation', data: daily.map(d => d.cache_creation), backgroundColor: tokenColors().cache_creation, stack: 'tokens' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: chartColors().label, boxWidth: 12 } } },
      scales: {
        x: { ticks: { color: chartColors().label, maxTicksLimit: RANGE_TICKS[selectedRange] }, grid: { color: chartColors().grid } },
        y: { ticks: { color: chartColors().label, callback: v => fmt(v) }, grid: { color: chartColors().grid } },
      }
    }
  });
}

function renderModelChart(byModel) {
  const ctx = document.getElementById('chart-model').getContext('2d');
  if (charts.model) charts.model.destroy();
  if (!byModel.length) { charts.model = null; return; }
  charts.model = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: byModel.map(m => m.model),
      datasets: [{ data: byModel.map(m => m.input + m.output), backgroundColor: modelColors(), borderWidth: 2, borderColor: '#ffffff' }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { color: chartColors().label, boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${fmt(ctx.raw)} tokens` } }
      }
    }
  });
}


function renderCacheHit(summary) {  // eslint-disable-line no-unused-vars
  const el = document.getElementById("cache-hit-card");
  if (!el) return;
  if (!summary || !summary.sessions_total) {
    el.style.display = "none";
    return;
  }
  const avgPct = (summary.avg_ratio_pct != null
    ? summary.avg_ratio_pct
    : (summary.avg_ratio * 100)).toFixed(1);
  const under = summary.sessions_underusing || 0;
  const thr = summary.input_threshold || 50000;
  el.style.display = "";
  const tail = under > 0
    ? `<strong>${under}</strong> session${under === 1 ? "" : "s"} are underusing caching (input &gt; ${fmt(thr)} tok, cache hit &lt; 30%) &mdash; see them tagged in the table.`
    : `No sessions are underusing the prompt cache right now.`;
  el.innerHTML = `<strong>Avg cache hit ratio:</strong> ${avgPct}%. ${tail}`;
}
function renderTimeOnTask() {  // eslint-disable-line no-unused-vars
  if (!rawData || !rawData.time_on_task) return;
  const days = rawData.time_on_task;
  if (!days.length) return;
  const todayMin = days[days.length - 1].active_minutes;
  const total = days.reduce((a, d) => a + d.active_minutes, 0);
  const avg = total / days.length;

  const fmtMin = m => {
    if (m < 1) return '0m';
    if (m < 60) return Math.round(m) + 'm';
    const h = Math.floor(m / 60), mm = Math.round(m % 60);
    return mm ? h + 'h ' + mm + 'm' : h + 'h';
  };
  const todayEl = document.getElementById('tot-today');
  const avgEl   = document.getElementById('tot-avg');
  const totEl   = document.getElementById('tot-total');
  if (todayEl) todayEl.textContent = fmtMin(todayMin);
  if (avgEl)   avgEl.textContent   = fmtMin(avg);
  if (totEl)   totEl.textContent   = fmtMin(total);

  const canvas = document.getElementById('chart-time-on-task');
  if (!canvas || typeof Chart === 'undefined') return;
  const ctx = canvas.getContext('2d');
  if (charts.timeOnTask) charts.timeOnTask.destroy();
  charts.timeOnTask = new Chart(ctx, {
    type: 'line',
    data: {
      labels: days.map(d => d.day),
      datasets: [{
        data: days.map(d => d.active_minutes),
        borderColor: chartColors().label,
        backgroundColor: 'rgba(94,106,210,0.18)',
        fill: true,
        tension: 0.35,
        pointRadius: 0,
        borderWidth: 1.5,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => fmtMin(ctx.raw) + ' active',
          },
        },
      },
      scales: {
        x: { display: false },
        y: { display: false, beginAtZero: true },
      },
    },
  });
}

function renderPareto(filteredSessions) {  // eslint-disable-line no-unused-vars
  const el = document.getElementById("pareto-card");
  if (!el) return;
  if (!filteredSessions || !filteredSessions.length) {
    el.style.display = "none";
    return;
  }
  const withCost = filteredSessions.map(s => Object.assign({}, s, {
    cost: calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation, s.cache_1h || 0),
    cost: calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation),
  })).filter(s => s.cost > 0);
  if (!withCost.length) { el.style.display = "none"; return; }
  const total = withCost.reduce((a, s) => a + s.cost, 0);
  const ranked = withCost.slice().sort((a, b) => b.cost - a.cost).slice(0, 5);
  const top = ranked.reduce((a, s) => a + s.cost, 0);
  const pct = (top / total * 100).toFixed(0);
  el.style.display = "";
  const names = ranked.map(s => `${projDisplay(s.project)} (${s.session_id})`).join(", ");
  el.innerHTML = `<strong>Cost concentration:</strong> top 5 sessions account for <strong>${pct}%</strong> of spend in the current range. <span style="color:var(--muted)">(${names})</span>`;
}

let toolsChart = null;
function renderToolsChart(rows) {  // eslint-disable-line no-unused-vars
  const ctx = document.getElementById('chart-tools');
  if (!ctx) return;
  const empty = document.getElementById('tools-chart-empty');
  // Aggregate the per-(day,tool) rows we just got into top-N tools by turns.
  const agg = {};
  for (const r of rows) {
    if (!agg[r.tool]) agg[r.tool] = { turns: 0, tokens: 0 };
    agg[r.tool].turns += r.turns || 0;
    agg[r.tool].tokens += r.tokens || 0;
  }
  // Sort by turn count desc, take top 12 so the chart stays readable.
  const ranked = Object.entries(agg)
    .map(([tool, v]) => ({ tool, turns: v.turns, tokens: v.tokens }))
    .sort((a, b) => b.turns - a.turns)
    .slice(0, 12);
  if (!ranked.length) {
    if (empty) empty.style.display = 'block';
    ctx.style.display = 'none';
    return;
  }
  if (empty) empty.style.display = 'none';
  ctx.style.display = '';
  if (toolsChart) toolsChart.destroy();
  toolsChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: ranked.map(r => r.tool),
      datasets: [{
        label: 'Turns',
        data: ranked.map(r => r.turns),
        backgroundColor: 'rgba(217, 119, 87, 0.7)',
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            afterLabel: (ctx) => {
              const r = ranked[ctx.dataIndex];
              return 'Tokens (in+out): ' + (r.tokens || 0).toLocaleString();
            },
          },
        },
      },
      scales: {
        x: { beginAtZero: true, ticks: { color: 'var(--muted)' } },
        y: { ticks: { color: 'var(--text)', autoSkip: false } },
      },
    },
  });
}
let _searchTerm = "";
function _onSearchInput(v) {  // eslint-disable-line no-unused-vars
  _searchTerm = (v || "").toLowerCase();
  applyFilter();  // re-render with the new filter
}

function _matchesSearch(s) {
  if (!_searchTerm) return true;
  const fields = [s.project, s.branch, s.session_id, s.session_name, s.model].filter(Boolean);
  return fields.some(f => String(f).toLowerCase().includes(_searchTerm));
}
function renderAnomalyBanner() {  // eslint-disable-line no-unused-vars
  const a = rawData && rawData.anomaly;
  const el = document.getElementById("anomaly-banner");
  if (!el || !a || !a.is_anomalous) {
    if (el) el.style.display = "none";
    return;
  }
  el.style.display = "";
  el.innerHTML = `⚠ <strong>Spend spike detected.</strong> Today's spend ($${a.today.toFixed(2)}) is ${a.ratio}x the 30-day average ($${a.mean.toFixed(2)}). Did an agent loop go haywire?`;
}
function renderDowHourHeatmap() {  // eslint-disable-line no-unused-vars
  const grid = rawData && rawData.dow_hour;
  const el = document.getElementById("dow-hour-grid");
  if (!el || !grid) return;
  // Find max for colour scaling
  let max = 0;
  for (let d = 0; d < 7; d++) for (let h = 0; h < 24; h++) max = Math.max(max, grid[d][h].turns);
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const cells = [];
  // Top-left empty corner + hour header
  cells.push(`<div></div>`);
  for (let h = 0; h < 24; h++) {
    cells.push(`<div style="text-align:center;${h % 6 ? "" : "color:var(--text);"}">${h % 6 ? "" : h}</div>`);
  }
  for (let d = 0; d < 7; d++) {
    cells.push(`<div style="text-align:right;padding-right:4px;color:var(--text);cursor:default !important;">${days[d]}</div>`);
    for (let h = 0; h < 24; h++) {
      const v = grid[d][h];
      const intensity = max > 0 ? v.turns / max : 0;
      const bg = `rgba(217,119,87,${0.05 + intensity * 0.85})`;
      const title = `${days[d]} ${h}:00 UTC — ${v.turns} turns, ${(v.tokens||0).toLocaleString()} tokens. Click to filter sessions.`;
      cells.push(`<div style="aspect-ratio:1/1;background:${bg};border-radius:2px;" title="${title}" onclick="_dhClick(${d},${h})"></div>`);
    }
  }
  el.innerHTML = cells.join("");
}

// ── Heatmap click handlers — drill down to matching sessions ─────────────
function _dhClick(d, h) {  // eslint-disable-line no-unused-vars
  // Day-of-week 0=Mon..6=Sun, hour 0..23 UTC. Filter the sessions table to
  // sessions whose last_timestamp falls in that bucket.
  const dayNames = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const input = document.getElementById('sessions-search');
  if (input) {
    input.value = '';  // clear search so the filter applies cleanly
    _searchTerm = '';
  }
  // Use a flash banner — least invasive: piggyback on the pareto-card slot.
  let banner = document.getElementById('heatmap-flash');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'heatmap-flash';
    banner.style.cssText = 'margin:8px 0 16px;padding:10px 14px;background:rgba(217,119,87,0.12);border-radius:8px;font-size:12px;color:var(--text);cursor:pointer;';
    banner.title = 'Click to clear';
    banner.onclick = () => banner.remove();
    const sr = document.getElementById('stats-row');
    if (sr && sr.parentNode) sr.parentNode.insertBefore(banner, sr.nextSibling);
  }
  const v = rawData.dow_hour[d][h];
  banner.innerHTML = '<strong>Filtered: ' + dayNames[d] + ' ' + h + ':00 UTC</strong> &mdash; ' + v.turns + ' turns, ' + (v.tokens||0).toLocaleString() + ' tokens. <span style="color:var(--muted)">(click banner to clear)</span>';
  banner.scrollIntoView({behavior: 'smooth', block: 'center'});
}
function _ycClick(date) {  // eslint-disable-line no-unused-vars
  const input = document.getElementById('sessions-search');
  if (input) { input.value = date; _onSearchInput(date); input.scrollIntoView({behavior:'smooth', block:'center'}); }
  let banner = document.getElementById('heatmap-flash');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'heatmap-flash';
    banner.style.cssText = 'margin:8px 0 16px;padding:10px 14px;background:rgba(0,113,227,0.12);border-radius:8px;font-size:12px;color:var(--text);cursor:pointer;';
    banner.onclick = () => banner.remove();
    const sr = document.getElementById('stats-row');
    if (sr && sr.parentNode) sr.parentNode.insertBefore(banner, sr.nextSibling);
  }
  banner.innerHTML = '<strong>Filtered: ' + date + '</strong> &mdash; sessions table filtered. <span style="color:var(--muted)">(click banner to clear)</span>';
}

let histoChart = null;
function renderHistogram() {  // eslint-disable-line no-unused-vars
  const h = rawData && rawData.cost_histogram;
  const stats = document.getElementById("histo-stats");
  const ctx = document.getElementById("chart-histo");
  if (!h || !ctx) {
    if (stats) stats.textContent = "";
    return;
  }
  stats.textContent =
    "n=" + h.n.toLocaleString() +
    " • p50 $" + h.p50 +
    " • p95 $" + h.p95 +
    " • p99 $" + h.p99 +
    " • max $" + h.max;
  const labels = h.edges.slice(0, -1).map((e, i) =>
    "$" + e.toFixed(4) + " – $" + h.edges[i + 1].toFixed(4)
  );
  if (histoChart) histoChart.destroy();
  histoChart = new Chart(ctx, {
    type: "bar",
    data: { labels: labels, datasets: [{ label: "Turns", data: h.buckets, backgroundColor: "rgba(217,119,87,0.7)" }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { ticks: { font: { size: 9 } } }, y: { beginAtZero: true } },
    },
  });
}
function renderPlanCard() {  // eslint-disable-line no-unused-vars
  const r = rawData && rawData.plan_recommendation;
  const el = document.getElementById("plan-card");
  if (!el || !r) return;
  if (!r.month_to_date || r.month_to_date < 1) {
    el.style.display = "none";
    return;
  }
  const p = r.plans[r.recommended];
  el.style.display = "";
  el.innerHTML = `<strong>Plan recommendation:</strong> at $${r.month_to_date.toFixed(2)} API-equivalent this month, <strong>${r.recommended}</strong> ($${p.price}/mo, includes ${p.included}) would be the cheapest subscription. <span style="color:var(--muted)">Compare: ${Object.entries(r.plans).map(([n, pp]) => n + " $" + pp.price).join(" • ")}</span>`;
}

function _openSession(sid) {  // eslint-disable-line no-unused-vars
  fetch("/api/session/" + encodeURIComponent(sid))
    .then(r => r.json())
    .then(d => _renderSessionModal(sid, d))
    .catch(e => alert("Could not load session: " + e));
}

function _renderSessionModal(sid, d) {  // eslint-disable-line no-unused-vars
  const modal = document.getElementById("session-modal");
  const body = document.getElementById("session-modal-body");
  const title = document.getElementById("session-modal-title");
  if (!modal || !body) return;
  if (d.error) {
    body.innerHTML = `<p style="color:#f87171;">${esc(d.error)}</p>`;
  } else {
    const s = d.session || {};
    title.textContent = "Session " + sid + (s.project_name ? " — " + s.project_name : "");
    const tools = (d.tools_breakdown || []).map(t =>
      `<li>${esc(t.tool)} <span style="color:var(--muted);">(${t.count})</span></li>`
    ).join("");
    const lastFew = (d.timeline || []).slice(-30);
    const rows = lastFew.map(t => `
      <tr>
        <td style="font-family:monospace;font-size:11px;">${(t.timestamp || "").slice(11,19)}</td>
        <td>${esc(t.model || "")}</td>
        <td style="text-align:right;">${(t.tokens || 0).toLocaleString()}</td>
        <td style="text-align:right;">$${(t.cost || 0).toFixed(4)}</td>
        <td style="text-align:right; color:var(--muted);">$${(t.cum_cost || 0).toFixed(2)}</td>
        <td style="color:var(--muted);">${esc(t.tool || "")}</td>
      </tr>
    `).join("");
    body.innerHTML = `
      <div style="display:flex; gap: 24px; font-size:12px; color:var(--muted); margin-bottom:16px;">
        <div>Project: <strong style="color:var(--text);">${esc(s.project_name || "?")}</strong></div>
        <div>Branch: <strong style="color:var(--text);">${esc(s.git_branch || "—")}</strong></div>
        <div>Model: <strong style="color:var(--text);">${esc(s.model || "?")}</strong></div>
        <div>Turns: <strong style="color:var(--text);">${d.turn_count_actual}</strong></div>
        <div>Total cost: <strong style="color:#4ade80;">$${(d.total_cost || 0).toFixed(4)}</strong></div>
      </div>
      <h3 style="margin: 12px 0 4px;">Tools used</h3>
      <ul style="margin: 0 0 16px 0; padding-left: 20px;">${tools || "<li style='color:var(--muted)'>none</li>"}</ul>
      <h3 style="margin: 16px 0 4px;">Last ${lastFew.length} turns</h3>
      <table style="width:100%; font-size:11px; border-collapse:collapse;">
        <thead><tr style="color:var(--muted); text-align:left;">
          <th>Time</th><th>Model</th><th style="text-align:right;">Tokens</th>
          <th style="text-align:right;">Cost</th><th style="text-align:right;">Cum</th><th>Tool</th>
        </tr></thead>
        <tbody>${rows || "<tr><td colspan='6' style='color:var(--muted);'>No turns</td></tr>"}</tbody>
      </table>
    `;
  }
  modal.style.display = "flex";
}

function _closeSessionModal() {  // eslint-disable-line no-unused-vars
  const m = document.getElementById("session-modal");
  if (m) m.style.display = "none";
}

// Esc closes the modal
document.addEventListener("keydown", e => {
  if (e.key === "Escape") _closeSessionModal();
});

function _renderSparkline(bins) {  // eslint-disable-line no-unused-vars
  if (!bins || !bins.length) return "";
  const W = 60, H = 14;
  const max = Math.max(1, ...bins);
  const step = W / bins.length;
  const pts = bins.map((v, i) => `${(i * step).toFixed(1)},${(H - (v / max) * H).toFixed(1)}`).join(" ");
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="vertical-align:middle;margin-left:4px;" aria-hidden="true"><polyline fill="none" stroke="var(--accent)" stroke-width="1" points="${pts}"></polyline></svg>`;
}
function _renderSensitiveBadge(s) {  // eslint-disable-line no-unused-vars
  // SOFT warning chip — flags sessions whose project/branch matched a
  // sensitive-content regex. Never blocks; just shows a tooltip listing
  // the matched keywords. Patterns configurable via ~/.claude/pii-patterns.json.
  const matches = s && s.sensitive_match;
  if (!matches || !matches.length) return "";
  const tip = `Sensitive markers matched: ${matches.join(', ')}`;
  return ` <span class="pii-badge" title="${esc(tip)}" aria-label="${esc(tip)}" style="display:inline-block;margin-left:4px;padding:0 5px;border-radius:6px;font-size:10px;line-height:14px;background:rgba(255,159,10,0.18);color:#b06b00;border:1px solid rgba(255,159,10,0.45);cursor:help;vertical-align:middle;">&#9888;</span>`;
}
function renderDowngradeSuggestions() {  // eslint-disable-line no-unused-vars
  const el = document.getElementById("downgrade-card");
  if (!el) return;
  const suggestions = (rawData && rawData.downgrade_suggestions) || [];
  const totalSavings = (rawData && rawData.downgrade_total_savings) || 0;
  if (!suggestions.length || totalSavings <= 0) {
    el.style.display = "none";
    return;
  }
  el.style.display = "";
  const top = suggestions.slice(0, 3);
  const names = top.map(s =>
    `${esc(s.project)} (${esc(s.session_id)} → ${esc(s.suggested_model.replace("claude-", ""))}, save ${fmtCostBig ? fmtCostBig(s.savings_usd) : "$" + s.savings_usd.toFixed(2)})`
  ).join(", ");
  el.innerHTML = `<strong>Downgrade hint:</strong> ${suggestions.length} opus/sonnet session${suggestions.length === 1 ? "" : "s"} could likely have used haiku — potential savings <strong>$${totalSavings.toFixed(2)}</strong> across the database. <span style="color:var(--muted)">Top: ${names}</span>`;
}
function renderCache1hOpportunities(opps) {
  const body = document.getElementById('cache-1h-body');
  if (!body) return;
  if (!opps || !opps.length) {
    body.innerHTML = '<tr><td colspan="5" class="muted" style="text-align:center;padding:20px">No long sessions with heavy cache writes detected — nothing to optimize.</td></tr>';
    return;
  }
  body.innerHTML = opps.map(o => `<tr>
      <td class="muted" style="font-family:monospace">${esc(o.session_id)}&hellip;</td>
      <td class="muted">${esc(o.duration_min)}m</td>
      <td class="num">${fmt(o.cache_creation_tokens)}</td>
      <td class="cost">${fmtCost(o.current_cost)}</td>
      <td class="cost" title="~40% of current cache-creation cost">${fmtCost(o.estimated_savings_with_1h)}</td>
    </tr>`).join('');
}
async function renderInbound() {  // eslint-disable-line no-unused-vars
  const card = document.getElementById("inbound-card");
  if (!card) return;
  try {
    const resp = await fetch("/api/inbound?limit=10");
    if (!resp.ok) { card.style.display = "none"; return; }
    const d = await resp.json();
    const events = (d && d.events) || [];
    if (!events.length) { card.style.display = "none"; return; }
    card.style.display = "";
    document.getElementById("inbound-count").textContent =
      "· last " + events.length + " event" + (events.length === 1 ? "" : "s");
    document.getElementById("inbound-list").innerHTML = events.map(ev => {
      const when = esc(ev.received_at || "");
      const type = esc(ev.type || "(unknown)");
      const ip = esc(ev.source_ip || "");
      const payload = esc(JSON.stringify(ev.payload || {}));
      return `<div style="padding:4px 0; border-top:1px solid var(--border);">
        <span style="color:var(--accent);">${type}</span>
        <span style="color:var(--muted);"> @ ${when} from ${ip}</span>
        <div style="color:var(--muted); white-space:pre-wrap; word-break:break-all;">${payload}</div>
      </div>`;
    }).join("");
  } catch (e) {
    card.style.display = "none";
  }
}

function renderProjectChart(byProject) {
  const top = byProject.slice(0, 10);
  const ctx = document.getElementById('chart-project').getContext('2d');
  if (charts.project) charts.project.destroy();
  if (!top.length) { charts.project = null; return; }
  charts.project = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: top.map(p => { const d = projDisplay(p.project); return d.length > 22 ? '\u2026' + d.slice(-20) : d; }),
      datasets: [
        { label: 'Input',  data: top.map(p => p.input),  backgroundColor: tokenColors().input },
        { label: 'Output', data: top.map(p => p.output), backgroundColor: tokenColors().output },
      ]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: chartColors().label, boxWidth: 12 } } },
      scales: {
        x: { ticks: { color: chartColors().label, callback: v => fmt(v) }, grid: { color: chartColors().grid } },
        y: { ticks: { color: chartColors().label, font: { size: 11 } }, grid: { color: chartColors().grid } },
      }
    }
  });
}

function renderSessionsTable(sessions) {
  document.getElementById('sessions-body').innerHTML = sessions.map(s => {
    const cost = calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation, s.cache_1h);
    const costCell = isBillable(s.model)
      ? `<td class="cost">${fmtCost(cost)}</td>`
      : `<td class="cost-na">n/a</td>`;
    const piiBadge = _renderSensitiveBadge(s);
    const ratioPct = Math.round((s.cache_hit_ratio || 0) * 100);
    const cacheBadge = s.cache_underusing
      ? ` <span class="cache-warn-badge" title="High input (${fmt(s.input)} tok) but only ${ratioPct}% cache hit — likely paying full price for repeat content.">cache underused</span>`
      : '';
    const sparkline = _renderSparkline(s.sparkline);
    const sidLink = `<a href="#" onclick="_openSession('${s.session_id}'); return false;" style="color:var(--accent); text-decoration:none;">${esc(s.session_id)}</a>`;
    const sessionCell = s.session_name
      ? `<td><span class="session-name">${esc(s.session_name)}</span> <span class="muted" style="font-family:monospace">(${sidLink}${sparkline}&hellip;)</span>${piiBadge}${cacheBadge}</td>`
      : `<td class="muted" style="font-family:monospace">${sidLink}${sparkline}&hellip;${piiBadge}${cacheBadge}</td>`;
    return `<tr class="session-row ${s._abClass || ''} ${selectedSessionId === s.session_id_full ? 'selected' : ''}" data-session-id="${esc(s.session_id_full)}">
      ${sessionCell}
      <td>${projCellHTML(s.project)}</td>
      <td class="muted">${esc(s.last)}</td>
      <td class="muted">${esc(s.duration_min)}m</td>
      <td><span class="model-tag">${esc(s.model)}</span></td>
      <td class="num">${s.turns}</td>
      <td class="num">${fmt(s.input)}</td>
      <td class="num">${fmt(s.output)}</td>
      ${costCell}
    </tr>`;
  }).join('');
  document.getElementById('sessions-body').addEventListener('click', function(e) {
    const row = e.target.closest('tr.session-row');
    if (row) selectSession(row.dataset.sessionId);
  });
}

function setModelSort(col) {
  if (modelSortCol === col) {
    modelSortDir = modelSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    modelSortCol = col;
    modelSortDir = 'desc';
  }
  updateModelSortIcons();
  applyFilter();
}

function updateModelSortIcons() {
  document.querySelectorAll('[id^="msort-"]').forEach(el => el.textContent = '');
  const icon = document.getElementById('msort-' + modelSortCol);
  if (icon) icon.textContent = modelSortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function sortModels(byModel) {
  return [...byModel].sort((a, b) => {
    let av, bv;
    if (modelSortCol === 'cost') {
      av = calcCost(a.model, a.input, a.output, a.cache_read, a.cache_creation, a.cache_1h);
      bv = calcCost(b.model, b.input, b.output, b.cache_read, b.cache_creation, b.cache_1h);
    } else {
      av = a[modelSortCol] ?? 0;
      bv = b[modelSortCol] ?? 0;
    }
    if (av < bv) return modelSortDir === 'desc' ? 1 : -1;
    if (av > bv) return modelSortDir === 'desc' ? -1 : 1;
    return 0;
  });
}

function renderModelCostTable(byModel) {
  document.getElementById('model-cost-body').innerHTML = sortModels(byModel).map(m => {
    const cost = calcCost(m.model, m.input, m.output, m.cache_read, m.cache_creation, m.cache_1h);
    const costCell = isBillable(m.model)
      ? `<td class="cost">${fmtCost(cost)}</td>`
      : `<td class="cost-na">n/a</td>`;
    return `<tr>
      <td><span class="model-tag">${esc(m.model)}</span></td>
      <td class="num">${fmt(m.turns)}</td>
      <td class="num">${fmt(m.input)}</td>
      <td class="num">${fmt(m.output)}</td>
      <td class="num">${fmt(m.cache_read)}</td>
      <td class="num">${fmt(m.cache_creation)}</td>
      ${costCell}
    </tr>`;
  }).join('');
}

// ── Project cost table sorting ────────────────────────────────────────────
function setProjectSort(col) {
  if (projectSortCol === col) {
    projectSortDir = projectSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    projectSortCol = col;
    projectSortDir = 'desc';
  }
  updateProjectSortIcons();
  applyFilter();
}

function updateProjectSortIcons() {
  document.querySelectorAll('[id^="psort-"]').forEach(el => el.textContent = '');
  const icon = document.getElementById('psort-' + projectSortCol);
  if (icon) icon.textContent = projectSortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function sortProjects(byProject) {
  return [...byProject].sort((a, b) => {
    const av = a[projectSortCol] ?? 0;
    const bv = b[projectSortCol] ?? 0;
    if (av < bv) return projectSortDir === 'desc' ? 1 : -1;
    if (av > bv) return projectSortDir === 'desc' ? -1 : 1;
    return 0;
  });
}

function renderProjectCostTable(byProject) {
  document.getElementById('project-cost-body').innerHTML = sortProjects(byProject).map(p => {
    return `<tr>
      <td>${projCellHTML(p.project)}</td>
      <td class="num">${p.sessions}</td>
      <td class="num">${fmt(p.turns)}</td>
      <td class="num">${fmt(p.input)}</td>
      <td class="num">${fmt(p.output)}</td>
      <td class="cost">${fmtCost(p.cost)}</td>
    </tr>`;
  }).join('');
}

// ── Project+Branch cost table sorting ────────────────────────────────────
function setProjectBranchSort(col) {
  if (branchSortCol === col) {
    branchSortDir = branchSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    branchSortCol = col;
    branchSortDir = 'desc';
  }
  updateProjectBranchSortIcons();
  applyFilter();
}

function updateProjectBranchSortIcons() {
  document.querySelectorAll('[id^="pbsort-"]').forEach(el => el.textContent = '');
  const icon = document.getElementById('pbsort-' + branchSortCol);
  if (icon) icon.textContent = branchSortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function sortProjectBranch(rows) {
  return [...rows].sort((a, b) => {
    const pa = (a.project || '').toLowerCase();
    const pb = (b.project || '').toLowerCase();
    if (pa < pb) return -1;
    if (pa > pb) return 1;
    const av = a[branchSortCol] ?? 0;
    const bv = b[branchSortCol] ?? 0;
    if (av < bv) return branchSortDir === 'desc' ? 1 : -1;
    if (av > bv) return branchSortDir === 'desc' ? -1 : 1;
    return 0;
  });
}

function renderProjectBranchCostTable(rows) {
  document.getElementById('project-branch-cost-body').innerHTML = sortProjectBranch(rows).map(pb => {
    return `<tr>
      <td>${projCellHTML(pb.project)}</td>
      <td class="muted" style="font-family:monospace">${esc(pb.branch || '\u2014')}</td>
      <td class="num">${pb.sessions}</td>
      <td class="num">${fmt(pb.turns)}</td>
      <td class="num">${fmt(pb.input)}</td>
      <td class="num">${fmt(pb.output)}</td>
      <td class="cost">${fmtCost(pb.cost)}</td>
    </tr>`;
  }).join('');
}

// ── CSV Export ────────────────────────────────────────────────────────────
function csvField(val) {
  const s = String(val);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

function csvTimestamp() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0')
    + '_' + String(d.getHours()).padStart(2,'0') + String(d.getMinutes()).padStart(2,'0');
}

function downloadCSV(reportType, header, rows) {
  const lines = [header.map(csvField).join(',')];
  for (const row of rows) {
    lines.push(row.map(csvField).join(','));
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = reportType + '_' + csvTimestamp() + '.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}

function exportSessionsCSV() {
  const header = ['Session ID', 'Session Name', 'Project', 'Project Display', 'Last Active', 'Duration (min)', 'Model', 'Turns', 'Input', 'Output', 'Cache Read', 'Cache Creation', 'Est. Cost'];
  const rows = lastFilteredSessions.map(s => {
    const cost = calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation, s.cache_1h);
    return [s.session_id, s.session_name || '', s.project, projDisplay(s.project), s.last, s.duration_min, s.model, s.turns, s.input, s.output, s.cache_read, s.cache_creation, cost.toFixed(4)];
  });
  downloadCSV('sessions', header, rows);
}

function exportProjectsCSV() {
  const header = ['Project', 'Project Display', 'Sessions', 'Turns', 'Input', 'Output', 'Cache Read', 'Cache Creation', 'Est. Cost'];
  const rows = lastByProject.map(p => {
    return [p.project, projDisplay(p.project), p.sessions, p.turns, p.input, p.output, p.cache_read, p.cache_creation, p.cost.toFixed(4)];
  });
  downloadCSV('projects', header, rows);
}

function exportProjectBranchCSV() {
  const header = ['Project', 'Project Display', 'Branch', 'Sessions', 'Turns', 'Input', 'Output', 'Cache Read', 'Cache Creation', 'Est. Cost'];
  const rows = lastByProjectBranch.map(pb => {
    return [pb.project, projDisplay(pb.project), pb.branch, pb.sessions, pb.turns, pb.input, pb.output, pb.cache_read, pb.cache_creation, pb.cost.toFixed(4)];
  });
  downloadCSV('projects_by_branch', header, rows);
}

// -- Cost by Branch (project + branch tuple, with cache columns) ----------
// Mirrors the Python _cost_by_branch helper so tests can verify shape.
function _costByBranch(sessions) {
  const DEFAULT = '(default)';
  const map = {};
  for (const s of sessions || []) {
    const project = s.project || '';
    const branch = (s.branch && s.branch.trim()) ? s.branch : DEFAULT;
    const key = project + '\x00' + branch;
    if (!map[key]) {
      map[key] = {
        project: project,
        branch: branch,
        sessions: 0,
        turns: 0,
        input: 0,
        output: 0,
        cache_read: 0,
        cache_creation: 0,
        cost: 0,
      };
    }
    const r = map[key];
    r.sessions++;
    r.turns          += s.turns || 0;
    r.input          += s.input || 0;
    r.output         += s.output || 0;
    r.cache_read     += s.cache_read || 0;
    r.cache_creation += s.cache_creation || 0;
    r.cost += calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation, s.cache_1h);
  }
  return Object.values(map).sort((a, b) => b.cost - a.cost);
}

function setBranchOnlySort(col) {
  if (cbSortCol === col) {
    cbSortDir = cbSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    cbSortCol = col;
    cbSortDir = (col === 'project' || col === 'branch') ? 'asc' : 'desc';
  }
  updateBranchOnlySortIcons();
  applyFilter();
}

function updateBranchOnlySortIcons() {
  document.querySelectorAll('[id^="cbsort-"]').forEach(el => el.textContent = '');
  const icon = document.getElementById('cbsort-' + cbSortCol);
  if (icon) icon.textContent = cbSortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function sortBranchOnly(rows) {
  return [...rows].sort((a, b) => {
    const av = a[cbSortCol];
    const bv = b[cbSortCol];
    if (typeof av === 'string' || typeof bv === 'string') {
      const sa = (av || '').toLowerCase();
      const sb = (bv || '').toLowerCase();
      if (sa < sb) return cbSortDir === 'desc' ? 1 : -1;
      if (sa > sb) return cbSortDir === 'desc' ? -1 : 1;
      return 0;
    }
    const na = av ?? 0;
    const nb = bv ?? 0;
    if (na < nb) return cbSortDir === 'desc' ? 1 : -1;
    if (na > nb) return cbSortDir === 'desc' ? -1 : 1;
    return 0;
  });
}

function renderBranchOnlyCostTable(rows) {
  document.getElementById('branch-only-cost-body').innerHTML = sortBranchOnly(rows).map(b => {
    return `<tr>
      <td>${esc(b.project)}</td>
      <td class="muted" style="font-family:monospace">${esc(b.branch)}</td>
      <td class="num">${b.sessions}</td>
      <td class="num">${fmt(b.turns)}</td>
      <td class="num">${fmt(b.input)}</td>
      <td class="num">${fmt(b.output)}</td>
      <td class="num">${fmt(b.cache_read)}</td>
      <td class="num">${fmt(b.cache_creation)}</td>
      <td class="cost">${fmtCost(b.cost)}</td>
    </tr>`;
  }).join('');
}

function exportBranchCSV() {
  const header = ['Project', 'Branch', 'Sessions', 'Turns', 'Input', 'Output', 'Cache Read', 'Cache Creation', 'Est. Cost'];
  const rows = lastByBranch.map(b => {
    return [b.project, b.branch, b.sessions, b.turns, b.input, b.output, b.cache_read, b.cache_creation, b.cost.toFixed(4)];
  });
  downloadCSV('branches', header, rows);
}
// ── Markdown copy ─────────────────────────────────────────────────────────
function _mdEscape(v) {
  // Pipes and newlines break GFM tables; replace them.
  return String(v == null ? '' : v).replace(/\|/g, '\\|').replace(/\r?\n/g, ' ');
}

function _tableToMarkdown(tableId, headers, rowsData) {
  // tableId is accepted for traceability / debug; rendering uses headers+rowsData.
  const head = '| ' + headers.map(_mdEscape).join(' | ') + ' |';
  const sep  = '|' + headers.map(() => '---').join('|') + '|';
  const body = rowsData.map(r => '| ' + r.map(_mdEscape).join(' | ') + ' |').join('\n');
  const md   = body ? head + '\n' + sep + '\n' + body : head + '\n' + sep;
  return md;
}

function _copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).then(
      () => true,
      () => _fallbackCopy(text)
    );
  }
  return Promise.resolve(_fallbackCopy(text));
}

function _fallbackCopy(text) {
  // Older browsers: textarea + execCommand('copy')
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'absolute';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch (e) {
    return false;
  }
}

let _toastTimer = null;
function _showToast(msg) {
  const el = document.getElementById('md-toast');
  if (!el) return;
  el.textContent = msg || 'Copied as markdown!';
  el.classList.add('show');
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.classList.remove('show'); }, 2000);
}

function _copyMD(tableId, headers, rowsData) {
  const md = _tableToMarkdown(tableId, headers, rowsData);
  Promise.resolve(_copyToClipboard(md)).then(ok => {
    _showToast(ok === false ? 'Copy failed' : 'Copied as markdown!');
  });
}

function copyModelMD() {
  const headers = ['Model', 'Turns', 'Input', 'Output', 'Cache Read', 'Cache Creation', 'Est. Cost'];
  const rows = (lastByModel || []).map(m => {
    const cost = calcCost(m.model, m.input, m.output, m.cache_read, m.cache_creation, m.cache_1h);
    return [m.model, m.turns, m.input, m.output, m.cache_read, m.cache_creation,
      isBillable(m.model) ? '$' + cost.toFixed(4) : 'n/a'];
  });
  _copyMD('model-cost-table', headers, rows);
}

function copySessionsMD() {
  const headers = ['Session', 'Project', 'Last Active', 'Duration (min)', 'Model', 'Turns', 'Input', 'Output', 'Est. Cost'];
  const rows = (lastFilteredSessions || []).map(sess => {
    const cost = calcCost(sess.model, sess.input, sess.output, sess.cache_read, sess.cache_creation, sess.cache_1h);
    return [sess.session_name || sess.session_id, sess.project, sess.last, sess.duration_min,
      sess.model, sess.turns, sess.input, sess.output, '$' + cost.toFixed(4)];
  });
  _copyMD('sessions-table', headers, rows);
}

function copyProjectsMD() {
  const headers = ['Project', 'Sessions', 'Turns', 'Input', 'Output', 'Est. Cost'];
  const rows = (lastByProject || []).map(pr => {
    return [pr.project, pr.sessions, pr.turns, pr.input, pr.output, '$' + pr.cost.toFixed(4)];
  });
  _copyMD('project-cost-table', headers, rows);
}

function copyProjectBranchMD() {
  const headers = ['Project', 'Branch', 'Sessions', 'Turns', 'Input', 'Output', 'Est. Cost'];
  const rows = (lastByProjectBranch || []).map(pb => {
    return [pb.project, pb.branch, pb.sessions, pb.turns, pb.input, pb.output, '$' + pb.cost.toFixed(4)];
  });
  _copyMD('project-branch-cost-table', headers, rows);
}

// ── Rescan ────────────────────────────────────────────────────────────────
async function _confirmReset() {  // eslint-disable-line no-unused-vars
  if (!confirm("This will delete usage.db entirely. You'll need to re-run\nscan from the terminal afterwards to repopulate it.\n\nContinue?")) return;
  fetch("/api/reset", { method: "POST" })
    .then(r => r.json())
    .then(d => {
      alert(d.ok ? "Database reset. Run python cli.py scan to repopulate." : ("Reset failed: " + (d.error || "unknown")));
      if (d.ok) loadData();
    })
    .catch(e => alert("Reset error: " + e));
}

async function triggerRescan() {
  const btn = document.getElementById('rescan-btn');
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = '\u21bb Scanning...';
  try {
    const resp = await fetch('/api/rescan', { method: 'POST' });
    const d = await resp.json();
    btn.textContent = '\u21bb Rescan (' + d.new + ' new, ' + d.updated + ' updated)';
    await loadData();
  } catch (e) {
    btn.textContent = '\u21bb Rescan (error)';
    console.error('rescan failed:', e);
  } finally {
    setTimeout(() => { btn.textContent = '\u21bb Rescan'; btn.disabled = false; }, 3000);
  }
}

// Apply localStorage prefs at startup if URL didn't carry any.
(function bootstrapPrefs() {
  try {
    if (window.location.search) return;  // URL takes precedence
    const p = _loadPrefs();
    if (Array.isArray(p.models) && p.models.length) {
      selectedModels = new Set(p.models);
    }
  } catch (e) {}
})();

let _liveTimer = null;
loadData();
startLivePolling();
// Keyboard shortcuts: only fire when the user isn't typing in an input.
document.addEventListener('keydown', (e) => {
  if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  switch (e.key) {
    case '/': {
      const inp = document.getElementById('sessions-search');
      if (inp) { e.preventDefault(); inp.focus(); inp.select(); }
      break;
    }
    case 'r': {
      const btn = document.getElementById('rescan-btn');
      if (btn) { e.preventDefault(); btn.click(); }
      break;
    }
    case 't': {
      // Toggle local <-> UTC for the hourly chart if the buttons exist
      const local = document.querySelector('[data-tz="local"]');
      const utc = document.querySelector('[data-tz="utc"]');
      if (local && utc) {
        e.preventDefault();
        (local.classList.contains('active') ? utc : local).click();
      }
      break;
    }
    case '?': {
      e.preventDefault();
      alert('Keyboard shortcuts:\n  /  focus search\n  r  rescan database\n  t  toggle hourly TZ\n  ?  this help');
      break;
    }
  }
});

_populateThemeDropdown();

// ── Data loading ───────────────────────────────────────────────────────────
function startLivePolling() {
  function tick() {
    fetch('/api/live').then(r => r.json()).then(d => {
      const el = document.getElementById('live-widget');
      if (!el) return;
      const n = (d.active || []).length;
      if (n === 0) {
        el.style.display = 'none';
        // Slow down to normal cadence
        if (window._refreshSeconds !== 30) window._refreshSeconds = 30;
      } else {
        el.style.display = '';
        // Tighten the auto-refresh cadence while something is live
        if (window._refreshSeconds !== 10) window._refreshSeconds = 10;
        const sample = d.active[0];
        el.textContent = '\u25c9 LIVE \u2014 ' + n + ' session' + (n === 1 ? '' : 's')
          + ' \u2022 $' + (sample.cost || 0).toFixed(2);
        // Tooltip with all active sessions
        el.title = (d.active || [])
          .map(s => `${s.session_id} (${s.project}) ${s.turns} turns, $${(s.cost || 0).toFixed(2)} — ${s.seconds_ago}s ago`)
          .join('\n');
      }
    }).catch(() => {});
  }
  if (_liveTimer) clearInterval(_liveTimer);
  tick();
  _liveTimer = setInterval(tick, 10000);  // probe every 10s — cheap
}

async function loadData() {
  try {
    const resp = await fetch('/api/data');
    const d = await resp.json();
    if (d.error) {
      document.body.innerHTML = '<div style="padding:40px;color:#f87171">' + esc(d.error) + '</div>';
      return;
    }
    const refreshNote = rangeIncludesToday(selectedRange) ? ' \u00b7 Auto-refresh in 30s' : '';
    document.getElementById('meta').textContent = 'Updated: ' + d.generated_at + refreshNote;

    const streakEl = document.getElementById('streak-badge');
    if (streakEl) {
      const n = (d && typeof d.streak === 'number') ? d.streak : 0;
      if (n > 0) {
        streakEl.textContent = '\ud83d\udd25 ' + n + '-day streak';
        streakEl.hidden = false;
      } else {
        streakEl.textContent = '';
        streakEl.hidden = true;
      }
    }

    const isFirstLoad = rawData === null;
    rawData = d;
    rebuildProjectDisplayCache();

    if (isFirstLoad) {
      // Restore range from URL, mark active button
      selectedRange = readURLRange();
      document.querySelectorAll('.range-btn').forEach(btn =>
        btn.classList.toggle('active', btn.dataset.range === selectedRange)
      );
      // Mark default TZ button active
      document.querySelectorAll('.tz-btn').forEach(btn =>
        btn.classList.toggle('active', btn.dataset.tz === hourlyTZ)
      );
      // Build model filter (reads URL for model selection too)
      buildFilterUI(d.all_models);
      // Build account dropdown (reads URL/prefs; hides itself when only 1 account)
      buildAccountUI(d.accounts || []);
      buildMachineFilterUI(d.all_machines || []);
      updateSortIcons();
      updateModelSortIcons();
      updateProjectSortIcons();
      updateProjectBranchSortIcons();
      updateCompareUI();
    }

    renderGitTraceCard(d.git_trace_recent || []);
    applyFilter();
    renderPlanLimits();
  } catch(e) {
    console.error(e);
  }
}

// ── Git-trace insight card ────────────────────────────────────────────────
// Renders one line per top session that produced commits in the last 7 days.
// Each session_id is linkified to the session detail panel by clicking the
// matching row in the sessions table (we use the standard session_id-full
// hash navigation).
function renderGitTraceCard(commits) {
  const el = document.getElementById('git-trace-card');
  if (!el) return;
  if (!Array.isArray(commits) || commits.length === 0) {
    el.style.display = 'none';
    return;
  }
  // 7-day window, newest first.
  const cutoffMs = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const recent = commits.filter(c => {
    const t = Date.parse(c.timestamp);
    return Number.isFinite(t) && t >= cutoffMs;
  });
  if (recent.length === 0) {
    el.style.display = 'none';
    return;
  }
  // Group by session_id (skip blank session_ids — they represent commits
  // made outside an active Claude Code session).
  const bySession = {};
  for (const c of recent) {
    if (!c.session_id) continue;
    if (!bySession[c.session_id]) {
      bySession[c.session_id] = { count: 0, latest: c };
    }
    bySession[c.session_id].count += 1;
    if (c.timestamp > bySession[c.session_id].latest.timestamp) {
      bySession[c.session_id].latest = c;
    }
  }
  const topSessions = Object.entries(bySession)
    .map(([sid, info]) => ({ sid, ...info }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 5);

  // The "Sessions resulted in N commits..." headline counts unique sessions
  // that produced at least one commit (so an attentive reader can sanity-
  // check the top-N list against the total).
  const sessionCount = Object.keys(bySession).length;
  const totalCommits = recent.length;
  const headline = `<strong>${sessionCount}</strong> session${sessionCount === 1 ? '' : 's'} produced <strong>${totalCommits}</strong> commit${totalCommits === 1 ? '' : 's'} in the last 7 days`;

  let body = '';
  if (topSessions.length) {
    const items = topSessions.map(t => {
      const shortSid = esc(t.sid.slice(0, 8));
      const repoBase = (t.latest.repo || '').split('/').pop() || '(unknown repo)';
      const msg = esc((t.latest.message || '').slice(0, 60));
      return `<li style="margin: 4px 0;"><a href="#session-${esc(t.sid)}" onclick="selectSessionByFullId('${esc(t.sid)}'); return false;" style="color: var(--accent); text-decoration: none; font-family: monospace;">${shortSid}</a> &middot; ${t.count} commit${t.count === 1 ? '' : 's'} &middot; <span style="color: var(--muted)">${esc(repoBase)}</span> &middot; <em>${msg}</em></li>`;
    }).join('');
    body = `<ul style="margin: 6px 0 0 0; padding-left: 20px; list-style: disc;">${items}</ul>`;
  }
  el.innerHTML = `<div style="font-weight: 500; margin-bottom: 2px;">Git activity &middot; ${headline}</div>${body}`;
  el.style.display = '';
}

// Tries to scroll the session detail row into view + click it. Falls back
// to a no-op if the session isn't in the currently-filtered table (e.g.
// older than the active range filter).
function selectSessionByFullId(fullId) {
  const row = document.querySelector(`tr.session-row[data-session-id="${fullId}"]`);
  if (row) {
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    row.click();
  }
}

let autoRefreshTimer = null;
function scheduleAutoRefresh() {
  if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; }
  if (rangeIncludesToday(selectedRange)) {
    autoRefreshTimer = setInterval(loadData, 30000);
  }
}

scheduleAutoRefresh();

// Register service worker so the dashboard is PWA-installable. Guard with
// feature-detect for older browsers (and for file:// previews where SW APIs
// are unavailable). Failures are swallowed — the dashboard works without it.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
  });
}

// ── Dashboard customization (drag-reorder + per-block hide) ────────────────
// Default canonical order — includes every section the mega-merge introduces
// (plan-limits, budget bar, anomaly banner, plan/downgrade/cache cards, git
// trace, inbound feed, time-on-task, tools chart, cost-by-branch table) so
// "Reset to defaults" puts them back in a sensible position.
const DASHBOARD_BLOCK_IDS = [
  'stats-row', 'plan-limits-card', 'pareto-card', 'budget-bar', 'anomaly-banner',
  'plan-card', 'downgrade-card', 'cache-hit-card', 'git-trace-card', 'inbound-card',
  'time-on-task-card', 'charts-grid-main', 'charts-grid-tools',
  'cost-by-model-table', 'recent-sessions-table', 'session-detail-card',
  'cost-by-project-table', 'cost-by-project-branch-table', 'cost-by-branch-card',
];
let dashboardPrefs = { order: [], hidden: [] };
let editMode = false;
let dragSrcId = null;

function _containerEl() { return document.querySelector('.container'); }

function _allBlocks() {
  return Array.from(_containerEl().querySelectorAll(':scope > [data-block-id]'));
}

function applyDashboardPrefs(prefs) {
  dashboardPrefs = {
    order:  Array.isArray(prefs && prefs.order)  ? prefs.order.slice()  : [],
    hidden: Array.isArray(prefs && prefs.hidden) ? prefs.hidden.slice() : [],
  };
  const container = _containerEl();
  if (!container) return;
  // Reorder: known IDs first in prefs.order, then any new blocks the user
  // hasn't seen yet (keeps forward compat when we add new sections).
  if (dashboardPrefs.order.length) {
    const blocksById = {};
    _allBlocks().forEach(b => { blocksById[b.dataset.blockId] = b; });
    const seen = new Set();
    dashboardPrefs.order.forEach(id => {
      if (blocksById[id]) { container.appendChild(blocksById[id]); seen.add(id); }
    });
    // Append unseen blocks at the end in their current DOM order.
    Object.keys(blocksById).forEach(id => {
      if (!seen.has(id)) container.appendChild(blocksById[id]);
    });
  }
  const hidden = new Set(dashboardPrefs.hidden);
  _allBlocks().forEach(b => {
    b.classList.toggle('block-hidden', hidden.has(b.dataset.blockId));
  });
}

async function fetchDashboardPrefs() {
  try {
    const r = await fetch('/api/dashboard-prefs');
    const p = await r.json();
    applyDashboardPrefs(p);
  } catch (e) { console.warn('dashboard prefs fetch failed', e); }
}

async function saveDashboardPrefs() {
  const order  = _allBlocks().map(b => b.dataset.blockId);
  const hidden = _allBlocks().filter(b => b.classList.contains('block-hidden')).map(b => b.dataset.blockId);
  dashboardPrefs = { order, hidden };
  try {
    await fetch('/api/dashboard-prefs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ order, hidden }),
    });
  } catch (e) { console.warn('dashboard prefs save failed', e); }
}

function _ensureOverlays() {
  _allBlocks().forEach(block => {
    if (block.querySelector(':scope > .block-edit-overlay')) return;
    const id = block.dataset.blockId;
    const overlay = document.createElement('div');
    overlay.className = 'block-edit-overlay';
    overlay.innerHTML =
      '<span class="block-drag-handle" title="Drag to reorder">⋮⋮</span>' +
      '<label class="block-hide-label"><input type="checkbox" data-hide-for="' + id + '"> Hide</label>';
    block.insertBefore(overlay, block.firstChild);
    const cb = overlay.querySelector('input[type="checkbox"]');
    cb.checked = block.classList.contains('block-hidden');
    cb.addEventListener('change', () => {
      block.classList.toggle('block-hidden', cb.checked);
    });
    // HTML5 drag-and-drop on the block itself, triggered from the handle.
    const handle = overlay.querySelector('.block-drag-handle');
    handle.addEventListener('mousedown', () => { block.setAttribute('draggable', 'true'); });
    block.addEventListener('dragstart', (e) => {
      dragSrcId = block.dataset.blockId;
      block.classList.add('dragging');
      try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragSrcId); } catch(_) {}
    });
    block.addEventListener('dragend', () => {
      block.classList.remove('dragging');
      block.removeAttribute('draggable');
      _allBlocks().forEach(b => b.classList.remove('drop-target'));
      dragSrcId = null;
    });
    block.addEventListener('dragover', (e) => {
      if (!editMode || dragSrcId === null || block.dataset.blockId === dragSrcId) return;
      e.preventDefault();
      try { e.dataTransfer.dropEffect = 'move'; } catch(_) {}
      block.classList.add('drop-target');
    });
    block.addEventListener('dragleave', () => { block.classList.remove('drop-target'); });
    block.addEventListener('drop', (e) => {
      if (!editMode || dragSrcId === null) return;
      e.preventDefault();
      block.classList.remove('drop-target');
      const src = document.querySelector('[data-block-id="' + dragSrcId + '"]');
      if (!src || src === block) return;
      const container = _containerEl();
      const blocks = _allBlocks();
      const srcIdx = blocks.indexOf(src);
      const dstIdx = blocks.indexOf(block);
      if (srcIdx < dstIdx) {
        container.insertBefore(src, block.nextSibling);
      } else {
        container.insertBefore(src, block);
      }
    });
  });
}

function toggleEditMode() {
  editMode = !editMode;
  document.body.classList.toggle('edit-mode', editMode);
  const btn = document.getElementById('customize-btn');
  if (btn) {
    btn.textContent = editMode ? 'Done' : 'Customize';
    btn.classList.toggle('editing', editMode);
  }
  if (editMode) {
    _ensureOverlays();
  } else {
    saveDashboardPrefs();
  }
}

async function resetDashboardPrefs() {
  // Reset to the canonical default order and unhide everything.
  const container = _containerEl();
  const blocksById = {};
  _allBlocks().forEach(b => { blocksById[b.dataset.blockId] = b; });
  DASHBOARD_BLOCK_IDS.forEach(id => {
    if (blocksById[id]) container.appendChild(blocksById[id]);
  });
  _allBlocks().forEach(b => b.classList.remove('block-hidden'));
  // Sync overlay checkboxes if they're rendered.
  document.querySelectorAll('.block-hide-label input[type="checkbox"]').forEach(cb => { cb.checked = false; });
  try {
    await fetch('/api/dashboard-prefs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ order: [], hidden: [] }),
    });
  } catch (e) { console.warn('reset failed', e); }
  dashboardPrefs = { order: [], hidden: [] };
}

// Apply server-side prefs as soon as the DOM is ready (before /api/data
// returns) so the user doesn't see blocks rearrange under them.
fetchDashboardPrefs();

</script>
</body>
</html>
"""


def render_html():
    """Inject the Python PRICING table into the HTML so the JS table
    can never drift from the Python one."""
    return HTML_TEMPLATE.replace(
        "/*__PRICING_JSON__*/",
        json.dumps(PRICING),
    ).encode("utf-8")



# Share / read-only mode.
# When SHARE_TOKEN is set, every request must include ?token=<value> in the
# query string (or X-Dashboard-Token header). Mutating endpoints (rescan,
# budget) return 403 regardless of token — share mode is read-only.
SHARE_TOKEN = None  # set by serve(share_token=...)


def _check_share_access(handler):
    """Verify share token (if mode is on). Returns True if request may proceed."""
    if not SHARE_TOKEN:
        return True
    # Header takes precedence; otherwise query param.
    tok = handler.headers.get("X-Dashboard-Token")
    if not tok:
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(handler.path).query)
        tok = (q.get("token") or [None])[0]
    if tok == SHARE_TOKEN:
        return True
    handler.send_response(401)
    handler.send_header("Content-Type", "text/plain")
    handler.end_headers()
    handler.wfile.write(b"Unauthorized: missing or invalid token.")
    return False
def _export_raw_turns(limit=200_000, db_path=None):
    """Return a list of all turns in DB. Limited to avoid eating the whole
    heap on absurdly large DBs; users with more should use sqlite3 directly."""
    db = db_path or DB_PATH
    if not db.exists():
        return []
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"""
        SELECT session_id, timestamp, model,
               input_tokens, output_tokens,
               cache_read_tokens, cache_creation_tokens,
               tool_name, cwd, message_id
        FROM turns
        ORDER BY timestamp ASC
        LIMIT {int(limit)}
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _export_csv(kind, db_path=None):
    """Return a CSV string for one of: daily, sessions, projects."""
    import csv, io as _io
    data = get_dashboard_data(db_path or DB_PATH)
    out = _io.StringIO()
    w = csv.writer(out)
    if kind == "daily":
        w.writerow(["day", "model", "input", "output", "cache_read", "cache_creation", "turns"])
        for r in data.get("daily_by_model", []):
            w.writerow([r["day"], r["model"], r["input"], r["output"],
                        r["cache_read"], r["cache_creation"], r["turns"]])
    elif kind == "sessions":
        w.writerow(["session_id", "session_name", "project", "branch", "last", "model",
                    "turns", "input", "output", "cache_read", "cache_creation"])
        for s in data.get("sessions_all", []):
            w.writerow([s["session_id"], s.get("session_name", ""), s["project"], s["branch"],
                        s["last"], s["model"], s["turns"], s["input"], s["output"],
                        s["cache_read"], s["cache_creation"]])
    elif kind == "projects":
        # Aggregate sessions by project for a project-level summary.
        agg = {}
        for s in data.get("sessions_all", []):
            p = s["project"]
            d = agg.setdefault(p, {"sessions": 0, "turns": 0, "input": 0, "output": 0,
                                   "cache_read": 0, "cache_creation": 0})
            d["sessions"] += 1
            for k in ("turns", "input", "output", "cache_read", "cache_creation"):
                d[k] += s[k]
        w.writerow(["project", "sessions", "turns", "input", "output", "cache_read", "cache_creation"])
        for p, d in sorted(agg.items(), key=lambda x: -x[1]["turns"]):
            w.writerow([p, d["sessions"], d["turns"], d["input"], d["output"],
                        d["cache_read"], d["cache_creation"]])
    else:
        w.writerow(["error"])
        w.writerow([f"unknown export type: {kind}"])
    return out.getvalue()
def _active_sessions(window_seconds=300, projects_dirs=None,
                     include_cowork=True, db_path=None):
    """Return sessions whose JSONL transcript was modified within the last
    `window_seconds`. Walks the same dirs as scanner.scan() but only stats
    files; no JSONL parsing. Returns [{session_id, project_name, model,
    last_modified, turns, input, output, cost}, ...]

    Args are kwargs-only so tests can pass hermetic fixtures."""
    import scanner
    from pricing import calc_cost
    import time as _time

    db = db_path or DB_PATH
    dirs = projects_dirs if projects_dirs is not None else scanner.DEFAULT_PROJECTS_DIRS

    now = _time.time()
    paths = []
    for d in dirs:
        if not d.exists():
            continue
        for p in d.rglob("*.jsonl"):
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if now - m <= window_seconds:
                paths.append((p, m))
    if include_cowork:
        try:
            import cowork
            cowork_dir = cowork.cowork_sessions_dir()
            if cowork_dir and cowork_dir.exists():
                for p in cowork_dir.rglob("audit.jsonl"):
                    try:
                        m = p.stat().st_mtime
                    except OSError:
                        continue
                    if now - m <= window_seconds:
                        paths.append((p, m))
        except Exception:  # noqa: BLE001 — cowork is optional
            pass

    if not paths or not db.exists():
        return []

    # Look up DB stats for the sessions whose files are active. The session
    # id is the JSONL filename stem for Claude Code; for Cowork it's
    # encoded in the parent dir name as "local_<sid>".
    candidate_ids = set()
    for p, _ in paths:
        if p.name == "audit.jsonl":
            parent = p.parent.name
            if parent.startswith("local_"):
                candidate_ids.add(parent[len("local_"):])
        else:
            candidate_ids.add(p.stem)

    if not candidate_ids:
        return []

    placeholders = ",".join("?" * len(candidate_ids))
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"""
        SELECT session_id, project_name, model, turn_count,
               total_input_tokens, total_output_tokens,
               total_cache_read, total_cache_creation, last_timestamp
        FROM sessions
        WHERE session_id IN ({placeholders})
           OR substr(session_id, 1, 36) IN ({placeholders})
    """, (*candidate_ids, *candidate_ids)).fetchall()
    conn.close()

    # Index by session id for lookup; merge with mtime so we can sort
    sessions_by_id = {r["session_id"]: dict(r) for r in rows}
    out = []
    seen = set()
    for p, m in paths:
        sid = (
            p.parent.name[len("local_"):] if p.name == "audit.jsonl" else p.stem
        )
        if sid in seen:
            continue
        seen.add(sid)
        s = sessions_by_id.get(sid) or {}
        if not s:
            # Match by 36-char prefix in case it was hashed differently
            for full_sid, candidate in sessions_by_id.items():
                if full_sid.startswith(sid[:36]):
                    s = candidate
                    break
        cost = 0.0
        if s.get("model"):
            cost = calc_cost(
                s["model"],
                s.get("total_input_tokens", 0),
                s.get("total_output_tokens", 0),
                s.get("total_cache_read", 0),
                s.get("total_cache_creation", 0),
            )
        out.append({
            "session_id":   sid[:8],
            "project":      s.get("project_name") or "unknown",
            "model":        s.get("model") or "unknown",
            "turns":        s.get("turn_count") or 0,
            "input":        s.get("total_input_tokens") or 0,
            "output":       s.get("total_output_tokens") or 0,
            "cost":         round(cost, 4),
            "last_modified": int(m),
            "seconds_ago":   int(now - m),
        })
    # Most recently active first
    return sorted(out, key=lambda x: x["seconds_ago"])


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if not _check_share_access(self):
            return
        # self.path includes the query string, but every URL the UI emits has
        # one (e.g. "/?range=all"); compare the bare path so bookmarkable
        # URLs don't fall through to 404.
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(render_html())

        elif path == "/api/health":
            # Lightweight liveness probe for Docker HEALTHCHECK / monitoring.
            # Returns server + DB state without doing any aggregation.
            payload = {
                "status": "ok" if DB_PATH.exists() else "no-db",
                "db_path": str(DB_PATH),
                "sessions": 0,
                "turns": 0,
            }
            try:
                if DB_PATH.exists():
                    c = sqlite3.connect(DB_PATH)
                    payload["sessions"] = c.execute("select count(*) from sessions").fetchone()[0]
                    payload["turns"] = c.execute("select count(*) from turns").fetchone()[0]
                    c.close()
            except Exception as e:  # noqa: BLE001
                payload["status"] = "error"
                payload["error"] = str(e)
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200 if payload["status"] != "error" else 500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/export.json":
            # Full JSON dump of everything /api/data carries, plus raw turns
            # so downstream tooling (BI, invoicing scripts) doesn't have to
            # re-implement aggregation.
            data = get_dashboard_data()
            data["turns"] = _export_raw_turns()
            body = json.dumps(data, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", "attachment; filename=claude-usage.json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/export.csv":
            # ?type=daily|sessions|projects (default: daily). Streams CSV
            # without loading more than one section at a time.
            qs = urlparse(self.path).query
            kind = "daily"
            for kv in qs.split("&"):
                if kv.startswith("type="):
                    kind = kv.split("=", 1)[1] or "daily"
            csv_body = _export_csv(kind).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header(
                "Content-Disposition", f'attachment; filename=claude-usage-{kind}.csv',
            )
            self.send_header("Content-Length", str(len(csv_body)))
            self.end_headers()
            self.wfile.write(csv_body)
        elif path == "/api/live":
            data = {"active": _active_sessions()}
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
        elif path.startswith("/api/session/"):
            sid = path.rsplit("/", 1)[-1]
            if not sid or not sid.isalnum():
                self.send_response(400); self.end_headers(); return
            d = _session_detail(sid)
            body = json.dumps(d).encode("utf-8")
            self.send_response(404 if "error" in d else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/data":
            data = get_dashboard_data()
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/session":
            parsed_url = urlparse(self.path)
            session_id = parse_qs(parsed_url.query).get("session_id", [""])[0]
            data = get_session_detail(session_id)
            body = json.dumps(data).encode("utf-8")
            self.send_response(200 if "error" not in data else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/api/themes":
            body = json.dumps(get_themes()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/fx-rates":
            data = _fetch_fx_rates()
            if data is None:
                payload = {
                    "error": "FX fetch failed",
                    "fallback": True,
                    "base": "USD",
                    "rates": {"USD": 1.0},
                    "as_of": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                }
            else:
                payload = data
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/inbound":
            parsed_url = urlparse(self.path)
            try:
                limit = int(parse_qs(parsed_url.query).get("limit", ["100"])[0])
            except ValueError:
                limit = 100
            limit = max(1, min(limit, 1000))
            events = read_inbound_events(limit=limit)
            body = json.dumps({"events": events, "count": len(events)}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/dashboard-prefs":
            prefs = _load_dashboard_prefs()
            payload = {
                "order":  prefs.get("order", [])  if isinstance(prefs.get("order"),  list) else [],
                "hidden": prefs.get("hidden", []) if isinstance(prefs.get("hidden"), list) else [],
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/themes":
            catalog_json = json.dumps(AWESOME_CATALOG)
            html = GALLERY_TEMPLATE.replace("__CATALOG_JSON__", catalog_json)
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        elif path == "/manifest.json":
            # PWA manifest — lets Chrome offer "Install Claude Usage" as an app.
            manifest = {
                "name": "Claude Code Usage Dashboard",
                "short_name": "Claude Usage",
                "description": "Local dashboard for tracking Claude Code token usage and cost.",
                "start_url": "/",
                "scope": "/",
                "display": "standalone",
                "orientation": "any",
                "theme_color": "#0071e3",
                "background_color": "#f5f5f7",
                "icons": [
                    {"src": "/icon.svg", "sizes": "192x192", "type": "image/svg+xml", "purpose": "any"},
                    {"src": "/icon.svg", "sizes": "512x512", "type": "image/svg+xml", "purpose": "any"},
                    {"src": "/icon.svg", "sizes": "any",     "type": "image/svg+xml", "purpose": "any maskable"},
                ],
            }
            body = json.dumps(manifest).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path.startswith("/api/text/"):
            # Single-number text endpoints for osascript / SwiftBar / cron + curl.
            # Always return a bare ASCII string with no trailing newline so
            # `do shell script "curl ..."` in AppleScript yields a clean value.
            stats = get_text_stats()
            key_map = {
                "/api/text/today-cost":      f"{stats['today_cost']:.2f}",
                "/api/text/month-cost":      f"{stats['month_cost']:.2f}",
                "/api/text/active-sessions": str(stats["active_sessions"]),
                "/api/text/budget-pct":      str(stats["budget_pct"]),
            }
            value = key_map.get(path)
            if value is None:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"not found")
                return
            body = value.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/sw.js":
            # Minimal service worker — install, claim clients, and pass fetches
            # through with a small cache for the Chart.js CDN bundle so repeat
            # loads are instant and the dashboard still boots when offline.
            body = SERVICE_WORKER_JS.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Service-Worker-Allowed", "/")
            self.end_headers()
            self.wfile.write(body)

        elif path == "/icon.svg":
            body = APP_ICON_SVG.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if not _check_share_access(self):
            return
        if SHARE_TOKEN:
            # Read-only share mode: writes are never allowed.
            self.send_response(403)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Forbidden: dashboard is in read-only share mode.")
            return
        path = urlparse(self.path).path if 'urlparse' in globals() else self.path.split('?', 1)[0]
        if path == "/api/reset":
            # Wipe usage.db entirely. Caller is expected to re-run scan
            # afterwards to repopulate (or use /api/rescan?full=1 in
            # forks where that exists).
            try:
                if DB_PATH.exists():
                    DB_PATH.unlink()
                # Re-create empty schema so /api/data doesn't 500 on next load
                import scanner
                conn = scanner.get_db(DB_PATH)
                scanner.init_db(conn)
                conn.close()
                body = json.dumps({"ok": True, "message": "Database reset."}).encode("utf-8")
                self.send_response(200)
            except Exception as e:  # noqa: BLE001
                body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        
        path = urlparse(self.path).path
        if path == "/api/tags":
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b"{}"
            try:
                req = json.loads(body)
            except json.JSONDecodeError:
                req = {}
            sid = req.get("session_id")
            tags = req.get("tags")
            cfg = _load_tags()
            if sid:
                if not tags:
                    cfg.pop(sid, None)
                else:
                    cfg[sid] = list(tags)
                _save_tags(cfg)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "tags": cfg.get(sid, [])}).encode("utf-8"))
            return
        if path == "/api/project-budget":
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b"{}"
            try:
                req = json.loads(body)
            except json.JSONDecodeError:
                req = {}
            proj = req.get("project")
            cap = req.get("monthly_usd")
            cfg = _load_project_budgets()
            if proj:
                if cap is None or cap == 0:
                    cfg.pop(proj, None)
                else:
                    cfg[proj] = float(cap)
                _save_project_budgets(cfg)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "budgets": cfg}).encode("utf-8"))
            return
        if path == "/api/rescan":
            # Default: incremental scan (fast, non-destructive).
            # Opt-in full rebuild with ?full=1 — useful when pricing or
            # parsing logic changes and historical rows need to be redone.
            # Pass DB_PATH / DEFAULT_PROJECTS_DIRS explicitly so tests that
            # patch the module globals are honored (scan's defaults are
            # frozen at def time and would otherwise target the real paths).
            import scanner
            db_path = DB_PATH
            full = "full=1" in (urlparse(self.path).query or "")
            if full and db_path.exists():
                db_path.unlink()
            result = scanner.scan(
                db_path=db_path,
                projects_dirs=scanner.DEFAULT_PROJECTS_DIRS,
                verbose=False,
            )
            # Evaluate user-defined alerts after the scan. Failures are
            # surfaced in the response but never break the rescan.
            try:
                import alerts as _alerts
                result["alerts"] = _alerts.evaluate_all()
            except Exception as exc:  # pragma: no cover - defensive
                result["alerts_error"] = str(exc)
            body = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/project-name":
            # Body: {"raw": "PhpstormProjects/PPC freelo", "display": "PPC.cz"}
            # Empty/whitespace display clears the alias (per spec).
            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw_body or b"{}")
            except json.JSONDecodeError:
                payload = None
            if not isinstance(payload, dict) or not isinstance(payload.get("raw"), str):
                err = json.dumps({"error": "Body must be {raw: str, display: str}"}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
                return
            raw_name = payload["raw"].strip()
            display = payload.get("display", "")
            if not isinstance(display, str):
                display = ""
            display = display.strip()
            if not raw_name:
                err = json.dumps({"error": "raw must be a non-empty string"}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
                return
            aliases = _load_project_aliases()
            if display:
                aliases[raw_name] = display
            else:
                aliases.pop(raw_name, None)
            saved = _save_project_aliases(aliases)
            body = json.dumps({"ok": True, "aliases": saved}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        elif path.startswith("/api/inbound/"):
            # Inbound webhook receiver: any caller can POST arbitrary JSON
            # to /api/inbound/<event_type>. We wrap it with envelope
            # metadata and append to ~/.claude/inbound.jsonl.
            event_type = path[len("/api/inbound/"):].strip("/")
            if not event_type or "/" in event_type:
                self.send_response(404)
                self.end_headers()
                return

            # Optional shared-secret check. If the operator configured a
            # secret, the request MUST present it via X-Inbound-Secret.
            expected = _load_inbound_secret()
            if expected is not None:
                provided = self.headers.get("X-Inbound-Secret", "")
                if provided != expected:
                    body = json.dumps({"error": "forbidden"}).encode("utf-8")
                    self.send_response(403)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                body = json.dumps({"error": "invalid JSON body"}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            source_ip = self.client_address[0] if self.client_address else ""
            try:
                record = append_inbound_event(event_type, payload, source_ip)
            except OSError as e:
                body = json.dumps({"error": f"write failed: {e}"}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            body = json.dumps({"ok": True, "received_at": record["received_at"]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/dashboard-prefs":
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                body_in = json.loads(raw.decode("utf-8") or "{}")
            except Exception as e:
                err = json.dumps({"ok": False, "error": "invalid json: " + str(e)}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
                return
            ok, result = _validate_dashboard_prefs(body_in)
            if not ok:
                err = json.dumps({"ok": False, "error": result}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
                return
            _save_dashboard_prefs(result)
            body = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()


def serve(host=None, port=None, share_token=None):
    global SHARE_TOKEN
    SHARE_TOKEN = share_token
    host = host or os.environ.get("HOST", "localhost")
    port = port or int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Dashboard running at http://{host}:{port}")
    if host not in ("localhost", "127.0.0.1", "::1"):
        print(f"  WARNING: bound to {host} — no authentication. "
              "Anyone reachable on this interface can read your project history "
              "and trigger /api/rescan.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    serve()
