"""Unit tests for libs/mcp_registry.py — the in-process MCP tool registry."""

import asyncio

import pytest

from PVE2Services.libs.mcp_registry import (
    get_mcp_registry,
    register_tool,
    reset_mcp_registry,
)


@pytest.fixture(autouse=True)
def clean_registry():
    reset_mcp_registry()
    yield
    reset_mcp_registry()


def test_register_and_list_read_only_filter():
    async def handler(params):
        return params

    register_tool("p1", name="a", handler=handler, description="d", input_schema={"type": "object"})
    register_tool("p2", name="b", handler=handler, read_only=False)

    keys = {s.key for s in get_mcp_registry().all()}
    assert keys == {"p1:a", "p2:b"}
    assert [s.key for s in get_mcp_registry().all(read_only_only=True)] == ["p1:a"]


def test_get_handler_executes():
    async def handler(params):
        return {"ok": params}

    register_tool("p1", name="a", handler=handler)
    resolved = get_mcp_registry().get_handler("p1:a")
    assert resolved is not None
    assert asyncio.run(resolved({"x": 1})) == {"ok": {"x": 1}}
    assert get_mcp_registry().get_handler("missing") is None


def test_spec_metadata_preserved():
    async def handler(params):  # pragma: no cover - not executed
        return None

    register_tool(
        "pve2dns",
        name="list_records",
        handler=handler,
        description="List records",
        input_schema={"type": "object", "properties": {}},
        capability="dns_records",
    )
    spec = get_mcp_registry().get("pve2dns:list_records")
    assert spec is not None
    assert spec.plugin_id == "pve2dns"
    assert spec.capability == "dns_records"
    assert spec.read_only is True


def test_non_callable_handler_rejected():
    with pytest.raises(TypeError):
        register_tool("p1", name="a", handler=object())


def test_reset_clears():
    async def handler(params):
        return None

    register_tool("p1", name="a", handler=handler)
    assert len(get_mcp_registry()) == 1
    reset_mcp_registry()
    assert len(get_mcp_registry()) == 0
