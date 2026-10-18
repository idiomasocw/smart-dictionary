# Smart Dictionary backend

Python 3.13 package `smart_dictionary`: the REST API (FastAPI), the database migrations (Alembic) and, in later phases, the content pipeline and workers. The design lives in [`../docs/03-system-design.md`](../docs/03-system-design.md).

## Local setup

```bash
supabase start                     # from the repo root: Postgres on 127.0.0.1:34322, Studio on :34323
cd backend
cp .env.example .env
uv sync
uv run smart-dictionary migrate    # alembic upgrade head
uv run smart-dictionary api        # http://127.0.0.1:8000/healthz
```

## Checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

## Layout

| Path | Contents |
|---|---|
| `src/smart_dictionary/core/` | Settings, logging, IDs, and later text normalization, keys and the EntryDoc models |
| `src/smart_dictionary/db/` | SQLAlchemy engine and sessions |
| `src/smart_dictionary/api/` | FastAPI app factory and routes |
| `src/smart_dictionary/cli.py` | `smart-dictionary api \| migrate`; `worker` arrives with the job queue |
| `migrations/` | Alembic revisions, written as plain SQL; no autogenerate |
| `tests/` | pytest; database tests skip themselves when no database is reachable |
