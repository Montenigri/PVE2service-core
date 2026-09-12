#!/usr/bin/env python
"""Fetch the first-party plugin sources into ``PVE2Services/plugins/``.

The open first-party plugins live in the separate **PVE2Services-Plugin** repo.
The core release pipeline checks that repo out at a pinned ref and copies the
dirs listed in ``plugins/bundled_plugins.txt`` into the image before building.
This script does the same thing locally, for development and tests.

Usage:
    python tools/sync_plugins.py                     # sibling repo checkout
    python tools/sync_plugins.py --source /path/to/PVE2Services-Plugin
    python tools/sync_plugins.py --source https://github.com/Montenigri/PVE2Services-Plugin.git \
        --ref v1.2.3

Environment fallbacks: ``PVE2_PLUGINS_SRC`` and ``PVE2_PLUGINS_REF``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "PVE2Services"
PLUGINS_DIR = PACKAGE_ROOT / "plugins"
BUNDLED_LIST = PLUGINS_DIR / "bundled_plugins.txt"


def read_bundled_list() -> list[str]:
    if not BUNDLED_LIST.is_file():
        raise SystemExit(f"{BUNDLED_LIST} not found")
    names: list[str] = []
    for raw in BUNDLED_LIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            names.append(line)
    return names


def materialize_source(source: str, ref: str | None, workdir: Path) -> Path:
    """Return a local path to the plugin repo checkout (cloning if needed)."""
    local = Path(source).expanduser()
    if local.is_dir():
        return local

    if not any(source.startswith(p) for p in ("http://", "https://", "git@", "ssh://")):
        raise SystemExit(f"source not found and not a git URL: {source}")

    dest = workdir / "pve2services-plugin"
    cmd = ["git", "clone", "--quiet", source, str(dest)]
    subprocess.run(cmd, check=True)
    if ref:
        subprocess.run(["git", "-C", str(dest), "checkout", "--quiet", ref], check=True)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=os.environ.get("PVE2_PLUGINS_SRC", ""),
                        help="path or git URL of the PVE2Services-Plugin repo")
    parser.add_argument("--ref", default=os.environ.get("PVE2_PLUGINS_REF", ""),
                        help="git ref (tag/branch/commit) to check out")
    args = parser.parse_args()

    source = args.source.strip() or str(REPO_ROOT.parent / "PVE2Services-Plugin")
    ref = args.ref.strip() or None
    names = read_bundled_list()
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="pve2-plugins-") as tmp:
        root = materialize_source(source, ref, Path(tmp))
        src_plugins = root / "plugins"
        if not src_plugins.is_dir():
            raise SystemExit(f"{src_plugins} not found — is --source the plugin repo?")

        missing = [n for n in names if not (src_plugins / n).is_dir()]
        if missing:
            raise SystemExit(f"Missing plugin dirs in source: {', '.join(missing)}")

        for name in names:
            target = PLUGINS_DIR / name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(
                src_plugins / name,
                target,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
            )
            print(f"synced {name}")

    print(f"done: {len(names)} plugin(s) in {PLUGINS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
