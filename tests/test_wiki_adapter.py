"""Tests for WikiAdapter — wiki.js API client."""

import os

import pytest

from PVE2Services.libs.wiki_adapter import PageNotFoundError, WikiAdapter


class TestWikiAdapter:
    @pytest.fixture
    def wiki(self):
        os.environ["WIKI_URL"] = "http://wiki.example.com"
        os.environ["WIKI_API_KEY"] = "test_api_key"
        adapter = WikiAdapter()
        yield adapter
        os.environ.pop("WIKI_URL", None)
        os.environ.pop("WIKI_API_KEY", None)

    def test_page_not_found_error(self):
        exc = PageNotFoundError("Page not found")
        assert str(exc) == "Page not found"
        assert isinstance(exc, Exception)
