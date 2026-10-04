"""Loader tests: manifest validation, trust-tier derivation, ordering, dupes.

Plugins are created as throwaway directories OUTSIDE the core checkout, so
they exercise the file-path loading branch (external plugins checkout).
"""

import types

import pytest

from PVE2Services.libs import plugin_loader
from PVE2Services.libs.plugin_loader import (
    PluginRegistry,
    discover_candidate_dirs,
    load_all_plugins,
)
from PVE2Services.libs.plugin_signing import SigningKeyPair, sign_file

VALID_MANIFEST = """\
id: fakeplug
name: Fake Plug
version: 2.3.4
core_compat: ">=0.1.0,<2.0.0"
capabilities:
  reads: [vm_state]
  writes: [notifications]
trust_tier: trusted
"""


@pytest.fixture()
def fresh_registry():
    saved = plugin_loader._registry
    plugin_loader._registry = PluginRegistry()
    yield plugin_loader._registry
    plugin_loader._registry = saved


@pytest.fixture()
def plug(tmp_path):
    """Create a minimal valid plugin dir; returns its path."""
    d = tmp_path / "plug"
    d.mkdir()

    def _make(name: str, manifest: str = VALID_MANIFEST, init: str = None):
        d2 = d / name
        d2.mkdir(exist_ok=True)
        (d2 / "plugin.yaml").write_text(manifest)
        (d2 / "__init__.py").write_text(init or "def load_plugin(app):\n    pass\n")
        return d2

    return _make


def test_discover_skips_hidden_and_files(plug):
    root = plug("dummy").parent
    (root / ".staging-x").mkdir()
    (root / "notaplugin.txt").write_text("x")
    names = [p.name for p in discover_candidate_dirs(root)]
    assert names == ["dummy"]


def test_valid_plugin_loads_trusted_bundled(plug, fresh_registry, monkeypatch, tmp_path):
    d = plug("plug")
    (d.parent / "bundled_plugins.txt").write_text("plug\n")
    app = types.SimpleNamespace(_loaded=[])
    ids, registry = load_all_plugins(app, d.parent)
    assert ids == ["plug"]  # dir key, lowercase
    info = registry.get("fakeplug")
    assert info is not None
    assert info.trust_tier == "trusted"
    assert info.version == "2.3.4"
    assert info.signed is False
    assert info.loaded is True
    assert info.capabilities == {"reads": ["vm_state"], "writes": ["notifications"]}


def test_missing_manifest_bundled_still_loads_with_defaults(plug, fresh_registry, monkeypatch):
    d = plug("plug")
    (d / "plugin.yaml").unlink()
    (d.parent / "bundled_plugins.txt").write_text("plug\n")
    ids, registry = load_all_plugins(types.SimpleNamespace(), d.parent)
    assert ids == ["plug"]
    info = registry.get("plug")
    assert info.loaded


def test_missing_manifest_third_party_not_loaded(plug, fresh_registry, monkeypatch):
    """Third-party dirs without a valid manifest are refused (fail-closed)."""
    d = plug("stranger")
    (d / "plugin.yaml").unlink()
    ids, registry = load_all_plugins(types.SimpleNamespace(), d.parent)
    info = registry.get("stranger")
    assert info.trust_tier == "third_party"
    assert info.loaded is False
    assert "manifest" in info.load_error.lower()
    assert ids == []


def test_incompatible_core_version_skipped(plug, fresh_registry, monkeypatch):
    d = plug("plug", manifest=VALID_MANIFEST.replace('">=0.1.0', '">=99.0.0'))
    ids, registry = load_all_plugins(types.SimpleNamespace(), d.parent)
    assert ids == []
    info = registry.get("fakeplug")
    assert info.loaded is False
    assert "core" in info.load_error.lower()


def test_broken_manifest_skipped_not_fatal(plug, fresh_registry, monkeypatch):
    d = plug("badplug", manifest="id: [not, a, mapping")
    ids, registry = load_all_plugins(types.SimpleNamespace(), d.parent)
    assert ids == []
    info = registry.get("badplug")
    assert info.loaded is False
    # The failed plugin must not make OTHER plugins fail to load
    d2 = plug("goodplug")
    (d2.parent / "bundled_plugins.txt").write_text("badplug\nplug\n")
    ids, _ = load_all_plugins(types.SimpleNamespace(), d2.parent)
    assert "goodplug" in ids and "badplug" in ids  # badplug bundled → loads with defaults


def test_declared_tier_is_display_only(plug, fresh_registry, monkeypatch, tmp_path):
    """A third-party plugin claiming 'trusted' in its manifest stays third_party."""
    d = plug("stranger", manifest=VALID_MANIFEST)
    ids, registry = load_all_plugins(types.SimpleNamespace(), d.parent)
    info = registry.get("fakeplug")
    # declared trusted but unbundled + unsigned → tier derived = third_party
    assert info.trust_tier == "third_party"
    assert info.declared_tier == "trusted"


def test_signed_manifest_becomes_trusted(plug, fresh_registry, monkeypatch, tmp_path):
    """Signature against the trusted key → trusted even if NOT bundled."""
    d = plug("storeplug")
    keypair = SigningKeyPair.generate()
    pub = tmp_path / "pub.pem"
    pub.write_bytes(keypair.public_pem())
    monkeypatch.setenv("PVE2_PLUGIN_SIGNING_PUBKEY", str(pub))
    sign_file(d / "plugin.yaml", keypair)
    ids, registry = load_all_plugins(types.SimpleNamespace(), d.parent)
    info = registry.get("fakeplug")
    assert info.trust_tier == "trusted"
    assert info.signed is True


