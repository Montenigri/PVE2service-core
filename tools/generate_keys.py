#!/usr/bin/env python
"""Backward-compatible shim around :mod:`PVE2Services.libs.plugin_keys`.

Real logic is installable so the plugin repo can use it from the ``pve2``
wheel (console script ``pve2-generate-keys``).
"""

from __future__ import annotations

from PVE2Services.libs.plugin_keys import main  # noqa: F401

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
