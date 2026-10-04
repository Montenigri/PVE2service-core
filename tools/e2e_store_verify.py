#!/usr/bin/env python
"""E2E verifier for the plugin signing -> store -> install channel.

Run by ``tools/e2e_plugin_store.sh`` inside a python:3.12 container with a REAL
MinIO endpoint (no moto). It performs the whole cycle in one process:

  1. ensure the bucket exists
  2. generate an ephemeral ed25519 keypair
  3. build + sign the target plugin and publish it to the store
  4. point the core's startup installer at that store and import the real app,
     which runs ``sync_installed_plugins()`` then plugin discovery
  5. assert: code + signed manifest + signature on disk, provenance file,
     signature verifies against the trusted public key, effective trust tier
     is ``trusted`` (signed) and the loader actually loads the plugin

Environment (all required unless noted):
  PVE2_STORE_ENDPOINT        e.g. http://localhost:19000
  PVE2_STORE_BUCKET          default pve2services-plugins
  PVE2_STORE_ACCESS_KEY / PVE2_STORE_SECRET_KEY
  PVE2_PLUGIN_SRC            host path, mounted at /plugin-src (default)
  PVE2_E2E_WORK_DIR          scratch dir (default /work)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    endpoint = os.environ.get("PVE2_STORE_ENDPOINT", "").strip()
    bucket = os.environ.get("PVE2_STORE_BUCKET", "pve2services-plugins").strip()
    access = os.environ.get("PVE2_STORE_ACCESS_KEY", "").strip()
    secret = os.environ.get("PVE2_STORE_SECRET_KEY", "").strip()
    if not endpoint:
        _fail("PVE2_STORE_ENDPOINT is required")

    plugin_src = Path(os.environ.get("PVE2_PLUGIN_SRC", "/plugin-src"))
    work = Path(os.environ.get("PVE2_E2E_WORK_DIR", "/work"))
    if not plugin_src.is_dir():
        _fail(f"plugin source dir not found: {plugin_src}")

    # 1) ensure the bucket exists (MinIO bucket is created by store-init, but
    #    make the verifier self-sufficient/idempotent).
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        config=Config(s3={"addressing_style": "path"}),
    )
    try:
        client.head_bucket(Bucket=bucket)
        print(f"OK  bucket {bucket!r} present")
    except Exception:
        client.create_bucket(Bucket=bucket)
        print(f"OK  bucket {bucket!r} created")

    # 2) ephemeral keypair; its public half becomes the trusted key.
    from PVE2Services.libs.plugin_signing import SigningKeyPair

    keypair = SigningKeyPair.generate()
    pub_path = work / "plugin_signing.pub"
    priv_path = work / "plugin_signing.pem"
    keypair.write_public_pem(pub_path)
    keypair.write_private_pem(priv_path)
    print("OK  keypair generated (public -> trusted key, private -> signer)")

    # 3) build + sign + publish.
    os.environ["PVE2_STORE_ENDPOINT"] = endpoint
    os.environ["PVE2_STORE_BUCKET"] = bucket
    os.environ["PVE2_STORE_ACCESS_KEY"] = access
    os.environ["PVE2_STORE_SECRET_KEY"] = secret
    os.environ.setdefault("PVE2_STORE_REGION", "us-east-1")

    from PVE2Services.libs.plugin_build import (
        build_plugin,
        load_publish_client,
        merge_local_index,
        publish,
    )

    build_dir = work / "build"
    entry = build_plugin(plugin_src, build_dir, keypair)
    plugin_id = entry["id"]
    merge_local_index(build_dir, [entry])
    publish_client, bucket_name = load_publish_client()
    publish(publish_client, bucket_name, build_dir, [entry])
    print(f"OK  published {plugin_id} v{entry['version']} to s3://{bucket_name}")

    # 4) install through the real core startup path.
    plugins_dir = work / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    os.environ["PVE2_PLUGIN_STORE_URL"] = endpoint
    os.environ["PVE2_PLUGIN_STORE_BUCKET"] = bucket
    os.environ["PVE2_PLUGIN_STORE_ACCESS_KEY"] = access
    os.environ["PVE2_PLUGIN_STORE_SECRET_KEY"] = secret
    os.environ["PVE2_PLUGIN_SIGNING_PUBKEY"] = str(pub_path)
    os.environ["PVE2_PLUGINS_INSTALL"] = plugin_id
    os.environ["PVE2_PLUGINS_DIR"] = str(plugins_dir)

    # Importing the app runs sync_installed_plugins(PLUGINS_DIR) then discovery.
    from PVE2Services.core.wiki_service.app import main as app_main  # noqa: F401

    # 5) assertions on the installed unit + effective trust.
    from PVE2Services.libs.plugin_loader import (
        CORE_VERSION,
        _derive_tier,
        load_all_plugins,
    )
    from PVE2Services.libs.plugin_manifest import PROVENANCE_FILENAME
    from PVE2Services.libs.plugin_signing import (
        resolve_public_key,
        verify_manifest_signature,
    )

    installed = plugins_dir / plugin_id
    for required in ("__init__.py", "plugin.yaml", "plugin.yaml.sig"):
        if not (installed / required).is_file():
            _fail(f"installed unit missing {required}: {installed}")

    prov = json.loads((installed / PROVENANCE_FILENAME).read_text())
    if prov.get("source") != "plugin-store" or prov.get("signed") is not True:
        _fail(f"unexpected provenance: {prov}")

    key = resolve_public_key()
    if not verify_manifest_signature(installed, public_key=key):
        _fail("installed manifest signature does not verify against trusted key")

    tier, signed = _derive_tier(installed, bundled=set(), public_key=key)
    if tier != "trusted" or not signed:
        _fail(f"effective tier is {tier!r} (signed={signed}), expected trusted")

    from fastapi import FastAPI

    loaded, _registry = load_all_plugins(FastAPI(), plugins_dir, core_version=CORE_VERSION)
    if plugin_id not in loaded:
        _fail(f"loader did not load {plugin_id!r}: {loaded}")

    print(f"OK  installed unit : {installed}")
    print(f"OK  provenance     : {json.dumps(prov)}")
    print(f"OK  trust tier     : {tier} (signed={signed})")
    print(f"OK  loader loaded  : {loaded}")
    print("PASS: signing -> store -> install -> trusted load verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
