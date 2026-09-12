"""Plugin store installer — populates the local plugins/ dir from an S3 bucket.

Distribution channel for the MVP "trusted" catalog (see
pve2services-plugin-store.md). Runs at core startup, BEFORE plugin discovery,
and is a strict no-op unless configured:

    PVE2_PLUGIN_STORE_URL      S3 endpoint (e.g. http://minio:9000)
    PVE2_PLUGIN_STORE_BUCKET   bucket name (default: pve2services-plugins)
    PVE2_PLUGIN_STORE_ACCESS_KEY / PVE2_PLUGIN_STORE_SECRET_KEY
    PVE2_PLUGIN_STORE_REGION   default us-east-1
    PVE2_PLUGIN_STORE_VERIFY_TLS  default true (set false only for self-signed MinIO)
    PVE2_PLUGINS_INSTALL       comma list: ``id`` (latest), ``id==1.2.0`` or ``id@1.2.0``

Per-package install pipeline (fail-closed at every step):

    index.json -> pick version -> download plugin.yaml + plugin.yaml.sig
      -> verify ed25519 signature against the project public key
      -> download package.zip -> verify sha256 matches manifest checksum
      -> safe-extract into a staging dir (traversal/bomb protection)
      -> sanity-check the extracted tree
      -> atomically swap into plugins/<id>/ + write provenance file

Already-installed plugins at the requested version are skipped unless
``PVE2_PLUGINS_FORCE=true``. The function is idempotent and safe to run on
several replicas sharing one volume (same bytes, last rename wins).
Failures of individual plugins are collected into the report; the store
MECHANISM failing (no config, unreachable bucket) never blocks core boot.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from PVE2Services.libs.errors import ManifestError, TrustError
from PVE2Services.libs.plugin_manifest import (
    MANIFEST_FILENAME,
    PROVENANCE_FILENAME,
    load_manifest,
    valid_plugin_id,
)
from PVE2Services.libs.plugin_signing import (
    MANIFEST_SIGNATURE_FILENAME,
    resolve_public_key,
    verify_bytes,
)

logger = logging.getLogger("pve2.plugin_store")

DEFAULT_BUCKET = "pve2services-plugins"
DEFAULT_REGION = "us-east-1"
DEFAULT_PACKAGE_MAX_BYTES = 100 * 1024 * 1024      # 100 MiB single package cap
DEFAULT_EXTRACT_MAX_BYTES = 2 * 100 * 1024 * 1024  # 200 MiB total extracted cap

STORE_URL_ENV = "PVE2_PLUGIN_STORE_URL"
BUCKET_ENV = "PVE2_PLUGIN_STORE_BUCKET"
ACCESS_KEY_ENV = "PVE2_PLUGIN_STORE_ACCESS_KEY"
SECRET_KEY_ENV = "PVE2_PLUGIN_STORE_SECRET_KEY"
REGION_ENV = "PVE2_PLUGIN_STORE_REGION"
VERIFY_TLS_ENV = "PVE2_PLUGIN_STORE_VERIFY_TLS"
INSTALL_LIST_ENV = "PVE2_PLUGINS_INSTALL"
FORCE_ENV = "PVE2_PLUGINS_FORCE"
PACKAGE_MAX_BYTES_ENV = "PVE2_PLUGIN_PACKAGE_MAX_BYTES"
EXTRACT_MAX_BYTES_ENV = "PVE2_PLUGIN_EXTRACT_MAX_BYTES"

_SCHEMA_VERSION = 1


def _env_flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


def store_enabled() -> bool:
    """True when a store endpoint is configured (installer runs at boot)."""
    return bool(os.environ.get(STORE_URL_ENV, "").strip())


def parse_install_request(raw: str) -> list[tuple[str, str | None]]:
    """Parse ``PVE2_PLUGINS_INSTALL`` into (plugin_id, version-or-None).

    Accepts ``pve2dns``, ``pve2dns==1.2.0`` and ``pve2dns@1.2.0`` (comma
    separated, whitespace tolerated). Version must be valid semver. Raises
    ValueError on malformed input so the caller can abort cleanly.
    """
    wanted: list[tuple[str, str | None]] = []
    for chunk in raw.split(","):
        entry = chunk.strip()
        if not entry:
            continue
        version: str | None = None
        plugin_id = entry
        for sep in ("==", "@"):
            if sep in entry:
                plugin_id, version = (part.strip() for part in entry.split(sep, 1))
                break
        if not valid_plugin_id(plugin_id):
            raise ValueError(f"Invalid plugin id in {INSTALL_LIST_ENV}: {entry!r}")
        if version is not None:
            try:
                Version(version)
            except InvalidVersion as exc:
                raise ValueError(
                    f"Invalid version for {plugin_id!r}: {version!r} ({exc})"
                ) from exc
        wanted.append((plugin_id, version))
    return wanted


def _make_client():
    """Create the boto3 S3 client for the configured endpoint."""
    import boto3
    from botocore.config import Config

    endpoint = os.environ[STORE_URL_ENV].strip()
    verify_tls = _env_flag(VERIFY_TLS_ENV, "true")
    # boto3 expects verify=False or a CA bundle path; strip if http anyway.
    verify: bool | str = verify_tls if endpoint.startswith("https") else False
    kwargs: dict[str, Any] = {
        "endpoint_url": endpoint,
        "region_name": os.environ.get(REGION_ENV, DEFAULT_REGION).strip() or DEFAULT_REGION,
        "verify": verify,
        "config": Config(
            connect_timeout=10,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    }
    access_key = os.environ.get(ACCESS_KEY_ENV, "").strip()
    secret_key = os.environ.get(SECRET_KEY_ENV, "").strip()
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client("s3", **kwargs)


def _fetch_index(client: Any, bucket: str) -> dict[str, Any]:
    """Download + parse index.json from the bucket root."""
    try:
        body = client.get_object(Bucket=bucket, Key="index.json")["Body"].read()
    except client.exceptions.NoSuchKey as exc:
        raise TrustError("Store index.json not found in bucket") from exc
    try:
        index = json.loads(body)
    except json.JSONDecodeError as exc:
        raise TrustError(f"Store index.json is not valid JSON: {exc}") from exc
    if not isinstance(index, dict) or not isinstance(index.get("plugins"), list):
        raise TrustError("Store index.json is missing the 'plugins' list")
    return index


def _resolve_version(
    index: dict[str, Any], plugin_id: str, requested: str | None
) -> dict[str, Any]:
    """Return the index entry of the target version (fail-closed)."""
    for entry in index["plugins"]:
        if isinstance(entry, dict) and entry.get("id") == plugin_id:
            break
    else:
        raise TrustError(f"Plugin {plugin_id!r} not present in the store index")

    versions = entry.get("versions") or []
    if not versions:
        raise TrustError(f"Plugin {plugin_id!r} has no published versions")

    def _as_version(v: dict[str, Any]) -> Version:
        try:
            return Version(str(v.get("version", "")))
        except InvalidVersion:
            return Version("0")

    if requested is None:
        return max(versions, key=_as_version)
    for v in versions:
        if v.get("version") == requested:
            return v
    raise TrustError(
        f"Version {requested!r} of plugin {plugin_id!r} not found in the store index"
    )


def _download_to(
    client: Any, bucket: str, key: str, dest: Path, max_bytes: int
) -> None:
    """Streaming download with a hard size cap (zip-bomb protection)."""
    obj = client.get_object(Bucket=bucket, Key=key)
    body = obj["Body"]
    requested = obj.get("ContentLength", max_bytes)
    if requested > max_bytes:
        raise TrustError(
            f"Remote object {key} is {requested} bytes (cap {max_bytes})"
        )
    total = 0
    with dest.open("wb") as fh:
        for chunk in body.iter_chunks(1024 * 64):
            total += len(chunk)
            if total > max_bytes:
                raise TrustError(f"Download of {key} exceeded {max_bytes} bytes")
            fh.write(chunk)


def _safe_extract_zip(zip_path: Path, dest_dir: Path, max_total_bytes: int) -> int:
    """Extract a zip safely (no traversal, size cap). Returns extracted bytes.

    Member names are normalized and validated to stay inside ``dest_dir``;
    absolute paths, drive letters and ``..`` components are rejected. Non-UTF8
    or absolute entries raise TrustError instead of guessing.
    """
    def _validate_member(info: zipfile.ZipInfo) -> str:
        """Normalize and validate one member name (file or directory)."""
        name = info.filename
        if not name:
            raise TrustError("Rejected empty zip member name")
        if "\x00" in name:
            raise TrustError(f"Rejected zip member with NUL byte: {name!r}")
        normalized = name.replace("\\", "/").rstrip("/")
        if not normalized:
            raise TrustError(f"Rejected useless zip member: {name!r}")
        if normalized.startswith("/") or (
            len(normalized) > 1 and normalized[1] == ":"
        ):
            raise TrustError(f"Rejected absolute path in package: {name!r}")
        target = (dest_dir / normalized).resolve()
        if dest_root != target and dest_root not in target.parents:
            raise TrustError(f"Rejected path traversal in package: {name!r}")
        return normalized

    total = 0
    dest_root = dest_dir.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        infos = [info for info in zf.infolist() if info.filename]
        # Validation pass FIRST (files AND directories — a zip entry like
        # "../evil/" must fail loudly before anything touches the disk).
        for info in infos:
            if info.is_dir():
                _validate_member(info)
                continue
            normalized = _validate_member(info)
            total += info.file_size
            if total > max_total_bytes:
                raise TrustError(
                    f"Extracted size exceeds cap of {max_total_bytes} bytes"
                )
        # Extract pass — re-validate (cheap) so names can never diverge.
        for info in infos:
            normalized = _validate_member(info)
            if info.is_dir():
                (dest_dir / normalized).mkdir(parents=True, exist_ok=True)
                continue
            target = dest_dir / normalized
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    return total


def _installed_version(plugin_dir: Path) -> str | None:
    """Version of the currently installed plugin dir, if parseable."""
    manifest_path = plugin_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        manifest = load_manifest(plugin_dir)
    except ManifestError:
        return None
    return manifest.version


def _already_satisfied(
    plugins_dir: Path, plugin_id: str, target_version: str, force: bool
) -> tuple[bool, str]:
    """True when the plugin is already installed at exactly ``target_version``."""
    # Look for the canonical dir name, and any bundle dir that maps to it.
    candidates = [d for d in plugins_dir.iterdir() if d.is_dir() and d.name.lower() == plugin_id]
    for directory in candidates:
        installed = _installed_version(directory)
        if installed is None:
            continue
        if force:
            return False, f"reinstall forced over v{installed}"
        if installed == target_version:
            return True, f"v{installed} already installed"
    return False, "not installed"


def install_plugin(
    client: Any,
    bucket: str,
    plugin_id: str,
    version: str | None,
    plugins_dir: Path,
    force: bool = False,
    index: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Install/upgrade a single plugin.

    Returns (status, detail) where status is ``installed`` / ``updated`` /
    ``skipped``. Raises TrustError/ManifestError on any integrity failure —
    callers must treat this plugin as NOT installed and leave existing state
    untouched. ``index`` may be passed by ``sync_installed_plugins`` to avoid
    re-downloading the catalog for every plugin.
    """
    # Resolve the target from the index FIRST: the "already installed" check
    # compares against the exact version we are about to install, so that a
    # newer store release upgrades an older local copy (latest = bare id).
    if index is None:
        index = _fetch_index(client, bucket)
    entry = _resolve_version(index, plugin_id, version)
    target_version = str(entry.get("version"))
    # The index is unsigned: never trust its `version` field for path or key
    # construction (e.g. "1.0.0/../../other") — parse it as a version first.
    try:
        Version(target_version)
    except InvalidVersion as exc:
        raise TrustError(
            f"Store index lists invalid version {target_version!r} for "
            f"{plugin_id!r}"
        ) from exc

    skip, reason = _already_satisfied(plugins_dir, plugin_id, target_version, force)
    if skip:
        return "skipped", reason

    package_max = int(os.environ.get(PACKAGE_MAX_BYTES_ENV, DEFAULT_PACKAGE_MAX_BYTES))
    extract_max = int(os.environ.get(EXTRACT_MAX_BYTES_ENV, DEFAULT_EXTRACT_MAX_BYTES))
    prefix = f"{plugin_id}/{target_version}"
    key_manifest = f"{prefix}/{MANIFEST_FILENAME}"
    key_sig = f"{prefix}/{MANIFEST_SIGNATURE_FILENAME}"
    key_package = f"{prefix}/package.zip"

    with tempfile.TemporaryDirectory(prefix="pve2-store-") as tmp:
        tmp_dir = Path(tmp)
        manifest_file = tmp_dir / MANIFEST_FILENAME
        sig_file = tmp_dir / MANIFEST_SIGNATURE_FILENAME
        package_file = tmp_dir / "package.zip"

        # 1) manifest + signature — authenticate the manifest first.
        _download_to(client, bucket, key_manifest, manifest_file, package_max)
        _download_to(client, bucket, key_sig, sig_file, package_max)
        public_key = resolve_public_key()
        if not verify_bytes(
            manifest_file.read_bytes(), sig_file.read_text().strip(), public_key
        ):
            raise TrustError(
                f"Signature of {key_manifest} does not verify against the "
                "project public key — refusing to install"
            )

        # The downloaded manifest must parse and declare the expected id.
        try:
            manifest = load_manifest(tmp_dir)
        except ManifestError as exc:
            raise TrustError(
                f"Signed manifest for {plugin_id!r} is invalid: {exc.message}"
            ) from exc
        if manifest.id != plugin_id:
            raise TrustError(
                f"Signed manifest declares id {manifest.id!r}, expected {plugin_id!r}"
            )
        if manifest.checksum is None:
            raise TrustError(
                f"Signed manifest for {plugin_id!r} has no checksum — "
                "package integrity cannot be verified"
            )
        if target_version != manifest.version:
            raise TrustError(
                f"Index entry says v{target_version} but signed manifest is "
                f"v{manifest.version}"
            )

        # 2) package — checksum must match the signed manifest.
        _download_to(client, bucket, key_package, package_file, package_max)
        digest = _sha256_file(package_file)
        if f"sha256:{digest}" != manifest.checksum:
            raise TrustError(
                f"Checksum mismatch for {plugin_id} v{target_version}: "
                f"expected {manifest.checksum}, got sha256:{digest}"
            )

        # 3) extract into a staging dir, sanity-check, then atomically swap.
        staging = plugins_dir / f".staging-{plugin_id}-{os.getpid()}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            _safe_extract_zip(package_file, staging, extract_max)
            # The package carries code only; the downloaded, signature-verified
            # manifest + sidecar complete the unit on disk.
            shutil.copy2(manifest_file, staging / MANIFEST_FILENAME)
            shutil.copy2(sig_file, staging / MANIFEST_SIGNATURE_FILENAME)
            if not (staging / "__init__.py").is_file():
                raise TrustError(
                    f"Package for {plugin_id!r} does not contain __init__.py"
                )
            extracted_manifest = load_manifest(staging)
            if extracted_manifest.id != plugin_id:
                raise TrustError(
                    f"Extracted manifest declares id {extracted_manifest.id!r}, "
                    f"expected {plugin_id!r}"
                )
            (staging / PROVENANCE_FILENAME).write_text(
                json.dumps(
                    {
                        "source": "plugin-store",
                        "bucket": bucket,
                        "plugin_id": plugin_id,
                        "version": target_version,
                        "checksum": manifest.checksum,
                        "signed": True,
                        "installed_at": datetime.now(timezone.utc).isoformat(),
                    },
                    indent=2,
                )
            )
        except TrustError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise TrustError(f"Failed to extract package for {plugin_id!r}: {exc}") from exc

        action = _swap_into_place(staging, plugins_dir / plugin_id)
        return action, f"v{target_version}"


