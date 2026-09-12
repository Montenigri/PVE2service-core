"""Plugin discovery and loading with manifest validation and trust tiers.

Replaces the old blind-import loop in core/wiki_service/app/main.py. The
contract stays the same — each plugin exposes ``load_plugin(app)`` — but now,

1. every plugin directory is validated (manifest schema, core compatibility)
   BEFORE the module is imported;
2. the effective trust tier is DERIVED here, never taken from the manifest:
     * ``plugin.yaml.sig`` verifying against the project ed25519 key -> trusted
     * directory listed in ``plugins/bundled_plugins.txt`` (shipped inside the
       same release artifact as the loader itself) -> trusted
     * anything else (manually dropped dirs, unsigned) -> third_party
3. results are recorded in a :class:`PluginRegistry` used by the hub UI and
   the ``/api/hub/plugins`` endpoint.

Loading errors are logged, never fatal — the core always boots.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from PVE2Services.libs.errors import ManifestError, TrustError
from PVE2Services.libs.plugin_manifest import (
    MANIFEST_FILENAME,
    PluginManifest,
    load_manifest,
)
from PVE2Services.libs.plugin_signing import resolve_public_key, verify_manifest_signature

logger = logging.getLogger("pve2.plugin_loader")

BUNDLED_LIST_FILENAME = "bundled_plugins.txt"

# Set by the FastAPI app definition in core/wiki_service/app/main.py
CORE_VERSION = "0.1.0"

# Plugins found here ship in the same release artifact as the core and are
# imported as ``PVE2Services.plugins.<Dir>``. A different plugins dir
# (``PVE2_PLUGINS_DIR`` — separate "plugins repo" checkout) is loaded by file
# path under a private namespace, so the core package tree stays untouched
# (ready_to_publish.md, "Separazione core/plugin").
REPO_PLUGINS_DIR = Path(__file__).resolve().parents[1] / "plugins"


def _import_plugin_module(
    plugins_dir: Path, directory: Path, manifest: PluginManifest | None
) -> tuple[Any, str]:
    """Import a plugin directory; return (entry_module, function_name).

    Monolithic mode (plugins inside the core checkout): absolute import as
    ``PVE2Services.plugins.<Dir>[.<submodule>]`` — module identity matches
    what tests and sys.modules expect.

    External checkout (``PVE2_PLUGINS_DIR``): load by file path under the
    reserved name ``pve2_external_<dir>`` — plugin code still imports
    ``PVE2Services.libs.*`` absolutely, but its package is loaded directly
    from wherever the plugins repo lives.
    """
    if manifest is not None:
        entry_module, func_name = manifest.module_and_function(directory.name)
    else:
        entry_module = f"PVE2Services.plugins.{directory.name}"
        func_name = "load_plugin"

    if plugins_dir.resolve() == REPO_PLUGINS_DIR.resolve():
        return importlib.import_module(entry_module), func_name

    # External checkout: file-path import as a standalone root package.
    base = f"pve2_external_{directory.name.lower()}"
    spec = importlib.util.spec_from_file_location(
        base,
        directory / "__init__.py",
        submodule_search_locations=[str(directory)],
    )
    if spec is None or spec.loader is None:
        raise ManifestError(
            f"Cannot import plugin package from {directory}",
            plugin_id=directory.name.lower(),
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[base] = module
    spec.loader.exec_module(module)

    if entry_module != f"PVE2Services.plugins.{directory.name}":
        # Entrypoint targets a submodule (e.g. "plugin.main:register"):
        # resolve it relative to the freshly loaded standalone package.
        relative = entry_module.rsplit(f".{directory.name}.", 1)[-1]
        module = importlib.import_module(f"{base}.{relative}")
    return module, func_name


class PluginInfo(BaseModel):
    """Everything the rest of the app needs to know about a discovered plugin."""

    id: str
    dir_name: str
    name: str
    version: str
    trust_tier: str  # effective tier: "trusted" | "third_party"
    declared_tier: str  # display-only, what the author wrote in the manifest
    capabilities: dict[str, list[str]]
    loaded: bool = False
    load_error: str | None = None
    core_compat: str = ""
    description: str = ""
    icon: str = "🔌"
    signed: bool = False
    bundled: bool = False

    model_config = {"extra": "forbid"}


@dataclass
class _PreparedPlugin:
    """Internal: validation result before the (potentially crashing) import."""

    info: PluginInfo
    manifest: PluginManifest | None
    loadable: bool  # manifest ok + core compatible — safe to import


def _read_bundled_list(plugins_dir: Path) -> set[str]:
    """Read the bundled first-party plugin list (one dir name per line)."""
    path = plugins_dir / BUNDLED_LIST_FILENAME
    if not path.is_file():
        logger.warning(
            "%s not found in %s — no plugin will be treated as bundled",
            BUNDLED_LIST_FILENAME,
            plugins_dir,
        )
        return set()
    bundled: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        bundled.add(line)
    return bundled


class PluginRegistry:
    """Thread-safe registry of discovered plugins."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._plugins: dict[str, PluginInfo] = {}

    def add(self, info: PluginInfo) -> None:
        with self._lock:
            self._plugins[info.id] = info

    def get(self, plugin_id: str) -> PluginInfo | None:
        with self._lock:
            return self._plugins.get(plugin_id)

    def all(self) -> list[PluginInfo]:
        with self._lock:
            return list(self._plugins.values())


