"""Tests for ConnectorConfig — config management and encryption."""

import os

import pytest

from PVE2Services.libs.connector_config import (
    ConnectorConfig,
    decrypt_value,
    encrypt_value,
    get_config_manager,
)


class TestEncryption:
    @pytest.fixture(autouse=True)
    def isolated_key_file(self, tmp_path, monkeypatch):
        """Point the auto-generated key file at a temp dir and clear env key."""
        import PVE2Services.libs.connector_config as cc

        monkeypatch.setenv("PVE2_ENCRYPTION_KEY_FILE", str(tmp_path / "test_key"))
        monkeypatch.delenv("PVE2_ENCRYPTION_KEY", raising=False)
        monkeypatch.setattr(cc, "_STRICT_SECRETS", False)
        yield

    def test_encrypt_decrypt_roundtrip(self):

        plaintext = "super_secret_api_key_12345"
        encrypted = encrypt_value(plaintext)
        assert encrypted != plaintext
        assert decrypt_value(encrypted) == plaintext

    def test_encrypts_without_env_key_via_persisted_key_file(self):
        """Encryption is on by default: a key file is auto-generated and reused."""
        import PVE2Services.libs.connector_config as cc

        secret = "plaintext_secret"
        encrypted = encrypt_value(secret)
        assert encrypted != secret
        assert os.path.exists(cc._DEFAULT_KEY_FILE)
        # A second call (new process simulation: re-read from file) still decrypts
        assert decrypt_value(encrypted) == secret

    def test_strict_mode_requires_env_key(self, monkeypatch):
        import PVE2Services.libs.connector_config as cc

        monkeypatch.setattr(cc, "_STRICT_SECRETS", True)
        with pytest.raises(RuntimeError):
            encrypt_value("x")


class TestConnectorConfig:
    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        old_url = os.environ.get("PVE2_DB_URL")
        self.db_path = str(tmp_path / "test_connector.db")
        os.environ["PVE2_DB_URL"] = f"sqlite:///{self.db_path}"
        self.config = ConnectorConfig()
        yield
        if old_url:
            os.environ["PVE2_DB_URL"] = old_url
        else:
            os.environ.pop("PVE2_DB_URL", None)

    def test_get_config_empty(self):
        config = self.config.get_config("nonexistent")
        assert config == {}

    def test_save_and_get_config(self):
        test_config = {"host": "example.com", "port": 8080}
        assert self.config.save_config("test_connector", test_config, enabled=True) is True
        
        retrieved = self.config.get_config("test_connector")
        assert retrieved["host"] == "example.com"
        assert retrieved["port"] == 8080

    def test_config_update(self):
        self.config.save_config("test_connector", {"key": "value1"})
        self.config.save_config("test_connector", {"key": "value2"})
        retrieved = self.config.get_config("test_connector")
        assert retrieved["key"] == "value2"

    def test_sync_state_operations(self):
        state = self.config.get_sync_state("test_connector")
        assert state["last_sync_status"] == "pending"
        assert state["sync_count"] == 0
        
        self.config.update_sync_state("test_connector", "success", items_synced=5)
        state = self.config.get_sync_state("test_connector")
        assert state["last_sync_status"] == "success"
        assert state["sync_count"] == 1
        assert state["items_synced"] == 5

    def test_sync_state_error(self):
        self.config.update_sync_state("test_connector", "error", error="Connection failed")
        state = self.config.get_sync_state("test_connector")
        assert state["last_sync_status"] == "error"
        assert state["error_count"] == 1
        assert state["last_error"] == "Connection failed"

    def test_list_connectors(self):
        self.config.save_config("conn1", {"a": 1})
        self.config.save_config("conn2", {"b": 2})
        connectors = self.config.list_connectors()
        assert len(connectors) == 2
        ids = [c["connector_id"] for c in connectors]
        assert "conn1" in ids
        assert "conn2" in ids

    def test_delete_config(self):
        self.config.save_config("to_delete", {"x": 1})
        assert self.config.delete_config("to_delete") is True
        assert self.config.get_config("to_delete") == {}

    def test_get_config_manager_singleton(self):
        m1 = get_config_manager()
        m2 = get_config_manager()
        assert m1 is m2
