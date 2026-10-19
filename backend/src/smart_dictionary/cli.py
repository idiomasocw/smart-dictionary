"""Command-line entry point: `smart-dictionary api | migrate | schema | data | fixtures | seeds`.

The container image runs api and migrate; `worker` joins them with the job queue.
"""

import argparse
import sys
from pathlib import Path

from smart_dictionary.paths import BACKEND_DIR, SCHEMA_PATH


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


def _data(action: str, ids: list[str], every: bool, force: bool) -> None:
    from smart_dictionary.core.logging import configure_logging
    from smart_dictionary.importers.fetch import fetch
    from smart_dictionary.importers.manifest import load_manifest
    from smart_dictionary.importers.run import import_dataset, in_import_order

    configure_logging("INFO")
    manifest = load_manifest()
    if action == "list":
        for dataset in manifest.values():
            print(f"{dataset.id:<15} {dataset.importer:<12} {dataset.license}")
        return
    unknown = sorted(set(ids) - manifest.keys())
    if unknown or not (ids or every):
        known = ", ".join(manifest)
        raise SystemExit(
            f"name datasets or pass --all (unknown: {unknown or 'none'}; known: {known})"
        )
    chosen = in_import_order(manifest.values() if every else (manifest[i] for i in ids))
    for dataset in chosen:
        if action == "fetch":
            path = fetch(dataset, force=force)
            print(f"{dataset.id}: {path or 'installed package, nothing to download'}")
        else:
            print(f"{dataset.id}: {import_dataset(dataset)} rows")


def _fixtures_load() -> None:
    from smart_dictionary.content.publish import load_fixtures
    from smart_dictionary.db.sync import connect
    from smart_dictionary.paths import FIXTURES_DIR

    with connect() as conn:
        keys = load_fixtures(conn, FIXTURES_DIR / "entries")
    print(f"published {len(keys)} fixture entries")


def _seeds(action: str, path: Path | None, out: Path | None) -> None:
    from smart_dictionary.core.logging import configure_logging
    from smart_dictionary.db.sync import connect
    from smart_dictionary.paths import OWNER_SEEDS, SEED_EXPORT
    from smart_dictionary.seeds import builder, owner, store

    configure_logging("INFO")
    if action == "export":
        target = out or SEED_EXPORT
        with connect() as conn:
            print(f"{store.export(conn, target)} candidates -> {target}")
        return
    # Three short steps, so the database never waits on the build: read, build, store.
    rows = owner.read_owner_seeds(path or OWNER_SEEDS)
    with connect() as conn:
        reference = builder.load_reference(conn)
    candidates = builder.build(rows, reference)
    with connect() as conn:
        inserted, updated = store.store(conn, candidates)
    print(builder.summary(candidates, reference))
    print(f"stored {inserted} candidates; {updated} already decided ones updated in place")


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

    data = commands.add_parser("data", help="open datasets: list, fetch or import (ref_* tables)")
    data.add_argument("action", choices=["list", "fetch", "import"])
    data.add_argument("ids", nargs="*", help="dataset ids from data/datasets.toml")
    data.add_argument("--all", action="store_true", dest="every", help="every dataset, in order")
    data.add_argument("--force", action="store_true", help="download again even if present")

    fixtures = commands.add_parser("fixtures", help="development entries (backend/fixtures)")
    fixtures.add_argument("action", choices=["load"])

    seeds = commands.add_parser("seeds", help="seed candidates from the owner's and open lists")
    seeds.add_argument("action", choices=["import", "export"])
    seeds.add_argument(
        "--file", type=Path, help="seed CSV (default data/seeds/owner/owner-seeds.csv)"
    )
    seeds.add_argument(
        "--out", type=Path, help="export path (default data/raw/seed-candidates.csv)"
    )

    args = parser.parse_args(argv)
    if args.command == "api":
        _api(args.host, args.port, args.reload)
    elif args.command == "migrate":
        _migrate(args.revision)
    elif args.command == "schema":
        _schema_export(args.check)
    elif args.command == "data":
        _data(args.action, args.ids, args.every, args.force)
    elif args.command == "fixtures":
        _fixtures_load()
    elif args.command == "seeds":
        _seeds(args.action, args.file, args.out)
