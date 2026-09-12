"""Tests for the audit logging system (libs/audit.py + /admin/log routes)."""

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from PVE2Services.libs.audit import AuditLogger, redact


@pytest.fixture()
def audit_logger(tmp_path):
    """A fresh AuditLogger pointed at a throwaway directory."""
    logger = AuditLogger(log_dir=tmp_path)
    yield logger
    logger.reset()


def _read_logs(log_dir: Path) -> str:
    out = ""
    for f in sorted(log_dir.iterdir()):
        if f.suffix == ".txt":
            out += f.read_text(encoding="utf-8")
    return out


def test_log_writes_daily_file(audit_logger, tmp_path):
    audit_logger.log("testplugin", "INFO", "Hello world")
    content = _read_logs(tmp_path)
    assert "[testplugin] INFO Hello world" in content
    assert "timestamp:" in content


def test_severity_casing_and_invalid(audit_logger, tmp_path):
    audit_logger.log("p", "warning", "Lowercase severity")
    audit_logger.log("p", "BOGUS", "Invalid severity")
    content = _read_logs(tmp_path)
    assert "[p] WARNING Lowercase severity" in content
    assert "[p] INFO Invalid severity" in content


def test_redact_masks_values(audit_logger, tmp_path):
    audit_logger.log(
        "p", "WARNING", "Setup failed",
        detail=f"proxmox_token: {redact('super-secret-token')}\n"
               f"api_key: {redact('abc')}",
    )
    content = _read_logs(tmp_path)
    # Redacted values should never contain the full secret
    assert "super-secret-token" not in content
    assert "api_key: ****" in content


def test_operation_nesting(audit_logger, tmp_path):
    with audit_logger.operation("p", "OK", "Outer op"):
        audit_logger.log("p", "INFO", "Inner op")
        with audit_logger.operation("p", "INFO", "Deep op"):
            audit_logger.log("p", "INFO", "Leaf op")
    content = _read_logs(tmp_path)
    lines = content.splitlines()
    outer = next(i for i, line in enumerate(lines) if "Outer op" in line)
    inner = next(i for i, line in enumerate(lines) if "Inner op" in line)
    deep = next(i for i, line in enumerate(lines) if "Deep op" in line)
    leaf = next(i for i, line in enumerate(lines) if "Leaf op" in line)
    assert outer < inner < deep < leaf
    assert lines[inner].startswith("  ")  # indented once
    assert lines[leaf].startswith("    ")  # indented twice


def test_list_files_and_get_content(audit_logger, tmp_path):
    audit_logger.log("p", "INFO", "Entry")
    files = audit_logger.list_files()
    assert len(files) >= 1
    name = files[0]["name"]
    assert re.match(r"^audit-\d{4}-\d{2}-\d{2}\.txt$", name)
    content = audit_logger.get_file_content(name)
    assert "Entry" in content
    assert audit_logger.get_file_content("../../etc/passwd") is None


def test_invalid_filename_rejected(audit_logger):
    assert audit_logger.get_file_content("not-a-log.txt") is None
    assert audit_logger.get_file_content("audit-foo.txt") is None


def test_retention_purge(audit_logger, tmp_path):
    today = datetime.now().date()
    old = today - timedelta(days=40)
    (tmp_path / f"audit-{old.isoformat()}.txt").write_text("old", encoding="utf-8")
    (tmp_path / f"audit-{today.isoformat()}.txt").write_text("new", encoding="utf-8")
    audit_logger.purge(retention_days=30)
    remaining = {f.name for f in tmp_path.iterdir()}
    assert f"audit-{old.isoformat()}.txt" not in remaining
    assert f"audit-{today.isoformat()}.txt" in remaining


def test_secret_keys_masked_automatically(audit_logger, tmp_path):
    audit_logger.log("p", "WARNING", "Login failed",
                     detail="username: admin\npassword: hunter2")
    content = _read_logs(tmp_path)
    assert "hunter2" not in content
    assert "password: hu***r2" in content
    assert "username: admin" in content


def test_log_router_files_requires_auth(authenticated_client, tmp_path):
    r = authenticated_client.get("/admin/log/files")
    assert r.status_code == 200
    data = r.json()
    assert "files" in data
    assert "retention_days" in data


def test_log_router_unauth_redirect(client):
    # /admin/log/files is not under /api/, so unauthenticated access redirects
    # to the login page instead of returning JSON 401.
    r = client.get("/admin/log/files")
    assert "Login" in r.text or r.status_code in (401, 307)
    assert "audit" not in r.text.lower() or "login" in r.text.lower()


def test_retention_set_endpoint(authenticated_client):
    r = authenticated_client.post("/admin/log/retention?days=45")
    assert r.status_code == 200
    assert r.json()["retention_days"] == 45


def test_log_page_requires_auth(client):
    # Unauthenticated /admin/log redirects to the login page.
    r = client.get("/admin/log")
    assert r.status_code in (401, 307) or "Login" in r.text
