"""Tests for the inbound webhook receiver: POST /api/inbound/<type>,
GET /api/inbound, log rotation, shared-secret auth, and the cli inbound
command."""

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard


def _post(url, body, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else b""
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    return urllib.request.urlopen(req)


class InboundServerTestBase(unittest.TestCase):
    """Boots a fresh HTTP server bound to an ephemeral port and points the
    module-level INBOUND_LOG / INBOUND_CONFIG at a tempdir so the user's
    real ~/.claude/inbound.jsonl is never touched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_log = dashboard.INBOUND_LOG
        self._orig_cfg = dashboard.INBOUND_CONFIG
        self._orig_max = dashboard.INBOUND_MAX_BYTES
        dashboard.INBOUND_LOG = Path(self.tmp) / "inbound.jsonl"
        dashboard.INBOUND_CONFIG = Path(self.tmp) / "inbound-config.json"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        dashboard.INBOUND_LOG = self._orig_log
        dashboard.INBOUND_CONFIG = self._orig_cfg
        dashboard.INBOUND_MAX_BYTES = self._orig_max
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"


class TestInboundPost(InboundServerTestBase):
    def test_post_without_config_is_accepted(self):
        """No config file means no auth requirement — every POST succeeds."""
        resp = _post(self.url("/api/inbound/test-event"), {"hello": "world"})
        self.assertEqual(resp.status, 200)
        out = json.loads(resp.read())
        self.assertTrue(out["ok"])
        # The line was actually written and parses as the documented envelope.
        line = dashboard.INBOUND_LOG.read_text().strip()
        rec = json.loads(line)
        self.assertEqual(rec["type"], "test-event")
        self.assertEqual(rec["payload"], {"hello": "world"})
        self.assertIn("received_at", rec)
        self.assertEqual(rec["source_ip"], "127.0.0.1")

    def test_post_unknown_event_type_is_accepted(self):
        """Arbitrary <event_type> path segments are passed through verbatim —
        the receiver doesn't gatekeep on a known-types whitelist."""
        for typ in ("monitoring.cpu", "anthropic_status", "weird-thing"):
            resp = _post(self.url(f"/api/inbound/{typ}"), {"n": 1})
            self.assertEqual(resp.status, 200, f"type {typ} rejected")
        events = dashboard.read_inbound_events()
        self.assertEqual(len(events), 3)
        self.assertEqual(
            sorted(e["type"] for e in events),
            ["anthropic_status", "monitoring.cpu", "weird-thing"],
        )


class TestInboundSecret(InboundServerTestBase):
    def setUp(self):
        super().setUp()
        dashboard.INBOUND_CONFIG.write_text(json.dumps({"secret": "s3cret"}))

    def test_post_without_secret_is_forbidden(self):
        try:
            _post(self.url("/api/inbound/x"), {"a": 1})
            self.fail("expected 403")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)
            self.assertFalse(dashboard.INBOUND_LOG.exists(),
                             "rejected POST must not append to the log")

    def test_post_with_valid_secret_succeeds(self):
        resp = _post(
            self.url("/api/inbound/x"),
            {"a": 1},
            headers={"X-Inbound-Secret": "s3cret"},
        )
        self.assertEqual(resp.status, 200)
        events = dashboard.read_inbound_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"], {"a": 1})

    def test_post_with_wrong_secret_is_forbidden(self):
        try:
            _post(
                self.url("/api/inbound/x"),
                {"a": 1},
                headers={"X-Inbound-Secret": "wrong"},
            )
            self.fail("expected 403")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)


class TestInboundGet(InboundServerTestBase):
    def test_get_returns_recent_events_newest_first(self):
        for i in range(3):
            _post(self.url("/api/inbound/seq"), {"i": i})
        with urllib.request.urlopen(self.url("/api/inbound?limit=100")) as r:
            d = json.loads(r.read())
        self.assertEqual(d["count"], 3)
        # Newest first: payload i=2 should come back at index 0.
        self.assertEqual([e["payload"]["i"] for e in d["events"]], [2, 1, 0])

    def test_get_respects_limit(self):
        for i in range(5):
            _post(self.url("/api/inbound/seq"), {"i": i})
        with urllib.request.urlopen(self.url("/api/inbound?limit=2")) as r:
            d = json.loads(r.read())
        self.assertEqual(d["count"], 2)
        self.assertEqual([e["payload"]["i"] for e in d["events"]], [4, 3])

    def test_get_empty_when_no_log(self):
        with urllib.request.urlopen(self.url("/api/inbound")) as r:
            d = json.loads(r.read())
        self.assertEqual(d, {"events": [], "count": 0})


