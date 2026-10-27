/**
 * Unit tests for the pure helpers — pricing math and USD formatting.
 *
 * The full `extension.ts` module imports `vscode`, which only exists inside
 * the editor; testing activation/deactivation would require
 * `@vscode/test-electron` (slow, downloads a VS Code build). For CI we keep
 * the assertions on the deterministic pieces.
 */
import { describe, expect, it } from "vitest";
import {
	calcCost,
	getPricing,
	monthPrefixUtc,
	sumCost,
	todayUtc,
	DailyRow,
} from "../pricing";
import { formatUsd } from "../extension";

describe("pricing", () => {
	it("knows opus-4-7 pricing exactly", () => {
		const p = getPricing("claude-opus-4-7");
		expect(p).not.toBeNull();
		expect(p!.input).toBe(5);
		expect(p!.output).toBe(25);
	});

	it("falls back to family keyword for unknown variants", () => {
		expect(getPricing("claude-sonnet-9000-experimental")?.input).toBe(3);
		expect(getPricing("some-haiku-fork")?.output).toBe(5);
	});

	it("returns null for non-Anthropic models", () => {
		expect(getPricing("gpt-4o")).toBeNull();
		expect(getPricing(undefined)).toBeNull();
		expect(getPricing("")).toBeNull();
	});

	it("calcCost matches hand-computed value", () => {
		// 1M input × $5 + 0.5M output × $25 = 5 + 12.5 = 17.5
		const cost = calcCost("claude-opus-4-7", {
			input: 1_000_000,
			output: 500_000,
			cache_read: 0,
			cache_creation: 0,
		});
		expect(cost).toBeCloseTo(17.5, 6);
	});

	it("calcCost returns 0 for unknown model", () => {
		expect(
			calcCost("gpt-4o", {
				input: 1_000_000,
				output: 1_000_000,
				cache_read: 0,
				cache_creation: 0,
			}),
		).toBe(0);
	});
});

describe("sumCost", () => {
	const rows: DailyRow[] = [
		{ day: "2026-05-23", model: "claude-opus-4-7",   input: 1_000_000, output: 0, cache_read: 0, cache_creation: 0 },
		{ day: "2026-05-22", model: "claude-sonnet-4-6", input: 1_000_000, output: 0, cache_read: 0, cache_creation: 0 },
		{ day: "2026-04-30", model: "claude-haiku-4-5",  input: 1_000_000, output: 0, cache_read: 0, cache_creation: 0 },
	];

	it("filters today only", () => {
		const today = sumCost(rows, (d) => d === "2026-05-23");
		expect(today).toBeCloseTo(5, 6);
	});

	it("filters by month prefix", () => {
		const may = sumCost(rows, (d) => d.startsWith("2026-05"));
		expect(may).toBeCloseTo(8, 6); // 5 (opus) + 3 (sonnet)
	});
});

describe("date helpers", () => {
	it("todayUtc yields YYYY-MM-DD", () => {
		const s = todayUtc(new Date("2026-05-23T15:00:00Z"));
		expect(s).toBe("2026-05-23");
	});

	it("monthPrefixUtc yields YYYY-MM", () => {
		const s = monthPrefixUtc(new Date("2026-05-23T15:00:00Z"));
		expect(s).toBe("2026-05");
	});
});

describe("formatUsd", () => {
	it("formats with 2 decimals", () => {
		expect(formatUsd(4.321)).toBe("$4.32");
		expect(formatUsd(0)).toBe("$0.00");
		expect(formatUsd(128)).toBe("$128.00");
	});

	it("clamps NaN and negatives to $0.00", () => {
		expect(formatUsd(NaN)).toBe("$0.00");
		expect(formatUsd(-1)).toBe("$0.00");
	});
});
