"""Tests for tools/build_plugin.py — packaging, signing, artifact integrity."""

import hashlib
import zipfile

import pytest

from PVE2Services.libs.plugin_manifest import load_manifest
from PVE2Services.libs.plugin_signing import (
    SigningKeyPair,
    load_public_key,
    verify_bytes,
    verify_manifest_signature,
)
from tests.test_plugin_loader_helpers import make_plugin


@pytest.fixture()
def keypair():
    return SigningKeyPair.generate()


def test_build_artifacts_are_complete_and_consistent(tmp_path, keypair):
    from tools.build_plugin import build_plugin

    plugin_dir = make_plugin(tmp_path / "pins", "packplug", {
        "capabilities": {"reads": ["vm_state"], "writes": []},
    })
    entry = build_plugin(plugin_dir, tmp_path / "build", keypair)

    root = tmp_path / "build" / "packplug" / "1.0.0"
    manifest_path = root / "plugin.yaml"
    sig_path = root / "plugin.yaml.sig"
    package = root / "package.zip"
    for p in (manifest_path, sig_path, package):
        assert p.is_file(), f"missing artifact {p}"

    manifest = load_manifest(root)
    digest = "sha256:" + hashlib.sha256(package.read_bytes()).hexdigest()
    assert manifest.checksum == digest
    assert entry["checksum"] == digest

    pub = load_public_key(keypair.public_pem())
    assert verify_bytes(manifest_path.read_bytes(), sig_path.read_text(), pub)
    assert verify_manifest_signature(root, public_key=pub) is True


def test_package_zip_excludes_manifest_and_cruft(tmp_path, keypair):
    from tools.build_plugin import build_plugin

    plugin_dir = make_plugin(tmp_path / "pins", "zplug", {}, extra_files={
        "subdir/lib.py": "X = 1\n",
        "subdir/lib.pyc": "binary-ish",
    })
    (plugin_dir / "__pycache__").mkdir()
    (plugin_dir / "__pycache__" / "cache.pyc").write_text("junk")
    build_plugin(plugin_dir, tmp_path / "build", keypair)
    names = zipfile.ZipFile(
        tmp_path / "build" / "zplug" / "1.0.0" / "package.zip"
    ).namelist()

    assert "__init__.py" in names
    assert "subdir/lib.py" in names
    assert not any(n.endswith(".pyc") for n in names)
    assert not any("__pycache__" in n for n in names)
    assert not any(n.endswith(".sig") for n in names)
    assert "plugin.yaml" not in names


def test_build_fails_on_invalid_manifest(tmp_path, keypair):
    from tools.build_plugin import build_plugin

    plugin_dir = make_plugin(tmp_path / "pins", "broken")
    (plugin_dir / "plugin.yaml").write_text("id: INVALID ID\n")
    with pytest.raises(SystemExit):
        build_plugin(plugin_dir, tmp_path / "build", keypair)


def test_merge_local_index_dedups_versions(tmp_path, keypair):
    from tools.build_plugin import build_plugin, merge_local_index

    plugin_dir = make_plugin(tmp_path / "pins", "idxplug")
    b1 = build_plugin(plugin_dir, tmp_path / "build", keypair)
    idx = merge_local_index(tmp_path / "build", [b1])
    assert [p["id"] for p in idx["plugins"]] == ["idxplug"]
    assert len(idx["plugins"][0]["versions"]) == 1
    b2 = build_plugin(plugin_dir, tmp_path / "build", keypair)
    idx = merge_local_index(tmp_path / "build", [b2])
    assert len(idx["plugins"][0]["versions"]) == 1  # same version → replaced


def test_unsigned_build_detected_by_loader(tmp_path, keypair):
    """If someone deletes the .sig from the build, the loader treats it as third_party."""
    from tools.build_plugin import build_plugin

    plugin_dir = make_plugin(tmp_path / "pins", "nosig")
    build_plugin(plugin_dir, tmp_path / "build", keypair)
    sig = tmp_path / "build" / "nosig" / "1.0.0" / "plugin.yaml.sig"
    sig.unlink()
    assert verify_manifest_signature(tmp_path / "build" / "nosig" / "1.0.0") is False
