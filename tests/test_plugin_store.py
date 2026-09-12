"""End-to-end installer tests (publish -> index -> install) with mocked S3.

Uses moto to emulate an S3-compatible endpoint (the same protocol MinIO
speaks), exercising the real flow: bucket artifacts, index.json resolution,
signature + checksum verification, staging/atomic swap, provenance, and the
error paths. moto intercepts the botocore layer, so the installer's own
boto3 client (endpoint PVE2_PLUGIN_STORE_URL) is served transparently.
"""

import json
import os
import tempfile
import zipfile

import pytest
from moto import mock_aws

from PVE2Services.libs import plugin_store as store
from PVE2Services.libs.errors import TrustError
from PVE2Services.libs.plugin_manifest import PROVENANCE_FILENAME, load_manifest
from PVE2Services.libs.plugin_signing import SigningKeyPair, verify_manifest_signature
from tests.test_plugin_loader_helpers import make_plugin

BUCKET = "pve2services-plugins"


@pytest.fixture()
def keypair():
    """Ephemeral keypair: its public half becomes the trusted bundle."""
    return SigningKeyPair.generate()


@pytest.fixture()
def trusted_env(monkeypatch, tmp_path, keypair):
    pub = tmp_path / "trusted.pub"
    pub.write_bytes(keypair.public_pem())
    monkeypatch.setenv("PVE2_PLUGIN_SIGNING_PUBKEY", str(pub))
    monkeypatch.setenv("PVE2_PLUGIN_STORE_URL", "http://minio.local:9000")
    monkeypatch.setenv("PVE2_PLUGIN_STORE_BUCKET", BUCKET)
    monkeypatch.setenv("PVE2_PLUGIN_STORE_REGION", "us-east-1")
    monkeypatch.setenv("PVE2_PLUGIN_STORE_ACCESS_KEY", "test")
    monkeypatch.setenv("PVE2_PLUGIN_STORE_SECRET_KEY", "test")
    return keypair


def _publish(keypair, scratch, specs, signer=None):
    """Build + sign plugins, upload artifacts and index (caller holds mock_aws)."""
    import boto3

    from tools.build_plugin import build_plugin, merge_local_index, publish

    signer = signer if signer is not None else keypair
    client = boto3.client("s3", region_name="us-east-1")
    try:
        client.create_bucket(Bucket=BUCKET)
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    entries = []
    for dir_name, manifest in specs:
        d = make_plugin(scratch / "src", dir_name, dict(manifest or {}))
        entries.append(build_plugin(d, scratch / "build", signer))
    merge_local_index(scratch / "build", entries)
    publish(client, BUCKET, scratch / "build", entries)


def _sync(monkeypatch, plugins_dir, install="goodplug", force=None):
    monkeypatch.setenv("PVE2_PLUGINS_INSTALL", install)
    if force is not None:
        monkeypatch.setenv("PVE2_PLUGINS_FORCE", force)
    else:
        monkeypatch.delenv("PVE2_PLUGINS_FORCE", raising=False)
    plugins_dir.mkdir(parents=True, exist_ok=True)
    # Point _make_client at a plain boto3 client: under mock_aws() the botocore
    # layer is intercepted regardless of the endpoint URL.
    from unittest.mock import patch

    import boto3

    with patch.object(
        store, "_make_client", lambda: boto3.client("s3", region_name="us-east-1")
    ):
        return store.sync_installed_plugins(plugins_dir)


def test_disabled_when_store_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("PVE2_PLUGIN_STORE_URL", raising=False)
    plugins_dir = tmp_path / "plugins"
    report = store.sync_installed_plugins(plugins_dir)
    assert report == {"enabled": False, "installed": [], "skipped": [], "errors": []}


def test_enabled_but_no_install_list_is_noop(monkeypatch, tmp_path, trusted_env):
    monkeypatch.delenv("PVE2_PLUGINS_INSTALL", raising=False)
    report = store.sync_installed_plugins(tmp_path / "plugins")
    assert report["enabled"] is True
    assert report["installed"] == [] and report["errors"] == []


