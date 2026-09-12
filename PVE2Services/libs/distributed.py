"""Cluster/HA primitives: DB-backed leases so multiple replicas can share work.

The lease table uses unix-epoch-milliseconds INTEGER timestamps so expiry
comparisons are plain numeric SQL — identical behavior on SQLite and Postgres.
Acquisition is a single atomic UPSERT guarded by a WHERE clause, which both
dialects support.
"""

import logging
import os
import socket
import uuid
from typing import Optional

from sqlalchemy import text

logger = logging.getLogger("pve2.distributed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS instance_locks (
    name TEXT PRIMARY KEY,
    holder TEXT NOT NULL,
    expires_at BIGINT NOT NULL
)
"""

_instance_id: Optional[str] = None


def instance_id() -> str:
    """Stable-per-process identifier: hostname + random suffix."""
    global _instance_id
    if _instance_id is None:
        pid = os.getpid()
        _instance_id = f"{socket.gethostname()}:{pid}:{uuid.uuid4().hex[:8]}"
    return _instance_id


class _LeaseDB:
    _db = None
    _ready = False

    @classmethod
    def get(cls):
        if cls._db is None:
            from PVE2Services.libs.db_adapter import get_db

            cls._db = get_db()
        if not cls._ready:
            with cls._db.engine.begin() as conn:
                conn.execute(text(_SCHEMA))
            cls._ready = True
        return cls._db


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def try_lock(name: str, ttl_seconds: int) -> bool:
    """Try to (re)acquire the named lease. True → this instance holds it.

    Succeeds when: the lease is free, expired, or already ours (renewal).
    A fresh lease held by another instance makes this return False.
    """
    holder = instance_id()
    now = _now_ms()
    expires = now + int(ttl_seconds * 1000)
    db = _LeaseDB.get()
    try:
        with db.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO instance_locks (name, holder, expires_at)
                    VALUES (:n, :h, :e)
                    ON CONFLICT(name) DO UPDATE SET
                        holder = excluded.holder,
                        expires_at = excluded.expires_at
                    WHERE instance_locks.holder = excluded.holder
                       OR instance_locks.expires_at < :now
                """),
                {"n": name, "h": holder, "e": expires, "now": now},
            )
            row = conn.execute(
                text("SELECT holder FROM instance_locks WHERE name = :n"), {"n": name}
            ).fetchone()
        return bool(row) and row[0] == holder
    except Exception:
        logger.exception("Lease acquisition failed for %s", name)
        # Fail-open would duplicate work across replicas; fail-closed skips the
        # job on this instance and lets whoever holds the lease run it.
        return False


def release_lock(name: str) -> None:
    """Release the lease if we hold it (graceful shutdown)."""
    db = _LeaseDB.get()
    try:
        with db.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM instance_locks WHERE name = :n AND holder = :h"),
                {"n": name, "h": instance_id()},
            )
    except Exception:
        logger.exception("Lease release failed for %s", name)


def lock_holder(name: str) -> Optional[str]:
    """Return the current holder of the lease, or None if free/expired."""
    db = _LeaseDB.get()
    try:
        with db.engine.connect() as conn:
            row = conn.execute(
                text("SELECT holder, expires_at FROM instance_locks WHERE name = :n"),
                {"n": name},
            ).fetchone()
        if row and row[1] >= _now_ms():
            return row[0]
        return None
    except Exception:
        logger.exception("Lease lookup failed for %s", name)
        return None
