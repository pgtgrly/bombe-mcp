"""CLI entry point for the Bombe MCP server."""

from __future__ import annotations

import argparse
import inspect
import logging
from pathlib import Path
from typing import Any

from bombe.config import build_settings
from bombe.store.database import Database
from bombe.tools.definitions import register_tools


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bombe",
        description="Structure-aware code retrieval MCP server.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path("."),
        help="Path to the repository root.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Optional path to SQLite database file. Defaults to <repo>/.bombe/bombe.db.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log verbosity level.",
    )
    parser.add_argument(
        "--init-only",
        action="store_true",
        help="Initialize storage and exit without starting MCP transport.",
    )
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = build_settings(
        repo=args.repo,
        db_path=args.db_path,
        log_level=args.log_level,
        init_only=args.init_only,
    )
    configure_logging(settings.log_level)
    db = Database(settings.db_path)
    db.init_schema()

    logging.getLogger(__name__).info(
        "Bombe server initialized (repo=%s, db=%s, init_only=%s)",
        settings.repo_root,
        settings.db_path,
        settings.init_only,
    )

    if settings.init_only:
        return

    class LocalServer:
        def __init__(self) -> None:
            self.tools: dict[str, object] = {}

        def register_tool(
            self,
            name: str,
            description: str,
            input_schema_or_handler: object,
            handler: object | None = None,
        ) -> None:
            if handler is None:
                input_schema = None
                resolved_handler = input_schema_or_handler
            else:
                input_schema = input_schema_or_handler
                resolved_handler = handler
            self.tools[name] = {
                "description": description,
                "input_schema": input_schema,
                "handler": resolved_handler,
            }

    server = LocalServer()
    register_tools(server, db, settings.repo_root.as_posix())
    logging.getLogger(__name__).info(
        "Registered %d tool handlers.", len(server.tools)
    )

    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        logging.getLogger(__name__).info(
            "MCP runtime package unavailable; running in local registration mode."
        )
        return

    mcp_server = FastMCP("bombe")
    for tool_name, tool in server.tools.items():
        description = str(tool["description"])
        input_schema = tool.get("input_schema")
        handler = tool["handler"]
        if not callable(handler):
            continue

        if hasattr(mcp_server, "tool"):
            decorator = mcp_server.tool(name=tool_name, description=description)

            @decorator
            def _wrapped(payload: dict[str, Any], _handler=handler):
                return _handler(payload)
            continue

        if hasattr(mcp_server, "register_tool"):
            register_sig = inspect.signature(mcp_server.register_tool)
            if "input_schema" in register_sig.parameters:
                mcp_server.register_tool(
                    name=tool_name,
                    description=description,
                    input_schema=input_schema,
                    handler=handler,
                )
            else:
                mcp_server.register_tool(tool_name, description, handler)

    if hasattr(mcp_server, "run"):
        logging.getLogger(__name__).info("Starting MCP STDIO server runtime.")
        mcp_server.run()


if __name__ == "__main__":
    main()