def test_installer_happy_path(trusted_env, tmp_path, keypair, monkeypatch):
    plugins_dir = tmp_path / "plugins"
    with mock_aws():
        _publish(keypair, tmp_path / "scratch", [("goodplug", {
            "capabilities": {"reads": ["vm_state"], "writes": ["dns_records"]},
        })])
        report = _sync(monkeypatch, plugins_dir)
    assert report["errors"] == [], report
    assert report["installed"] == ["goodplug"]
    installed = plugins_dir / "goodplug"
    assert (installed / "__init__.py").is_file()
    # installed unit is signature-verifiable → loader derives trusted tier
    assert verify_manifest_signature(installed) is True
    provenance = json.loads((installed / PROVENANCE_FILENAME).read_text())
    assert provenance["signed"] is True
    assert provenance["plugin_id"] == "goodplug"
    assert provenance["version"] == "1.0.0"
    assert load_manifest(installed).id == "goodplug"


def test_installed_plugin_skipped_on_second_run(trusted_env, tmp_path, keypair, monkeypatch):
    plugins_dir = tmp_path / "plugins"
    with mock_aws():
        _publish(keypair, tmp_path / "scratch", [("goodplug", {})])
        first = _sync(monkeypatch, plugins_dir)
        second = _sync(monkeypatch, plugins_dir)
    assert first["installed"] == ["goodplug"]
    assert second["installed"] == []
    assert second["skipped"] == ["goodplug"]


def test_version_pinned_install(trusted_env, tmp_path, keypair, monkeypatch):
    plugins_dir = tmp_path / "plugins"
    with mock_aws():
        _publish(keypair, tmp_path / "scratch", [
            ("goodplug", {"version": "1.0.0"}),
            ("goodplug", {"version": "1.1.0"}),
        ])
        report = _sync(monkeypatch, plugins_dir, install="goodplug==1.0.0")
    assert report["errors"] == []
    assert load_manifest(plugins_dir / "goodplug").version == "1.0.0"


def test_unknown_pinned_version_fails_closed(trusted_env, tmp_path, keypair, monkeypatch):
    plugins_dir = tmp_path / "plugins"
    with mock_aws():
        _publish(keypair, tmp_path / "scratch", [("goodplug", {"version": "1.0.0"})])
        report = _sync(monkeypatch, plugins_dir, install="goodplug==9.9.9")
    assert len(report["errors"]) == 1 and "goodplug" in report["errors"][0]["id"]
    assert not (plugins_dir / "goodplug").exists()


def test_tampered_manifest_refused(trusted_env, tmp_path, keypair, monkeypatch):
    """Remote artifacts re-signed by a foreign key must NOT be installed."""
    plugins_dir = tmp_path / "plugins"
    scratch = tmp_path / "scratch"
    foreign = SigningKeyPair.generate()
    with mock_aws():
        _publish(keypair, scratch, [("goodplug", {})], signer=foreign)
        report = _sync(monkeypatch, plugins_dir)
    assert len(report["errors"]) == 1
    assert "public key" in report["errors"][0]["error"]
    assert not (plugins_dir / "goodplug").exists()


def test_tampered_package_checksum_refused(trusted_env, tmp_path, keypair, monkeypatch):
    """Swapping package.zip in the bucket breaks the signed checksum."""
    import boto3

    plugins_dir = tmp_path / "plugins"
    scratch = tmp_path / "scratch"
    with mock_aws():
        _publish(keypair, scratch, [("goodplug", {})])
        client = boto3.client("s3", region_name="us-east-1")
        client.put_object(
            Bucket=BUCKET, Key="goodplug/1.0.0/package.zip", Body=b"tampered bytes"
        )
        report = _sync(monkeypatch, plugins_dir)
    assert len(report["errors"]) == 1
    assert "checksum" in report["errors"][0]["error"].lower()
    assert not (plugins_dir / "goodplug").exists()


