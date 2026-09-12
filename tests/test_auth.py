import pytest
from fastapi.testclient import TestClient

from PVE2Services.core.wiki_service.app.auth import (
    _hash_password,
    _verify_password,
    create_token,
    verify_token,
)
from PVE2Services.core.wiki_service.app.main import app

# -- bcrypt / password hashing tests --

def test_password_hashing_and_verification():
    password = "mySecret123"
    hashed = _hash_password(password)
    assert _verify_password(password, hashed) is True
    assert _verify_password("wrong", hashed) is False


def test_password_hash_is_different_each_time():
    password = "same"
    h1 = _hash_password(password)
    h2 = _hash_password(password)
    assert h1 != h2
    assert _verify_password(password, h1) is True
    assert _verify_password(password, h2) is True


# -- token lifecycle tests --

def test_token_lifecycle():
    token = create_token("alice", expiry_hours=1)
    assert isinstance(token, str)
    assert verify_token(token) == "alice"
    assert verify_token("invalid") is None
    assert verify_token("bad.bad") is None


# -- helper to bootstrap admin account --

def _setup_admin(client: TestClient, username="admin", password="pve2admin"):
    """Create the admin account (first-boot setup). Idempotent."""
    r = client.post("/admin/setup", json={
        "username": username,
        "new_password": password,
    })
    assert r.status_code in (200, 403), r.text


# -- integration / route tests --

def test_login_success_and_protected_routes():
    c = TestClient(app)
    # first-boot setup
    _setup_admin(c)
    r = c.post("/admin/login", json={"username": "admin", "password": "pve2admin"})
    assert r.status_code == 200, r.text
    # Now the client has a session cookie, so /admin/hub should succeed
    r2 = c.get("/admin/hub", follow_redirects=False)
    assert r2.status_code != 302
    assert "/admin/login" not in r2.headers.get("location", "")


def test_login_failure():
    c = TestClient(app)
    _setup_admin(c)
    r = c.post("/admin/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_security_headers_present():
    c = TestClient(app)
    r = c.get("/health")
    assert r.status_code == 200
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("X-XSS-Protection") == "1; mode=block"


def test_settings_page_requires_auth():
    c = TestClient(app)
    r = c.get("/admin/settings", follow_redirects=False)
    # Unauthenticated requests for /admin/* htmlocab.html pages should redirect to /admin/login
    assert r.status_code in (302, 307)
    assert "/admin/login" in r.headers["location"]


@pytest.fixture
def authenticated_client():
    """Return a TestClient with an active session cookie.

    Resets the admin password hash first so the fixture is order-independent
    even after tests that change credentials.
    """
    import bcrypt

    from PVE2Services.libs.db_adapter import DBAdapter

    client = TestClient(app)
    _setup_admin(client)
    default_hash = bcrypt.hashpw(b"pve2admin", bcrypt.gensalt()).decode()
    DBAdapter().set_setting("admin_password_hash", default_hash)
    r = client.post("/admin/login", json={"username": "admin", "password": "pve2admin"})
    assert r.status_code == 200
    return client


def test_change_password(authenticated_client):
    c = authenticated_client
    # Current password must be correct
    r = c.post("/admin/settings/credentials", json={
        "current_password": "guest",
        "new_password": "newpassword123"
    })
    assert r.status_code == 403

    # Use correct current password
    r = c.post("/admin/settings/credentials", json={
        "current_password": "pve2admin",
        "new_password": "newpassword123"
    })
    assert r.status_code == 200
    assert "updated" in r.json().get("message", "").lower() or "success" in r.json().get("message", "").lower()

    # Logout and re-login with new password
    c.post("/admin/logout")
    r = c.post("/admin/login", json={"username": "admin", "password": "newpassword123"})
    assert r.status_code == 200


# -- CSRF / origin validation tests --

def test_cross_origin_post_rejected(authenticated_client):
    """A forged cross-site POST (different Origin) must be rejected with 403."""
    r = authenticated_client.post(
        "/pages",
        json={"title": "evil", "content": "x"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert r.status_code == 403


def test_cross_origin_referer_rejected(authenticated_client):
    r = authenticated_client.post(
        "/pages",
        json={"title": "evil", "content": "x"},
        headers={"Referer": "https://evil.example.com/page"},
    )
    assert r.status_code == 403


def test_same_origin_post_allowed(authenticated_client):
    host = authenticated_client.base_url.netloc.decode()
    r = authenticated_client.post(
        "/pages",
        json={"title": "ok", "content": "x"},
        headers={"Origin": f"http://{host}"},
    )
    assert r.status_code == 201


def test_no_origin_header_post_allowed(authenticated_client):
    """Non-browser clients (curl/service calls) send no Origin — allowed."""
    r = authenticated_client.post("/pages", json={"title": "api", "content": "x"})
    assert r.status_code == 201
