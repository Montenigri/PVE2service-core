"""Ed25519 signing/verification for plugin packages and manifests.

Trust model (see pve2services-plugin-store.md):
- The release pipeline signs every published artifact with the project's
  private ed25519 key (kept in GitHub Secrets, never in the repo).
- The loader verifies signatures against the public key bundled with the
  core (`keys/plugin_signing.pub`). A valid signature against THAT key is the
  ONLY thing that makes a package "trusted"; a plugin can never claim trust
  by declaring it in its manifest.

Artifact scheme:
- ``plugin.yaml.sig``  = base64 ed25519 signature over the raw bytes of
  ``plugin.yaml`` — authenticates the manifest (and thereby the checksum of
  the packaged zip recorded inside it).
- The installer verifies manifest signature AND package checksum before
  extracting anything onto disk.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from PVE2Services.libs.errors import TrustError

logger = logging.getLogger("pve2.plugin_signing")

# Repo root: PVE2Services/libs/plugin_signing.py -> libs/ -> PVE2Services/ -> <repo root>
# (the import package is nested one level below the repo root).
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PUBLIC_KEY_PATH = REPO_ROOT / "keys" / "plugin_signing.pub"

# Detached signature sidecar of plugin.yaml (used in packages and by the loader)
MANIFEST_SIGNATURE_FILENAME = "plugin.yaml.sig"

PUBLIC_KEY_ENV = "PVE2_PLUGIN_SIGNING_PUBKEY"


class SigningKeyPair:
    """In-memory ed25519 keypair used by the release/build tooling."""

    def __init__(self, private_key: Ed25519PrivateKey | None = None):
        if private_key is None:
            self._private = Ed25519PrivateKey.generate()
        else:
            if not isinstance(private_key, Ed25519PrivateKey):
                raise TypeError("private_key must be an Ed25519PrivateKey")
            self._private = private_key
        self._public = self._private.public_key()

    @classmethod
    def generate(cls) -> "SigningKeyPair":
        return cls()

    def sign_bytes(self, data: bytes) -> bytes:
        return self._private.sign(data)

    def write_private_pem(self, path: Path) -> None:
        pem = self._private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pem)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def write_public_pem(self, path: Path) -> None:
        path.write_bytes(self.public_pem())

    def public_pem(self) -> bytes:
        return self._public.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def private_pem(self) -> bytes:
        return self._private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )


def load_public_key(pem_data: bytes | str) -> Ed25519PublicKey:
    """Parse a PEM SubjectPublicKeyInfo into an Ed25519PublicKey."""
    if isinstance(pem_data, str):
        pem_data = pem_data.encode("utf-8")
    try:
        key = serialization.load_pem_public_key(pem_data)
    except (ValueError, TypeError) as exc:
        raise TrustError(f"Invalid public key PEM: {exc}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise TrustError(
            f"Public key must be ed25519, got {key.__class__.__name__}"
        )
    return key


def resolve_public_key(explicit_path: str | Path | None = None) -> Ed25519PublicKey:
    """Resolve the trusted public key.

    Order: explicit path argument > ``PVE2_PLUGIN_SIGNING_PUBKEY`` env (file
    path or raw PEM) > repo default ``keys/plugin_signing.pub``.
    """
    candidates: list[str] = []
    if explicit_path:
        candidates.append(str(explicit_path))
    env_value = os.environ.get(PUBLIC_KEY_ENV, "")
    if env_value:
        candidates.append(env_value)
    candidates.append(str(DEFAULT_PUBLIC_KEY_PATH))

    errors: list[str] = []
    for candidate in candidates:
        # Raw inline PEM (env may contain the key itself, not a path)
        if "-----BEGIN" in candidate:
            try:
                return load_public_key(candidate)
            except TrustError as exc:
                errors.append(f"inline PEM: {exc.message}")
                continue
        path = Path(candidate)
        if not path.is_file():
            errors.append(f"{path}: not found")
            continue
        try:
            return load_public_key(path.read_bytes())
        except TrustError as exc:
            errors.append(f"{path}: {exc.message}")
            continue
   
    raise TrustError("Plugin signing public key not found. " + "; ".join(errors))


def sign_file(path: Path, keypair: SigningKeyPair, sig_path: Path | None = None) -> Path:
    """Sign the raw bytes of ``path`` and write ``path`` + '.sig' (base64)."""
    sig = keypair.sign_bytes(path.read_bytes())
    target = sig_path if sig_path is not None else path.with_name(path.name + ".sig")
    target.write_text(base64.b64encode(sig).decode("ascii"))
    return target


def verify_bytes(data: bytes, signature_b64: str, public_key: Ed25519PublicKey) -> bool:
    """Verify base64-encoded detached signature over ``data``. Never raises."""
    try:
        signature = base64.b64decode(signature_b64.encode("ascii"), validate=True)
    except (ValueError, TypeError):
        logger.debug("Signature is not valid base64")
        return False
    try:
        public_key.verify(signature, data)
        return True
    except InvalidSignature:
        logger.debug("Signature verification failed (invalid signature)")
        return False
    except Exception:
        logger.exception("Unexpected error during signature verification")
        return False


def verify_manifest_signature(
    plugin_dir: Path,
    public_key: Ed25519PublicKey | None = None,
    manifest_name: str = "plugin.yaml",
) -> bool:
    """Verify the detached signature of ``plugin.yaml`` inside ``plugin_dir``.

    Returns True only when a signature file exists AND verifies against the
    trusted project key. Missing signature, tampered manifest or unsigned
    content are all untrusted — callers must treat False as third_party, per
    the trust model (missing signature is never an error, just not trusted).
    """
    manifest_path = plugin_dir / manifest_name
    sig_path = plugin_dir / (manifest_name + ".sig")
    if not manifest_path.is_file() or not sig_path.is_file():
        return False
    if public_key is None:
        public_key = resolve_public_key()
    try:
        signature_b64 = sig_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        logger.warning("Unreadable signature file %s: %s", sig_path, exc)
        return False
    return verify_bytes(manifest_path.read_bytes(), signature_b64, public_key)