def _swap_into_place(staging: Path, target: Path) -> str:
    """Atomically move ``staging`` onto ``target``; restores on failure."""
    backup: Path | None = None
    if target.exists():
        backup = target.with_name(f".old-{target.name}-{os.getpid()}")
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        os.rename(target, backup)
    try:
        os.rename(staging, target)
    except OSError as exc:
        if backup is not None:
            os.rename(backup, target)
        shutil.rmtree(staging, ignore_errors=True)
        raise TrustError(f"Failed to move {staging} into place: {exc}") from exc
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)
        return "updated"
    return "installed"


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 128), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync_installed_plugins(plugins_dir: Path) -> dict[str, Any]:
    """Entry point called at core startup: ensure requested plugins are present.

    Never raises. Returns a report dict:
        {"enabled": bool, "installed": [...], "skipped": [...], "errors": [...]}
    """
    report: dict[str, Any] = {"enabled": False, "installed": [], "skipped": [], "errors": []}
    if not store_enabled():
        return report
    report["enabled"] = True
    if not os.environ.get(INSTALL_LIST_ENV, "").strip():
        logger.info("Plugin store configured but %s is empty — nothing to install", INSTALL_LIST_ENV)
        return report
    try:
        wanted = parse_install_request(os.environ[INSTALL_LIST_ENV])
    except ValueError as exc:
        logger.error("Plugin store disabled for this run: %s", exc)
        report["errors"].append({"id": "*", "error": str(exc)})
        return report

    client = _make_client()
    bucket = os.environ.get(BUCKET_ENV, DEFAULT_BUCKET).strip() or DEFAULT_BUCKET
    force = _env_flag(FORCE_ENV)
    # One index fetch for the whole request set; install_plugin reuses it.
    index = _fetch_index(client, bucket)
    logger.info(
        "Plugin store sync: %d requested plugin(s) from s3://%s", len(wanted), bucket
    )
    for plugin_id, version in wanted:
        label = f"{plugin_id}@{version}" if version else plugin_id
        try:
            status, detail = install_plugin(
                client, bucket, plugin_id, version, plugins_dir, force, index=index
            )
            logger.info("Plugin store: %s -> %s (%s)", label, status, detail)
            # "updated" belongs to the installed bucket: v-new on disk
            report["installed" if status == "updated" else status].append(label)
        except (TrustError, ManifestError) as exc:
            logger.error("Plugin store: %s NOT installed: %s", label, exc.message)
            report["errors"].append({"id": label, "error": exc.message})
        except Exception as exc:  # noqa: BLE001 — remote/storage errors are expected
            logger.error("Plugin store: %s NOT installed: %s", label, exc)
            report["errors"].append({"id": label, "error": str(exc)})
    return report
