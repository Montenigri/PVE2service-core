"""Unified plugin hub interface."""

import html
import logging

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from PVE2Services.libs.plugin_loader import get_registry

from .templates_renderer import read_text

logger = logging.getLogger("hub")

# Populated by main.py after plugin discovery (fallback when the loader
# could not run — e.g. import wired before registry existed).
_loaded_plugins: list[str] = []


def set_loaded_plugins(plugin_ids: list[str]):
    """Set the list of loaded plugin IDs (called by main.py)."""
    global _loaded_plugins
    _loaded_plugins = plugin_ids


def create_hub_page():
    """Generate the main hub page."""
    return read_text("hub.html")


# Fallback display data only — when a plugin has no manifest the hub still
# shows a generic entry instead of crashing.
_FALLBACK_META = {"name": "{id}", "icon": "🔌", "type": "connector"}


def _esc(value: str) -> str:
    """HTML-escape manifest/author-controlled strings.

    The hub client renders these into innerHTML; a third-party manifest must
    not be able to inject markup into the admin UI.
    """
    return html.escape(str(value), quote=True)


def _load_error_of(info) -> str:
    return _esc(info.load_error) if info.load_error else ""


def _hub_entry(pid: str, info) -> dict:
    if info is not None:
        return {
            "id": _esc(info.id),
            "name": _esc(info.name),
            "icon": _esc(info.icon),
            "type": "connector",
            "version": _esc(info.version),
            "trust_tier": info.trust_tier,
            "declared_tier": info.declared_tier,
            "capabilities": {
                "reads": [_esc(c) for c in info.capabilities.get("reads", [])],
                "writes": [_esc(c) for c in info.capabilities.get("writes", [])],
            },
            "loaded": info.loaded,
            "signed": info.signed,
            "description": _esc(info.description),
            "load_error": _load_error_of(info),
            "core_compat": _esc(info.core_compat),
        }
    name = html.escape(_FALLBACK_META["name"].format(id=pid), quote=True)
    return {
        "id": _esc(pid),
        "name": name,
        "icon": _esc(_FALLBACK_META["icon"]),
        "type": _FALLBACK_META["type"],
        "version": "",
        "trust_tier": "unknown",
        "declared_tier": "unknown",
        "capabilities": {"reads": [], "writes": []},
        "loaded": True,
        "signed": False,
        "description": "",
        "load_error": "",
        "core_compat": "",
    }

def load_hub(app: FastAPI):
    """Register hub routes."""

    @app.get("/admin/hub", response_class=HTMLResponse)
    async def hub_page():
        """Main plugin hub page."""
        return create_hub_page()

    @app.get("/api/hub/plugins")
    async def list_all_plugins():
        """List all loaded plugins dynamically, with store metadata."""
        registry = get_registry()
        infos = {p.id: p for p in registry.all()}
        plugins: list[dict] = []
        # Preserve the load order recorded by main.py; unknown ids appended.
        seen: set[str] = set()
        for pid in _loaded_plugins:
            seen.add(pid)
            plugins.append(_hub_entry(pid, infos.get(pid)))
        for pid, info in infos.items():
            if pid not in seen:
                plugins.append(_hub_entry(pid, info))
        return {"plugins": plugins}

    @app.get("/")
    async def root():
        """Root endpoint."""
        return {"message": "PVE2Services running. Visit /admin/login to access the admin panel"}
