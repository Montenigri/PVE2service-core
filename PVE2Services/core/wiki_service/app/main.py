import asyncio
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from sqlalchemy import text

from PVE2Services.libs.db_adapter import DBAdapter
from PVE2Services.libs.errors import register_error_handlers
from PVE2Services.libs.plugin_loader import CORE_VERSION, load_all_plugins
from PVE2Services.libs.scheduler import (
    reschedule_job,
    scheduler_status,
    start_scheduler,
    stop_scheduler,
)
from PVE2Services.libs.sync_engine import Engine
from PVE2Services.libs.wiki_adapter import WikiAdapter

from .auth import load_auth
from .db import (
    get_changelog,
    get_health_history,
    get_settings,
    get_storage_history,
    get_sync_state,
    get_templates,
)
from .hub import load_hub, set_loaded_plugins
from .log_router import router as log_router
from .proxmox_adapter import create_client_if_possible
from .store import PageCreate, create_page, get_page, list_pages
from .sync import run_sync

try:
    from PVE2Services.libs.audit import audit as _audit
except ImportError:
    _audit = None

load_dotenv()

logger = logging.getLogger("wiki_service")
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

_BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal", "100.100.100.200"}

# Default: the bundled plugins/ dir inside this checkout (monolithic mode).
# Override with PVE2_PLUGINS_DIR to load plugins from a separate checkout —
# useful when core and plugins live in different repos (see
# ready_to_publish.md, "Separazione core/plugin").
_custom_plugins_dir = os.environ.get("PVE2_PLUGINS_DIR", "").strip()
PLUGINS_DIR = (
    Path(_custom_plugins_dir).expanduser().resolve()
    if _custom_plugins_dir
    else Path(__file__).resolve().parents[3] / "plugins"
)

# Load order hint for the hub UI. Plugins discovered in plugins/ are loaded in
# this order (dir name lowercased); anything unknown is appended, sorted.
PLUGIN_ORDER = [
    "pve2core", "pve2dash", "pve2audit", "pve2dns",
    "pve2notify", "pve2nut", "pve2proxy", "pve2power", "pve2drift", "pve2wiki",
]


def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.hostname in _BLOCKED_HOSTS:
            return False
        if parsed.hostname and parsed.hostname.startswith("169.254."):
            return False
        return parsed.scheme in ("https", "http")
    except Exception:
        return False


app = FastAPI(title="PVE2 Wiki Service", version=CORE_VERSION)

register_error_handlers(app)

@app.middleware("http")
async def add_security_headers(request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

load_auth(app)
load_hub(app)
app.include_router(log_router)


@app.get("/health")
async def health():
    """Liveness + DB reachability. Unauthenticated and cheap (no Proxmox call)."""
    db_ok = True
    try:
        db = DBAdapter()
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "error",
    }


@app.get("/pages")
async def api_list_pages():
    return list_pages()


@app.post("/pages", status_code=201)
async def api_create_page(payload: PageCreate):
    return create_page(payload.title, payload.content)