def test_id_mismatch_refused(trusted_env, tmp_path, keypair, monkeypatch):
    """A bundle whose manifest id ≠ requested id must be refused."""
    plugins_dir = tmp_path / "plugins"
    scratch = tmp_path / "scratch"
    with mock_aws():
        import boto3
        import yaml as _yaml

        from tools.build_plugin import build_plugin

        # Plugin dir 'goodplug' carries manifest id 'other' → build artifacts
        # land under build/other/; upload them under the goodplug key to
        # simulate a mislabeled release.
        d = make_plugin(scratch / "src", "goodplug", {"id": "other"})
        build_plugin(d, scratch / "build", keypair)
        client = boto3.client("s3", region_name="us-east-1")
        try:
            client.create_bucket(Bucket=BUCKET)
        except client.exceptions.BucketAlreadyOwnedByYou:
            pass
        src = scratch / "build" / "other" / "1.0.0"
        for name in ("package.zip", "plugin.yaml", "plugin.yaml.sig"):
            client.upload_file(str(src / name), BUCKET, f"goodplug/1.0.0/{name}")
        import json as _json

        manifest_dict = _yaml.safe_load(src.joinpath("plugin.yaml").read_text())
        fake_index = {
            "schema_version": 1,
            "plugins": [{
                "id": "goodplug", "name": "Good Plug",
                "versions": [{
                    "id": "goodplug", "name": "Good Plug", "version": "1.0.0",
                    "checksum": manifest_dict["checksum"], "channel": "trusted",
                }],
            }],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            _json.dump(fake_index, fh)
            tmp_name = fh.name
        client.upload_file(tmp_name, BUCKET, "index.json")
        os.unlink(tmp_name)
        report = _sync(monkeypatch, plugins_dir)
    assert len(report["errors"]) == 1
    assert "declares id" in report["errors"][0]["error"]
    assert not (plugins_dir / "goodplug").exists()


def test_plugin_missing_from_index_fails_closed(trusted_env, tmp_path, keypair, monkeypatch):
    plugins_dir = tmp_path / "plugins"
    with mock_aws():
        _publish(keypair, tmp_path / "scratch", [("goodplug", {})])
        report = _sync(monkeypatch, plugins_dir, install="ghostplug")
    assert len(report["errors"]) == 1 and "ghostplug" in report["errors"][0]["id"]


def test_poisoned_version_in_index_fails_closed(trusted_env, tmp_path, keypair, monkeypatch):
    """An index 'version' carrying path parts must be rejected before any S3 key."""
    import boto3

    plugins_dir = tmp_path / "plugins"
    with mock_aws():
        _publish(keypair, tmp_path / "scratch", [("goodplug", {})])
        client = boto3.client("s3", region_name="us-east-1")
        index = json.loads(
            client.get_object(Bucket=BUCKET, Key="index.json")["Body"].read()
        )
        index["plugins"][0]["versions"][0]["version"] = "1.0.0/../../../evil"
        client.put_object(
            Bucket=BUCKET, Key="index.json", Body=json.dumps(index).encode()
        )
        report = _sync(monkeypatch, plugins_dir, install="goodplug")
    assert len(report["errors"]) == 1
    assert "invalid version" in report["errors"][0]["error"].lower()
    assert not (plugins_dir / "goodplug").exists()


def test_malformed_install_request_reported(trusted_env, tmp_path, monkeypatch):
    plugins_dir = tmp_path / "plugins"
    with mock_aws():
        report = _sync(monkeypatch, plugins_dir, install="GOOD!PLUG")
    assert report["installed"] == []
    assert len(report["errors"]) == 1
    assert "invalid" in report["errors"][0]["error"].lower()


def test_parse_install_request_accepts_dashed_ids_and_pins():
    requests = store.parse_install_request(" pve2dns , pve2-drift==1.2.0, ghost@0.9.1 ")
    assert requests == [
        ("pve2dns", None), ("pve2-drift", "1.2.0"), ("ghost", "0.9.1")
    ]
    assert store.parse_install_request("") == []
    with pytest.raises(ValueError):
        store.parse_install_request("1bad")
    with pytest.raises(ValueError):
        store.parse_install_request("pve2dns==not-a-version")


def test_upgrade_flow_v1_to_v2(trusted_env, tmp_path, keypair, monkeypatch):
    plugins_dir = tmp_path / "plugins"
    scratch = tmp_path / "scratch"
    with mock_aws():
        _publish(keypair, scratch, [("goodplug", {"version": "1.0.0"})])
        _sync(monkeypatch, plugins_dir)
        # publish a newer version of the same plugin
        _publish(keypair, scratch, [("goodplug", {"version": "1.1.0"})])
        report = _sync(monkeypatch, plugins_dir)  # bare id → latest
        assert report["installed"] == ["goodplug"]
        assert "updated" in report["installed"][0] or load_manifest(
            plugins_dir / "goodplug"
        ).version == "1.1.0"
    assert load_manifest(plugins_dir / "goodplug").version == "1.1.0"
    leftover = [p.name for p in plugins_dir.iterdir() if p.name.startswith((".staging", ".old"))]
    assert leftover == []


def test_force_reinstall_same_version(trusted_env, tmp_path, keypair, monkeypatch):
    plugins_dir = tmp_path / "plugins"
    with mock_aws():
        _publish(keypair, tmp_path / "scratch", [("goodplug", {})])
        _sync(monkeypatch, plugins_dir)
        report = _sync(monkeypatch, plugins_dir, force="true")
    assert report["installed"] == ["goodplug"]


def test_bundled_same_version_not_reinstalled(trusted_env, tmp_path, keypair, monkeypatch):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    # pretend the plugin is already installed (same version as the store one)
    make_plugin(plugins_dir, "goodplug")
    with mock_aws():
        _publish(keypair, tmp_path / "scratch", [("goodplug", {})])
        report = _sync(monkeypatch, plugins_dir)
    assert report["installed"] == []
    assert report["skipped"] == ["goodplug"]


def test_safe_extract_rejects_path_traversal(tmp_path, monkeypatch):

    target = tmp_path / "victim"
    payload = "EVIL"
    with zipfile.ZipFile(tmp_path / "evil.zip", "w") as zf:
        zf.writestr("__init__.py", "X = 1\n")
        zf.writestr("../evil.txt", payload)
    with pytest.raises(TrustError):
        store._safe_extract_zip(tmp_path / "evil.zip", target, 1024 * 1024)
    # nothing leaked outside the extracted dir
    assert not (tmp_path / "evil.txt").exists()
    assert not target.exists()


def test_safe_extract_rejects_dir_entry_traversal(tmp_path):
    """A zip DIRECTORY entry like "../evil/" must not escape dest either."""

    with zipfile.ZipFile(tmp_path / "evil.zip", "w") as zf:
        zf.writestr("__init__.py", "X = 1\n")
        zf.writestr("../evil-dir/", "")
    with pytest.raises(TrustError):
        store._safe_extract_zip(tmp_path / "evil.zip", tmp_path / "out", 1024 * 1024)
    assert not (tmp_path / "evil-dir").exists()
    assert not (tmp_path / "out" / "..").parent.joinpath("evil-dir").exists()


def test_safe_extract_rejects_zip_bomb(tmp_path):

    with zipfile.ZipFile(tmp_path / "bomb.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("__init__.py", "X = 'x' * 10\n")
        zf.writestr("huge.txt", b"\0" * (16 * 1024 * 1024))
    with pytest.raises(TrustError):
        store._safe_extract_zip(tmp_path / "bomb.zip", tmp_path / "out", 512 * 1024)
    assert not (tmp_path / "out").exists()