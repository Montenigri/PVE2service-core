"""Tests for cluster primitives: DB-backed leases and shared rate limiter."""

import time

import pytest
from sqlalchemy import text as sa_text

from PVE2Services.libs import distributed


@pytest.fixture(autouse=True)
def clean_locks(db):
    with db.engine.begin() as conn:
        try:
            conn.execute(sa_text("DELETE FROM instance_locks"))
        except Exception:
            pass
    yield


def test_try_lock_acquires_and_renews():
    assert distributed.try_lock("job:test", ttl_seconds=60) is True
    # Renewal by the same holder succeeds
    assert distributed.try_lock("job:test", ttl_seconds=60) is True
    assert distributed.lock_holder("job:test") == distributed.instance_id()


def test_second_instance_cannot_acquire_fresh_lock():
    original = distributed.instance_id()
    assert distributed.try_lock("job:test", ttl_seconds=60) is True
    # Simulate another instance
    distributed._instance_id = "other:1:abc"
    try:
        assert distributed.try_lock("job:test", ttl_seconds=60) is False
        assert distributed.lock_holder("job:test") == original
    finally:
        distributed._instance_id = None


def test_expired_lock_can_be_taken_over(db):
    distributed.try_lock("job:test", ttl_seconds=60)
    # Force expiry by backdating
    with db.engine.begin() as conn:
        conn.execute(
            sa_text("UPDATE instance_locks SET expires_at = :e WHERE name = :n"),
            {"e": int(time.time() * 1000) - 1000, "n": "job:test"},
        )
    distributed._instance_id = "other:2:def"
    try:
        assert distributed.try_lock("job:test", ttl_seconds=60) is True
        assert distributed.lock_holder("job:test") == "other:2:def"
    finally:
        distributed._instance_id = None


def test_release_lock():
    distributed.try_lock("job:test", ttl_seconds=60)
    distributed.release_lock("job:test")
    assert distributed.lock_holder("job:test") is None
    # Free lock can be acquired by anyone
    distributed._instance_id = "other:3:ghi"
    try:
        assert distributed.try_lock("job:test", ttl_seconds=60) is True
    finally:
        distributed._instance_id = None


def test_scheduler_job_skipped_when_lease_held_elsewhere():
    """The scheduler wrapper must not run the job if another instance leads."""
    calls = []
    from PVE2Services.libs.scheduler import add_job, remove_job

    # Hold the lease as a different instance
    distributed._instance_id = "leader:1:xyz"
    assert distributed.try_lock("job:clustertest", ttl_seconds=300) is True
    distributed._instance_id = None

    add_job("clustertest", lambda: calls.append(1), seconds=300)
    try:
        from PVE2Services.libs.scheduler import _scheduler

        job = _scheduler.get_job("clustertest")
        job.func()  # invoke the wrapped function directly
        assert calls == []
    finally:
        remove_job("clustertest")
        distributed.release_lock("job:clustertest")


def test_scheduler_job_runs_when_we_hold_lease():
    calls = []
    from PVE2Services.libs.scheduler import add_job, remove_job

    add_job("clustertest2", lambda: calls.append(1), seconds=300)
    try:
        assert distributed.try_lock("job:clustertest2", ttl_seconds=300) is True
        from PVE2Services.libs.scheduler import _scheduler

        job = _scheduler.get_job("clustertest2")
        job.func()
        assert calls == [1]
    finally:
        remove_job("clustertest2")


def test_rate_limiter_shared_across_instances():
    """Two limiter objects (as on two replicas) share the same counters."""
    from unittest.mock import MagicMock

    from PVE2Services.core.wiki_service.app.auth import _RateLimiter, reset_all_rate_limiters

    reset_all_rate_limiters()
    a = _RateLimiter(max_attempts=2, window_seconds=60, block_seconds=300)
    b = _RateLimiter(max_attempts=2, window_seconds=60, block_seconds=300)

    req = MagicMock()
    req.headers = {}
    req.client.host = "10.0.0.9"

    assert a.is_blocked(req) is False
    a.record(req)
    b.record(req)
    b.record(req)  # 3rd attempt across "instances" → block
    assert a.is_blocked(req) is True
    assert b.is_blocked(req) is True

    # Reset from one instance clears for both
    a.reset(req)
    assert b.is_blocked(req) is False
    reset_all_rate_limiters()


def test_rate_limiter_block_expires():
    from unittest.mock import MagicMock

    from PVE2Services.core.wiki_service.app.auth import _RateLimiter, reset_all_rate_limiters

    reset_all_rate_limiters()
    lim = _RateLimiter(max_attempts=1, window_seconds=60, block_seconds=1)
    req = MagicMock()
    req.headers = {}
    req.client.host = "10.0.0.10"

    lim.record(req)
    lim.record(req)
    assert lim.is_blocked(req) is True
    time.sleep(1.1)
    assert lim.is_blocked(req) is False
    reset_all_rate_limiters()
