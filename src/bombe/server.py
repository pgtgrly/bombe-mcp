"""CLI entry point for the Bombe MCP server."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

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

        def register_tool(self, name: str, description: str, handler: object) -> None:
            self.tools[name] = {"description": description, "handler": handler}

    server = LocalServer()
    register_tools(server, db, settings.repo_root.as_posix())
    logging.getLogger(__name__).info(
        "Registered %d tool handlers.", len(server.tools)
    )


if __name__ == "__main__":
    main()