def test_foreign_signature_is_third_party(plug, fresh_registry, monkeypatch, tmp_path):
    d = plug("rogueplug")
    keypair = SigningKeyPair.generate()
    pub = tmp_path / "pub.pem"
    pub.write_bytes(keypair.public_pem())
    monkeypatch.setenv("PVE2_PLUGIN_SIGNING_PUBKEY", str(pub))
    other = SigningKeyPair.generate()
    sign_file(d / "plugin.yaml", other)  # signed with a foreign key
    ids, registry = load_all_plugins(types.SimpleNamespace(), d.parent)
    info = registry.get("fakeplug")
    assert info.trust_tier == "third_party"
    assert info.signed is False


def test_duplicate_key_prefers_signed(plug, fresh_registry, monkeypatch, tmp_path):
    keypair = SigningKeyPair.generate()
    pub = tmp_path / "pub.pem"
    pub.write_bytes(keypair.public_pem())
    monkeypatch.setenv("PVE2_PLUGIN_SIGNING_PUBKEY", str(pub))
    plug("plug")  # unsigned, key "plug"
    signed = plug("Plug")  # signed, SAME lowercase key "plug"
    sign_file(signed / "plugin.yaml", keypair)
    ids, registry = load_all_plugins(types.SimpleNamespace(), plug("plug").parent)
    info = registry.get("fakeplug")
    assert info.signed is True
    assert info.dir_name == "Plug"
    assert ids == ["plug"]  # single winner, canonical lowercase key


def test_plugin_order_respected(plug, fresh_registry, monkeypatch):
    plug("zzz_last")
    plug("aaa_first")
    root = plug("mmb_mid").parent
    ids, _ = load_all_plugins(
        types.SimpleNamespace(), root, plugin_order=["mmb_mid", "aaa_first"]
    )
    assert ids[0] == "mmb_mid"
    assert set(ids[1:]) == {"aaa_first", "zzz_last"}


def test_load_failure_is_not_fatal(plug, fresh_registry, monkeypatch):
    # plugin code that raises at import time never crashes the core
    d = plug("boom", init="raise RuntimeError('boom')\n")
    root = d.parent
    ids, registry = load_all_plugins(types.SimpleNamespace(), root)
    assert ids == []
    info = registry.get("fakeplug")
    assert info.load_error is not None


def test_entrypoint_missing_function(plug, fresh_registry, monkeypatch):
    d = plug("nofunc", init="X = 1\n")  # has __init__ but no load_plugin
    ids, registry = load_all_plugins(types.SimpleNamespace(), d.parent)
    info = registry.get("fakeplug")
    assert ids == []
    assert info.load_error is not None


def test_alternative_entrypoint_function(plug, fresh_registry, monkeypatch):
    d = plug("altplug", manifest=VALID_MANIFEST.replace("load_plugin", "register"))
    ids, registry = load_all_plugins(types.SimpleNamespace(), d.parent)
    assert ids == ["altplug"]


MCP_MANIFEST = VALID_MANIFEST + """\
mcp:
  enabled: true
  tools:
    - name: ping_tool
      description: ping
      handler: "mcp_tools:ping_tool"
      capability: dns_records
      read_only: true
      input_schema:
        type: object
        properties: {}
"""


def test_mcp_tools_registered_from_manifest(plug, fresh_registry):
    from PVE2Services.libs.mcp_registry import get_mcp_registry

    d = plug("mcpmod", manifest=MCP_MANIFEST)
    (d / "mcp_tools.py").write_text(
        "async def ping_tool(params):\n    return {'pong': True}\n"
    )
    ids, _ = load_all_plugins(types.SimpleNamespace(), d.parent)
    assert "mcpmod" in ids
    specs = get_mcp_registry().all(read_only_only=True)
    assert any(s.plugin_id == "fakeplug" and s.name == "ping_tool" for s in specs)


def test_broken_mcp_handler_does_not_kill_plugin(plug, fresh_registry):
    d = plug(
        "badmcp",
        manifest=MCP_MANIFEST.replace("mcp_tools:ping_tool", "mcp_tools:missing"),
    )
    (d / "mcp_tools.py").write_text("X = 1\n")
    ids, registry = load_all_plugins(types.SimpleNamespace(), d.parent)
    assert "badmcp" in ids  # plugin still loads despite the broken tool
    assert registry.get("fakeplug").loaded is True


def test_mcp_registry_reset_between_loads(plug, fresh_registry, tmp_path):
    from PVE2Services.libs.mcp_registry import get_mcp_registry

    d = plug("mcpmod", manifest=MCP_MANIFEST)
    (d / "mcp_tools.py").write_text(
        "async def ping_tool(params):\n    return {}\n"
    )
    load_all_plugins(types.SimpleNamespace(), d.parent)
    assert len(get_mcp_registry()) == 1

    # A separate root with no MCP tools clears the previous entries.
    other = tmp_path / "other"
    plain = other / "plain"
    plain.mkdir(parents=True)
    (plain / "plugin.yaml").write_text(VALID_MANIFEST)
    (plain / "__init__.py").write_text("def load_plugin(app):\n    pass\n")
    load_all_plugins(types.SimpleNamespace(), other)
    assert len(get_mcp_registry()) == 0

