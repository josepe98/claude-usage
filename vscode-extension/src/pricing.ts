/**
 * Mirror of the project's `pricing.py` table — USD per million tokens.
 * Kept in lock-step manually; if the Python table grows a new model family,
 * add the same entry here.
 */
export interface ModelPricing {
	input: number;
	output: number;
	cache_read: number;
	cache_write_5m: number;
	cache_write_1h: number;
}

export const PRICING: Record<string, ModelPricing> = {
	"claude-opus-4-7":   { input: 5.00, output: 25.00, cache_read: 0.50, cache_write_5m: 6.25, cache_write_1h: 10.0 },
	"claude-opus-4-6":   { input: 5.00, output: 25.00, cache_read: 0.50, cache_write_5m: 6.25, cache_write_1h: 10.0 },
	"claude-opus-4-5":   { input: 5.00, output: 25.00, cache_read: 0.50, cache_write_5m: 6.25, cache_write_1h: 10.0 },
	"claude-sonnet-4-7": { input: 3.00, output: 15.00, cache_read: 0.30, cache_write_5m: 3.75, cache_write_1h: 6.0 },
	"claude-sonnet-4-6": { input: 3.00, output: 15.00, cache_read: 0.30, cache_write_5m: 3.75, cache_write_1h: 6.0 },
	"claude-sonnet-4-5": { input: 3.00, output: 15.00, cache_read: 0.30, cache_write_5m: 3.75, cache_write_1h: 6.0 },
	"claude-haiku-4-7":  { input: 1.00, output:  5.00, cache_read: 0.10, cache_write_5m: 1.25, cache_write_1h: 2.0 },
	"claude-haiku-4-6":  { input: 1.00, output:  5.00, cache_read: 0.10, cache_write_5m: 1.25, cache_write_1h: 2.0 },
	"claude-haiku-4-5":  { input: 1.00, output:  5.00, cache_read: 0.10, cache_write_5m: 1.25, cache_write_1h: 2.0 },
};

export function getPricing(model: string | null | undefined): ModelPricing | null {
	if (!model) return null;
	if (PRICING[model]) return PRICING[model];
	for (const key of Object.keys(PRICING)) {
		if (model.startsWith(key)) return PRICING[key];
	}
	const m = model.toLowerCase();
	if (m.includes("opus"))   return PRICING["claude-opus-4-7"];
	if (m.includes("sonnet")) return PRICING["claude-sonnet-4-6"];
	if (m.includes("haiku"))  return PRICING["claude-haiku-4-5"];
	return null;
}

export interface DailyRow {
	day: string;            // "YYYY-MM-DD"
	model: string;
	input: number;
	output: number;
	cache_read: number;
	cache_creation: number;
	cache_1h?: number;
	turns?: number;
}

export function calcCost(model: string, row: Omit<DailyRow, "day" | "model">): number {
	const p = getPricing(model);
	if (!p) return 0;
	return (
		(row.input          || 0) * p.input          / 1_000_000 +
		(row.output         || 0) * p.output         / 1_000_000 +
		(row.cache_read     || 0) * p.cache_read     / 1_000_000 +
		(row.cache_creation || 0) * p.cache_write_5m / 1_000_000 +
		(row.cache_1h       || 0) * p.cache_write_1h / 1_000_000
	);
}

/**
 * Sum cost across `daily_by_model` rows whose `day` matches the predicate.
 * Days come from the dashboard as UTC `YYYY-MM-DD` strings — we compare them
 * as strings to stay timezone-stable with what the dashboard shows.
 */
export function sumCost(rows: DailyRow[], dayMatches: (day: string) => boolean): number {
	let total = 0;
	for (const r of rows) {
		if (dayMatches(r.day)) total += calcCost(r.model, r);
	}
	return total;
}

export function todayUtc(now: Date = new Date()): string {
	return now.toISOString().slice(0, 10);
}

export function monthPrefixUtc(now: Date = new Date()): string {
	return now.toISOString().slice(0, 7); // "YYYY-MM"
}
