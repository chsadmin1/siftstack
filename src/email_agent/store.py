"""Durable send history + suppression for the email cadence.

Stdlib sqlite3, WAL mode, same shape as `sms_agent.store`. This is a much
smaller surface than the SMS store: there is no inbound event log or
conversation state in v1 (one-way outbound only), just "what has this address
already had" and "who must never be emailed again".
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sent (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL,
    record_uuid   TEXT,
    touch         INTEGER NOT NULL,
    subject       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'sent',   -- sent | failed
    error         TEXT,
    sent_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sent_email ON sent(email, id);

CREATE TABLE IF NOT EXISTS suppression (
    email         TEXT PRIMARY KEY,
    reason        TEXT NOT NULL,   -- opt_out | bounced | manual
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_email(raw: Any) -> str:
    e = str(raw or "").strip().lower()
    return e if "@" in e and "." in e.split("@")[-1] else ""


def _conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(config.DB_PATH), timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.executescript(SCHEMA)
        _local.conn = c
    return c


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    c = _conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
    except Exception:
        c.execute("ROLLBACK")
        raise
    else:
        c.execute("COMMIT")


def init() -> Path:
    _conn()
    return config.DB_PATH


# --------------------------------------------------------------- suppression

def is_suppressed(email: str) -> str:
    """The reason this address is suppressed, or "" if it is not."""
    row = _conn().execute(
        "SELECT reason FROM suppression WHERE email=?", (clean_email(email),)
    ).fetchone()
    return row["reason"] if row else ""


def suppress(email: str, reason: str) -> None:
    e = clean_email(email)
    if not e:
        return
    with tx() as c:
        c.execute(
            "INSERT INTO suppression(email, reason, created_at) VALUES (?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET reason=excluded.reason",
            (e, reason, now()),
        )


# --------------------------------------------------------------------- sent

def log_send(email: str, record_uuid: str, touch: int, subject: str,
             status: str = "sent", error: str = "") -> None:
    with tx() as c:
        c.execute(
            "INSERT INTO sent(email, record_uuid, touch, subject, status, error, sent_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (clean_email(email), record_uuid, touch, subject, status, error or None, now()),
        )


def prior_touches(email: str) -> dict:
    """{"touches": {n, ...}, "last": iso} for successfully sent touches only.
    A failed send must not count as delivered, or the person silently skips it."""
    touches: set = set()
    last = ""
    for row in _conn().execute(
        "SELECT touch, sent_at FROM sent WHERE email=? AND status='sent' ORDER BY id",
        (clean_email(email),),
    ):
        touches.add(row["touch"])
        if row["sent_at"] > last:
            last = row["sent_at"]
    return {"touches": touches, "last": last}


def sent_today(day: Optional[str] = None) -> int:
    day = day or now()[:10]
    row = _conn().execute(
        "SELECT COUNT(*) AS n FROM sent WHERE status='sent' AND sent_at >= ?", (day,)
    ).fetchone()
    return row["n"] if row else 0


# --------------------------------------------------------------------- meta

def get_meta(key: str) -> str:
    row = _conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else ""


def set_meta(key: str, value: str) -> None:
    with tx() as c:
        c.execute(
            "INSERT INTO meta(key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
