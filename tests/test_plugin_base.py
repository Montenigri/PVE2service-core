"""Tests for plugin_base utilities."""

from PVE2Services.libs.plugin_base import (
    get_plugin_info,
    require_config,
    safe_import,
)


class TestGetPluginInfo:
    def test_default_info(self):
        info = get_plugin_info("pve2core")
        assert info["plugin"] == "pve2core"
        assert "version" in info
        assert info["status"] == "active"

    def test_name_formatting(self):
        info = get_plugin_info("pve2dns")
        assert "PVE2" in info["name"]


class TestRequireConfig:
    def test_missing_keys(self):
        config = {"host": "example.com"}
        error = require_config(config, "host", "port", "api_key")
        assert error is not None
        assert "port" in error
        assert "api_key" in error

    def test_all_present(self):
        config = {"host": "example.com", "port": 80, "api_key": "123"}
        error = require_config(config, "host", "port", "api_key")
        assert error is None

    def test_empty_config(self):
        error = require_config({}, "required_key")
        assert error is not None


class TestSafeImport:
    def test_valid_import(self):
        result = safe_import("os", "path")
        assert result is not None

    def test_invalid_module(self):
        result = safe_import("nonexistent_module", "something")
        assert result is None

    def test_invalid_symbol(self):
        result = safe_import("os", "nonexistent_symbol")
        assert result is None
