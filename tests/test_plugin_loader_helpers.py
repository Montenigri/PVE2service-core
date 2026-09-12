"""Shared factories for plugin-store tests (fake plugin dirs, keys, indexes)."""

from __future__ import annotations

import yaml


def make_plugin(
    root,
    dir_name: str,
    manifest: dict | None = None,
    init: str = "def load_plugin(app):\n    pass\n",
    extra_files: dict[str, str] | None = None,
) -> "object":
    """Create ``<root>/<dir_name>/`` with plugin.yaml + __init__.py.

    The manifest dict is serialized as YAML; ``id`` defaults to dir_name
    lowercased, ``version`` to 1.0.0. Returns the plugin dir.
    """
    d = root / dir_name
    d.mkdir(parents=True, exist_ok=True)
    data = dict(manifest or {})
    data.setdefault("id", dir_name.lower())
    data.setdefault("name", dir_name.title())
    data.setdefault("version", "1.0.0")
    data.setdefault("core_compat", ">=0.1.0,<2.0.0")
    data.setdefault("trust_tier", "third_party")
    caps = data.setdefault("capabilities", {"reads": [], "writes": []})
    if "reads" in caps or "writes" in caps:
        data["capabilities"] = {
            "reads": caps.get("reads", []),
            "writes": caps.get("writes", []),
        }
    (d / "plugin.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    if init is not None:
        (d / "__init__.py").write_text(init)
    for name, content in (extra_files or {}).items():
        target = d / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return d
