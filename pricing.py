"""
Anthropic API pricing — single source of truth for both the Python CLI cost
calculator and the JavaScript dashboard.

USD per million tokens. Updated April 2026.
Source: https://docs.claude.com/en/docs/about-claude/pricing
"""

PRICING = {
    "claude-opus-4-7":   {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "claude-opus-4-6":   {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "claude-opus-4-5":   {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "claude-sonnet-4-7": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-7":  {"input": 1.00, "output":  5.00, "cache_read": 0.10, "cache_write": 1.25},
    "claude-haiku-4-6":  {"input": 1.00, "output":  5.00, "cache_read": 0.10, "cache_write": 1.25},
    "claude-haiku-4-5":  {"input": 1.00, "output":  5.00, "cache_read": 0.10, "cache_write": 1.25},
}


def get_pricing(model):
    """Look up per-MTok pricing for a model name.

    Tries exact match, then prefix match, then keyword fallback (any name
    containing 'opus'/'sonnet'/'haiku' falls back to the latest of that family).
    Returns None for unknown / non-Anthropic models so callers can show 'n/a'.
    """
    if not model:
        return None
    if model in PRICING:
        return PRICING[model]
    for key in PRICING:
        if model.startswith(key):
            return PRICING[key]
    m = model.lower()
    if "opus" in m:
        return PRICING["claude-opus-4-7"]
    if "sonnet" in m:
        return PRICING["claude-sonnet-4-6"]
    if "haiku" in m:
        return PRICING["claude-haiku-4-5"]
    return None


def calc_cost(model, inp, out, cache_read, cache_creation):
    """Cost in USD for one batch of token usage. Returns 0 for unknown models."""
    p = get_pricing(model)
    if p is None:
        return 0.0
    return (
        (inp or 0)            * p["input"]       / 1_000_000
        + (out or 0)          * p["output"]      / 1_000_000
        + (cache_read or 0)   * p["cache_read"]  / 1_000_000
        + (cache_creation or 0) * p["cache_write"] / 1_000_000
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
