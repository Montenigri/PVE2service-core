"""PVE2_PLUGINS_DIR override — loading plugins from a separate checkout.

Supports the "core repo + plugins repo" split described in ready_to_publish.md
("Separazione core/plugin"): the core discovers plugins from a directory
outside its own checkout; plugin packages are imported by file path under a
private namespace instead of PVE2Services.plugins.*.
"""

import sys
import types

import pytest

from PVE2Services.libs import plugin_loader
from PVE2Services.libs.plugin_loader import PluginRegistry


@pytest.fixture()
def fresh_registry():
    saved = plugin_loader._registry
    plugin_loader._registry = PluginRegistry()
    yield plugin_loader._registry
    plugin_loader._registry = saved


@pytest.fixture()
def external_plugins_root(tmp_path):
    """A standalone plugins checkout containing one unsigned plugin."""
    root = tmp_path / "pve2-plugins" / "plugins"
    root.mkdir(parents=True)
    d = root / "externalplug"
    d.mkdir()
    (d / "__init__.py").write_text("def load_plugin(app):\n    pass\n")
    (d / "plugin.yaml").write_text(
        "id: externalplug\n"
        "name: External Plug\n"
        "version: 0.2.0\n"
        'core_compat: ">=0.1.0,<2.0.0"\n'
    )
    return root


def _load(plugins_root):
    return plugin_loader.load_all_plugins(types.SimpleNamespace(), plugins_root)


def test_loads_plugins_from_external_checkout(fresh_registry, external_plugins_root):
    ids, registry = _load(external_plugins_root)
    assert "externalplug" in ids
    info = registry.get("externalplug")
    assert info.version == "0.2.0"
    # not in the core's bundled list (external checkout) -> third_party
    assert info.trust_tier == "third_party"
    # loaded under the private external namespace, not PVE2Services.plugins.*
    assert "pve2_external_externalplug" in sys.modules


def test_external_submodule_entrypoint(fresh_registry, external_plugins_root):
    (external_plugins_root / "externalplug" / "plugin").mkdir()
    (external_plugins_root / "externalplug" / "plugin" / "main.py").write_text(
        "def register(app):\n    pass\n"
    )
    (external_plugins_root / "externalplug" / "plugin.yaml").write_text(
        "id: externalplug\nname: External Plug\nversion: 0.2.0\n"
        'core_compat: ">=0.1.0,<2.0.0"\nentrypoint: "plugin.main:register"\n'
    )
    ids, registry = _load(external_plugins_root)
    assert "externalplug" in ids
    assert registry.get("externalplug").loaded


def test_default_plugins_dir_matches_repo_layout():
    """Monolithic mode still resolves the checkout's own plugins/ dir."""
    from PVE2Services.core.wiki_service.app import main as main_mod

    assert main_mod.PLUGINS_DIR == plugin_loader.REPO_PLUGINS_DIR.resolve()
