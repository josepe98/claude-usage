# Claude Code Usage Dashboard

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)
[![claude-code](https://img.shields.io/badge/claude--code-black?style=flat-square)](https://claude.ai/code)

**See exactly how you're using Claude Code — tokens, costs, models, and projects, all from your local data.**

Claude Code writes detailed usage logs locally — token counts, models, sessions, projects — regardless of your plan. This dashboard reads those logs and turns them into charts and cost estimates. Works on API, Pro, and Max plans.

![Claude Usage Dashboard](docs/screenshot.png)

**Originally created by:** [The Product Compass Newsletter](https://www.productcompass.pm)  
**This fork** ([josepe98/claude-usage](https://github.com/josepe98/claude-usage)) is actively maintained and includes bug fixes and features not yet merged upstream. PRs welcome.

> **What's different here vs [phuryn/claude-usage](https://github.com/phuryn/claude-usage)**
>
> This fork has diverged substantially. A partial list of what's been added beyond upstream:
>
> **Bug fixes**
> - Hourly chart crash — dashboard went blank when touching any filter ([#74](https://github.com/phuryn/claude-usage/issues/74))
> - Blank dashboard for users with non-standard model names ([#76](https://github.com/phuryn/claude-usage/issues/76), [#106](https://github.com/phuryn/claude-usage/issues/106))
> - Bookmarkable URLs (`?range=30d`) returning 404 ([#80](https://github.com/phuryn/claude-usage/issues/80))
> - Concurrent requests blocked — slow data loads no longer freeze the dashboard ([#78](https://github.com/phuryn/claude-usage/issues/78))
>
> **Dashboard features**
> - **5 bundled themes** (Apple, Linear, Vercel, Notion, Stripe) with a gallery at `/themes` and a quick-switch dropdown in the header
> - **Session detail view** — click any session row to see its full turn history, tool usage, and working directories
> - **Period delta badges** — each stat card shows +N%/−N% vs the prior equivalent window
> - **Pareto cost card** — shows what share of total spend your top 5 sessions account for
> - **Search** — filter the sessions table by project, branch, or model as you type
> - **In-dashboard rescan** — refresh data without restarting the server
> - **Filter state persistence** — selected date range and model filters survive page reloads
> - **Non-billable model callout** — the Est. Cost card names any models excluded from calculations
> - **Mobile-responsive layout** — CSS grid adapts cleanly to narrow viewports
>
> **Data and tracking**
> - **Claude Desktop Cowork sessions** — audit logs from the desktop app are scanned and displayed alongside CLI sessions
> - **Two cache tiers** — 5-minute (1.25×) and 1-hour (2.0×) prompt-cache pricing tracked separately
> - **Streaming deduplication** — resumed or interrupted sessions don't produce duplicate turn records
>
> **Ops**
> - `GET /api/health` — lightweight endpoint for Docker health checks and monitoring probes

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

### Docker

```bash
docker compose up -d
# Open http://localhost:8080
```

Mounts `~/.claude` into the container, so the SQLite DB at `~/.claude/usage.db`
persists on the host and CLI commands (`python3 cli.py today`) keep working
alongside the container.

To run single-shot CLI commands without starting the server:

```bash
docker compose run --rm claude-usage python3 cli.py today
docker compose run --rm claude-usage python3 cli.py stats
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

## Files

| File | Purpose |
|------|---------|
| `scanner.py` | Parses JSONL transcripts, writes to `~/.claude/usage.db` |
| `dashboard.py` | HTTP server + single-page HTML/JS dashboard |
| `cli.py` | `scan`, `today`, `stats`, `dashboard`, `theme` commands |
