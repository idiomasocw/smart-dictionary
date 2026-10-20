"""The `smart-dictionary` command: api, migrate, schema, data, fixtures, seeds, factory and db.

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


def _seeds_queue(
    lemmas: list[str] | None, levels: list[str] | None, limit: int | None, dry_run: bool
) -> None:
    from smart_dictionary.core.text import norm
    from smart_dictionary.db.sync import connect
    from smart_dictionary.factory.queue import queue

    def progress(done: int, total: int) -> None:
        print(f"queueing: {done} of {total}", file=sys.stderr, flush=True)

    with connect() as conn:
        queued = queue(
            conn,
            lemmas=[norm(lemma) for lemma in lemmas] if lemmas else None,
            levels=levels,
            limit=limit,
            dry_run=dry_run,
            progress=progress,
        )
        if dry_run:
            conn.rollback()
    for item in queued[:20]:
        print(f"{item.priority:>6}  {item.cefr or '--'}  {item.key}")
    if len(queued) > 20:
        print(f"... and {len(queued) - 20} more")
    print(f"{'would queue' if dry_run else 'queued'} {len(queued)} entries")


def _requests_sync(dry_run: bool) -> None:
    """Turn every published entry's links to missing words into suggestions, then apply the
    auto-approval to every pending request of a student or a link."""
    from smart_dictionary.content import related, word_requests
    from smart_dictionary.core.settings import get_settings
    from smart_dictionary.db.sync import connect

    tenant = get_settings().default_tenant
    with connect() as conn:
        entries = conn.execute(
            "select key, doc->'related' from entries where published_at is not null"
            " and status <> 'retired' and jsonb_array_length(doc->'related') > 0 order by key"
        ).fetchall()
        suggested = related.suggest(conn, [(key, links) for key, links in entries], tenant=tenant)
        pending = conn.execute(
            "select id, lemma from word_requests where status = 'pending'"
            " and origin in ('student', 'related') order by lemma"
        ).fetchall()
        approved = [
            (lemma, found.key, found.decided_by)
            for request_id, lemma in pending
            if (found := word_requests.auto_approve(conn, request_id, tenant=tenant))
        ]
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    approved += [(item.lemma, item.approved, item.approved_by or "")
                 for item in suggested if item.approved]  # fmt: skip
    would = "would be " if dry_run else ""
    print(f"{len(entries)} published entries have related links")
    print(f"{len(suggested)} new suggestions {would or 'were '}made from them")
    for lemma, key, by in sorted(approved):
        print(f"  approved at once ({by}): {lemma} -> {key}")
    print(f"{len(approved)} {would}approved at once; the others wait for you in the console")


def _seeds_curate(path: Path | None, dry_run: bool) -> None:
    """Apply a curation file (seeds.curate) the owner reviewed; --dry-run changes nothing."""
    from collections import Counter

    from smart_dictionary.db.sync import connect
    from smart_dictionary.seeds import curate

    if path is None:
        raise SystemExit("seeds curate needs --file <csv>")
    try:
        shape, rows = curate.read(path)
    except curate.Invalid as error:
        raise SystemExit(f"{path}: {error}") from None
    with connect() as conn:
        outcomes = curate.apply(conn, shape, rows, label=path.name)
        left = curate.waiting(conn, shape, rows)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    for outcome in outcomes:
        if outcome.changed or outcome.action.startswith("skipped"):
            print(f"{outcome.target}: {outcome.action}")
    counts = Counter(o.action.split(":")[0].split(" as ")[0] for o in outcomes)
    print(", ".join(f"{name} {count}" for name, count in counts.most_common()))
    if left:
        what = "seeds" if shape == "seeds" else "multiword plain verbs"
        print(f"{len(left)} {what} still wait for a decision, not in this file: "
              + ", ".join(left[:20]) + (" …" if len(left) > 20 else ""))  # fmt: skip
    if dry_run:
        print("Dry run: nothing was changed.")
    elif shape == "seeds":
        print("Next: uv run smart-dictionary seeds queue --dry-run, then without --dry-run.")


def _factory(action: str, name: str | None, runs: int) -> None:
    from smart_dictionary.db.sync import connect
    from smart_dictionary.factory import progress

    if action == "status":
        with connect() as conn:
            print("\n".join(progress.report(conn, runs)))
        return
    _token("factory", action, name)


def _token(scope: str, action: str, name: str | None) -> None:
    """Create a token with this scope and print it once, or revoke the ones with this name."""
    from smart_dictionary.db.sync import connect
    from smart_dictionary.factory import tokens

    if not name:
        raise SystemExit(f"{scope} {action} needs --name")
    with connect() as conn:
        if action == "revoke":
            count = tokens.revoke(conn, name, scope)
            print(f"revoked {count} {scope} token(s) named {name!r}")
            return
        token = tokens.create(conn, name, [scope])
    print(f"{scope} token {name!r}, shown only now:", file=sys.stderr)
    print(token)


def _db_app_role(role: str) -> None:
    from smart_dictionary.core.settings import get_settings
    from smart_dictionary.db.roles import check_login, reset_password
    from smart_dictionary.db.sync import connect

    with connect() as conn:
        url = reset_password(conn, get_settings().database_url, role)
    check_login(url, role)
    print(f"{role} has a new password and logs in. Its DATABASE_URL:", file=sys.stderr)
    print(url)


def _audio(action: str, limit: int | None, shard: str, every: int) -> None:
    """Voice what published entries lack: once (sync), or forever (loop, the container)."""
    import time

    from smart_dictionary.audio.engine import Speaker
    from smart_dictionary.audio.sync import Outbox, Report, sync
    from smart_dictionary.core.settings import get_settings
    from smart_dictionary.db.sync import connect

    settings = get_settings()
    index, _, count = shard.partition("/")
    part = (int(index), int(count or 1))
    speaker = Speaker(settings.audio_model_dir, settings.audio_threads)
    outbox = Outbox(settings.audio_outbox)

    def show(report: Report) -> None:
        print(f"audio: {report.made} files made so far", file=sys.stderr, flush=True)

    seen: set[str] = set()
    while True:
        # A loop pass stops after a hundred files, so uploads get confirmed as it goes.
        started = time.monotonic()
        with connect() as conn:
            report = sync(conn, speaker, outbox, limit=limit if action == "sync" else 100,
                          shard=part, progress=show if action == "sync" else None)  # fmt: skip
        took = time.monotonic() - started
        # Each problem once: a pronunciation the converter can't read stays unreadable.
        for problem in report.problems:
            if problem not in seen:
                seen.add(problem)
                print(f"  {problem}", file=sys.stderr, flush=True)
        if action == "sync" or report.made or report.confirmed or report.lost:
            speed = f", x{report.audio_ms / 1000 / took:.1f} real time" if report.made else ""
            print(
                f"made {report.made} files ({report.audio_ms / 1000:.0f} s of audio in "
                f"{took:.0f} s{speed}), confirmed {report.confirmed} uploads, "
                f"forgot {report.lost} lost files",
                flush=True,
            )
        if action == "sync":
            return
        if report.made == 0:
            time.sleep(every)


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
    seeds.add_argument("action", choices=["import", "export", "queue", "curate"])
    seeds.add_argument(
        "--file",
        type=Path,
        help="import: seed CSV (default data/seeds/owner/owner-seeds.csv); curate: the"
        " curation file to apply (data/seeds/curation/)",
    )
    seeds.add_argument(
        "--out", type=Path, help="export path (default data/raw/seed-candidates.csv)"
    )
    seeds.add_argument("--lemma", action="append", help="queue: only this headword (repeatable)")
    seeds.add_argument(
        "--level", action="append", choices=["A1", "A2", "B1", "B2", "C1", "C2"],
        help="queue: only candidates listed at this level (repeatable)",
    )  # fmt: skip
    seeds.add_argument("--limit", type=int, help="queue: at most this many, by priority")
    seeds.add_argument(
        "--dry-run", action="store_true", help="queue and curate: show, change nothing"
    )

    requests = commands.add_parser("requests", help="word requests (design §6.7)")
    requests.add_argument(
        "action", choices=["sync"],
        help="suggest the words published entries link to, and approve what the rule allows",
    )  # fmt: skip
    requests.add_argument("--dry-run", action="store_true", help="show, change nothing")

    factory = commands.add_parser("factory", help="the factory's progress and tokens")
    factory.add_argument("action", choices=["status", "token", "revoke"])
    factory.add_argument("--name", help="token and revoke: a label such as 'routine'")
    factory.add_argument("--runs", type=int, default=10, help="status: how many recent runs")

    audit = commands.add_parser("audit", help="tokens for the audit routine (design §6.6)")
    audit.add_argument("action", choices=["token", "revoke"])
    audit.add_argument("--name", required=True, help="a label such as 'audit'")

    admin = commands.add_parser("admin", help="tokens for the admin API (the LMS's console)")
    admin.add_argument("action", choices=["token", "revoke"])
    admin.add_argument("--name", required=True, help="a label such as 'lms'")

    db = commands.add_parser("db", help="database administration")
    db.add_argument("action", choices=["app-role"], help="new password for the API's role")
    db.add_argument("--role", default="dictionary_app")

    audio = commands.add_parser("audio", help="pronunciation and example audio (Kokoro)")
    audio.add_argument("action", choices=["sync", "loop"])
    audio.add_argument("--limit", type=int, help="sync: stop after this many files")
    audio.add_argument("--shard", default="0/1", help="sync: i/n, this process's share")
    audio.add_argument("--every", type=int, default=60, help="loop: seconds to wait when idle")

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
    elif args.command == "seeds" and args.action == "queue":
        _seeds_queue(args.lemma, args.level, args.limit, args.dry_run)
    elif args.command == "seeds" and args.action == "curate":
        _seeds_curate(args.file, args.dry_run)
    elif args.command == "seeds":
        _seeds(args.action, args.file, args.out)
    elif args.command == "requests":
        _requests_sync(args.dry_run)
    elif args.command == "factory":
        _factory(args.action, args.name, args.runs)
    elif args.command == "admin":
        _token("admin", args.action, args.name)
    elif args.command == "audit":
        _token("audit", args.action, args.name)
    elif args.command == "db":
        _db_app_role(args.role)
    elif args.command == "audio":
        _audio(args.action, args.limit, args.shard, args.every)
