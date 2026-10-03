"""Test fixtures: a pretend Tally on a free port and an MCP client wired to the server."""

from __future__ import annotations

import asyncio
import json
import socket

import pytest

from rajtally_mcp import config, demo_tally
from rajtally_mcp.server import build_server


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def tally(tmp_path, monkeypatch):
    """A fresh demo company for every test."""
    port = free_port()
    monkeypatch.setenv("RAJTALLY_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("TALLY_PORT", str(port))
    monkeypatch.setenv("TALLY_HOST", "127.0.0.1")
    for name in ("TALLY_COMPANY", "TALLY_ACCESS", "TALLY_ENCODING", "TALLY_GST_SCHEMA", "TALLY_ODBC_DSN"):
        monkeypatch.delenv(name, raising=False)
    config.reset()
    server, data = demo_tally.start(port)
    yield data
    server.shutdown()
    server.server_close()
    config.reset()


class Caller:
    """Calls MCP tools the way a client would, through the real protocol layer."""

    def __init__(self) -> None:
        self.server = build_server()

    def __call__(self, tool: str, **arguments):
        async def run():
            from mcp.client import Client
            async with Client(self.server) as client:
                return await client.call_tool(tool, arguments)

        result = asyncio.run(run())
        if result.is_error:
            raise ToolFailed(result.content[0].text)
        if result.structured_content is not None:
            return result.structured_content
        text = result.content[0].text if result.content else ""
        try:
            return json.loads(text)
        except ValueError:
            return text

    def tools(self):
        async def run():
            from mcp.client import Client
            async with Client(self.server) as client:
                return (await client.list_tools()).tools
        return asyncio.run(run())


class ToolFailed(Exception):
    pass


@pytest.fixture()
def call(tally):
    return Caller()
