"""Command line entry point.

    rajtally-mcp                 run the MCP server over stdio (what desktop clients launch)
    rajtally-mcp --http          run it over streamable HTTP on port 8000
    rajtally-mcp --check         test the connection to Tally and exit
    rajtally-mcp --demo          start a small pretend Tally on port 9000 to try things without Tally
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="rajtally-mcp", description="MCP server for Tally.ERP 9 / TallyPrime")
    parser.add_argument("--http", action="store_true", help="serve over streamable HTTP instead of stdio")
    parser.add_argument("--host", help="Tally host (default localhost)")
    parser.add_argument("--port", type=int, help="Tally port (default 9000)")
    parser.add_argument("--company", help="company name to use")
    parser.add_argument("--access", choices=["read_only", "read_write", "full"], help="access level")
    parser.add_argument("--check", action="store_true", help="test the Tally connection and exit")
    parser.add_argument("--demo", action="store_true", help="run a pretend Tally server for trying things out")
    args = parser.parse_args()

    for flag, env in (("host", "TALLY_HOST"), ("port", "TALLY_PORT"), ("company", "TALLY_COMPANY"),
                      ("access", "TALLY_ACCESS")):
        value = getattr(args, flag)
        if value is not None:
            os.environ[env] = str(value)

    if args.demo:
        from .demo_tally import serve
        serve(port=args.port or 9000)
        return

    from . import config
    from .server import build_server

    # command-line flags beat the saved settings file for this run
    overrides = {flag: getattr(args, flag) for flag in ("host", "port", "company", "access")
                 if getattr(args, flag) is not None}
    if overrides:
        config.update(overrides, persist=False)

    if args.check:
        from .tools.connection import register

        class Collect:
            def __init__(self) -> None:
                self.tools: dict[str, object] = {}

            def tool(self, **_: object):
                def wrap(fn):
                    self.tools[fn.__name__] = fn
                    return fn
                return wrap

        collected = Collect()
        register(collected)
        status = collected.tools["tally_status"]()
        print(json.dumps(status, indent=2, ensure_ascii=False))
        sys.exit(0 if status.get("connected") and not status.get("problem") else 1)

    build_server().run(transport="streamable-http" if args.http else "stdio")


if __name__ == "__main__":
    main()
