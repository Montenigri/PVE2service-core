"""In-process MCP tool registry shared by plugins and the PVE2LLM assistant.

The MCP "standard" for PVE2Services is declarative: each plugin may ship an
optional ``mcp:`` block in its ``plugin.yaml`` listing the read-only tools it
exposes. The plugin loader resolves every ``handler`` (``module:function``
relative to the plugin package) and registers it here. The PVE2LLM plugin
(assistant name **Winky**) reads this registry, turns the tools into the
provider's tool-calling schema and executes them in-process — no network MCP
transport, no extra authentication surface.

Handlers are async callables with the signature ``handler(params: dict) -> Any``
where ``params`` is the JSON object the LLM decided to pass. ``read_only`` is
enforced here: :meth:`MCPRegistry.all` can filter to read-only tools and the
assistant only ever exposes those.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class MCPToolSpec:
    """A resolvable tool: metadata plus the plugin that owns it."""

    plugin_id: str
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    capability: str = ""
    read_only: bool = True

    @property
    def key(self) -> str:
        """Globally unique key (``plugin_id:tool_name``)."""
        return f"{self.plugin_id}:{self.name}"


@dataclass
class _RegisteredTool:
    spec: MCPToolSpec
    handler: Handler


class MCPRegistry:
    """Thread-safe registry of MCP tools contributed by loaded plugins."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tools: dict[str, _RegisteredTool] = {}

    def register(self, spec: MCPToolSpec, handler: Handler) -> None:
        if not callable(handler):
            raise TypeError(f"handler for tool {spec.key!r} is not callable")
        with self._lock:
            self._tools[spec.key] = _RegisteredTool(spec=spec, handler=handler)

    def all(self, read_only_only: bool = False) -> list[MCPToolSpec]:
        with self._lock:
            specs = [entry.spec for entry in self._tools.values()]
        if read_only_only:
            specs = [s for s in specs if s.read_only]
        return sorted(specs, key=lambda s: (s.plugin_id, s.name))

    def get_handler(self, key: str) -> Handler | None:
        with self._lock:
            entry = self._tools.get(key)
        return entry.handler if entry else None

    def get(self, key: str) -> MCPToolSpec | None:
        with self._lock:
            entry = self._tools.get(key)
        return entry.spec if entry else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._tools)

    def reset(self) -> None:
        with self._lock:
            self._tools.clear()


_registry = MCPRegistry()


def get_mcp_registry() -> MCPRegistry:
    """Return the process-wide MCP tool registry."""
    return _registry


def reset_mcp_registry() -> MCPRegistry:
    """Clear the process-wide registry (tests / re-discovery)."""
    _registry.reset()
    return _registry


def register_tool(
    plugin_id: str,
    *,
    name: str,
    handler: Handler,
    description: str = "",
    input_schema: dict[str, Any] | None = None,
    capability: str = "",
    read_only: bool = True,
) -> None:
    """Convenience wrapper around :meth:`MCPRegistry.register`."""
    spec = MCPToolSpec(
        plugin_id=plugin_id,
        name=name,
        description=description,
        input_schema=input_schema or {},
        capability=capability,
        read_only=read_only,
    )
    _registry.register(spec, handler)
