#!/usr/bin/env python
"""Backward-compatible shim around :mod:`PVE2Services.libs.plugin_build`.

The real logic lives in the installable package so that the plugin repo can
run it from the ``pve2`` wheel (console script ``pve2-build-plugin``) without
duplicating code. This module keeps ``from tools.build_plugin import ...``
working for the core test suite and local scripts.
"""

from __future__ import annotations

from PVE2Services.libs.plugin_build import (  # noqa: F401
    build_plugin,
    main,
    merge_local_index,
    publish,
)

__all__ = ["build_plugin", "merge_local_index", "publish", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
