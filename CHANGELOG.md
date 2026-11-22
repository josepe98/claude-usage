# Changelog

## v1.8.1 — 2026-11-22

- Fix 1-hour cache writes being left out of most cost figures: the dashboard's Est. Cost card, Cost by Model table, session, project and branch costs, the Pareto card and the plan card, and `cli.py today` / `week` / `stats`. Claude Code writes mostly to the 1-hour cache, so these figures were understated by roughly 45%. The session detail view and `cli.py timeline` already counted them.

## v1.8.0 — 2026-11-20

**Pricing**
- Price Fable 5 / 5.1 / 5.2 and Mythos 5 / 5.1 / 5.2, which were costed at $0, shown as "n/a" and left out of the default model filter; add Opus 4.8 / 5 / 5.5, Sonnet 5 / 5.5 and Haiku 5.5 at the November 2026 rates (Opus 5.5 and Haiku 5.5 were costed at older, higher 4.x rates) (#75)
- Price lookup tries the longest model-ID prefix first, and unknown family names fall back to the newest model of that family (#75)
- A model counts as billable when it has a price in `pricing.py`; Fable and Mythos sessions rank above Opus when choosing a session's primary model (#75)

**Dashboard**
- Recent Sessions hides scheduled-task runs (such as the plan-usage sampler) behind a "Scheduled runs (N)" toggle; they still count in every total (#74)
- The server scans for new transcripts automatically, at most once a minute, so new sessions appear without a Rescan (#74)
- Fix the dashboard showing no data when no 5-hour window was active; a failing allowance summary no longer blanks the rest of the page (#72)

**Requirements**
- Python 3.11 or later is now required; CI tests 3.11 and 3.12 (#73)

## v1.7.1 — 2026-11-08

- Add weekly plan-usage history tracking: `ratelimits.py` logs the highest usage reading within each weekly window, per model (Fable budgets), and extra-usage spend if applicable
- Add `cli.py weekly` command to show per-week totals and plan limits in the terminal
- Update dashboard Files table to document `ratelimits.py`

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
