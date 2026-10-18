"""Command-line entry point: `smart-dictionary api | migrate | schema export`.

The container image runs these same commands; `worker` joins them with the job queue.
"""

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
SCHEMA_PATH = BACKEND_DIR.parent / "factory" / "schema" / "entry.v1.json"


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


def _schema_export(check: bool) -> None:
    from smart_dictionary.core.entrydoc import schema_text

    text = schema_text()
    if check:
        current = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else ""
        if current != text:
            print(
                "factory/schema/entry.v1.json is out of date: "
                "run `uv run smart-dictionary schema export`",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print("factory/schema/entry.v1.json is up to date")
        return
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {SCHEMA_PATH}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="smart-dictionary")
    commands = parser.add_subparsers(dest="command", required=True)

    api = commands.add_parser("api", help="run the HTTP API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument("--reload", action="store_true")

    migrate = commands.add_parser("migrate", help="apply database migrations")
    migrate.add_argument("revision", nargs="?", default="head")

    schema = commands.add_parser("schema", help="the EntryDoc JSON Schema for the factory")
    schema.add_argument("action", choices=["export"])
    schema.add_argument("--check", action="store_true", help="fail if the file is out of date")

    args = parser.parse_args(argv)
    if args.command == "api":
        _api(args.host, args.port, args.reload)
    elif args.command == "migrate":
        _migrate(args.revision)
    elif args.command == "schema":
        _schema_export(args.check)
