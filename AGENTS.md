# AGENTS.md

Guidance for any coding agent (Codex, Claude Code, etc.) working on this repository.

> **Naming note.** This project *analyzes* Claude Code's local usage logs, so "Claude Code" below always refers to that product (the source of the JSONL data) — not to the agent reading this file. The agent working on the codebase is referred to as "the coding agent" or just "you".

## Project shape

Five Python files, stdlib only, no `pip install` step. Python 3.8+.

- [scanner.py](scanner.py) — parses Claude Code JSONL transcripts into a SQLite DB at `~/.claude/usage.db`.
- [cli.py](cli.py) — terminal commands (`scan` / `today` / `week` / `stats` / `dashboard` / `theme`).
- [dashboard.py](dashboard.py) — single-file `http.server` serving an embedded HTML/JS SPA on `localhost:8080`.
- [pricing.py](pricing.py) — single source of truth for model pricing (Python + JS both import/inline from here). Also contains `PRICING_HISTORY` for time-keyed historic rates and `get_pricing_at()` / `calc_cost_at()` for accurate costing of old sessions.
- [cowork.py](cowork.py) — scans Claude Desktop Cowork audit logs and merges them into the same SQLite DB.

Use `python` on Windows, `python3` on macOS/Linux. Both work the same.

## Common commands

```
python3 cli.py scan                  # incremental scan (fast on re-run)
python3 cli.py today                 # today's usage by model
python3 cli.py week                  # last 7 days, per-day + by-model
python3 cli.py stats                 # all-time stats
python3 cli.py dashboard             # scan + open http://localhost:8080
python3 cli.py scan --projects-dir PATH    # scan a custom transcripts dir
HOST=0.0.0.0 PORT=9000 python3 cli.py dashboard

python3 -m unittest discover -s tests -v             # full test suite (CI runs this)
python3 -m unittest tests.test_scanner -v            # one file
python3 -m unittest tests.test_scanner.TestProjectNameFromCwd.test_windows_path  # one test
```

CI ([.github/workflows/tests.yml](.github/workflows/tests.yml)) runs the suite on Python 3.9 / 3.11 / 3.12 against `main` and PRs.

## Architecture

### Data flow

```
~/.claude/projects/**/*.jsonl   →   scanner.parse_jsonl_file()
~/Library/.../Xcode/...                  ↓
~/.claude/cowork/*/audit.jsonl  →   cowork.scan_cowork()
                              aggregate_sessions() → upsert_sessions() + insert_turns()
                                         ↓
                              ~/.claude/usage.db (SQLite)
                                         ↓
                  cli.py queries   ←──────────→   dashboard.py /api/data
```

By default the scanner walks both `~/.claude/projects/` and the Xcode coding-assistant directory; missing dirs are silently skipped. Override with `--projects-dir`.

### SQLite schema (created/migrated in [scanner.py](scanner.py) `init_db`)

- **`turns`** — one row per assistant API response. The source of truth for tokens and per-model attribution.
- **`sessions`** — aggregated per session (denormalized totals + chosen primary model).
- **`processed_files`** — incremental-scan tracking: `(path, mtime, lines)`. A file is skipped if its mtime matches; if it grew, only lines past the stored `lines` count are processed.

A conditional unique index on `turns.message_id` (where non-empty) lets `INSERT OR IGNORE` cheaply dedupe replays across rescans.

### Non-obvious invariants

These three things will bite you if you don't know them:

1. **Streaming dedupe by `message.id`.** Claude Code writes multiple JSONL records per API response — only the *last* one for a given `message.id` has the final usage tallies. `parse_jsonl_file` keeps the last record per `message_id` in a dict; earlier records are discarded. Don't sum across records of the same `message_id`.

2. **Session totals are recomputed from `turns` at the end of `scan()`.** During an incremental scan `upsert_sessions` adds tokens additively, but `insert_turns` uses `INSERT OR IGNORE` against the `message_id` unique index — so if a turn is a duplicate, session totals would drift. The final `UPDATE sessions ... (SELECT SUM ... FROM turns)` block reconciles this. Preserve it if you refactor scan logic.

3. **Session primary model priority is opus > sonnet > haiku** (`_model_priority` in [scanner.py](scanner.py)). This prevents a subagent's haiku turn from overwriting the session's opus model when an existing session is updated. Per-turn model is always honored in the `turns` table; only the session-level summary uses the priority.

### Cost calculation

