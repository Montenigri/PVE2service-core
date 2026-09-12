"""Authentication system — JWT-like tokens via stdlib HMAC-SHA256 + bcrypt password hashing."""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import bcrypt
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text

from .templates_renderer import read_text

logger = logging.getLogger("pve2.auth")

try:
    from PVE2Services.libs.audit import audit as _audit
except ImportError:
    _audit = None

_SECRET_FILE = os.getenv("PVE2_SECRET_FILE", ".pve2_secret")
_SESSION_SECURE = os.getenv("PVE2_SESSION_SECURE", "true").lower() in ("1", "true", "yes")
_TRUST_XFF = os.getenv("PVE2_TRUST_XFF", "false").lower() in ("1", "true", "yes")


# --- Rate limiter (DB-backed: shared across replicas, survives restarts) ---
_SCHEMA_RATE_LIMITS = """
CREATE TABLE IF NOT EXISTS rate_limit_events (
    key TEXT NOT NULL,
    ts BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_limit_blocks (
    key TEXT PRIMARY KEY,
    until BIGINT NOT NULL
)
"""


class _RateLimiter:
    """Sliding-window rate limiter persisted in the DB.

    State lives in `rate_limit_events` / `rate_limit_blocks` (epoch-ms ints),
    so all app replicas enforce the same limit and blocks survive restarts.
    """

    def __init__(self, max_attempts: int = 5, window_seconds: int = 60, block_seconds: int = 300):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self.block_duration = block_seconds
        self._db = None

    def _conn(self):
        if self._db is None:
            from PVE2Services.libs.db_adapter import get_db

            self._db = get_db()
            with self._db.engine.begin() as conn:
                for stmt in _SCHEMA_RATE_LIMITS.split(";"):
                    if stmt.strip():
                        conn.execute(text(stmt))
        return self._db.engine

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def is_blocked(self, request: Request) -> bool:
        key = self._key(request)
        now = self._now_ms()
        with self._conn().begin() as conn:
            row = conn.execute(
                text("SELECT until FROM rate_limit_blocks WHERE key = :k"), {"k": key}
            ).fetchone()
            if row and row[0] > now:
                return True
            if row:
                conn.execute(text("DELETE FROM rate_limit_blocks WHERE key = :k"), {"k": key})
        return False

    def record(self, request: Request):
        key = self._key(request)
        now = self._now_ms()
        with self._conn().begin() as conn:
            conn.execute(
                text("INSERT INTO rate_limit_events (key, ts) VALUES (:k, :t)"),
                {"k": key, "t": now},
            )
            conn.execute(
                text("DELETE FROM rate_limit_events WHERE key = :k AND ts < :cutoff"),
                {"k": key, "cutoff": now - self.window * 1000},
            )
            count = conn.execute(
                text("SELECT COUNT(*) FROM rate_limit_events WHERE key = :k"), {"k": key}
            ).scalar()
            if count > self.max_attempts:
                conn.execute(
                    text("""
                        INSERT INTO rate_limit_blocks (key, until) VALUES (:k, :u)
                        ON CONFLICT(key) DO UPDATE SET until = excluded.until
                    """),
                    {"k": key, "u": now + self.block_duration * 1000},
                )
                conn.execute(
                    text("DELETE FROM rate_limit_events WHERE key = :k"), {"k": key}
                )

    def reset(self, request: Request):
        key = self._key(request)
        with self._conn().begin() as conn:
            conn.execute(text("DELETE FROM rate_limit_events WHERE key = :k"), {"k": key})
            conn.execute(text("DELETE FROM rate_limit_blocks WHERE key = :k"), {"k": key})

    def _key(self, request: Request) -> str:
        if _TRUST_XFF:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"


def reset_all_rate_limiters():
    """Clear all rate-limit state (used between tests)."""
    from PVE2Services.libs.db_adapter import get_db

    engine = get_db().engine
    with engine.begin() as conn:
        for table in ("rate_limit_events", "rate_limit_blocks"):
            try:
                conn.execute(text(f"DELETE FROM {table}"))
            except Exception:
                pass


_login_limiter = _RateLimiter(max_attempts=5, window_seconds=60, block_seconds=300)
_creds_limiter = _RateLimiter(max_attempts=5, window_seconds=60, block_seconds=300)

# --- Dummy hash for constant-time comparison ---
_dummy_secret = secrets.token_bytes(32).replace(b"\x00", b"\x01")
_DUMMY_HASH = bcrypt.hashpw(_dummy_secret, bcrypt.gensalt()).decode()