class TestInboundRotation(unittest.TestCase):
    """Direct unit test for the rotation helper — bypasses HTTP so we can
    set a tiny cap and confirm the rename-and-drop behaviour deterministically."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_log = dashboard.INBOUND_LOG
        self._orig_max = dashboard.INBOUND_MAX_BYTES
        dashboard.INBOUND_LOG = Path(self.tmp) / "inbound.jsonl"
        dashboard.INBOUND_MAX_BYTES = 200  # bytes

    def tearDown(self):
        dashboard.INBOUND_LOG = self._orig_log
        dashboard.INBOUND_MAX_BYTES = self._orig_max
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rotates_when_over_cap(self):
        # Pre-seed a log that's already over the cap.
        dashboard.INBOUND_LOG.parent.mkdir(parents=True, exist_ok=True)
        dashboard.INBOUND_LOG.write_text("x" * 300)
        rotated = dashboard.INBOUND_LOG.with_suffix(".jsonl.1")

        # The next append should rotate first, then write a fresh line.
        dashboard.append_inbound_event("rot", {"k": "v"}, "127.0.0.1")
        self.assertTrue(rotated.exists(), "rotated file should exist")
        self.assertEqual(rotated.read_text(), "x" * 300)
        active = dashboard.INBOUND_LOG.read_text().strip()
        self.assertEqual(json.loads(active)["type"], "rot")

    def test_oldest_rotation_is_dropped(self):
        # An existing .jsonl.1 must be replaced (not stacked) so we cap
        # disk usage at 2 * INBOUND_MAX_BYTES.
        dashboard.INBOUND_LOG.parent.mkdir(parents=True, exist_ok=True)
        rotated = dashboard.INBOUND_LOG.with_suffix(".jsonl.1")
        rotated.write_text("ancient")
        dashboard.INBOUND_LOG.write_text("x" * 300)

        dashboard.append_inbound_event("rot", {}, "127.0.0.1")
        self.assertNotEqual(rotated.read_text(), "ancient",
                            "previous .jsonl.1 must be overwritten")
        self.assertEqual(rotated.read_text(), "x" * 300)


class TestInboundCli(unittest.TestCase):
    """End-to-end-ish: invoke cli.py inbound as a subprocess so dispatch,
    argument parsing, and the dashboard import path are all exercised."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = Path(self.tmp) / "inbound.jsonl"
        # Hand-write three events directly so the CLI test is independent
        # of the HTTP layer.
        self.log.write_text(
            json.dumps({"type": "a", "received_at": "2026-05-23T10:00:00Z",
                        "source_ip": "127.0.0.1", "payload": {"i": 1}}) + "\n"
            + json.dumps({"type": "b", "received_at": "2026-05-23T10:01:00Z",
                          "source_ip": "127.0.0.1", "payload": {"i": 2}}) + "\n"
            + json.dumps({"type": "c", "received_at": "2026-05-23T10:02:00Z",
                          "source_ip": "127.0.0.1", "payload": {"i": 3}}) + "\n"
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_cli(self, args):
        # Monkey-patch INBOUND_LOG via a tiny shim that imports dashboard,
        # rewrites the path, then defers to cli.cmd_inbound.
        repo = Path(__file__).resolve().parent.parent
        code = (
            f"import sys; sys.path.insert(0, {repr(str(repo))});"
            f"import dashboard;"
            f"dashboard.INBOUND_LOG = __import__('pathlib').Path({repr(str(self.log))});"
            f"import cli; cli.cmd_inbound(tail={args.get('tail')!r})"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_cli_prints_all_events_by_default(self):
        out = self._run_cli({"tail": None})
        # All three event types appear in the output.
        self.assertIn("type=a" if False else " a ", out)  # spacing-tolerant
        for t in ("a", "b", "c"):
            self.assertIn(f" {t} ", out, f"event {t} missing from CLI output")
        # Oldest-first ordering: 'a' lands before 'c'.
        self.assertLess(out.index(" a "), out.index(" c "))

    def test_cli_tail_limits_output(self):
        out = self._run_cli({"tail": "1"})
        self.assertIn(" c ", out)
        self.assertNotIn(" a ", out)
        self.assertNotIn(" b ", out)


if __name__ == "__main__":
    unittest.main()
