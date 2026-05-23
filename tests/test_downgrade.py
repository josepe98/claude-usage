"""Tests for model downgrade suggestions (dashboard._downgrade_suggestions)."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard
from scanner import get_db, init_db, upsert_sessions, insert_turns


def _make_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    conn = get_db(db_path)
    init_db(conn)
    return conn, db_path


def _add_session(conn, session_id, model, project, turns_specs,
                 first_ts="2026-04-08T09:00:00Z",
                 last_ts="2026-04-08T11:00:00Z"):
    """turns_specs: list of dicts with input/output/cache_read/cache_creation/tool_name/cache_1h."""
    sums = dict(inp=0, out=0, cr=0, cc=0, cc1h=0)
    for t in turns_specs:
        sums["inp"] += t.get("input", 0)
        sums["out"] += t.get("output", 0)
        sums["cr"] += t.get("cache_read", 0)
        sums["cc"] += t.get("cache_creation", 0)
        sums["cc1h"] += t.get("cache_1h", 0)
    upsert_sessions(conn, [{
        "session_id": session_id, "project_name": project,
        "first_timestamp": first_ts, "last_timestamp": last_ts,
        "git_branch": "main", "model": model,
        "total_input_tokens": sums["inp"], "total_output_tokens": sums["out"],
        "total_cache_read": sums["cr"], "total_cache_creation": sums["cc"],
        "total_cache_1h": sums["cc1h"],
        "turn_count": len(turns_specs),
    }])
    insert_turns(conn, [{
        "session_id": session_id,
        "timestamp": t.get("timestamp", first_ts),
        "model": model,
        "input_tokens": t.get("input", 0),
        "output_tokens": t.get("output", 0),
        "cache_read_tokens": t.get("cache_read", 0),
        "cache_creation_tokens": t.get("cache_creation", 0),
        "cache_1h_tokens": t.get("cache_1h", 0),
        "tool_name": t.get("tool_name"),
        "cwd": "/tmp",
        "message_id": f"{session_id}-msg-{i}",
    } for i, t in enumerate(turns_specs)])
    conn.commit()


class TestDowngradeSuggestions(unittest.TestCase):

    def setUp(self):
        self.conn, self.db_path = _make_db()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    # ── Helper unit tests ────────────────────────────────────────────────
    def test_is_downgradable_opus_yes(self):
        self.assertTrue(dashboard._is_downgradable_model("claude-opus-4-7"))

    def test_is_downgradable_sonnet_yes(self):
        self.assertTrue(dashboard._is_downgradable_model("claude-sonnet-4-6"))

    def test_is_downgradable_haiku_no(self):
        self.assertFalse(dashboard._is_downgradable_model("claude-haiku-4-7"))

    def test_is_downgradable_unknown_no(self):
        self.assertFalse(dashboard._is_downgradable_model(""))
        self.assertFalse(dashboard._is_downgradable_model(None))
        self.assertFalse(dashboard._is_downgradable_model("gpt-4"))

    def test_suggest_target_model_matches_generation(self):
        self.assertEqual(dashboard._suggest_target_model("claude-opus-4-7"),
                         "claude-haiku-4-7")
        self.assertEqual(dashboard._suggest_target_model("claude-sonnet-4-6"),
                         "claude-haiku-4-6")

    def test_suggest_target_model_falls_back(self):
        # No matching generation for an alien suffix → fall back to newest haiku
        target = dashboard._suggest_target_model("claude-opus-3-0")
        self.assertIn("haiku", target)

    # ── Core scenarios ──────────────────────────────────────────────────
    def test_opus_session_small_turns_is_flagged(self):
        """Opus session with small input/output per turn, simple tools,
        no long-context, no Task — should be flagged for downgrade to haiku."""
        turns = [
            {"input": 800, "output": 300, "cache_read": 100, "cache_creation": 50, "tool_name": "Read"},
            {"input": 900, "output": 200, "cache_read": 100, "cache_creation": 50, "tool_name": "Edit"},
            {"input": 700, "output": 250, "cache_read": 100, "cache_creation": 50, "tool_name": "Bash"},
            {"input": 850, "output": 280, "cache_read": 100, "cache_creation": 50, "tool_name": "Read"},
            {"input": 750, "output": 220, "cache_read": 100, "cache_creation": 50, "tool_name": "Grep"},
            {"input": 800, "output": 230, "cache_read": 100, "cache_creation": 50, "tool_name": "Edit"},
        ]
        _add_session(self.conn, "sess-small-opus", "claude-opus-4-7",
                     "user/easy-project", turns)
        suggestions = dashboard._downgrade_suggestions(self.conn)
        ids = [s["session_id_full"] for s in suggestions]
        self.assertIn("sess-small-opus", ids)
        flagged = [s for s in suggestions if s["session_id_full"] == "sess-small-opus"][0]
        self.assertEqual(flagged["current_model"], "claude-opus-4-7")
        self.assertEqual(flagged["suggested_model"], "claude-haiku-4-7")
        self.assertGreater(flagged["savings_usd"], 0)
        self.assertLess(flagged["projected_cost"], flagged["current_cost"])

    def test_opus_session_with_long_context_not_flagged(self):
        """Opus session with big inputs and lots of cache creation — that's
        a long-context session that would actually need the bigger model."""
        # Each turn: huge cache_creation; large input. This blows past both
        # the small-tokens-per-turn check and the cache-creation ratio check.
        turns = [
            {"input": 50000, "output": 8000, "cache_read": 5000,
             "cache_creation": 100000, "tool_name": "Read"},
            {"input": 60000, "output": 9000, "cache_read": 5000,
             "cache_creation": 110000, "tool_name": "Edit"},
            {"input": 55000, "output": 7500, "cache_read": 5000,
             "cache_creation": 95000, "tool_name": "Read"},
            {"input": 52000, "output": 7200, "cache_read": 5000,
             "cache_creation": 90000, "tool_name": "Edit"},
            {"input": 48000, "output": 6800, "cache_read": 5000,
             "cache_creation": 88000, "tool_name": "Read"},
        ]
        _add_session(self.conn, "sess-long-opus", "claude-opus-4-7",
                     "user/big-context", turns)
        suggestions = dashboard._downgrade_suggestions(self.conn)
        ids = [s["session_id_full"] for s in suggestions]
        self.assertNotIn("sess-long-opus", ids)

    def test_opus_session_with_task_tool_not_flagged(self):
        """Sessions that dispatched subagents (Task tool) lean on the larger
        model's planning capability — vetoed regardless of size."""
        turns = [
            {"input": 800, "output": 300, "cache_read": 100, "cache_creation": 50, "tool_name": "Read"},
            {"input": 900, "output": 250, "cache_read": 100, "cache_creation": 50, "tool_name": "Edit"},
            {"input": 700, "output": 200, "cache_read": 100, "cache_creation": 50, "tool_name": "Bash"},
            {"input": 850, "output": 280, "cache_read": 100, "cache_creation": 50, "tool_name": "Task"},
            {"input": 750, "output": 220, "cache_read": 100, "cache_creation": 50, "tool_name": "Read"},
        ]
        _add_session(self.conn, "sess-task-opus", "claude-opus-4-7",
                     "user/agentic-work", turns)
        suggestions = dashboard._downgrade_suggestions(self.conn)
        ids = [s["session_id_full"] for s in suggestions]
        self.assertNotIn("sess-task-opus", ids)

    def test_opus_session_with_web_search_not_flagged(self):
        turns = [
            {"input": 500, "output": 200, "cache_read": 0, "cache_creation": 0, "tool_name": "Read"},
            {"input": 600, "output": 250, "cache_read": 0, "cache_creation": 0, "tool_name": "WebSearch"},
            {"input": 700, "output": 300, "cache_read": 0, "cache_creation": 0, "tool_name": "Read"},
            {"input": 550, "output": 220, "cache_read": 0, "cache_creation": 0, "tool_name": "Edit"},
            {"input": 500, "output": 200, "cache_read": 0, "cache_creation": 0, "tool_name": "Bash"},
        ]
        _add_session(self.conn, "sess-web-opus", "claude-opus-4-7",
                     "user/research", turns)
        suggestions = dashboard._downgrade_suggestions(self.conn)
        ids = [s["session_id_full"] for s in suggestions]
        self.assertNotIn("sess-web-opus", ids)

    def test_haiku_session_never_suggested(self):
        """A haiku session should never appear in downgrade suggestions —
        there is nothing cheaper to downgrade to."""
        turns = [
            {"input": 500, "output": 200, "cache_read": 50, "cache_creation": 25, "tool_name": "Read"},
            {"input": 600, "output": 250, "cache_read": 50, "cache_creation": 25, "tool_name": "Edit"},
            {"input": 700, "output": 300, "cache_read": 50, "cache_creation": 25, "tool_name": "Bash"},
            {"input": 550, "output": 220, "cache_read": 50, "cache_creation": 25, "tool_name": "Read"},
            {"input": 500, "output": 200, "cache_read": 50, "cache_creation": 25, "tool_name": "Edit"},
        ]
        _add_session(self.conn, "sess-haiku", "claude-haiku-4-7",
                     "user/cheap-work", turns)
        suggestions = dashboard._downgrade_suggestions(self.conn)
        ids = [s["session_id_full"] for s in suggestions]
        self.assertNotIn("sess-haiku", ids)
        # And no entry should ever have current_model containing haiku
        for s in suggestions:
            self.assertNotIn("haiku", s["current_model"].lower())

    def test_sonnet_session_flagged(self):
        """Sonnet sessions with small turns should also be flagged."""
        turns = [
            {"input": 700, "output": 250, "cache_read": 100, "cache_creation": 50, "tool_name": "Read"},
            {"input": 800, "output": 280, "cache_read": 100, "cache_creation": 50, "tool_name": "Edit"},
            {"input": 750, "output": 260, "cache_read": 100, "cache_creation": 50, "tool_name": "Bash"},
            {"input": 700, "output": 240, "cache_read": 100, "cache_creation": 50, "tool_name": "Grep"},
            {"input": 650, "output": 220, "cache_read": 100, "cache_creation": 50, "tool_name": "Read"},
            {"input": 800, "output": 280, "cache_read": 100, "cache_creation": 50, "tool_name": "Edit"},
        ]
        _add_session(self.conn, "sess-sonnet-easy", "claude-sonnet-4-6",
                     "user/easy", turns)
        suggestions = dashboard._downgrade_suggestions(self.conn)
        ids = [s["session_id_full"] for s in suggestions]
        self.assertIn("sess-sonnet-easy", ids)

    def test_results_ordered_by_savings_desc(self):
        """Top result should have the largest savings."""
        # Big opus session: lots of tokens × opus-haiku cost delta
        big = [{"input": 4000, "output": 1500, "cache_read": 200,
                "cache_creation": 100, "tool_name": "Read"} for _ in range(20)]
        # Small opus session: same shape but fewer turns
        small = [{"input": 1000, "output": 400, "cache_read": 100,
                  "cache_creation": 50, "tool_name": "Edit"} for _ in range(6)]
        _add_session(self.conn, "sess-big",   "claude-opus-4-7",  "p1", big)
        _add_session(self.conn, "sess-small", "claude-opus-4-7",  "p2", small)
        suggestions = dashboard._downgrade_suggestions(self.conn)
        ids = [s["session_id_full"] for s in suggestions]
        self.assertEqual(ids[0], "sess-big")
        # Strictly non-increasing savings
        savings = [s["savings_usd"] for s in suggestions]
        self.assertEqual(savings, sorted(savings, reverse=True))

    def test_top_n_cap(self):
        """Helper should cap results at _DOWNGRADE_TOP_N (default 20)."""
        # Create 25 qualifying opus sessions
        for i in range(25):
            turns = [
                {"input": 500, "output": 200, "cache_read": 0,
                 "cache_creation": 0, "tool_name": "Read"} for _ in range(6)
            ]
            _add_session(self.conn, f"sess-{i:03d}", "claude-opus-4-7",
                         f"p{i}", turns)
        suggestions = dashboard._downgrade_suggestions(self.conn)
        self.assertLessEqual(len(suggestions), dashboard._DOWNGRADE_TOP_N)
        self.assertEqual(len(suggestions), 20)

    def test_tiny_session_below_min_turns_skipped(self):
        """A session with < _DOWNGRADE_MIN_TURNS shouldn't be flagged at all —
        too little signal to make a confident recommendation."""
        turns = [
            {"input": 500, "output": 200, "cache_read": 0,
             "cache_creation": 0, "tool_name": "Read"},
        ]
        _add_session(self.conn, "sess-tiny", "claude-opus-4-7",
                     "user/tiny", turns)
        suggestions = dashboard._downgrade_suggestions(self.conn)
        ids = [s["session_id_full"] for s in suggestions]
        self.assertNotIn("sess-tiny", ids)

    # ── API integration ─────────────────────────────────────────────────
    def test_api_data_includes_downgrade_suggestions(self):
        """get_dashboard_data must surface downgrade_suggestions + total."""
        turns = [
            {"input": 800, "output": 300, "cache_read": 100, "cache_creation": 50, "tool_name": "Read"},
            {"input": 900, "output": 200, "cache_read": 100, "cache_creation": 50, "tool_name": "Edit"},
            {"input": 700, "output": 250, "cache_read": 100, "cache_creation": 50, "tool_name": "Bash"},
            {"input": 850, "output": 280, "cache_read": 100, "cache_creation": 50, "tool_name": "Read"},
            {"input": 750, "output": 220, "cache_read": 100, "cache_creation": 50, "tool_name": "Grep"},
            {"input": 800, "output": 230, "cache_read": 100, "cache_creation": 50, "tool_name": "Edit"},
        ]
        _add_session(self.conn, "sess-easy", "claude-opus-4-7",
                     "user/easy", turns)
        self.conn.close()
        self.conn = None  # so tearDown doesn't double-close

        data = dashboard.get_dashboard_data(db_path=self.db_path)
        self.assertIn("downgrade_suggestions", data)
        self.assertIn("downgrade_total_savings", data)
        self.assertIsInstance(data["downgrade_suggestions"], list)
        self.assertGreater(len(data["downgrade_suggestions"]), 0)
        self.assertGreater(data["downgrade_total_savings"], 0)

        # Reopen so tearDown's close-then-unlink is symmetric
        self.conn = sqlite3.connect(self.db_path)

    def test_api_data_returns_empty_list_when_no_sessions(self):
        self.conn.close()
        self.conn = None
        data = dashboard.get_dashboard_data(db_path=self.db_path)
        self.assertIn("downgrade_suggestions", data)
        self.assertEqual(data["downgrade_suggestions"], [])
        self.assertEqual(data["downgrade_total_savings"], 0)
        self.conn = sqlite3.connect(self.db_path)


