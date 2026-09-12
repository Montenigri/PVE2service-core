"""Unit tests for libs/plugin_signing.py — ed25519 roundtrip and tamper cases."""

import pytest

from PVE2Services.libs.errors import TrustError
from PVE2Services.libs.plugin_signing import (
    SigningKeyPair,
    load_public_key,
    resolve_public_key,
    sign_file,
    verify_bytes,
    verify_manifest_signature,
)


@pytest.fixture()
def keypair():
    return SigningKeyPair.generate()


def test_sign_verify_roundtrip(tmp_path, keypair):
    target = tmp_path / "plugin.yaml"
    target.write_text("id: demo\n")
    sig_path = sign_file(target, keypair)
    pub = load_public_key(keypair.public_pem())
    assert verify_bytes(target.read_bytes(), sig_path.read_text(), pub)


def test_tampered_content_fails(tmp_path, keypair):
    target = tmp_path / "plugin.yaml"
    target.write_text("id: demo\n")
    sig_path = sign_file(target, keypair)
    target.write_text("id: hacked\n")  # flip a byte after signing
    pub = load_public_key(keypair.public_pem())
    assert not verify_bytes(target.read_bytes(), sig_path.read_text(), pub)


def test_wrong_key_fails(tmp_path, keypair):
    target = tmp_path / "plugin.yaml"
    target.write_text("id: demo\n")
    sig_path = sign_file(target, keypair)
    other = load_public_key(SigningKeyPair.generate().public_pem())
    assert not verify_bytes(target.read_bytes(), sig_path.read_text(), other)


def test_invalid_base64_is_falsy_not_exception(keypair):
    pub = load_public_key(keypair.public_pem())
    assert not verify_bytes(b"data", "!!!not-base64!!!", pub)
    assert not verify_bytes(b"data", "", pub)


def test_load_public_key_rejects_non_ed25519():
    with pytest.raises(TrustError):
        load_public_key(b"not a pem at all")


def test_verify_manifest_signature(tmp_path, keypair):
    plugin_dir = tmp_path / "plug"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text("id: demo\n")
    # No signature file -> untrusted, NOT an error
    pub = load_public_key(keypair.public_pem())
    assert verify_manifest_signature(plugin_dir, public_key=pub) is False
    # Written + valid -> trusted
    sign_file(plugin_dir / "plugin.yaml", keypair)
    assert verify_manifest_signature(plugin_dir, public_key=pub) is True
    # Tampered manifest (signed other file)
    (plugin_dir / "plugin.yaml").write_text("id: changed\n")
    assert verify_manifest_signature(plugin_dir, public_key=pub) is False


def test_resolve_public_key_order(tmp_path, keypair, monkeypatch):
    key_file = tmp_path / "trusted.pub"
    key_file.write_bytes(keypair.public_pem())
    monkeypatch.setenv("PVE2_PLUGIN_SIGNING_PUBKEY", str(key_file))
    key = resolve_public_key()
    assert key.public_bytes_raw() == load_public_key(keypair.public_pem()).public_bytes_raw()
    # explicit path argument wins over env
    other_file = tmp_path / "other.pub"
    other_keypair = SigningKeyPair.generate()
    other_file.write_bytes(other_keypair.public_pem())
    resolved = resolve_public_key(other_file)
    assert resolved.public_bytes_raw() == load_public_key(
        other_keypair.public_pem()
    ).public_bytes_raw()


def test_resolve_public_key_missing_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("PVE2_PLUGIN_SIGNING_PUBKEY", raising=False)
    monkeypatch.setattr(
        "PVE2Services.libs.plugin_signing.DEFAULT_PUBLIC_KEY_PATH",
        tmp_path / "does-not-exist.pub",
    )
    with pytest.raises(TrustError):
        resolve_public_key()
