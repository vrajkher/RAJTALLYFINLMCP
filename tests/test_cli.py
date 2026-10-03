"""The server as users actually run it: the command line and the stdio transport."""

import asyncio
import json
import os
import subprocess
import sys


def _env(tally, tmp_path):
    port = os.environ["TALLY_PORT"]
    return {**os.environ, "TALLY_PORT": port, "TALLY_HOST": "127.0.0.1",
            "RAJTALLY_SETTINGS": str(tmp_path / "cli-settings.json")}


def test_check_command_reports_the_connection(tally, tmp_path):
    done = subprocess.run([sys.executable, "-m", "rajtally_mcp", "--check"], capture_output=True, text=True,
                          env=_env(tally, tmp_path), timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["active_company"] == "Demo Traders Pvt Ltd"
    down = subprocess.run([sys.executable, "-m", "rajtally_mcp", "--check", "--port", "1"],
                          capture_output=True, text=True, env=_env(tally, tmp_path), timeout=60)
    assert down.returncode == 1 and json.loads(down.stdout)["connected"] is False


def test_server_works_over_stdio(tally, tmp_path):
    from mcp import StdioServerParameters
    from mcp.client import Client

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "rajtally_mcp", "--access", "read_only"],
                                       env=_env(tally, tmp_path))
        async with Client(params) as client:
            tools = await client.list_tools()
            balance = await client.call_tool("tally_trial_balance", {})
            blocked = await client.call_tool("tally_create_group", {"name": "X", "parent": "Sundry Debtors"})
            guide = await client.read_resource("tally://guide")
            ledger = await client.read_resource("tally://ledger/HDFC%20Bank")
            return client.server_info, tools.tools, balance, blocked, guide, ledger

    info, tools, balance, blocked, guide, ledger = asyncio.run(run())
    assert info.name == "rajtally" and len(tools) == 53
    assert balance.structured_content["difference"] == 0.0
    assert blocked.is_error and "read_only mode" in blocked.content[0].text
    assert "STEP 1" in guide.contents[0].text
    assert json.loads(ledger.contents[0].text)["summary"]["closing_balance"] == 485310.0
