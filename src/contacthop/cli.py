"""``contacthop`` command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

import uvicorn


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="contacthop", description="ContactHop harness")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the ContactHop API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    sub.add_parser("init-db", help="create database tables directly (dev; prefer migrate)")

    migrate = sub.add_parser("migrate", help="apply Alembic migrations")
    migrate.add_argument(
        "revision", nargs="?", default="head", help="target revision (default: head)"
    )

    mcp = sub.add_parser(
        "mcp", help="run the MCP server exposing ContactHop as agent tools"
    )
    mcp.add_argument(
        "--base-url",
        default=os.environ.get("CONTACTHOP_URL", "http://127.0.0.1:8000"),
        help="ContactHop API to drive (env: CONTACTHOP_URL)",
    )
    mcp.add_argument(
        "--api-key",
        default=os.environ.get("CONTACTHOP_API_KEY"),
        help="Bearer key for the API, if configured (env: CONTACTHOP_API_KEY)",
    )
    mcp.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="stdio for runtimes that spawn the server (default); http/sse to listen",
    )
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=8090)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    if args.command == "serve":
        uvicorn.run(
            "contacthop.main:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
    elif args.command == "init-db":
        asyncio.run(_init_db())
    elif args.command == "migrate":
        _migrate(args.revision)
    elif args.command == "mcp":
        from contacthop.mcp_server import build_server

        server = build_server(base_url=args.base_url, api_key=args.api_key)
        server.settings.host = args.host
        server.settings.port = args.port
        server.run(transport=args.transport)


async def _init_db() -> None:
    from contacthop.config import Settings
    from contacthop.db.session import Database

    settings = Settings()
    db = Database(settings.database_url)
    await db.create_all()
    await db.dispose()
    print(f"tables created in {settings.database_url}")


def _migrate(revision: str) -> None:
    from contacthop.config import Settings
    from contacthop.db.migrate import upgrade

    settings = Settings()
    upgrade(settings.database_url, revision)
    print(f"migrated {settings.database_url} to {revision}")


if __name__ == "__main__":
    main()