@app.get("/pages/{page_id}")
async def api_get_page(page_id: int):
    page = get_page(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Not found")
    return page


# Load plugins via the manifest-validating loader (see libs/plugin_loader.py).
#
# Order of operations at import time:
#   1. plugin-store sync (optional, no-op unless PVE2_PLUGIN_STORE_URL is set)
#   2. manifest validation + trust-tier derivation
#   3. plugin import + load_plugin(app)
#
# Neither step can be fatal: store problems are logged, plugin failures are
# logged — the core always boots, matching the previous behaviour.
try:
    from PVE2Services.libs.plugin_store import sync_installed_plugins

    _sync_report = sync_installed_plugins(PLUGINS_DIR)
    if _sync_report.get("installed"):
        logger.info("Plugin store: installed %s", ", ".join(_sync_report["installed"]))
    if _sync_report.get("errors"):
        logger.warning("Plugin store: %d error(s) during sync (see log)", len(_sync_report["errors"]))
except Exception as e:  # noqa: BLE001 — store problems must never block boot
    logger.exception("Plugin store sync failed (continuing without it): %s", e)

_loaded_plugin_ids: list[str] = []
try:
    _loaded_plugin_ids, _plugin_registry = load_all_plugins(
        app, PLUGINS_DIR, core_version=CORE_VERSION, plugin_order=PLUGIN_ORDER
    )
except Exception:
    logger.exception("Fatal error in plugin loading stage — continuing")
    _loaded_plugin_ids, _plugin_registry = [], None  # type: ignore[assignment]

set_loaded_plugins(_loaded_plugin_ids)


@app.get("/nodes")
async def api_nodes():
    """Return nodes from Proxmox using DB-stored credentials."""
    client = await create_client_if_possible(None, None, None)
    if not client:
        return []
    try:
        return await client.get_nodes()
    finally:
        await client.close()


@app.get("/vms")
async def api_vms():
    client = await create_client_if_possible(None, None, None)
    if not client:
        return []
    try:
        vms = await client.get_vms()
        lxcs = await client.get_lxcs()
        return {"vms": vms, "lxcs": lxcs}
    finally:
        await client.close()


@app.get("/storage")
async def api_storage():
    client = await create_client_if_possible(None, None, None)
    if not client:
        return []
    try:
        storage = await client.get_storage()
        disks = await client.get_disks()
        return {"storage": storage, "disks": disks}
    finally:
        await client.close()


@app.get("/cluster/status")
async def api_cluster_status():
    client = await create_client_if_possible(None, None, None)
    if not client:
        return []
    try:
        return await client.get_cluster_status()
    finally:
        await client.close()


@app.get("/db/settings")
async def api_db_settings():
    try:
        return get_settings()
    except FileNotFoundError:
        return {}


@app.get("/db/storage_history")
async def api_db_storage_history(limit: int = 50):
    try:
        return get_storage_history(limit)
    except FileNotFoundError:
        return []


@app.get("/db/health_history")
async def api_db_health_history(limit: int = 50):
    try:
        return get_health_history(limit)
    except FileNotFoundError:
        return []


@app.get("/db/sync_state")
async def api_db_sync_state(limit: int = 50):
    try:
        return get_sync_state(limit)
    except FileNotFoundError:
        return []


@app.get("/db/changelog")
async def api_db_changelog(limit: int = 50):
    try:
        return get_changelog(limit)
    except FileNotFoundError:
        return []


@app.get("/db/templates")
async def api_db_templates():
    try:
        return get_templates()
    except FileNotFoundError:
        return {}


@app.post("/sync/run")
async def api_sync_run(limit: int = 50):
    """Run sync from DB `sync_state` and create pages."""
    return run_sync(limit)
@app.post("/sync/run/full")
async def api_sync_run_full(limit: int = 50):
    """Run the full sync engine: fetch from Proxmox and update wiki + DB."""
    try:
        db = DBAdapter()
    except FileNotFoundError:
        return {"error": "DB not found"}

    settings = get_settings()
    proxmox_url = settings.get("proxmox_url", "")
    if proxmox_url and not _is_safe_url(proxmox_url):
        return {"error": "Proxmox URL is not allowed (SSRF protection)"}
    client = await create_client_if_possible(
        proxmox_url,
        settings.get("proxmox_token_id"),
        settings.get("proxmox_secret"),
        settings.get("proxmox_insecure", "false").lower() in ("1", "true", "yes"),
    )

    engine = Engine(db, pve_client=client, wiki=WikiAdapter())
    try:
        result = await engine.run()
        if _audit:
            try:
                _audit.log("sync", "INFO", "Full sync completed",
                           detail=f"processed: {result.get('processed', 0)}\n"
                                  f"created: {result.get('created', 0)}\n"
                                  f"updated: {result.get('updated', 0)}")
            except Exception:
                pass
        return result
    finally:
        if client:
            await client.close()


# --- Periodic sync scheduler (configurable cadence, persisted across restarts) ---
MIN_SYNC_INTERVAL_SECONDS = 30
MAX_SYNC_INTERVAL_SECONDS = 86400
DEFAULT_SYNC_INTERVAL_SECONDS = 300


async def _build_sync_job():
    """Create the periodic Proxmox -> DB -> wiki sync callable."""
    db = DBAdapter()
    settings = get_settings()
    proxmox_url = settings.get("proxmox_url", "")
    if proxmox_url and not _is_safe_url(proxmox_url):
        raise ValueError("Proxmox URL is not allowed (SSRF protection)")
    client = await create_client_if_possible(
        proxmox_url,
        settings.get("proxmox_token_id"),
        settings.get("proxmox_secret"),
        settings.get("proxmox_insecure", "false").lower() in ("1", "true", "yes"),
    )
    engine = Engine(db, pve_client=client, wiki=WikiAdapter())

    def _sync_job():
        try:
            asyncio.run(engine.run())
        except Exception:
            logger.exception("Background sync failed")

    return _sync_job


def _sync_interval_setting(db: "DBAdapter") -> int:
    try:
        return int(db.get_setting("core_sync_interval_seconds") or DEFAULT_SYNC_INTERVAL_SECONDS)
    except (TypeError, ValueError):
        return DEFAULT_SYNC_INTERVAL_SECONDS


async def _apply_schedule(interval_seconds: int, enable: bool | None = None) -> dict:
    """Persist the interval; reschedule live when the job should be running."""
    if not (MIN_SYNC_INTERVAL_SECONDS <= interval_seconds <= MAX_SYNC_INTERVAL_SECONDS):
        return {
            "error": f"Interval must be between {MIN_SYNC_INTERVAL_SECONDS} "
                     f"and {MAX_SYNC_INTERVAL_SECONDS} seconds"
        }
    try:
        db = DBAdapter()
    except FileNotFoundError:
        return {"error": "DB not found"}

    db.set_setting("core_sync_interval_seconds", str(interval_seconds))
    result: dict = {"interval_seconds": interval_seconds, "running": False}

    job_running = any(j.get("id") == "pve2_sync" for j in scheduler_status().get("jobs", []))
    enabled_setting = (db.get_setting("core_sync_enabled") or "false").lower() == "true"
    should_run = enable if enable is not None else (enabled_setting or job_running)
    if should_run:
        try:
            job = await _build_sync_job()
        except ValueError as e:
            return {"error": str(e)}
        result["running"] = reschedule_job("pve2_sync", job, seconds=interval_seconds)
    return result


@app.post("/admin/scheduler/start")
async def admin_start_scheduler(interval_seconds: int = DEFAULT_SYNC_INTERVAL_SECONDS):
    result = await _apply_schedule(interval_seconds, enable=True)
    if "error" not in result:
        DBAdapter().set_setting("core_sync_enabled", "true")
        result["enabled"] = True
        if _audit:
            try:
                _audit.log("scheduler", "INFO", "Sync scheduler started",
                           detail=f"interval_seconds: {interval_seconds}")
            except Exception:
                pass
    return result


@app.post("/admin/scheduler/stop")
async def admin_stop_scheduler():
    stopped = stop_scheduler()
    try:
        DBAdapter().set_setting("core_sync_enabled", "false")
    except Exception:
        logger.exception("Could not persist scheduler state")
    if stopped and _audit:
        try:
            _audit.log("scheduler", "INFO", "Sync scheduler stopped")
        except Exception:
            pass
    return {"stopped": stopped}


@app.post("/admin/scheduler/interval")
async def admin_set_scheduler_interval(interval_seconds: int):
    """Change the sync cadence; applies live when the scheduler is running."""
    result = await _apply_schedule(interval_seconds)
    if "error" not in result and _audit:
        try:
            _audit.log("scheduler", "INFO", "Sync scheduler interval changed",
                       detail=f"interval_seconds: {interval_seconds}")
        except Exception:
            pass
    return result


@app.get("/admin/scheduler/status")
async def admin_scheduler_status():
    st = scheduler_status()
    # "running" here means the pve2_sync JOB exists — the APScheduler process
    # itself stays alive after the first start even with zero jobs.
    st["running"] = any(j.get("id") == "pve2_sync" for j in st.get("jobs", []))
    try:
        db = DBAdapter()
        st["interval_seconds"] = _sync_interval_setting(db)
        st["enabled"] = (db.get_setting("core_sync_enabled") or "false").lower() == "true"
    except FileNotFoundError:
        st["interval_seconds"] = DEFAULT_SYNC_INTERVAL_SECONDS
        st["enabled"] = False
    return st


@app.on_event("startup")
async def restore_sync_schedule():
    """Resume the periodic sync after a restart when it was left enabled."""
    try:
        db = DBAdapter()
    except Exception:
        return
    if _audit is not None:
        try:
            retention = db.get_setting("audit_retention_days")
            _audit.configure(
                retention_days=max(1, int(retention)) if retention else 30,
            )
            _audit.purge()
        except Exception:
            logger.exception("Could not configure audit logger")
    if (db.get_setting("core_sync_enabled") or "true").lower() != "true":
        return
    seconds = _sync_interval_setting(db)
    try:
        job = await _build_sync_job()
    except ValueError as e:
        logger.warning("Sync schedule autostart skipped: %s", e)
        return
    except Exception as e:
        logger.warning("Sync schedule autostart failed: %s", e)
        return
    start_scheduler(job, seconds=seconds)
    if _audit:
        try:
            _audit.log("scheduler", "INFO", "Sync schedule restored",
                       detail=f"interval_seconds: {seconds}")
        except Exception:
            pass
    logger.info("Sync schedule restored — running every %ds", seconds)
