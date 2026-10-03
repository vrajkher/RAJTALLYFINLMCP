"""Tool modules. Each one exposes ``register(mcp)`` and adds its tools to the server."""

from mcp_types import ToolAnnotations

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)
