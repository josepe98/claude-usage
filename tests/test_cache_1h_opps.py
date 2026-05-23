"""Tests for the 1-hour cache opportunity detector.

The detector flags sessions where switching from the default 5-minute cache
tier to Anthropic's 1-hour tier would have saved money. A session qualifies
when:

  * duration > 30 minutes
  * total_cache_creation > 100 000 tokens
  * model is a known billable family (opus / sonnet / haiku)

Savings are projected as ~40 % of the current cache-creation cost
(mid-band of the 30-50 % heuristic range).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scanner import get_db, init_db, upsert_sessions
from dashboard import (
    _cache_1h_opportunities,
    get_dashboard_data,
    HTML_TEMPLATE,
    CACHE_1H_MIN_DURATION_MIN,
    CACHE_1H_MIN_CACHE_CREATION,
    CACHE_1H_SAVINGS_FRACTION,
)


def _make_session(session_id, first_ts, last_ts, cache_creation,
                  model="claude-sonnet-4-6"):
    """Build a session dict with sensible defaults for the rest of the columns."""
    return {
        "session_id":           session_id,
        "project_name":         "user/proj",
        "first_timestamp":      first_ts,
        "last_timestamp":       last_ts,
        "git_branch":           "main",
        "model":                model,
        "total_input_tokens":   1000,
        "total_output_tokens":  500,
        "total_cache_read":     0,
        "total_cache_creation": cache_creation,
        "turn_count":           10,
    }


class TestCache1hOpportunities(unittest.TestCase):
    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpfile.close()
        self.db_path = Path(self.tmpfile.name)
        self.conn = get_db(self.db_path)
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    # ── individual filter rules ──────────────────────────────────────────

    def test_short_session_not_flagged(self):
        """Sessions <=30 minutes shouldn't surface even if cache is huge."""
        upsert_sessions(self.conn, [_make_session(
            "short-session-id-0001",
            "2026-04-08T09:00:00Z",
            "2026-04-08T09:15:00Z",   # 15 min
            cache_creation=500_000,
        )])
        self.conn.commit()
        self.assertEqual(_cache_1h_opportunities(self.conn), [])

    def test_low_cache_session_not_flagged(self):
        """Long sessions with modest cache writes shouldn't surface."""
        upsert_sessions(self.conn, [_make_session(
            "low-cache-session-0001",
            "2026-04-08T09:00:00Z",
            "2026-04-08T11:00:00Z",   # 2h
            cache_creation=50_000,    # under threshold
        )])
        self.conn.commit()
        self.assertEqual(_cache_1h_opportunities(self.conn), [])

    def test_long_high_cache_session_flagged(self):
        """The headline case: long + cache-heavy session must surface."""
        upsert_sessions(self.conn, [_make_session(
            "long-heavy-session-0001",
            "2026-04-08T09:00:00Z",
            "2026-04-08T10:30:00Z",   # 90 min
            cache_creation=500_000,
            model="claude-sonnet-4-6",
        )])
        self.conn.commit()

        opps = _cache_1h_opportunities(self.conn)
        self.assertEqual(len(opps), 1)
        o = opps[0]
        self.assertEqual(o["session_id"], "long-hea")  # 8-char truncation
        self.assertEqual(o["duration_min"], 90.0)
        self.assertEqual(o["cache_creation_tokens"], 500_000)

    def test_estimated_savings_positive_and_below_current_cost(self):
        """Savings must be positive and strictly less than current cost."""
        upsert_sessions(self.conn, [_make_session(
            "savings-check-id-00001",
            "2026-04-08T09:00:00Z",
            "2026-04-08T11:00:00Z",   # 120 min
            cache_creation=1_000_000,
            model="claude-opus-4-7",  # cache_write_5m @ $6.25 / 1M
        )])
        self.conn.commit()

        opps = _cache_1h_opportunities(self.conn)
        self.assertEqual(len(opps), 1)
        o = opps[0]
        # current_cost = 1_000_000 * 6.25 / 1e6 = $6.25
        self.assertAlmostEqual(o["current_cost"], 6.25, places=2)
        self.assertGreater(o["estimated_savings_with_1h"], 0)
        self.assertLess(o["estimated_savings_with_1h"], o["current_cost"])
        # Savings within the 30-50 % heuristic band
        ratio = o["estimated_savings_with_1h"] / o["current_cost"]
        self.assertGreaterEqual(ratio, 0.30)
        self.assertLessEqual(ratio, 0.50)
        # And exactly the documented mid-band value
        self.assertAlmostEqual(ratio, CACHE_1H_SAVINGS_FRACTION, places=3)

    def test_unknown_model_is_skipped(self):
        """Sessions on non-billable models shouldn't be flagged (no pricing)."""
        upsert_sessions(self.conn, [_make_session(
            "unknown-model-id-00001",
            "2026-04-08T09:00:00Z",
            "2026-04-08T10:30:00Z",
            cache_creation=500_000,
            model="gpt-4-mystery",
        )])
        self.conn.commit()
        self.assertEqual(_cache_1h_opportunities(self.conn), [])

    def test_results_sorted_by_savings_and_capped_at_10(self):
        """When more than 10 candidates exist, return top-10 by savings desc."""
        sessions = []
        for i in range(15):
            cache = 200_000 + i * 100_000  # ascending cache -> ascending savings
            sessions.append(_make_session(
                f"sess-{i:02d}-padding-xx",
                "2026-04-08T09:00:00Z",
                "2026-04-08T11:00:00Z",
                cache_creation=cache,
            ))
        upsert_sessions(self.conn, sessions)
        self.conn.commit()

        opps = _cache_1h_opportunities(self.conn)
        self.assertEqual(len(opps), 10)
        savings = [o["estimated_savings_with_1h"] for o in opps]
        self.assertEqual(savings, sorted(savings, reverse=True))
        # The biggest cache (i=14, cache=1_600_000) should be on top
        self.assertEqual(opps[0]["cache_creation_tokens"], 1_600_000)

    # ── exposure through /api/data ──────────────────────────────────────

    def test_api_data_includes_cache_1h_opportunities_key(self):
        """get_dashboard_data() must surface the new field."""
        upsert_sessions(self.conn, [_make_session(
            "api-payload-id-00001x",
            "2026-04-08T09:00:00Z",
            "2026-04-08T10:30:00Z",
            cache_creation=500_000,
        )])
        self.conn.commit()

        data = get_dashboard_data(db_path=self.db_path)
        self.assertIn("cache_1h_opportunities", data)
        self.assertIsInstance(data["cache_1h_opportunities"], list)
        self.assertEqual(len(data["cache_1h_opportunities"]), 1)
        o = data["cache_1h_opportunities"][0]
        for key in (
            "session_id", "duration_min", "cache_creation_tokens",
            "current_cost", "estimated_savings_with_1h",
        ):
            self.assertIn(key, o)


class TestCache1hHTMLWiring(unittest.TestCase):
    """Verify the HTML template carries the insight card and renderer."""

    def test_template_has_cache_1h_section(self):
        self.assertIn('id="cache-1h-card"', HTML_TEMPLATE)
        self.assertIn('id="cache-1h-body"', HTML_TEMPLATE)
        self.assertIn("1-Hour Cache Opportunities", HTML_TEMPLATE)

    def test_template_has_renderer_and_invocation(self):
        self.assertIn("renderCache1hOpportunities", HTML_TEMPLATE)
        # Called with the server-supplied list
        self.assertIn("rawData.cache_1h_opportunities", HTML_TEMPLATE)

    def test_template_documents_threshold_band(self):
        """The card should hint at the savings heuristic for users."""
        self.assertIn("40%", HTML_TEMPLATE)

    def test_thresholds_match_helper_constants(self):
        """The user-facing copy should match the constants the helper uses.

        If these constants drift, the docs go stale; this test catches it.
        """
        self.assertEqual(CACHE_1H_MIN_DURATION_MIN, 30)
        self.assertEqual(CACHE_1H_MIN_CACHE_CREATION, 100_000)
        self.assertIn("30 minutes", HTML_TEMPLATE)
        self.assertIn("100k", HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
