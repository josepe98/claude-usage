# Claude Code Usage Dashboard

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)
[![claude-code](https://img.shields.io/badge/claude--code-black?style=flat-square)](https://claude.ai/code)

**See exactly how you're using Claude Code — tokens, costs, models, and projects, all from your local data.**

Claude Code writes detailed usage logs locally — token counts, models, sessions, projects — regardless of your plan. This dashboard reads those logs and turns them into charts and cost estimates. Works on API, Pro, and Max plans.

![Claude Usage Dashboard](docs/screenshot.png)

**Originally created by:** [The Product Compass Newsletter](https://www.productcompass.pm)  
**This fork** ([josepe98/claude-usage](https://github.com/josepe98/claude-usage)) is actively maintained and includes bug fixes and features not yet merged upstream. PRs welcome.

> **What's different here vs [phuryn/claude-usage](https://github.com/phuryn/claude-usage)**
> - **Fixed:** Hourly chart crash — dashboard went blank when touching any filter ([#74](https://github.com/phuryn/claude-usage/issues/74))
> - **Fixed:** Blank dashboard for users with non-standard model names ([#76](https://github.com/phuryn/claude-usage/issues/76), [#106](https://github.com/phuryn/claude-usage/issues/106))
> - **Fixed:** Bookmarkable URLs (`?range=30d`) returning 404 ([#80](https://github.com/phuryn/claude-usage/issues/80))
> - **Fixed:** Concurrent requests blocked — slow data loads no longer freeze the dashboard ([#78](https://github.com/phuryn/claude-usage/issues/78))
> - **New:** Appearance gallery with 5 bundled themes (Apple, Linear, Vercel, Notion, Stripe) at `/themes`

---

## What this tracks

Works on **API, Pro, and Max plans** — Claude Code writes local usage logs regardless of subscription type. This tool reads those logs and gives you visibility that Anthropic's UI doesn't provide.

Captures usage from:
- **Claude Code CLI** (`claude` command in terminal)
- **VS Code extension** (Claude Code sidebar)
- **Dispatched Code sessions** (sessions routed through Claude Code)

**Also captured:**
- **Claude Desktop Cowork sessions** — via local audit logs (see [Cowork sessions](#cowork-sessions) below)

---

## Requirements

- Python 3.8+
- No third-party packages — uses only the standard library (`sqlite3`, `http.server`, `json`, `pathlib`)

> Anyone running Claude Code already has Python installed.

## Quick Start

No `pip install`, no virtual environment, no build step.

### Windows
```
git clone https://github.com/josepe98/claude-usage
cd claude-usage
python cli.py dashboard
```

### macOS / Linux
```
git clone https://github.com/josepe98/claude-usage
cd claude-usage
python3 cli.py dashboard
```

---

## Usage

> **macOS/Linux:** use `python3`. **Windows:** use `python`. If neither works, see [Requirements](#requirements).

```
# Scan JSONL files and populate the database (~/.claude/usage.db)
python cli.py scan

# Show today's usage summary by model (in terminal)
python cli.py today

# Show the last 7 days (per-day breakdown + by-model totals)
python cli.py week

# Show all-time statistics (in terminal)
python cli.py stats

# Scan + open browser dashboard at http://localhost:8080
python cli.py dashboard

# Custom host and port via environment variables
HOST=0.0.0.0 PORT=9000 python cli.py dashboard

# Scan a custom projects directory
python cli.py scan --projects-dir /path/to/transcripts

# List available themes
python cli.py theme list

# Apply a theme (apple, linear, vercel, notion, stripe)
python cli.py theme set linear

# Launch the tray / menu-bar app (see "Menu bar app" below)
python cli.py tray
```

The scanner is incremental — it tracks each file's path and modification time, so re-running `scan` is fast and only processes new or changed files.

By default, the scanner checks both `~/.claude/projects/` and the Xcode Claude integration directory (`~/Library/Developer/Xcode/CodingAssistant/ClaudeAgentConfig/projects/`), skipping any that don't exist. Use `--projects-dir` to scan a custom location instead.

---

## How it works

Claude Code writes one JSONL file per session to `~/.claude/projects/`. Each line is a JSON record; `assistant`-type records contain:
- `message.usage.input_tokens` — raw prompt tokens
- `message.usage.output_tokens` — generated tokens
- `message.usage.cache_creation_input_tokens` — tokens written to prompt cache
- `message.usage.cache_read_input_tokens` — tokens served from prompt cache
- `message.model` — the model used (e.g. `claude-sonnet-4-6`)

`scanner.py` parses those files and stores the data in a SQLite database at `~/.claude/usage.db`.

`dashboard.py` serves a single-page dashboard on `localhost:8080` with Chart.js charts (loaded from CDN). It auto-refreshes every 30 seconds and supports model filtering with bookmarkable URLs. The bind address and port can be overridden with `HOST` and `PORT` environment variables (defaults: `localhost`, `8080`).

---

## Cost estimates

Costs are calculated using **Anthropic API pricing as of April 2026** ([claude.com/pricing#api](https://claude.com/pricing#api)).

**Only models whose name contains `opus`, `sonnet`, or `haiku` are included in cost calculations.** Local models, unknown models, and any other model names are excluded (shown as `n/a`).

| Model | Input | Output | Cache Write | Cache Read |
|-------|-------|--------|------------|-----------|
| claude-opus-4-7 | $5.00/MTok | $25.00/MTok | $6.25/MTok | $0.50/MTok |
| claude-opus-4-6 | $5.00/MTok | $25.00/MTok | $6.25/MTok | $0.50/MTok |
| claude-sonnet-4-6 | $3.00/MTok | $15.00/MTok | $3.75/MTok | $0.30/MTok |
| claude-haiku-4-5 | $1.00/MTok | $5.00/MTok | $1.25/MTok | $0.10/MTok |

> **Note:** These are API prices. If you use Claude Code via a Max or Pro subscription, your actual cost structure is different (subscription-based, not per-token).

---

---

## Menu bar app

A tiny tray / menu-bar icon that shows today's Claude spend at a glance and a
dropdown with "Today" / "This month" totals. Click **Open Dashboard** to launch
the full UI in your browser.

The tray app polls `http://localhost:8080/api/health` and `/api/data` every
60 seconds, so you need the dashboard running in the background (or as a service)
for it to display data. An offline indicator appears if the dashboard is unreachable.

### Install (optional dependency)

```
# macOS — native NSStatusBar via rumps
pip install rumps

# Linux / Windows — cross-platform indicator via pystray
pip install pystray pillow
```

`rumps` / `pystray` are NOT required to run the rest of the dashboard — they
are only needed for the tray feature. The tray module imports them lazily, so
nothing else is affected if they aren't installed.

### Launch

```
# Defaults to http://localhost:8080
python cli.py tray

# Point at a different dashboard
python cli.py tray --url http://my-host:9000
```

### Badge colour

The icon hints at today's spend level so you can tell at a glance:

| Today's spend | Colour |
|---|---|
| < $1     | green |
| $1 – $10 | amber |
| > $10    | red   |
## Team mode (workspace)

By default the dashboard is single-machine -- each laptop scans its own
`~/.claude/projects/` and writes to its own `~/.claude/usage.db`. Add a
`~/.claude/workspace.json` to point multiple machines at one shared database
for team-level rollups (cost by engineer, total spend, etc.).

**Option A: shared SQLite file (zero setup)**

```json
{
  "backend": "sqlite",
  "machine_id": "jakduch-mbp",
  "team": "engineering",
  "db_path": "/Volumes/Dropbox/team/claude-usage.db"
}
```

Works with Dropbox / NFS / SMB / iCloud Drive. No daemon, no server. Caveat:
SQLite write locking is best-effort over network filesystems -- fine for a
small team, but pick Postgres if you have heavy concurrent scans.

**Option B: Postgres (recommended for >3 people)**

```json
{
  "backend": "postgres",
  "machine_id": "jakduch-mbp",
  "team": "engineering"
}
```

Set the DSN as an env var so it isn't committed anywhere:

```sh
export CLAUDE_USAGE_PG="postgresql://user:pass@host:5432/claude_usage"
pip install psycopg2-binary
python3 cli.py scan
```

`psycopg2-binary` is an optional dep -- only required when `backend = "postgres"`.

**What you get**

- `machine_id` column on `turns` + `sessions` (idempotent migration -- safe to
  upgrade an existing single-machine DB)
- `/api/data` payload gains a `by_machine` aggregation and `all_machines` list
- Dashboard adds a **Machine** filter dropdown (auto-hidden if only one
  machine has reported in -- existing single-laptop users see no UI change)

**Backward compatibility**: if `~/.claude/workspace.json` doesn't exist,
behavior is unchanged -- local SQLite at `~/.claude/usage.db`, no team
plumbing engaged. Existing databases are migrated in place on first scan.

**Env-var overrides** (handy for CI / Docker):

| Variable | Purpose |
|---|---|
| `CLAUDE_USAGE_BACKEND` | `sqlite` or `postgres` |
| `CLAUDE_USAGE_PG` | Postgres DSN |
| `CLAUDE_USAGE_MACHINE_ID` | Override the machine label |
| `CLAUDE_USAGE_TEAM` | Team label |

---

## Cowork sessions

Claude Desktop (the agent / Cowork mode in the desktop app) writes a per-session
audit log to its userData directory:

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/local-agent-mode-sessions/` |
| Windows | `%APPDATA%/Claude/local-agent-mode-sessions/` |
| Linux | `$XDG_CONFIG_HOME/Claude/local-agent-mode-sessions/` (default `~/.config/...`) |

`scan` automatically picks these up alongside `~/.claude/projects/`. Sessions
appear in the dashboard under project names like `Cowork/<8-char-id>`.

Token totals come from the authoritative `result.modelUsage` blocks (the same
numbers Anthropic uses for billing), so cost estimates line up with what
the API reports rather than aggregating per-event streaming chunks.

## Shell tab completion

Generate a completion script for your shell and source it. Subcommand names
(`scan`, `today`, `dashboard`, …) and long flags (`--projects-dir`, `--host`,
`--port`, …) will then tab-complete.

```
# bash
python3 cli.py completions bash > ~/.claude-usage-completion.bash
echo 'source ~/.claude-usage-completion.bash' >> ~/.bashrc

# zsh — drop into any directory on $fpath
python3 cli.py completions zsh > ~/.zsh/completions/_claude-usage

# fish
python3 cli.py completions fish > ~/.config/fish/completions/claude-usage.fish
```

## Files

| File | Purpose |
|------|---------|
| `scanner.py` | Parses JSONL transcripts, writes to `~/.claude/usage.db` |
| `dashboard.py` | HTTP server + single-page HTML/JS dashboard |
| `cli.py` | `scan`, `today`, `stats`, `dashboard`, `theme`, `completions` commands |
| `cli.py` | `scan`, `today`, `stats`, `dashboard`, `theme` commands |
| `workspace.py` | Team-mode config: backend selection, machine_id, lazy psycopg2 import |

## Custom Alerts

Define rules in `~/.claude/alerts.json` and the dashboard will evaluate them
after every scan. Each rule pairs a *condition* (in a tiny safe DSL) with an
*action* (shell command or HTTP webhook). Per-rule cooldowns prevent the
same alert from firing repeatedly.

Example `~/.claude/alerts.json`:

```json
[
  {
    "name": "Daily over $50",
    "condition": "today_cost > 50",
    "action": {"type": "shell", "cmd": "say 'Spending alert'"},
    "cooldown_minutes": 60
  },
  {
    "name": "Project budget hit",
    "condition": "project == 'client-X' AND month_to_date > 200",
    "action": {
      "type": "webhook",
      "url": "https://hooks.slack.com/services/...",
      "payload": {"text": "Client X over budget"}
    },
    "cooldown_minutes": 30
  }
]
```

### Available variables

| Variable | Meaning |
|---|---|
| `today_cost` | Total USD spent today across all models/projects |
| `month_to_date` | Total USD spent this calendar month |
| `turn_cost` | USD cost of the most recent turn |
| `model` | Model id of the most recent turn (e.g. `claude-opus-4-7`) |
| `project` | Project name of the most recent turn |

### Supported operators

Comparisons: `>`, `>=`, `<`, `<=`, `==`, `!=`. Boolean: `AND`, `OR`, `NOT`
(uppercase or lowercase). String literals are single-quoted.

### CLI

```
python3 cli.py alerts list                       # show every rule
python3 cli.py alerts test                       # dry-run (no actions executed)
python3 cli.py alerts trigger "Daily over $50"   # force-fire a rule
```

### Safety

Conditions are parsed with `ast.parse()` and walked by a strict allowlist of
node types (`Compare`, `BoolOp`, `Name`, `Constant`, `UnaryOp`). There is
**no** call to `eval()` or `exec()` anywhere in the alerts pipeline -
function calls, attribute access, imports, and subscripts are all rejected.
Shell actions run via `subprocess.run` with a 10s timeout; webhooks via
`urllib.request` with a 10s timeout.
