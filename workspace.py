"""
workspace.py - Team / multi-machine mode for claude-usage.

When multiple developers want to share a single usage database (cost rollups
across a team), they can point each machine at the same Postgres instance,
or at a shared SQLite file on a network drive (Dropbox / NFS / SMB).

Configuration lives in ~/.claude/workspace.json:

    {
      "backend": "postgres",   // or "sqlite" (default)
      "machine_id": "jakduch-mbp",
      "team": "engineering",
      "db_path": "/Volumes/team/claude-usage.db"   // sqlite-only
    }

Backward compat: if no workspace.json exists, behavior is identical to the
single-machine SQLite default (~/.claude/usage.db, no machine_id stamping
beyond a per-record default of "local").
"""

import json
import os
import socket
from pathlib import Path

CONFIG_PATH = Path.home() / ".claude" / "workspace.json"
DEFAULT_MACHINE_ID = "local"

# Env vars take precedence over config file (useful for CI and Docker).
ENV_BACKEND = "CLAUDE_USAGE_BACKEND"
ENV_PG_DSN  = "CLAUDE_USAGE_PG"
ENV_MACHINE = "CLAUDE_USAGE_MACHINE_ID"
ENV_TEAM    = "CLAUDE_USAGE_TEAM"


def _default_machine_id():
    """Reasonable default if config + env are both silent."""
    try:
        return socket.gethostname() or DEFAULT_MACHINE_ID
    except Exception:
        return DEFAULT_MACHINE_ID


def load_config(path=CONFIG_PATH):
    """Read workspace config. Returns a dict with defaults applied.

    The dict always has keys: backend, machine_id, team, db_path, pg_dsn.
    Env vars override the on-disk file. Missing file -> SQLite local default.
    """
    raw = {}
    p = Path(path)
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            raw = {}

    backend = os.environ.get(ENV_BACKEND) or raw.get("backend") or "sqlite"
    backend = backend.lower().strip()
    if backend not in ("sqlite", "postgres"):
        backend = "sqlite"

    machine_id = (
        os.environ.get(ENV_MACHINE)
        or raw.get("machine_id")
        or _default_machine_id()
    )
    team = os.environ.get(ENV_TEAM) or raw.get("team") or ""

    db_path = raw.get("db_path")  # sqlite override (e.g. shared drive)
    pg_dsn = os.environ.get(ENV_PG_DSN) or raw.get("pg_dsn") or raw.get("dsn")

    return {
        "backend": backend,
        "machine_id": str(machine_id),
        "team": str(team),
        "db_path": db_path,
        "pg_dsn": pg_dsn,
    }


def is_team_mode(config=None):
    """True if the user has opted into a shared backend (postgres or
    explicit shared-sqlite path)."""
    c = config or load_config()
    if c["backend"] == "postgres":
        return True
    return bool(c.get("db_path"))


def get_postgres_connection(dsn=None):
    """Lazy-import psycopg2 so the dep stays optional. Raises a friendly
    error if the user picked postgres without installing the driver."""
    try:
        import psycopg2  # noqa: F401
        import psycopg2.extras  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "Postgres backend selected but psycopg2 is not installed. "
            "Run: pip install psycopg2-binary"
        ) from e

    import psycopg2
    import psycopg2.extras  # noqa: F401

    cfg = load_config()
    dsn = dsn or cfg.get("pg_dsn")
    if not dsn:
        raise RuntimeError(
            f"Postgres backend selected but no DSN. Set ${ENV_PG_DSN} "
            "or add 'pg_dsn' to ~/.claude/workspace.json"
        )
    conn = psycopg2.connect(dsn)
    return conn


# Schema migrations applicable to any backend.
# Tries each ALTER and swallows the dialect-specific "column already exists"
# error so the migration is idempotent across SQLite + Postgres.

MACHINE_ID_MIGRATIONS = (
    "ALTER TABLE turns ADD COLUMN machine_id TEXT",
    "ALTER TABLE sessions ADD COLUMN machine_id TEXT",
)


def _is_postgres_conn(conn):
    mod = type(conn).__module__ or ""
    return "psycopg2" in mod


def ensure_machine_id_columns(conn):
    """Add machine_id columns to turns + sessions if missing.

    Works for sqlite3.Connection and psycopg2 connections. Each ALTER is
    wrapped in its own try/except because Postgres aborts the whole
    transaction on error, while sqlite just raises on the bad statement.
    """
    is_pg = _is_postgres_conn(conn)
    for sql in MACHINE_ID_MIGRATIONS:
        try:
            if is_pg:
                # New transaction per statement so a duplicate-column error
                # doesn't poison the rest.
                cur = conn.cursor()
                cur.execute(sql)
                conn.commit()
                cur.close()
            else:
                conn.execute(sql)
        except Exception:
            if is_pg:
                try:
                    conn.rollback()
                except Exception:
                    pass
            # Column already exists -- fine, that's the idempotent path.


def stamp_machine_id(rows, machine_id):
    """In-place: add machine_id to a list of turn / session dicts that
    don't already carry one. Returns the list (for chaining)."""
    for r in rows:
        if not r.get("machine_id"):
            r["machine_id"] = machine_id
    return rows
