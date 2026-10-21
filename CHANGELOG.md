# Changelog

## Unreleased

- Add session name support: the dashboard now shows the human-readable name set via Claude Code's `/rename` (from `customTitle` / `agentName` JSONL records), alongside the session ID, and exports it to CSV (#56)

## v1.7.0 — 2026-10-21

- Add plan-allowance tracking for Claude.ai Pro / Max subscribers: `ratelimits.py` runs as Claude Code's status line, shows `5h N% · wk N%`, and records the server-reported 5-hour and weekly utilization to `~/.claude/claude-usage/rate_limits.jsonl`
- Add Plan Allowance card to the dashboard: per-window gauges, reset countdowns, a warning when the current pace would exhaust a window before it resets, and a 7-day history chart
- `cli.py today` now opens with 5-hour / weekly usage and reset times
- New `rate_limit_snapshots` table, filled by `scan`, `cli.py today` and `/api/data`

## 2026-04-09

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