_registry = PluginRegistry()


def get_registry() -> PluginRegistry:
    """Global registry shared by loader, hub UI and info endpoints."""
    return _registry


def reset_registry() -> PluginRegistry:
    """Swap in a fresh registry (used by tests and re-discovery flows)."""
    global _registry
    _registry = PluginRegistry()
    return _registry


def _dir_rank(directory: Path, bundled: set[str]) -> tuple[int, int]:
    """Rank for duplicate-key resolution: signed > bundled > unsigned."""
    manifest_path = directory / MANIFEST_FILENAME
    signed = 1 if manifest_path.is_file() and (directory / (MANIFEST_FILENAME + ".sig")).is_file() else 0
    return (signed, 1 if directory.name in bundled else 0)


def _derive_tier(
    plugin_dir: Path,
    *,
    bundled: bool,
    public_key: Any = None,
) -> tuple[str, bool]:
    """Derive the EFFECTIVE trust tier of a plugin directory.

    Returns (tier, signed). Decision table:

    ============ ============== ============== ==========================
    .sig file    bundled        tier           rationale
    ============ ============== ============== ==========================
    verifies     no             trusted        store-installed, signed
    verifies     yes            trusted        store-installed, signed
    absent       yes            trusted        first-party, same artifact
    absent       no             third_party    manually dropped
    invalid      yes/no         third_party    tampered or foreign key
    ============ ============== ============== ==========================
    """
    signed = verify_manifest_signature(plugin_dir, public_key=public_key)
    if signed:
        return "trusted", True
    if bundled:
        return "trusted", False
    return "third_party", False


def discover_candidate_dirs(plugins_dir: Path) -> list[Path]:
    """Deterministic sibling name list of dirs that look like plugins."""
    if not plugins_dir.is_dir():
        return []
    candidates = [
        entry
        for entry in sorted(plugins_dir.iterdir(), key=lambda p: p.name)
        if entry.is_dir() and (entry / "__init__.py").is_file()
    ]
    # Skip hidden/internal dirs used by the installer for staging/backups.
    return [d for d in candidates if not d.name.startswith(".")]


def _prepare_plugin(
    directory: Path,
    bundled: set[str],
    core_version: str,
    public_key: Any,
) -> _PreparedPlugin:
    """Validate manifest + derive tier for one candidate dir (no import)."""
    is_bundled = directory.name in bundled

    def _fallback_info(**overrides: Any) -> PluginInfo:
        base: dict[str, Any] = {
            "id": directory.name.lower(),
            "dir_name": directory.name,
            "name": directory.name,
            "version": "0.0.0",
            "trust_tier": "third_party",
            "declared_tier": "third_party",
            "capabilities": {"reads": [], "writes": []},
            "bundled": is_bundled,
        }
        base.update(overrides)
        return PluginInfo(**base)

    try:
        manifest = load_manifest(directory)
    except ManifestError as exc:
        reason = f"Invalid or missing manifest: {exc.message}"
        logger.warning("Skipping plugin dir %s: %s", directory.name, exc.message)
        info = _fallback_info(loaded=False, load_error=exc.message)
        if is_bundled:
            # First-party plugin without (valid) manifest: still load it — the
            # discovery contract predates manifests — but flag it loudly.
            logger.warning(
                "Bundled plugin %s has no valid manifest; loading with defaults. "
                "Add a %s file to fix this notice.",
                directory.name,
                MANIFEST_FILENAME,
            )
            info.loaded = True
            info.load_error = f"manifest invalid/missing ({exc.message})"
        return _PreparedPlugin(info=info, manifest=None, loadable=info.loaded)

    if not is_bundled and manifest.id != directory.name.lower():
        logger.warning(
            "Plugin dir %s declares id %r — manifest id is used as canonical key",
            directory.name,
            manifest.id,
        )

    if not manifest.satisfies_core(core_version):
        reason = f"Requires core {manifest.core_compat}, running core is {core_version}"
        logger.warning("Skipping plugin %s: %s", manifest.id, reason)
        info = _fallback_info(
            id=manifest.id,
            name=manifest.name,
            version=manifest.version,
            core_compat=manifest.core_compat,
            icon=manifest.icon,
            description=manifest.description,
            declared_tier=manifest.trust_tier,
            loaded=False,
            load_error=reason,
        )
        return _PreparedPlugin(info=info, manifest=manifest, loadable=False)

    tier, signed = _derive_tier(directory, bundled=is_bundled, public_key=public_key)
    declared = manifest.trust_tier
    if declared != tier:
        logger.info(
            "Plugin %s declares trust_tier=%r but effective tier is %r "
            "(declared tier is display-only and never used for security)",
            manifest.id, declared, tier,
        )

    info = PluginInfo(
        id=manifest.id,
        dir_name=directory.name,
        name=manifest.name,
        version=manifest.version,
        trust_tier=tier,
        declared_tier=declared,
        capabilities=manifest.capabilities.model_dump(),
        core_compat=manifest.core_compat,
        description=manifest.description,
        icon=manifest.icon,
        signed=signed,
        bundled=is_bundled,
        loaded=False,
    )
    return _PreparedPlugin(info=info, manifest=manifest, loadable=True)


