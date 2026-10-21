# Changelog

## v1.7.0 — 2026-10-21

- Add plan-allowance tracking for Claude.ai Pro / Max subscribers: `ratelimits.py` runs as Claude Code's status line, shows `5h N% · wk N%`, and records the server-reported 5-hour and weekly utilization to `~/.claude/claude-usage/rate_limits.jsonl`
- Add Plan Allowance card to the dashboard: per-window gauges, reset countdowns, a warning when the current pace would exhaust a window before it resets, and a 7-day history chart
- `cli.py today` now opens with 5-hour / weekly usage and reset times
- New `rate_limit_snapshots` table, filled by `scan`, `cli.py today` and `/api/data`

## v1.6.1 — 2026-08-30

- Fix Docker image missing `cowork.py` and `pricing.py`
- Docs: pricing table dated August 2026, note model variants in `pricing.py`, custom date-range picker in the feature list

## v1.6.0 — 2026-06-09

First release of the josepe98/claude-usage fork, branched from phuryn/claude-usage on 2026-04-24. Contributions from Jakub Duchek, Hayden Haines and Kelvis Artigas.

**Dashboard**
- Custom date-range picker with presets, plus a Today range button
- Bundled themes (Apple, Linear, Vercel, Notion, Stripe) with a `/themes` gallery and header quick-switch
- Session detail modal: turn history, tool usage, working directories (`/api/session-detail`)
- Session names from `/rename` (`customTitle` / `agentName`) shown alongside the session ID and in CSV export (#56)
- Per-session tags (`~/.claude/tags.json`), sessions search, and inline turn-rate sparklines
- Day-of-week × hour heatmap with LOCAL/UTC toggle; header streak counter
- Period delta badges on stat cards, Pareto cost-concentration card, plan-tier comparison card
- Short model labels sorted by first-seen date; non-billable models noted under Est. Cost
- Markdown copy buttons on tables, keyboard shortcuts (`/`, `r`, `t`, `?`), localStorage prefs
- Two-row filter bar, git hash in footer, mobile layout under 640px
- Installable as a PWA; Reset DB button and `/api/reset`; `/api/health` endpoint

**Data and cost**
- Claude Desktop Cowork sessions scanned from local audit logs
- 5-minute and 1-hour cache tiers priced separately
- Time-keyed pricing history so old sessions are costed at the rates in effect then
- `pricing.py` is the single source of truth for Python and JS

**CLI**
- `timeline <session_id>` writes a markdown session log

**Fixes**
- Streaming dedupe, concurrent requests no longer blocked (`ThreadingHTTPServer`), bookmarkable `?query` URLs no longer 404
- Blank dashboard when no billable model names exist; hourly filter uses range bounds
- Streak anchored to local date; heatmap honours LOCAL/UTC

**Infrastructure**
- Docker support with compose
- Playwright browser smoke tests in CI; CI runs on fork PRs
- Chart.js CDN pinned with Subresource Integrity

## 2026-04-09 (upstream, before the fork)

- Fix token counts inflated ~2x by deduplicating streaming events that share the same message ID
- Fix session cost totals that were inflated when sessions spanned multiple JSONL files
- Fix pricing to match current Anthropic API rates (Opus $5/$25, Sonnet $3/$15, Haiku $1/$5)
- Add CI test suite (84 tests) and GitHub Actions workflow running on every PR
- Add sortable columns to Sessions, Cost by Model, and new Cost by Project tables
- Add CSV export for Sessions and Projects (all filtered data, not just top 20)
- Add Rescan button to dashboard for full database rebuild
- Add Xcode project directory support and `--projects-dir` CLI option
- Non-Anthropic models (gemma, glm, etc.) no longer incorrectly charged at Sonnet rates
- CLI and dashboard now both compute costs per-turn for consistent results
