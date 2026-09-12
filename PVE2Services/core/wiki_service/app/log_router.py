"""Audit log viewer — list, view, and download daily audit log files.

The audit system writes human-readable .txt files (one per day) to the
configured log directory. This router exposes:

- GET  /admin/log            the Logs admin page
- GET  /admin/log/files      JSON list of available log files
- GET  /admin/log/file       raw content of a specific log file
- GET  /admin/log/retention  current retention config
- POST /admin/log/retention  set retention days
- GET  /admin/log/download   download a log file

All routes require an authenticated session (handled by the auth middleware).
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

try:
    from PVE2Services.libs.audit import audit as _audit
except ImportError:
    _audit = None

from .templates_renderer import render

router = APIRouter()


def _retention_days() -> int:
    """Read the retention setting from the DB, falling back to the default."""
    try:
        from PVE2Services.libs.db_adapter import DBAdapter

        value = DBAdapter().get_setting("audit_retention_days")
        if value:
            return max(1, int(value))
    except Exception:
        pass
    return 30


@router.get("/admin/log", response_class=HTMLResponse)
async def log_page():
    return HTMLResponse(content=render("log.html"))


@router.get("/admin/log/files")
async def log_files():
    if _audit is None:
        raise HTTPException(status_code=500, detail="Audit module unavailable")
    return {"files": _audit.list_files(), "retention_days": _retention_days()}


@router.get("/admin/log/file")
async def log_file(
    name: str = Query(..., description="Log filename, e.g. audit-2026-09-02.txt"),
):
    if _audit is None:
        raise HTTPException(status_code=500, detail="Audit module unavailable")
    path = Path(name).name
    if path != name:
        raise HTTPException(status_code=400, detail="Invalid log filename")
    content = _audit.get_file_content(path)
    if content is None:
        raise HTTPException(status_code=404, detail="Log file not found")
    return PlainTextResponse(content=content)


@router.get("/admin/log/download")
async def log_download(name: str = Query(...)):
    if _audit is None:
        raise HTTPException(status_code=500, detail="Audit module unavailable")
    path = Path(name).name
    if path != name:
        raise HTTPException(status_code=400, detail="Invalid log filename")
    content = _audit.get_file_content(path)
    if content is None:
        raise HTTPException(status_code=404, detail="Log file not found")
    return PlainTextResponse(
        content=content,
        headers={"Content-Disposition": f'attachment; filename="{path}"'},
    )


@router.get("/admin/log/retention")
async def log_retention():
    return {"retention_days": _retention_days()}


@router.post("/admin/log/retention")
async def log_retention_set(days: int = Query(..., ge=1, le=3650)):
    try:
        from PVE2Services.libs.db_adapter import DBAdapter

        DBAdapter().set_setting("audit_retention_days", str(days))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save retention: {e}") from e
    if _audit is not None:
        try:
            _audit.log("log_viewer", "INFO", "Audit retention changed",
                       detail=f"retention_days: {days}")
        except Exception:
            pass
    return {"retention_days": days}
