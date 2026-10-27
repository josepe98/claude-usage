import * as vscode from "vscode";
import * as http from "http";
import * as https from "https";
import { URL } from "url";
import {
	DailyRow,
	monthPrefixUtc,
	sumCost,
	todayUtc,
} from "./pricing";

const CFG_SECTION = "claudeUsage";
const CMD_OPEN = "claudeUsage.openDashboard";
const CMD_REFRESH = "claudeUsage.refresh";

interface HealthResponse {
	status: "ok" | "no-db" | "error";
	sessions?: number;
	turns?: number;
	error?: string;
}

interface DataResponse {
	daily_by_model?: DailyRow[];
	error?: string;
}

let statusBarItem: vscode.StatusBarItem | undefined;
let pollTimer: NodeJS.Timeout | undefined;
let noDbWarned = false;

export function activate(context: vscode.ExtensionContext): void {
	statusBarItem = vscode.window.createStatusBarItem(
		vscode.StatusBarAlignment.Right,
		100,
	);
	statusBarItem.command = CMD_OPEN;
	statusBarItem.text = "$(graph-line) Claude Usage …";
	statusBarItem.tooltip = "Loading Claude usage…";
	statusBarItem.show();
	context.subscriptions.push(statusBarItem);

	context.subscriptions.push(
		vscode.commands.registerCommand(CMD_OPEN, () => {
			const url = getDashboardUrl();
			void vscode.env.openExternal(vscode.Uri.parse(url));
		}),
	);

	context.subscriptions.push(
		vscode.commands.registerCommand(CMD_REFRESH, () => {
			void poll();
		}),
	);

	context.subscriptions.push(
		vscode.workspace.onDidChangeConfiguration((e) => {
			if (e.affectsConfiguration(CFG_SECTION)) {
				schedulePolling();
				void poll();
			}
		}),
	);

	context.subscriptions.push({
		dispose: () => {
			if (pollTimer) {
				clearInterval(pollTimer);
				pollTimer = undefined;
			}
		},
	});

	schedulePolling();
	void poll();
}

export function deactivate(): void {
	if (pollTimer) {
		clearInterval(pollTimer);
		pollTimer = undefined;
	}
	if (statusBarItem) {
		statusBarItem.dispose();
		statusBarItem = undefined;
	}
}

function getDashboardUrl(): string {
	const cfg = vscode.workspace.getConfiguration(CFG_SECTION);
	const raw = (cfg.get<string>("dashboardUrl") || "http://localhost:8080").trim();
	return raw.replace(/\/+$/, "");
}

function getRefreshSeconds(): number {
	const cfg = vscode.workspace.getConfiguration(CFG_SECTION);
	const v = cfg.get<number>("refreshSeconds");
	if (typeof v !== "number" || !isFinite(v) || v < 5) return 30;
	return Math.floor(v);
}

function schedulePolling(): void {
	if (pollTimer) {
		clearInterval(pollTimer);
		pollTimer = undefined;
	}
	const seconds = getRefreshSeconds();
	pollTimer = setInterval(() => {
		void poll();
	}, seconds * 1000);
}

async function poll(): Promise<void> {
	const base = getDashboardUrl();
	try {
		const health = await fetchJson<HealthResponse>(`${base}/api/health`);
		if (health.status === "no-db") {
			renderNoDb();
			if (!noDbWarned) {
				noDbWarned = true;
				void vscode.window.showWarningMessage(
					"Claude Usage: dashboard has no database yet. Run `python3 cli.py scan` to populate it.",
				);
			}
			return;
		}
		if (health.status === "error") {
			renderError(health.error || "dashboard returned error");
			return;
		}
		// healthy → re-arm the warning so a future no-db will notify again.
		noDbWarned = false;

		const data = await fetchJson<DataResponse>(`${base}/api/data`);
		if (data.error || !Array.isArray(data.daily_by_model)) {
			renderError(data.error || "missing daily_by_model");
			return;
		}
		renderTotals(data.daily_by_model);
	} catch (err: unknown) {
		const msg = err instanceof Error ? err.message : String(err);
		renderError(msg);
	}
}

function renderTotals(rows: DailyRow[]): void {
	if (!statusBarItem) return;
	const today = todayUtc();
	const month = monthPrefixUtc();
	const todayCost = sumCost(rows, (d) => d === today);
	const monthCost = sumCost(rows, (d) => d.startsWith(month));
	statusBarItem.text = `$(graph-line) ${formatUsd(todayCost)} today / ${formatUsd(monthCost)} this month`;
	statusBarItem.tooltip = new vscode.MarkdownString(
		`**Claude Usage**\n\n` +
		`- Today (${today}): **${formatUsd(todayCost)}**\n` +
		`- Month-to-date (${month}): **${formatUsd(monthCost)}**\n\n` +
		`Click to open the dashboard.`,
	);
	statusBarItem.backgroundColor = undefined;
}

function renderNoDb(): void {
	if (!statusBarItem) return;
	statusBarItem.text = "$(database) Claude Usage: no DB";
	statusBarItem.tooltip = "Dashboard reports no database. Run `python3 cli.py scan`.";
	statusBarItem.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
}

function renderError(message: string): void {
	if (!statusBarItem) return;
	statusBarItem.text = "$(warning) Claude Usage: offline";
	statusBarItem.tooltip = `Could not reach dashboard: ${message}`;
	statusBarItem.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
}

export function formatUsd(n: number): string {
	if (!isFinite(n) || n < 0) n = 0;
	return `$${n.toFixed(2)}`;
}

export function fetchJson<T>(url: string, timeoutMs = 5000): Promise<T> {
	return new Promise((resolve, reject) => {
		let u: URL;
		try {
			u = new URL(url);
		} catch (e) {
			reject(new Error(`bad URL: ${url}`));
			return;
		}
		const lib = u.protocol === "https:" ? https : http;
		const req = lib.get(url, (res) => {
			const chunks: Buffer[] = [];
			res.on("data", (c: Buffer) => chunks.push(c));
			res.on("end", () => {
				const body = Buffer.concat(chunks).toString("utf-8");
				if (res.statusCode && res.statusCode >= 400) {
					reject(new Error(`HTTP ${res.statusCode}: ${body.slice(0, 200)}`));
					return;
				}
				try {
					resolve(JSON.parse(body) as T);
				} catch (e) {
					reject(new Error(`invalid JSON: ${(e as Error).message}`));
				}
			});
		});
		req.on("error", reject);
		req.setTimeout(timeoutMs, () => {
			req.destroy(new Error(`timeout after ${timeoutMs}ms`));
		});
	});
}
