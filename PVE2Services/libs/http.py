"""Shared HTTP client factory for all plugins."""

import os

import httpx

_VERIFY_SSL = os.getenv("PVE2_VERIFY_SSL", "false").lower() in ("1", "true", "yes")


def get_http_client(timeout: int = 10) -> httpx.AsyncClient:
    """Return an AsyncClient with the global SSL verification setting.

    Set PVE2_VERIFY_SSL=true to enable strict SSL verification.
    """
    return httpx.AsyncClient(verify=_VERIFY_SSL, timeout=timeout)