def _load_or_create_secret() -> str:
    if os.path.exists(_SECRET_FILE):
        with open(_SECRET_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    secret = base64.b64encode(secrets.token_bytes(32)).decode()
    try:
        with open(_SECRET_FILE, "w", encoding="utf-8") as f:
            f.write(secret)
        os.chmod(_SECRET_FILE, 0o600)
    except OSError:
        logger.warning("Cannot write secret to %s; sessions will not persist across restarts", _SECRET_FILE)
    return secret


_SECRET = _load_or_create_secret()


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except (ValueError, AttributeError):
        return False


def _get_credentials(db=None):
    """Return (username, password_hash, is_set). If no password is set, hash is None."""
    if db:
        try:
            user = db.get_setting("admin_user")
            stored = db.get_setting("admin_password_hash")
            if stored:
                return user, stored, True
            return user, None, False
        except Exception:
            pass
    return None, None, False


def create_token(username: str, expiry_hours: int = 24) -> str:
    expiry = (datetime.now(timezone.utc) + timedelta(hours=expiry_hours)).isoformat()
    payload = json.dumps({"u": username, "e": expiry}, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_token(token: str) -> Optional[str]:
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None
        payload_b64, sig = parts
        expected = hmac.new(_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        pad = 4 - len(payload_b64) % 4
        if pad != 4:
            payload_b64 += "=" * pad
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if datetime.fromisoformat(payload["e"]) < datetime.now(timezone.utc):
            return None
        return payload["u"]
    except Exception:
        return None


def _generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _verify_csrf(request: Request) -> bool:
    header_token = request.headers.get("x-csrf-token")
    cookie_token = request.cookies.get("pve2_csrf")
    if not header_token or not cookie_token:
        return False
    return hmac.compare_digest(header_token, cookie_token)


def _migrate_legacy_hash(db, password: str, stored_hash: str) -> str:
    """Upgrade a legacy SHA-256 hash to bcrypt on successful login."""
    if stored_hash and not stored_hash.startswith("$2b$"):
        if hashlib.sha256(password.encode()).hexdigest() == stored_hash:
            new_hash = _hash_password(password)
            db.set_setting("admin_password_hash", new_hash)
            logger.info("Migrated legacy password hash to bcrypt")
            return new_hash
    return stored_hash


_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _same_origin(request: Request) -> bool:
    """CSRF defense: mutating browser requests must originate from this host.

    Browsers always send Origin (or Referer) on cross-site requests, so a
    mismatch proves the request was forged elsewhere. Non-browser clients
    (curl, service calls) send neither and are allowed through — they are not
    CSRF vectors. Complements the SameSite=lax session cookie.
    """
    host = request.headers.get("host", "")
    origin = request.headers.get("origin")
    if origin:
        return urlparse(origin).netloc == host
    referer = request.headers.get("referer")
    if referer:
        return urlparse(referer).netloc == host
    return True


def load_auth(app: FastAPI):
    from PVE2Services.libs.db_adapter import DBAdapter

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        # Exact match for exempt paths (first-boot setup is also exempt)
        if path in ("/health", "/admin/login", "/admin/setup"):
            return await call_next(request)
        # Drift ingestion authenticates via its own API key (validated inside
        # the endpoint), not via the admin session cookie.
        if path == "/api/v1/state/ingest":
            return await call_next(request)
        is_api = path.startswith("/api/")
        token = request.cookies.get("pve2_session")
        if not token:
            if is_api:
                return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
            return RedirectResponse(url="/admin/login")
        username = verify_token(token)
        if not username:
            if is_api:
                return JSONResponse(status_code=401, content={"detail": "Session expired"})
            response = RedirectResponse(url="/admin/login")
            response.delete_cookie("pve2_session")
            return response
        if request.method in _MUTATING_METHODS and not _same_origin(request):
            return JSONResponse(status_code=403, content={"detail": "Cross-origin request rejected"})
        return await call_next(request)

    @app.get("/admin/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        token = request.cookies.get("pve2_session")
        if token and verify_token(token):
            return RedirectResponse(url="/admin/hub")
        return HTMLResponse(content=read_text("login.html"))

    @app.post("/admin/login")
    async def do_login(request: Request):
        if _login_limiter.is_blocked(request):
            return JSONResponse(status_code=429, content={"detail": "Too many requests. Please try again later."})

        body = await request.json()
        username = body.get("username", "")
        password = body.get("password", "")
        db = DBAdapter()
        expected_user, expected_hash, is_set = _get_credentials(db)

        hash_to_check = expected_hash if expected_hash else _DUMMY_HASH
        valid = False
        if username and username == expected_user:
            if _verify_password(password, hash_to_check):
                valid = True
            else:
                migrated = _migrate_legacy_hash(db, password, expected_hash)
                if migrated != expected_hash and _verify_password(password, migrated):
                    valid = True

        if not valid:
            _verify_password(password, _DUMMY_HASH)

        if valid:
            _login_limiter.reset(request)
            token = create_token(username)
            if _audit:
                try:
                    _audit.log("auth", "OK", "Login successful",
                               detail=f"username: {username}")
                except Exception:
                    pass
            response = JSONResponse(content={"ok": True, "message": "Login successful"})
            response.set_cookie(
                key="pve2_session", value=token,
                httponly=True, samesite="lax", secure=_SESSION_SECURE,
                max_age=86400,
            )
            csrf_token = _generate_csrf_token()
            response.set_cookie(
                key="pve2_csrf", value=csrf_token,
                httponly=False, samesite="lax", secure=_SESSION_SECURE,
                max_age=86400,
            )
            return response

        _login_limiter.record(request)
        if _audit:
            try:
                _audit.log("auth", "WARNING", "Login failed",
                           detail=f"username: {username}")
            except Exception:
                pass
        return JSONResponse(status_code=401, content={"detail": "Invalid credentials"})

    @app.post("/admin/logout")
    async def do_logout():
        if _audit:
            try:
                _audit.log("auth", "INFO", "Logout")
            except Exception:
                pass
        response = JSONResponse(content={"ok": True})
        response.delete_cookie("pve2_session")
        response.delete_cookie("pve2_csrf")
        return response

    @app.post("/admin/setup")
    async def do_setup(request: Request):
        """First-boot setup — create admin account. No auth required."""
        db = DBAdapter()
        expected_user, expected_hash, is_set = _get_credentials(db)
        if is_set:
            return JSONResponse(status_code=403, content={"detail": "Setup already completed"})
        body = await request.json()
        username = body.get("username", "admin")
        new_pass = body.get("new_password", "")
        if len(new_pass) < 8:
            return JSONResponse(status_code=400, content={"detail": "Password must be at least 8 characters"})
        db.set_setting("admin_user", username)
        db.set_setting("admin_password_hash", _hash_password(new_pass))
        if _audit:
            try:
                _audit.log("auth", "CRITICAL", "Admin setup completed",
                           detail=f"username: {username}")
            except Exception:
                pass
        return {"ok": True, "message": "Setup completed successfully"}

    @app.get("/admin/settings", response_class=HTMLResponse)
    async def settings_page():
        return HTMLResponse(content=read_text("settings.html"))

    @app.post("/admin/settings/credentials")
    async def change_credentials(request: Request):
        if _creds_limiter.is_blocked(request):
            return JSONResponse(status_code=429, content={"detail": "Too many requests. Please try again later."})

        body = await request.json()
        db = DBAdapter()
        expected_user, expected_hash, is_set = _get_credentials(db)

        # If no password is set (first boot), allow setting it without current_password
        if not is_set:
            new_pass = body.get("new_password")
            if not new_pass or len(new_pass) < 8:
                return JSONResponse(status_code=400, content={"detail": "Password must be at least 8 characters"})
            db.set_setting("admin_password_hash", _hash_password(new_pass))
            username = body.get("username") or "admin"
            db.set_setting("admin_user", username)
            if _audit:
                try:
                    _audit.log("auth", "CRITICAL", "Admin credentials set",
                               detail=f"username: {username}")
                except Exception:
                    pass
            return {"ok": True, "message": "Credentials set successfully"}

        current = body.get("current_password", "")
        hash_to_check = expected_hash if expected_hash else _DUMMY_HASH
        if not _verify_password(current, hash_to_check):
            _creds_limiter.record(request)
            if _audit:
                try:
                    _audit.log("auth", "WARNING", "Credential change failed",
                               detail="Invalid current password")
                except Exception:
                    pass
            return JSONResponse(status_code=403, content={"detail": "Current password is invalid"})

        new_user = body.get("username")
        new_pass = body.get("new_password")

        if new_user:
            db.set_setting("admin_user", new_user)
        if new_pass:
            if len(new_pass) < 8:
                _creds_limiter.record(request)
                if _audit:
                    try:
                        _audit.log("auth", "WARNING", "Credential change failed",
                                   detail="New password too short")
                    except Exception:
                        pass
                return JSONResponse(status_code=400, content={"detail": "Password must be at least 8 characters"})
            db.set_setting("admin_password_hash", _hash_password(new_pass))

        _creds_limiter.reset(request)
        if _audit:
            try:
                _audit.log("auth", "CRITICAL", "Admin credentials updated",
                           detail=f"username: {new_user or expected_user}\n"
                                  f"password_changed: {bool(new_pass)}")
            except Exception:
                pass
        return {"ok": True, "message": "Credentials updated successfully"}
