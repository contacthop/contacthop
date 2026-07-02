"""``contacthop`` command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="contacthop", description="ContactHop harness")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the ContactHop API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    sub.add_parser("init-db", help="create database tables")

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


async def _init_db() -> None:
    from contacthop.config import Settings
    from contacthop.db.session import Database

    settings = Settings()
    db = Database(settings.database_url)
    await db.create_all()
    await db.dispose()
    print(f"tables created in {settings.database_url}")


if __name__ == "__main__":
    main()
