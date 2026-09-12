"""Tests for centralized error handlers."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from PVE2Services.libs.errors import (
    ConfigError,
    DatabaseError,
    PluginError,
    register_error_handlers,
)


@pytest.fixture
def app_with_errors():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/test-plugin-error")
    async def raise_plugin_error():
        raise PluginError("Plugin failed", plugin_id="test_plugin")

    @app.get("/test-config-error")
    async def raise_config_error():
        raise ConfigError("Missing config", plugin_id="test_config")

    @app.get("/test-db-error")
    async def raise_db_error():
        raise DatabaseError("DB connection failed")

    @app.get("/test-generic-error")
    async def raise_generic_error():
        raise ValueError("Something went wrong")

    return app


class TestErrorHandlers:
    @pytest.fixture(autouse=True)
    def setup_client(self, app_with_errors):
        self.client = TestClient(app_with_errors, raise_server_exceptions=False)

    def test_plugin_error(self):
        r = self.client.get("/test-plugin-error")
        assert r.status_code == 500
        data = r.json()
        assert data["type"] == "plugin_error"
        assert data["plugin"] == "test_plugin"

    def test_config_error(self):
        r = self.client.get("/test-config-error")
        assert r.status_code == 400
        data = r.json()
        assert data["type"] == "config_error"

    def test_db_error(self):
        r = self.client.get("/test-db-error")
        assert r.status_code == 500
        data = r.json()
        assert data["type"] == "database_error"

    def test_generic_error(self):
        r = self.client.get("/test-generic-error")
        assert r.status_code == 500
        data = r.json()
        assert data["type"] == "internal_error"
