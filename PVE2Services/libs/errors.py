"""Centralized error handlers for FastAPI application."""

import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("pve2.errors")


class PluginError(Exception):
    """Base exception for plugin errors."""
    def __init__(self, message: str, plugin_id: str = "", details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.plugin_id = plugin_id
        self.details = details or {}
        super().__init__(message)


class ConfigError(PluginError):
    """Configuration validation error."""
    pass


class ConnectorError(PluginError):
    """Connector operation error (SSH, API, etc.)."""
    pass


class DatabaseError(PluginError):
    """Database operation error."""
    pass


class SyncError(PluginError):
    """Synchronization error."""
    pass


class ManifestError(PluginError):
    """Plugin manifest (plugin.yaml) is missing, malformed or fails validation."""
    pass


class TrustError(PluginError):
    """Plugin package signature verification or trust-tier derivation failed."""
    pass


def register_error_handlers(app: FastAPI):
    """Register centralized error handlers on the FastAPI app."""

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "type": "http_error"},
        )

    @app.exception_handler(PluginError)
    async def plugin_error_handler(request: Request, exc: PluginError):
        logger.error("Plugin error [%s]: %s", exc.plugin_id, exc.message)
        return JSONResponse(
            status_code=500,
            content={
                "detail": exc.message,
                "type": "plugin_error",
                "plugin": exc.plugin_id,
                "details": exc.details,
            },
        )

    @app.exception_handler(ConfigError)
    async def config_error_handler(request: Request, exc: ConfigError):
        logger.error("Config error [%s]: %s", exc.plugin_id, exc.message)
        return JSONResponse(
            status_code=400,
            content={
                "detail": exc.message,
                "type": "config_error",
                "plugin": exc.plugin_id,
            },
        )

    @app.exception_handler(DatabaseError)
    async def database_error_handler(request: Request, exc: DatabaseError):
        logger.error("Database error: %s", exc.message)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Database operation failed",
                "type": "database_error",
            },
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "type": "internal_error",
            },
        )