def load_all_plugins(
    app: Any,
    plugins_dir: Path,
    core_version: str = CORE_VERSION,
    public_key: Any = None,
    plugin_order: list[str] | None = None,
) -> tuple[list[str], PluginRegistry]:
    """Discover, validate, tier-classify and load every plugin.

    ``plugin_order`` (lowercase dir keys) is an optional load/display-order
    hint — known keys load in the given order, unknown ones appended sorted.
    Returns (loaded_plugin_ids, registry). Failures on individual plugins are
    logged and skipped — never fatal; the core always boots.
    """
    if public_key is None:
        try:
            public_key = resolve_public_key()
        except TrustError as exc:
            logger.warning(
                "Proceeding without a trusted signing key: %s", exc.message
            )
            public_key = None

    bundled = _read_bundled_list(plugins_dir)
    candidates = discover_candidate_dirs(plugins_dir)

    # Deterministic duplicate-key resolution: signed > bundled > unsigned.
    best: dict[str, Path] = {}
    losers: list[tuple[str, str]] = []  # (loser dir name, reason)
    for directory in sorted(candidates, key=lambda p: p.name):
        key = directory.name.lower()
        incumbent = best.get(key)
        if incumbent is None:
            best[key] = directory
            continue
        if _dir_rank(directory, bundled) > _dir_rank(incumbent, bundled):
            losers.append((incumbent.name, f"superseded by {directory.name}"))
            best[key] = directory
        else:
            losers.append((directory.name, f"loses to {incumbent.name}"))
    for name, reason in losers:
        logger.warning("Duplicate plugin directory %s ignored (%s)", name, reason)

    ordered: list[tuple[str, Path]] = list(best.items())

    if plugin_order:
        rank_map = {key: idx for idx, key in enumerate(plugin_order)}
        tail = max(rank_map.values(), default=-1) + 1
        ordered.sort(key=lambda item: (
            rank_map.get(item[0], tail), item[0]
        ))

    registry = _registry
    loaded_ids: list[str] = []

    for key, directory in ordered:
        prepared = _prepare_plugin(directory, bundled, core_version, public_key)
        registry.add(prepared.info)
        if not prepared.loadable:
            continue
        try:
            module, func_name = _import_plugin_module(
                plugins_dir, directory, prepared.manifest
            )
            entry_func = getattr(module, func_name, None)
            if entry_func is None or not callable(entry_func):
                raise ManifestError(
                    f"Entrypoint {func_name!r} not found or not callable in {module.__name__}",
                    plugin_id=prepared.info.id,
                )
            entry_func(app)
            prepared.info.loaded = True
            loaded_ids.append(key)
            logger.info(
                "Plugin %s %s loaded (tier=%s, signed=%s)",
                prepared.info.id, prepared.info.version,
                prepared.info.trust_tier, prepared.info.signed,
            )
        except Exception as exc:  # noqa: BLE001 — plugin code is untrusted
            prepared.info.loaded = False
            prepared.info.load_error = str(exc)
            logger.exception(
                "Error loading plugin %s (%s): %s", prepared.info.id, directory.name, exc
            )
    return loaded_ids, registry
