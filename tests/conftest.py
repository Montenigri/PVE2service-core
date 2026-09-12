"""Shared pytest configuration.

The repo root contains both the ``PVE2Services/`` import package and the
``tools/`` package, so putting the repo root on ``sys.path`` makes
``PVE2Services.*`` and ``tools.*`` importable from the test-suite.
"""

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Use a dedicated test database file (cleaned up between runs).
# An explicit PVE2_DB_URL (e.g. set by CI for the Postgres leg) is respected.
if not os.environ.get("PVE2_DB_URL"):
    _test_db = str(ROOT / "pve2_test.db")
    os.environ["PVE2_DB_URL"] = f"sqlite:///{_test_db}"
    # Clean up old test DB if it exists
    if os.path.exists(_test_db):
        os.remove(_test_db)

# TestClient uses http, so secure cookies must be disabled
os.environ.setdefault("PVE2_SESSION_SECURE", "false")


@pytest.fixture(autouse=True)
def reset_rate_limiter(tmp_path):
    """Reset rate limiter state before each test (DB-backed now)."""
    from PVE2Services.core.wiki_service.app.auth import reset_all_rate_limiters

    # Point the audit logger at a throwaway temp dir so tests never write
    # into the repo's real `logs/` directory.
    try:
        from PVE2Services.libs.audit import audit as _audit
        _audit.configure(log_dir=tmp_path / "audit_logs")
        _audit.reset()
    except ImportError:
        pass

    reset_all_rate_limiters()
    yield
    reset_all_rate_limiters()


@pytest.fixture(scope="session")
def db():
    """Return a DBAdapter instance for the test database."""
    from PVE2Services.libs.db_adapter import DBAdapter
    adapter = DBAdapter()
    yield adapter


@pytest.fixture(scope="session")
def client():
    """Return a TestClient for the FastAPI app."""
    from PVE2Services.core.wiki_service.app.main import app
    return TestClient(app)


@pytest.fixture(scope="session")
def authenticated_client():
    """Return a TestClient with an active session cookie."""
    import bcrypt

    from PVE2Services.core.wiki_service.app.main import app
    from PVE2Services.libs.db_adapter import DBAdapter

    c = TestClient(app)

    # Create admin account
    c.post("/admin/setup", json={"username": "admin", "new_password": "pve2admin"})

    # Reset password to ensure deterministic state
    db_adapter = DBAdapter()
    default_hash = bcrypt.hashpw(b"pve2admin", bcrypt.gensalt()).decode()
    db_adapter.set_setting("admin_password_hash", default_hash)

    # Login
    c.post("/admin/login", json={"username": "admin", "password": "pve2admin"})
    return c
