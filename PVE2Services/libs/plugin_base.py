"""Plugin interface protocol and base utilities for PVE2Services plugins."""

import logging
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from fastapi import FastAPI

logger = logging.getLogger("pve2.plugin_base")


@runtime_checkable
class PluginProtocol(Protocol):
    """Protocol that all PVE2Services plugins should implement."""

    def load_plugin(self, app: FastAPI) -> None:
        """Load the plugin into the FastAPI application.
        
        This is the main entry point for plugin initialization.
        Should register routes, middleware, and any startup/shutdown events.
        """
        ...


def get_plugin_info(plugin_id: str) -> Dict[str, Any]:
    """Get standard plugin info metadata. Override in plugin's /info endpoint."""
    name_map = {
        "pve2core": "PVE2 Core",
        "pve2dash": "PVE2 Dash",
        "pve2audit": "PVE2 Audit",
        "pve2dns": "PVE2 DNS",
        "pve2notify": "PVE2 Notify",
        "pve2proxy": "PVE2 Proxy",
        "pve2power": "PVE2 Power",
        "pve2wiki": "PVE2 Wiki",
    }
    return {
        "plugin": plugin_id,
        "name": name_map.get(plugin_id, plugin_id.replace("pve2", "PVE2 ").replace("_", " ").title()),
        "version": "0.1.0",
        "description": "",
        "status": "active",
    }


def require_config(config: Dict[str, Any], *keys: str) -> Optional[str]:
    """Validate that required config keys are present.
    
    Returns error message if any key is missing, None otherwise.
    """
    missing = [k for k in keys if not config.get(k)]
    if missing:
        return f"Missing required config: {', '.join(missing)}"
    return None


def safe_import(module_path: str, symbol: str) -> Any:
    """Safely import a symbol from a module, returning None on failure."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, symbol, None)
    except Exception as e:
        logger.warning("Failed to import %s from %s: %s", symbol, module_path, e)
        return None


async def get_core_machines() -> list:
    """Return PVE2Core's cached machines as dicts (empty list if unavailable).

    Replaces the old HTTP self-calls to /api/plugins/pve2core/machines, which
    fail with 401 now that the auth middleware protects /api/*.
    """
    try:
        from PVE2Services.plugins import PVE2Core as core_plugin

        cache = getattr(core_plugin, "_cache", None)
        if cache is None:
            return []
        return [m.model_dump() for m in await cache.get_all()]
    except Exception:
        logger.warning("Could not read PVE2Core machine cache", exc_info=True)
        return []


async def get_core_machine(machine_id: str) -> Optional[Any]:
    """Return a single machine (dict) from PVE2Core's cache, or None."""
    try:
        from PVE2Services.plugins import PVE2Core as core_plugin

        cache = getattr(core_plugin, "_cache", None)
        if cache is None:
            return None
        m = await cache.get_by_id(str(machine_id))
        return m.model_dump() if m else None
    except Exception:
        logger.warning("Could not read machine %s from PVE2Core cache", machine_id)
        return None


async def update_core_machine_status(machine_id: str, status: str) -> bool:
    """Patch one machine's status in PVE2Core's cache (post power-action)."""
    try:
        from PVE2Services.plugins import PVE2Core as core_plugin

        cache = getattr(core_plugin, "_cache", None)
        if cache is None:
            return False
        return await cache.set_status(str(machine_id), status)
    except Exception:
        logger.warning("Could not update machine %s status in PVE2Core cache", machine_id)
        return False


async def get_core_health() -> Dict[str, Any]:
    """Return PVE2Core health info (connected, cache size, last sync)."""
    machines = await get_core_machines()
    try:
        from PVE2Services.plugins import PVE2Core as core_plugin

        cache = getattr(core_plugin, "_cache", None)
        last_sync = getattr(cache, "_last_sync", None) if cache else None
    except Exception:
        last_sync = None
    return {
        "status": "ok" if machines else "empty",
        "proxmox_connected": bool(machines),
        "cache_size": len(machines),
        "last_sync": last_sync.isoformat() if last_sync else None,
    }
