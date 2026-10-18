"""Command-line entry point: `smart-dictionary api | migrate`.

The container image runs these same commands; `worker` joins them with the job queue.
"""

import argparse
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def alembic_config():
    from alembic.config import Config

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return config


def _api(host: str, port: int, reload: bool) -> None:
    import uvicorn

    from smart_dictionary.core.runtime import uvicorn_loop

    uvicorn.run(
        "smart_dictionary.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        loop=uvicorn_loop(),
    )


def _migrate(revision: str) -> None:
    from alembic import command

    command.upgrade(alembic_config(), revision)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="smart-dictionary")
    commands = parser.add_subparsers(dest="command", required=True)

    api = commands.add_parser("api", help="run the HTTP API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument("--reload", action="store_true")

    migrate = commands.add_parser("migrate", help="apply database migrations")
    migrate.add_argument("revision", nargs="?", default="head")

    args = parser.parse_args(argv)
    if args.command == "api":
        _api(args.host, args.port, args.reload)
    elif args.command == "migrate":
        _migrate(args.revision)
