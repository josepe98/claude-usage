"""Tests for the cache hit ratio analyzer (dashboard._cache_hit_*)."""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


def _build_db(sessions):
    """Return an in-memory sqlite connection with a minimal `sessions` table.

    `sessions` is a list of (session_id, input, cache_read) tuples.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            total_input_tokens INTEGER,
            total_cache_read INTEGER
        )
    """)
    conn.executemany(
        "INSERT INTO sessions(session_id, total_input_tokens, total_cache_read) VALUES (?, ?, ?)",
        sessions,
    )
    conn.commit()
    return conn


class TestCacheHitRatio(unittest.TestCase):
    def test_zero_input_and_zero_cache_is_zero(self):
        self.assertEqual(dashboard._cache_hit_ratio(0, 0), 0.0)

    def test_no_cache_hits(self):
        self.assertEqual(dashboard._cache_hit_ratio(1000, 0), 0.0)

    def test_full_cache_hits(self):
        self.assertEqual(dashboard._cache_hit_ratio(0, 1000), 1.0)

    def test_half_and_half(self):
        self.assertAlmostEqual(dashboard._cache_hit_ratio(500, 500), 0.5, places=6)

    def test_none_inputs_treated_as_zero(self):
        self.assertEqual(dashboard._cache_hit_ratio(None, None), 0.0)
        self.assertEqual(dashboard._cache_hit_ratio(None, 100), 1.0)


class TestCacheHitCategory(unittest.TestCase):
    def test_low_bucket(self):
        self.assertEqual(dashboard._cache_hit_category(0.0), "low")
        self.assertEqual(dashboard._cache_hit_category(0.299), "low")

    def test_medium_bucket(self):
        self.assertEqual(dashboard._cache_hit_category(0.30), "medium")
        self.assertEqual(dashboard._cache_hit_category(0.50), "medium")
        self.assertEqual(dashboard._cache_hit_category(0.70), "medium")

    def test_high_bucket(self):
        self.assertEqual(dashboard._cache_hit_category(0.7001), "high")
        self.assertEqual(dashboard._cache_hit_category(1.0), "high")


class TestCacheHitAnalysis(unittest.TestCase):
    def test_empty_db_has_safe_defaults(self):
        conn = _build_db([])
        out = dashboard._cache_hit_analysis(conn)
        self.assertEqual(out["per_session"], {})
        s = out["summary"]
        self.assertEqual(s["sessions_total"], 0)
        self.assertEqual(s["sessions_with_cache"], 0)
        self.assertEqual(s["sessions_underusing"], 0)
        self.assertEqual(s["avg_ratio"], 0.0)
        self.assertEqual(s["by_category"], {"low": 0, "medium": 0, "high": 0})

    def test_mixed_population_categorisation(self):
        conn = _build_db([
            # low-ratio sessions
            ("s-low-small", 10_000, 1_000),     # 9% — low but small
            ("s-low-big",   100_000, 5_000),    # ~4.8% — LOW + big input → underusing
            # medium-ratio session
            ("s-med",       50_000, 50_000),    # 50%
            # high-ratio session
            ("s-high",      10_000, 90_000),    # 90%
        ])
        out = dashboard._cache_hit_analysis(conn)
        self.assertEqual(out["summary"]["sessions_total"], 4)
        self.assertEqual(out["summary"]["sessions_with_cache"], 4)
        self.assertEqual(out["summary"]["by_category"]["low"], 2)
        self.assertEqual(out["summary"]["by_category"]["medium"], 1)
        self.assertEqual(out["summary"]["by_category"]["high"], 1)
        # Only s-low-big crosses the input>50k AND ratio<30% bar
        self.assertEqual(out["summary"]["sessions_underusing"], 1)
        self.assertTrue(out["per_session"]["s-low-big"]["underusing"])
        self.assertFalse(out["per_session"]["s-low-small"]["underusing"])
        self.assertFalse(out["per_session"]["s-high"]["underusing"])

    def test_average_ratio_is_mean_across_sessions(self):
        # ratios: 0.0, 0.5, 1.0 → mean 0.5
        conn = _build_db([
            ("a", 1_000, 0),
            ("b", 1_000, 1_000),
            ("c", 0, 1_000),
        ])
        out = dashboard._cache_hit_analysis(conn)
        self.assertAlmostEqual(out["summary"]["avg_ratio"], 0.5, places=4)
        self.assertAlmostEqual(out["summary"]["avg_ratio_pct"], 50.0, places=1)

    def test_input_threshold_is_configurable(self):
        # 40k input would normally be under the default 50k bar.
        conn = _build_db([("s", 40_000, 1_000)])  # ratio ~2.4%
        default = dashboard._cache_hit_analysis(conn)
        self.assertEqual(default["summary"]["sessions_underusing"], 0)
        custom = dashboard._cache_hit_analysis(conn, input_threshold=10_000)
        self.assertEqual(custom["summary"]["sessions_underusing"], 1)
        self.assertEqual(custom["summary"]["input_threshold"], 10_000)


class TestDashboardWiring(unittest.TestCase):
    def test_html_template_has_insight_card(self):
        self.assertIn('id="cache-hit-card"', dashboard.HTML_TEMPLATE)
        self.assertIn("renderCacheHit", dashboard.HTML_TEMPLATE)

    def test_html_template_renders_underusing_badge(self):
        self.assertIn("cache-warn-badge", dashboard.HTML_TEMPLATE)
        self.assertIn("cache_underusing", dashboard.HTML_TEMPLATE)

    def test_get_dashboard_data_exposes_summary_and_ratios(self):
        """End-to-end: dashboard.get_dashboard_data on a tiny fixture DB
        should attach cache_hit_ratio per session and a cache_hit_summary."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = Path(tf.name)
        try:
            conn = sqlite3.connect(db_path)
            # Re-use scanner's migrations to build the real schema.
            import scanner
            scanner.init_db(conn)
            scanner._migrate_schema(conn)
            conn.execute("""
                INSERT INTO sessions(
                    session_id, project_name, first_timestamp, last_timestamp,
                    total_input_tokens, total_output_tokens,
                    total_cache_read, total_cache_creation,
                    model, turn_count, git_branch
                ) VALUES
                    ('sess-low-big', 'proj', '2026-05-01T00:00:00Z', '2026-05-01T00:30:00Z',
                     200000, 1000, 5000, 0, 'claude-opus-4-7', 10, 'main'),
                    ('sess-high',    'proj', '2026-05-02T00:00:00Z', '2026-05-02T00:30:00Z',
                     1000, 1000, 90000, 0, 'claude-opus-4-7', 10, 'main')
            """)
            conn.commit()
            conn.close()

            data = dashboard.get_dashboard_data(db_path=db_path)
            self.assertIn("cache_hit_summary", data)
            summary = data["cache_hit_summary"]
            self.assertEqual(summary["sessions_total"], 2)
            self.assertGreaterEqual(summary["sessions_underusing"], 1)
            by_sid = {s["session_id_full"]: s for s in data["sessions_all"]}
            self.assertIn("cache_hit_ratio", by_sid["sess-low-big"])
            self.assertTrue(by_sid["sess-low-big"]["cache_underusing"])
            self.assertFalse(by_sid["sess-high"]["cache_underusing"])
            self.assertEqual(by_sid["sess-high"]["cache_hit_category"], "high")
        finally:
            try:
                db_path.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
