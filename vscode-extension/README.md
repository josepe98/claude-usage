# Claude Usage (VS Code extension)

Show live Claude Code spend in the VS Code status bar, polled from your local
[claude-usage](https://github.com/josepe98/claude-usage) dashboard.

```
$(graph-line) $4.32 today / $128 this month
```

Click the status bar item to open the full dashboard in your browser.

## Requirements

The dashboard must be running locally (default `http://localhost:8080`):

```bash
python3 cli.py serve
```

## Settings

| Setting                       | Default                 | Description                                |
| ----------------------------- | ----------------------- | ------------------------------------------ |
| `claudeUsage.dashboardUrl`    | `http://localhost:8080` | Base URL of the dashboard (no trailing /). |
| `claudeUsage.refreshSeconds`  | `30`                    | Polling interval for `/api/data`.          |

## Install

```bash
cd vscode-extension
npm i
npm run build
npx vsce package
code --install-extension claude-usage-*.vsix
```

Or, for ad-hoc development, open this folder in VS Code and press F5 to
launch an Extension Development Host.

## How it works

- Polls `GET /api/health` first. If it returns `status: "no-db"`, a one-shot
  notification banner asks you to run `python3 cli.py scan`.
- Polls `GET /api/data`, sums `daily_by_model` rows with the same pricing
  table the dashboard uses, and renders `today` (UTC day) + `this month`
  (UTC month) totals.

## Commands

- **Claude Usage: Open Dashboard** — open `dashboardUrl` in your browser.
- **Claude Usage: Refresh Now** — force a poll outside the timer.