Costs are computed **per turn** (each turn knows its own model), then summed. This is true in both the CLI ([cli.py](cli.py) `calc_cost`) and the dashboard JS ([dashboard.py](dashboard.py) `calcCost` inside the embedded HTML). Aggregating tokens first and applying a single price is wrong for sessions that span multiple models.

**[pricing.py](pricing.py) is the single source of truth.** The Python CLI imports from it directly; the dashboard inlines the `PRICING` dict into the embedded JS at server startup. Do not maintain a separate pricing table in `cli.py` or `dashboard.py` — add new models and rate changes to `pricing.py` only.

`get_pricing` / `getPricing` resolve in three tiers: exact match → `startswith` (handles date-suffixed model IDs like `claude-opus-4-7-20260215`) → substring fallback on `opus` / `sonnet` / `haiku`. Models that don't match any tier return `None` and are billed at $0 (shown as `n/a`).

When Anthropic changes a rate, add a new entry to `PRICING_HISTORY` (newest-first, partial dicts allowed) *in addition to* updating `PRICING`. This lets `calc_cost_at()` correctly cost old sessions at the rates that were in effect when they ran.

### Dashboard server

`ThreadingHTTPServer`-based (so slow `/api/data` calls don't block other requests), with a `urlparse`-based router so bookmarkable `?query=string` URLs don't 404. Endpoints:

- `GET /` — serves the embedded SPA (`HTML_TEMPLATE`)
- `GET /api/data` — full JSON snapshot from `get_dashboard_data()`. All history; client filters by date range and model.
- `GET /api/health` — liveness check, returns DB path and row counts.
- `GET /api/session` — per-session detail from `get_session_detail()`.
- `GET /api/session-detail` — extended drill-down payload (timeline, cumulative cost, tools breakdown) used by the session modal.
- `POST /api/rescan` — deletes the DB and runs a full rescan.
- `GET /manifest.json`, `/sw.js`, `/icon.svg` — PWA assets (installable dashboard).

The entire UI lives in `HTML_TEMPLATE` as a raw string. Chart.js is loaded from CDN.

## Testing notes

- `tests/test_scanner.py` and `tests/test_dashboard.py` use `tempfile.NamedTemporaryFile` for an isolated DB; never touch the user's real `~/.claude/usage.db`.
- The `/api/rescan` test patches `dashboard.DB_PATH` and `scanner.DEFAULT_PROJECTS_DIRS` — keep that contract intact.
- On Windows, `~/.claude/` may not exist on a fresh checkout. `get_db` creates the parent dir (`mkdir(parents=True, exist_ok=True)`) — don't remove that or `sqlite3.connect` will fail in CI / fresh installs.

## Respecting contributors

When merging community PRs, **preserve the original author's commit so they get GitHub contributor credit**. In practice:

- `git fetch origin pull/<N>/head:pr-<N>` → `git merge --no-ff pr-<N>` keeps the author commit verbatim inside the merge bubble (don't squash, don't rebase-flatten).
- For a partial merge — when only one hunk of a PR is wanted — use `git cherry-pick <commit-sha>` against the specific upstream commit so authorship is preserved. If the diff isn't a clean single commit, fall back to applying the hunk manually + adding a `Co-Authored-By: Name <email>` trailer.
- Improvements that the bot/maintainer makes _on top_ of a contributor's work go in **separate follow-up commits**, not amendments to the contributor's commit.
- When closing duplicate PRs (multiple authors fixed the same bug independently), thank each one and explain that landing the earliest version isn't a quality judgment.

## Versioning and releases

This project follows [SemVer](https://semver.org/):

1. **Tags always.** Every version that lands on `main` gets an annotated git tag (`git tag -a vX.Y.Z -m "vX.Y.Z"`). Tags pay off in three places: Homebrew formula pinning, `git log vX.Y.Z-1..vX.Y.Z` for changelog work, and `gh release create` if a release needs promoting retroactively.

2. **Formal GitHub Releases only for major versions.** Patch and minor bumps ship as a tag and a CHANGELOG entry only. Formal `gh release create` with release notes is reserved for major versions where breaking changes warrant a notification to watchers.

### Homebrew formula and self-referential SHA

The Homebrew formula at `Formula/claude-usage.rb` lives inside this same repo. When bumping it: the formula's `url` must point at the **previous** release's tarball, never its own. In v1.1.1 the formula points at v1.1.0's commit-SHA tarball — that's the trade-off of keeping the formula in-tree.
