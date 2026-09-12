#!/usr/bin/env python
"""Build, sign and publish PVE2Services plugins to the plugin store.

Per-version artifacts (uploaded to s3://<bucket>/<plugin_id>/<version>/):

    package.zip      plugin code, WITHOUT the manifest
    plugin.yaml      manifest; ``checksum`` = sha256 of package.zip
    plugin.yaml.sig  base64 ed25519 signature over the raw bytes of plugin.yaml

Why the manifest is not inside the zip: the checksum it carries is the hash of
the zip itself — stamping a manifest with "sha256(self+me)" is circular. The
installer reassembles the unit on disk (code + signed manifest + signature),
which is exactly what the loader expects to find in the plugins/ dir.

The manifest signature is what makes a plugin TRUSTED; the checksum bounds the
package bytes. Both are verified fail-closed by the installer (libs/plugin_store.py).

Examples:
    pve2-build-plugin plugins/PVE2DNS                  # build+sign
    pve2-build-plugin plugins/* --publish              # upload+index

The same logic is reachable as ``python tools/build_plugin.py`` from the core
checkout and from the plugin repo (installed ``pve2`` wheel).

Publish env:
    PVE2_STORE_ENDPOINT / PVE2_STORE_BUCKET / PVE2_STORE_ACCESS_KEY /
    PVE2_STORE_SECRET_KEY / PVE2_STORE_REGION
    PVE2_PLUGIN_SIGNING_KEY   path to the ed25519 private key (required)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization

from PVE2Services.libs.errors import ManifestError
from PVE2Services.libs.plugin_manifest import (
    MANIFEST_FILENAME,
    load_manifest,
)
from PVE2Services.libs.plugin_signing import (
    MANIFEST_SIGNATURE_FILENAME,
    SigningKeyPair,
    load_public_key,
    resolve_public_key,
    sign_file,
    verify_bytes,
)

DEFAULT_BUCKET = "pve2services-plugins"
SKIP_DIRS = {"__pycache__", ".pytest_cache", "build", ".staging"}
SKIP_SUFFIXES = {".pyc", ".sig", ".bak"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 128), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_keypair(priv_path: Path) -> SigningKeyPair:
    if not priv_path.is_file():
        raise SystemExit(
            f"Signing key {priv_path} not found. Generate one with "
            "tools/generate_keys.py — keep the private key out of the repo."
        )
    key = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
    return SigningKeyPair(key)


def build_plugin(plugin_dir: Path, out_dir: Path, keypair: SigningKeyPair) -> dict:
    """Build + sign one plugin; returns its index entry."""
    import zipfile

    import yaml

    try:
        manifest = load_manifest(plugin_dir)
    except ManifestError as exc:
        # CLI-friendly failure: pydantic errors roll up to SystemExit too
        raise SystemExit(f"invalid manifest in {plugin_dir}: {exc.message}") from exc
    build_root = out_dir / manifest.id / manifest.version
    if build_root.exists():
        shutil.rmtree(build_root)
    build_root.mkdir(parents=True)

    package_path = build_root / "package.zip"
    members: list[Path] = []
    for member in sorted(plugin_dir.rglob("*")):
        if not member.is_file():
            continue
        rel = member.relative_to(plugin_dir)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if member.suffix in SKIP_SUFFIXES:
            continue
        if member.name in (MANIFEST_FILENAME, MANIFEST_SIGNATURE_FILENAME):
            continue
        members.append(member)

    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in members:
            zf.write(member, arcname=str(member.relative_to(plugin_dir)))

    checksum = f"sha256:{_sha256_file(package_path)}"
    stamped = manifest.model_copy(update={"checksum": checksum})
    manifest_file = build_root / MANIFEST_FILENAME
    payload = json.loads(stamped.model_dump_json())
    manifest_file.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))

    signature_file = sign_file(manifest_file, keypair)

    # Self-check: re-verify everything we just produced, as an installer would.
    if not verify_bytes(
        manifest_file.read_bytes(),
        signature_file.read_text().strip(),
        load_public_key(keypair.public_pem()),
    ):
        raise SystemExit(f"Internal error: signature self-check failed for {manifest.id}")

    return {
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "checksum": checksum,
        "core_compat": manifest.core_compat,
        "channel": "trusted",
        "published_at": datetime.now(timezone.utc).isoformat(),
    }


def _merge_builds_into_index(index: dict, builds: list[dict]) -> dict:
    """Insert/replace ``builds`` inside ``index`` (by id + version)."""
    for entry in builds:
        plugin = next((p for p in index["plugins"] if p.get("id") == entry["id"]), None)
        if plugin is None:
            plugin = {"id": entry["id"], "name": entry["name"], "versions": []}
            index["plugins"].append(plugin)
        plugin["name"] = entry["name"] or plugin.get("name") or entry["id"]
        plugin["versions"] = [
            v for v in plugin["versions"] if v.get("version") != entry["version"]
        ]
        plugin["versions"].append(entry)
    index["plugins"].sort(key=lambda p: p.get("id", ""))
    index["generated_at"] = datetime.now(timezone.utc).isoformat()
    return index


def merge_local_index(out_dir: Path, builds: list[dict]) -> dict:
    """Update the local index.json with freshly built versions."""
    index_path = out_dir / "index.json"
    if index_path.is_file():
        index = json.loads(index_path.read_text())
        if not isinstance(index, dict) or index.get("schema_version") != _SCHEMA_VERSION:
            index = {"schema_version": _SCHEMA_VERSION, "plugins": []}
    else:
        index = {"schema_version": _SCHEMA_VERSION, "plugins": []}
    index = _merge_builds_into_index(index, builds)
    index_path.write_text(json.dumps(index, indent=2))
    return index


def _empty_index() -> dict:
    return {"schema_version": _SCHEMA_VERSION, "plugins": []}


def load_publish_client() -> tuple:
    endpoint = os.environ.get("PVE2_STORE_ENDPOINT", "").strip()
    if not endpoint:
        raise SystemExit("--publish requires PVE2_STORE_ENDPOINT")
    import boto3
    from botocore.config import Config

    bucket = os.environ.get("PVE2_STORE_BUCKET", DEFAULT_BUCKET).strip() or DEFAULT_BUCKET
    kwargs: dict[str, Any] = {
        "endpoint_url": endpoint,
        "region_name": os.environ.get("PVE2_STORE_REGION", "us-east-1").strip() or "us-east-1",
        "verify": True if endpoint.startswith("https") else False,
        "config": Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 3}),
    }
    access_key = os.environ.get("PVE2_STORE_ACCESS_KEY", "").strip()
    secret_key = os.environ.get("PVE2_STORE_SECRET_KEY", "").strip()
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client("s3", **kwargs), bucket


_SCHEMA_VERSION = 1


def publish(client, bucket: str, out_dir: Path, builds: list[dict]) -> None:
    """Upload per-version artifacts, then upload a MERGED index snapshot.

    The uploaded index is remote(older) + local(new builds) merged together:
    uploading the local index as-is would drop versions published by other
    runs of the release pipeline.
    """
    for entry in builds:
        root = out_dir / entry["id"] / entry["version"]
        for name in ("package.zip", MANIFEST_FILENAME, MANIFEST_SIGNATURE_FILENAME):
            client.upload_file(
                str(root / name), bucket, f"{entry['id']}/{entry['version']}/{name}"
            )

    try:
        remote = json.loads(client.get_object(Bucket=bucket, Key="index.json")["Body"].read())
    except client.exceptions.NoSuchKey:
        remote = {}
    merged = remote if isinstance(remote, dict) else {}
    if merged.get("schema_version") != _SCHEMA_VERSION:
        merged = _empty_index()

    merged = _merge_builds_into_index(merged, builds)
    index_path = out_dir / "index.json"
    index_path.write_text(json.dumps(merged, indent=2))
    client.upload_file(str(index_path), bucket, "index.json")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Build, sign and publish PVE2Services plugins."
    )
    parser.add_argument("plugin_dirs", nargs="*", help="plugin directories to build")
    parser.add_argument("--out", default="build", help="local build output dir")
    parser.add_argument("--publish", action="store_true", help="upload to the S3 store")
    parser.add_argument(
        "--show-fingerprint",
        action="store_true",
        help="print the fingerprint of the trusted public key and exit",
    )
    parser.add_argument(
        "--signing-key",
        default=None,
        help="ed25519 private key PEM path (default: $PVE2_PLUGIN_SIGNING_KEY)",
    )
    args = parser.parse_args()

    if args.show_fingerprint:
        key = resolve_public_key()
        print("trusted key fingerprint:", key.public_bytes_raw().hex()[:32])
        return 0
    if not args.plugin_dirs:
        parser.error("provide at least one plugin directory")

    priv_env = os.environ.get("PVE2_PLUGIN_SIGNING_KEY", "")
    key_path = args.signing_key or priv_env or "plugin_signing.pem"
    keypair = _load_keypair(Path(key_path))

    out_dir = Path(args.out)
    builds: list[dict] = []
    for raw in args.plugin_dirs:
        plugin_dir = Path(raw)
        if not plugin_dir.is_dir():
            raise SystemExit(f"{plugin_dir} is not a directory")
        entry = build_plugin(plugin_dir, out_dir, keypair)
        builds.append(entry)
        print(
            f"built {entry['id']} v{entry['version']} "
            f"{entry['checksum'][:19]}... -> {out_dir}/{entry['id']}/{entry['version']}/"
        )

    merge_local_index(out_dir, builds)
    print(f"local index -> {out_dir}/index.json ({len(builds)} version(s) processed)")

    if args.publish:
        client, bucket = load_publish_client()
        publish(client, bucket, out_dir, builds)
        print(f"published {len(builds)} package(s) to s3://{bucket}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
