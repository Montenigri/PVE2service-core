import bcrypt
import pytest
from fastapi.testclient import TestClient

from PVE2Services.core.wiki_service.app.auth import reset_all_rate_limiters
from PVE2Services.core.wiki_service.app.main import app
from PVE2Services.libs.db_adapter import DBAdapter


@pytest.fixture
def authed():
    """Create a fresh authenticated TestClient with clean DB state."""
    db = DBAdapter()
    db.set_setting("admin_user", "admin")
    default_hash = bcrypt.hashpw(b"pve2admin", bcrypt.gensalt()).decode()
    db.set_setting("admin_password_hash", default_hash)
    reset_all_rate_limiters()
    c = TestClient(app)
    r = c.post("/admin/login", json={"username": "admin", "password": "pve2admin"})
    assert r.status_code == 200, r.text
    return c


def test_health():
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert body["database"] in ("ok", "error")


def test_create_and_get_page(authed):
    payload = {"title": "Hello", "content": "World"}
    r = authed.post("/pages", json=payload)
    assert r.status_code == 201
    page = r.json()
    assert page["title"] == "Hello"

    r2 = authed.get(f"/pages/{page['id']}")
    assert r2.status_code == 200
    assert r2.json()["content"] == "World"


def test_db_endpoints(authed):
    r = authed.get("/db/settings")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)

    for endpoint in ("/db/storage_history", "/db/health_history", "/db/sync_state", "/db/changelog"):
        r2 = authed.get(endpoint)
        assert r2.status_code == 200
        assert isinstance(r2.json(), list)

    r6 = authed.get("/db/templates")
    assert r6.status_code == 200
    assert isinstance(r6.json(), dict)


def test_sync_run_creates_pages(authed):
    r = authed.post("/sync/run")
    assert r.status_code == 200
    res = r.json()
    assert "processed" in res and "created" in res
    r2 = authed.get("/pages")
    assert r2.status_code == 200
    pages = r2.json()
    assert isinstance(pages, list)
    assert len(pages) >= res["created"]