class TestHTMLWiring(unittest.TestCase):
    """The dashboard HTML must wire up the downgrade-card and renderer."""

    def test_html_has_downgrade_card(self):
        self.assertIn('id="downgrade-card"', dashboard.HTML_TEMPLATE)

    def test_html_has_render_downgrade_function(self):
        self.assertIn("renderDowngradeSuggestions", dashboard.HTML_TEMPLATE)

    def test_render_downgrade_invoked_in_apply_filter(self):
        """The render function must actually be called from the filter pipeline,
        otherwise the card would never appear."""
        # renderDowngradeSuggestions should be both defined AND called.
        # Defined: "function renderDowngradeSuggestions("
        # Called:  "renderDowngradeSuggestions();" (no args, on its own)
        self.assertIn("function renderDowngradeSuggestions(", dashboard.HTML_TEMPLATE)
        self.assertIn("renderDowngradeSuggestions();", dashboard.HTML_TEMPLATE)

    def test_render_downgrade_reads_server_payload(self):
        """The renderer should use rawData.downgrade_suggestions, not its
        own client-side heuristic — the source of truth is the server."""
        # Extract the function body
        start = dashboard.HTML_TEMPLATE.index("function renderDowngradeSuggestions(")
        end = dashboard.HTML_TEMPLATE.index("function ", start + 1)
        body = dashboard.HTML_TEMPLATE[start:end]
        self.assertIn("downgrade_suggestions", body)
        self.assertIn("downgrade_total_savings", body)


if __name__ == "__main__":
    unittest.main()
