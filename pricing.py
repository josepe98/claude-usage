"""
Anthropic API pricing — single source of truth for both the Python CLI cost
calculator and the JavaScript dashboard.

USD per million tokens. Updated November 2026.
Source: https://platform.claude.com/docs/en/about-claude/pricing

Cache reads aren't always 0.1x input: Fable/Mythos 5.1 and 5.2 are 0.025x and
Opus 5.5 / Sonnet 5.5 are 0.05x, so every rate is listed explicitly.
Haiku 5.5 is listed at its prompts-up-to-100K rates; longer prompts cost 5x.
"""

PRICING = {
    "claude-fable-5-2":  {"input": 8.00,  "output": 40.00, "cache_read": 0.20, "cache_write": 10.0,  "cache_write_5m": 10.0,  "cache_write_1h": 16.0},
    "claude-fable-5-1":  {"input": 10.00, "output": 50.00, "cache_read": 0.25, "cache_write": 12.50, "cache_write_5m": 12.50, "cache_write_1h": 20.0},
    "claude-fable-5":    {"input": 10.00, "output": 50.00, "cache_read": 1.00, "cache_write": 12.50, "cache_write_5m": 12.50, "cache_write_1h": 20.0},
    "claude-mythos-5-2": {"input": 8.00,  "output": 40.00, "cache_read": 0.20, "cache_write": 10.0,  "cache_write_5m": 10.0,  "cache_write_1h": 16.0},
    "claude-mythos-5-1": {"input": 10.00, "output": 50.00, "cache_read": 0.25, "cache_write": 12.50, "cache_write_5m": 12.50, "cache_write_1h": 20.0},
    "claude-mythos-5":   {"input": 10.00, "output": 50.00, "cache_read": 1.00, "cache_write": 12.50, "cache_write_5m": 12.50, "cache_write_1h": 20.0},
    "claude-opus-5-5":   {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.0,  "cache_write_5m": 5.0,  "cache_write_1h": 8.0},
    "claude-opus-5":     {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25, "cache_write_5m": 6.25, "cache_write_1h": 10.0},
    "claude-opus-4-8":   {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25, "cache_write_5m": 6.25, "cache_write_1h": 10.0},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.10, "cache_write": 2.50, "cache_write_5m": 2.50, "cache_write_1h": 4.0},
    "claude-sonnet-5":   {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50, "cache_write_5m": 2.50, "cache_write_1h": 4.0},
    "claude-haiku-5-5":  {"input": 0.10, "output":  0.50, "cache_read": 0.01, "cache_write": 0.125, "cache_write_5m": 0.125, "cache_write_1h": 0.20},
    "claude-opus-4-7":   {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25, "cache_write_5m": 6.25, "cache_write_1h": 10.0},
    "claude-opus-4-6":   {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25, "cache_write_5m": 6.25, "cache_write_1h": 10.0},
    "claude-opus-4-5":   {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25, "cache_write_5m": 6.25, "cache_write_1h": 10.0},
    "claude-sonnet-4-7": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75, "cache_write_5m": 3.75, "cache_write_1h": 6.0},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75, "cache_write_5m": 3.75, "cache_write_1h": 6.0},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75, "cache_write_5m": 3.75, "cache_write_1h": 6.0},
    "claude-haiku-4-7":  {"input": 1.00, "output":  5.00, "cache_read": 0.10, "cache_write": 1.25, "cache_write_5m": 1.25, "cache_write_1h": 2.0},
    "claude-haiku-4-6":  {"input": 1.00, "output":  5.00, "cache_read": 0.10, "cache_write": 1.25, "cache_write_5m": 1.25, "cache_write_1h": 2.0},
    "claude-haiku-4-5":  {"input": 1.00, "output":  5.00, "cache_read": 0.10, "cache_write": 1.25, "cache_write_5m": 1.25, "cache_write_1h": 2.0},
}


