"""Plugin manifest (plugin.yaml) model, loading and validation.

Every plugin ships a ``plugin.yaml`` next to its code. The loader validates the
manifest *before* importing the plugin module so that incompatible or malformed
plugins never reach ``import`` (which could crash the core).

Security note: the ``trust_tier`` field declared in the manifest is
**display-only**. The effective tier is derived by the loader from the verified
provenance of the package (see libs/plugin_loader.py), never from this field.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, Field, field_validator, model_validator

from PVE2Services.libs.errors import ManifestError

logger = logging.getLogger("pve2.plugin_manifest")

MANIFEST_FILENAME = "plugin.yaml"
MANIFEST_SIGNATURE_FILENAME = "plugin.yaml.sig"
PROVENANCE_FILENAME = ".pve2_provenance.json"

# Internal capability vocabulary (documented in PLUGIN_DEVELOPMENT.md).
# Declared here so manifests can be linted against known capabilities; unknown
# capabilities are allowed (future-proof) but flagged in logs.
KNOWN_READ_CAPABILITIES = {
    "cluster_state",
    "node_state",
    "vm_state",
    "storage_state",
    "machine_cache",
    "connector_config",
    "audit_log",
}
KNOWN_WRITE_CAPABILITIES = {
    "machine_cache",
    "sync_state",
    "audit_findings",
    "notifications",
    "dns_records",
    "power_schedules",
    "proxy_configs",
    "wiki_pages",
    "db_settings",
    "drift_state",
    "nut_history",
    "nut_events",
}

_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_CHECKSUM_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
# Strict 3-component semver (X.Y.Z) with optional pre-release/build suffix
_VERSION_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_ENTRYPOINT_RE = re.compile(
    r"^(?P<module>[A-Za-z_][A-Za-z0-9_.]*):(?P<func>[A-Za-z_][A-Za-z0-9_]*)$"
)

# Entrypoint paths are relative to the plugin directory, e.g.
#   "__init__:load_plugin"  -> plugins/<dir>/__init__.py :: load_plugin
#   "plugin.main:register"  -> plugins/<dir>/plugin/main.py :: register
# The prefix "PVE2Services.plugins.<Dir>." is allowed for backwards
# compatibility but not required.


class PluginCapabilities(BaseModel):
    """Declared read/write surface of a plugin (display + future enforcement)."""

    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)

    @field_validator("reads", "writes")
    @classmethod
    def _validate_items(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", item):
                raise ValueError(
                    f"capability items must be lowercase identifiers, got {item!r}"
                )
            if item not in cleaned:
                cleaned.append(item)
        return cleaned

    model_config = {"extra": "forbid"}


class PluginManifest(BaseModel):
    """Schema for ``plugin.yaml``.

    Required fields: id, name, version, core_compat. Everything else has a
    sane default so a minimal manifest is 4 lines.
    """

    id: str
    name: str
    version: str
    core_compat: str
    entrypoint: str = "__init__:load_plugin"
    description: str = ""
    author: str = ""
    homepage: str = ""
    icon: str = "🔌"
    checksum: str | None = None
    capabilities: PluginCapabilities = Field(default_factory=PluginCapabilities)
    # Display-only, declared by the author. NEVER used for security decisions.
    trust_tier: str = "third_party"
    license_required: bool = False
    license_server_url: str | None = None

    model_config = {"extra": "forbid"}

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not _PLUGIN_ID_RE.match(value or ""):
            raise ValueError(
                f"plugin id {value!r} is invalid: must match {_PLUGIN_ID_RE.pattern}"
            )
        return value

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not value or len(value) > 120:
            raise ValueError("plugin name must be a non-empty string (max 120 chars)")
        return value

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        if not _VERSION_RE.match(value or ""):
            raise ValueError(
                f"plugin version {value!r} must be 3-part semver X.Y.Z "
                "(optional -pre-release/+build suffix)"
            )
        try:
            Version(value)
        except InvalidVersion as exc:
            raise ValueError(f"plugin version {value!r} is not valid semver: {exc}") from exc
        return value

    @field_validator("core_compat")
    @classmethod
    def _validate_core_compat(cls, value: str) -> str:
        try:
            SpecifierSet(value)
        except InvalidSpecifier as exc:
            raise ValueError(f"core_compat {value!r} is not a valid specifier set: {exc}") from exc
        return value

    @field_validator("entrypoint")
    @classmethod
    def _validate_entrypoint(cls, value: str) -> str:
        match = _ENTRYPOINT_RE.match(value or "")
        if not match:
            raise ValueError(
                f"entrypoint {value!r} is invalid: expected 'module.path:function_name'"
            )
        module = match.group("module")
        if module.startswith(".") or ".." in module:
            raise ValueError(f"entrypoint module {module!r} must not be relative")
        return value

    @field_validator("checksum")
    @classmethod
    def _validate_checksum(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not _CHECKSUM_RE.match(value):
            raise ValueError(
                "checksum must look like 'sha256:<64 lowercase hex chars>', "
                f"got {value!r}"
            )
        return value

    @field_validator("icon")
    @classmethod
    def _validate_icon(cls, value: str) -> str:
        if not value or len(value) > 16:
            raise ValueError("icon must be a short string (max 16 chars)")
        return value

    @field_validator("trust_tier")
    @classmethod
    def _validate_trust_tier(cls, value: str) -> str:
        # Display-only field: still constrained so the UI can render it safely.
        if value not in ("trusted", "third_party"):
            raise ValueError("trust_tier must be 'trusted' or 'third_party'")
        return value

    @field_validator("license_server_url")
    @classmethod
    def _validate_license_url(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if not re.match(r"^https://[^\s]+$", value):
            raise ValueError("license_server_url must be an https:// URL")
        return value

    @model_validator(mode="after")
    def _validate_license_consistency(self) -> "PluginManifest":
        if self.license_required and not self.license_server_url:
            raise ValueError(
                "license_required=true requires license_server_url to be set"
            )
        return self

    @property
    def version_obj(self) -> Version:
        return Version(self.version)

    def satisfies_core(self, core_version: str) -> bool:
        """True when the manifest's core_compat range contains ``core_version``."""
        return Version(core_version) in SpecifierSet(self.core_compat)

    def module_and_function(self, plugin_dir_name: str) -> tuple[str, str]:
        """Resolve the entrypoint to an absolute module path and function name.

        Bare-relative entrypoints (``__init__:load_plugin``) are resolved
        against ``PVE2Services.plugins.<DirName>``, where ``__init__`` maps to
        the package module itself (importing it again as
        `...plugins.<dir>.__init__` would execute the file a second time).
        """
        match = _ENTRYPOINT_RE.match(self.entrypoint)
        assert match is not None  # validated in __init__
        module, func = match.group("module"), match.group("func")
        if not module.startswith("PVE2Services.plugins."):
            if module == "__init__":
                module = f"PVE2Services.plugins.{plugin_dir_name}"
            else:
                module = f"PVE2Services.plugins.{plugin_dir_name}.{module}"
        return module, func

    def warn_unknown_capabilities(self) -> None:
        """Log (non-fatal) capabilities outside the known vocabulary."""
        for cap in self.capabilities.reads:
            if cap not in KNOWN_READ_CAPABILITIES:
                logger.info("Plugin %s declares unknown read capability %r", self.id, cap)
        for cap in self.capabilities.writes:
            if cap not in KNOWN_WRITE_CAPABILITIES:
                logger.info(
                    "Plugin %s declares unknown write capability %r", self.id, cap
                )


