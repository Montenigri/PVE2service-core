"""Unit tests for libs/plugin_manifest.py — schema, validation, core_compat."""

import pytest

from PVE2Services.libs.errors import ManifestError
from PVE2Services.libs.plugin_manifest import (
    check_core_compat,
    parse_manifest,
)

BASE = {
    "id": "pve2test",
    "name": "PVE2 Test",
    "version": "1.0.0",
    "core_compat": ">=0.1.0,<2.0.0",
}


def test_minimal_manifest_parses_with_defaults():
    m = parse_manifest(BASE)
    assert m.entrypoint == "__init__:load_plugin"
    assert m.icon == "🔌"
    assert m.checksum is None
    assert m.capabilities.reads == []
    assert m.trust_tier == "third_party"
    assert m.license_required is False
def test_full_manifest_roundtrip():
    m = parse_manifest(
        {
            **BASE,
            "version": "1.2.3",
            "icon": "🧩",
            "description": "demo",
            "entrypoint": "plugin.main:register",
            "checksum": "sha256:" + "0" * 64,
            "capabilities": {"reads": ["vm_state", "tags"], "writes": ["audit_findings"]},
            "trust_tier": "trusted",
        }
    )
    assert m.version == "1.2.3"
    assert m.capabilities.reads == ["vm_state", "tags"]
    assert m.entrypoint == "plugin.main:register"


def test_invalid_plugin_id_rejected():
    for bad in ("Bad_ID", "1abc", "x" * 65, "", "a b", "UPPER"):
        with pytest.raises(ManifestError):
            parse_manifest({**BASE, "id": bad})


def test_invalid_version_rejected():
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "version": "1.0"})


def test_invalid_core_compat_rejected():
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "core_compat": "banana"})


def test_invalid_entrypoint_rejected():
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "entrypoint": "__init__."})


def test_relative_entrypoint_rejected():
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "entrypoint": ".hidden:load"})


def test_unknown_top_level_field_rejected():
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "trust_level": "trusted"})  # typo'd field name


def test_checksum_format_validated():
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "checksum": "md5:abc"})
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "checksum": "sha256:xyz"})
    assert parse_manifest({**BASE, "checksum": "sha256:" + "a" * 64}).checksum is not None


def test_license_required_needs_server_url():
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "license_required": True})
    assert parse_manifest(
        {**BASE, "license_required": True, "license_server_url": "https://lic.example.com"}
    ).license_required


def test_bad_license_server_url_rejected():
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "license_server_url": "http://insecure.example"})


def test_capability_items_validated():
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "capabilities": {"reads": ["BAD CAP!"]}})
    m = parse_manifest({**BASE, "capabilities": {"reads": ["vm_state", "vm_state"]}})
    assert m.capabilities.reads == ["vm_state"]  # deduped


def test_core_compat_ranges():
    assert check_core_compat(parse_manifest(BASE), "0.1.0")
    assert check_core_compat(parse_manifest(BASE), "1.9.9")
    assert not check_core_compat(parse_manifest(BASE), "0.0.9")
    assert not check_core_compat(parse_manifest(BASE), "2.0.0")
    # non-semver plugin version with pre-release handling
    m = parse_manifest({**BASE, "core_compat": "==1.2.0"})
    assert check_core_compat(m, "1.2.0")
    assert not check_core_compat(m, "1.2.1")


def test_capabilities_extra_keys_rejected():
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "capabilities": {"reads": ["vm_state"], "exec": ["all"]}})


def test_trust_tier_values_validated():
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "trust_tier": "supertrusted"})
    assert parse_manifest({**BASE, "trust_tier": "third_party"}).trust_tier == "third_party"


def test_module_and_function_resolution():
    m = parse_manifest({**BASE, "entrypoint": "__init__:load_plugin"})
    assert m.module_and_function("PVE2DNS") == (
        "PVE2Services.plugins.PVE2DNS",
        "load_plugin",
    )
    m = parse_manifest({**BASE, "entrypoint": "plugin.main:register"})
    assert m.module_and_function("myplugin") == (
        "PVE2Services.plugins.myplugin.plugin.main",
        "register",
    )
    m = parse_manifest({**BASE, "entrypoint": "PVE2Services.plugins.PVE2DNS.__init__:load_plugin"})
    assert m.module_and_function("whatever") == (
        "PVE2Services.plugins.PVE2DNS.__init__",
        "load_plugin",
    )


# --- MCP block ------------------------------------------------------------


def test_manifest_without_mcp_has_none():
    assert parse_manifest(BASE).mcp is None


def test_mcp_block_parses():
    m = parse_manifest(
        {
            **BASE,
            "mcp": {
                "enabled": True,
                "tools": [
                    {
                        "name": "list_records",
                        "description": "List records",
                        "handler": "mcp_tools:list_records",
                        "capability": "dns_records",
                        "input_schema": {"type": "object"},
                    }
                ],
            },
        }
    )
    assert m.mcp is not None
    assert m.mcp.enabled is True
    assert m.mcp.tools[0].name == "list_records"
    assert m.mcp.tools[0].read_only is True
    assert m.mcp.tools[0].handler == "mcp_tools:list_records"


def test_mcp_invalid_tool_name_rejected():
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "mcp": {"tools": [{"name": "Bad Name", "handler": "m:t"}]}})


def test_mcp_invalid_handler_rejected():
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "mcp": {"tools": [{"name": "tool", "handler": "nofunc"}]}})


def test_mcp_duplicate_tool_names_rejected():
    with pytest.raises(ManifestError):
        parse_manifest(
            {
                **BASE,
                "mcp": {
                    "tools": [
                        {"name": "tool", "handler": "m:f"},
                        {"name": "tool", "handler": "m:g"},
                    ]
                },
            }
        )


def test_mcp_unknown_key_rejected():
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "mcp": {"enabled": True, "typo": 1}})


def test_mcp_tool_unknown_key_rejected():
    with pytest.raises(ManifestError):
        parse_manifest(
            {**BASE, "mcp": {"tools": [{"name": "tool", "handler": "m:f", "typo": 1}]}}
        )