# Keyword fallback for names no key matches: newest model of each family.
FAMILY_FALLBACK = [
    ("fable", "claude-fable-5-2"),
    ("mythos", "claude-mythos-5-2"),
    ("opus", "claude-opus-5-5"),
    ("sonnet", "claude-sonnet-5-5"),
    ("haiku", "claude-haiku-5-5"),
]


def get_pricing(model):
    """Look up per-MTok pricing for a model name.

    Tries exact match, then prefix match (longest key first, so
    `claude-opus-5-5-<date>` doesn't match `claude-opus-5`), then keyword
    fallback (any name containing 'fable'/'mythos'/'opus'/'sonnet'/'haiku'
    falls back to the latest of that family).
    Returns None for unknown / non-Anthropic models so callers can show 'n/a'.
    """
    if not model:
        return None
    if model in PRICING:
        return PRICING[model]
    for key in sorted(PRICING, key=len, reverse=True):
        if model.startswith(key):
            return PRICING[key]
    m = model.lower()
    for keyword, latest in FAMILY_FALLBACK:
        if keyword in m:
            return PRICING[latest]
    return None


def calc_cost(model, inp, out, cache_read, cache_creation, cache_1h=0):
    """Cost in USD for one batch of token usage. Returns 0 for unknown models.

    `cache_creation` is the 5-minute ephemeral cache write count; `cache_1h`
    is the 1-hour tier, billed at 2.0x base input (vs 1.25x for 5m).
    The 1h parameter is kwarg-only (positional default 0) so existing
    callers stay source-compatible.
    """
    p = get_pricing(model)
    if p is None:
        return 0.0
    return (
        (inp or 0)            * p["input"]          / 1_000_000
        + (out or 0)          * p["output"]         / 1_000_000
        + (cache_read or 0)   * p["cache_read"]     / 1_000_000
        + (cache_creation or 0) * p["cache_write_5m"] / 1_000_000
        + (cache_1h or 0)     * p["cache_write_1h"] / 1_000_000
    )


# ── Time-keyed pricing history ────────────────────────────────────────────
# Newest first. When Anthropic changes a rate, add a new entry on top with the
# effective date; older entries stay valid for rows whose timestamp predates
# the change.
#
# Each entry's `pricing` may be a *partial* dict (only the models that changed).
# The lookup falls back to the next-most-recent entry, then to the current
# PRICING above, then to the keyword-fallback rules in get_pricing().

PRICING_HISTORY = [
    # Example: {"effective": "2026-04-01", "pricing": {"claude-opus-4-7": {...}}},
    # Today's PRICING is the implicit head — no need to duplicate.
]


def get_pricing_at(model, ts):
    """Look up rates for `model` that were in effect at timestamp `ts`.
    `ts` may be a string (ISO 8601), date, or datetime. Falls back to the
    current PRICING when the timestamp is on or after the latest known
    effective date, or when no history is configured."""
    if not ts or not PRICING_HISTORY:
        return get_pricing(model)
    # Normalise to a YYYY-MM-DD string for comparison; sort order is lexicographic.
    if hasattr(ts, "strftime"):
        day = ts.strftime("%Y-%m-%d")
    else:
        day = str(ts)[:10]
    # PRICING_HISTORY is newest-first; iterate to find the first entry
    # whose effective date <= the row's day. Skip entries with no relevant
    # model.
    for entry in PRICING_HISTORY:
        if day >= entry["effective"]:
            p = entry["pricing"].get(model)
            if p is not None:
                return p
            # Model not in this snapshot — keep walking the history.
    return get_pricing(model)


def calc_cost_at(model, inp, out, cache_read, cache_creation, ts):
    """Like calc_cost(), but uses pricing in effect at `ts`."""
    p = get_pricing_at(model, ts)
    if p is None:
        return 0.0
    return (
        (inp or 0)            * p["input"]       / 1_000_000
        + (out or 0)          * p["output"]      / 1_000_000
        + (cache_read or 0)   * p["cache_read"]  / 1_000_000
        + (cache_creation or 0) * p["cache_write"] / 1_000_000
    )