def valid_plugin_id(plugin_id: str) -> bool:
    """True when ``plugin_id`` follows the manifest id rule (slug, lowercase)."""
    return bool(_PLUGIN_ID_RE.match(plugin_id or ""))


def parse_manifest(data: dict[str, Any], source: str = "<memory>") -> PluginManifest:
    """Parse and validate a manifest dict. Raises ManifestError with context."""
    try:
        manifest = PluginManifest.model_validate(data)
    except Exception as exc:
        raise ManifestError(
            f"Invalid plugin manifest at {source}: {exc}", plugin_id=str(data.get("id", ""))
        ) from exc
    manifest.warn_unknown_capabilities()
    return manifest


def load_manifest(plugin_dir: Path) -> PluginManifest:
    """Load + validate ``plugin.yaml`` from a plugin directory.

    Raises ManifestError when the file is missing or invalid.
    """
    path = plugin_dir / MANIFEST_FILENAME
    if not path.is_file():
        raise ManifestError(
            f"Manifest {MANIFEST_FILENAME} not found in {plugin_dir}", plugin_id=plugin_dir.name
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(
            f"Manifest {path} is not valid YAML: {exc}", plugin_id=plugin_dir.name
        ) from exc
    if not isinstance(raw, dict):
        raise ManifestError(
            f"Manifest {path} must contain a YAML mapping, got {type(raw).__name__}",
            plugin_id=plugin_dir.name,
        )
    return parse_manifest(raw, source=str(path))


def check_core_compat(manifest: PluginManifest, core_version: str) -> bool:
    """True when the plugin declares compatibility with the running core."""
    return manifest.satisfies_core(core_version)
