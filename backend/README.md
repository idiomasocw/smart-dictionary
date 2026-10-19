# Smart Dictionary backend

Python 3.13 package `smart_dictionary`: the REST API (FastAPI), the database migrations (Alembic), the open-dataset importers and, in later phases, the content pipeline and workers. The design lives in [`../docs/03-system-design.md`](../docs/03-system-design.md).

## Local setup

```bash
supabase start                             # from the repo root: Postgres on 127.0.0.1:34322, Studio on :34323
cd backend
cp .env.example .env
uv sync
uv run smart-dictionary migrate            # alembic upgrade head
uv run smart-dictionary data fetch --all   # open datasets into ../data/raw/ (about 0.6 GB)
uv run smart-dictionary data import --all  # load them into the ref_* tables
uv run smart-dictionary fixtures load      # publish the hand-written entries in fixtures/
uv run smart-dictionary api                # http://127.0.0.1:8000/healthz
```

`smart-dictionary data list` shows the datasets in `../data/datasets.toml`; `data fetch` and `data import` also take dataset ids instead of `--all`.

`smart-dictionary seeds import` builds `seed_candidates` from `../data/seeds/owner/owner-seeds.csv` (written by `../scripts/normalize_owner_lists.py`) and the open lists, and `seeds export` writes them, ranked, to `../data/raw/seed-candidates.csv`. Seed candidates join the Wiktionary keep set and the builder reads Wiktionary, so on a new database run, in this order, `data import --all`, `seeds import`, `data import kaikki-en` and `seeds import`. Without Wiktionary rows the first build folds some list words into others (chiselled into chisel), and the Wiktionary import then misses their rows.

The read API (`/v1/lookup`, `/v1/suggest`, `/v1/entries/{key}`) is specified in design §5, and outside production its interactive docs are at `/docs`. Errors are `application/problem+json`. When `BBB_PROXY_SECRET` is set, every `/v1` request needs the proxy's `X-Dict-Secret` header.

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
| `src/smart_dictionary/core/` | Settings, logging, IDs, text normalization, content keys, the EntryDoc models and the request `Principal` |
| `src/smart_dictionary/db/` | The async SQLAlchemy engine for the API, a plain psycopg connection for importers and publishing, and the API's least-privilege role (`dictionary_app`) |
| `src/smart_dictionary/api/` | FastAPI app factory, authentication, problem+json errors and routes |
| `src/smart_dictionary/search/` | Lookup and autocomplete: the SQL generators and their ranking |
| `src/smart_dictionary/content/` | Publishing entry documents, their search projections and the student's entry card |
| `src/smart_dictionary/importers/` | Fetchers and parsers that load the open datasets into the `ref_*` tables |
| `src/smart_dictionary/seeds/` | The seed builder: the owner's seed file and the open lists, as ranked `seed_candidates` |
| `src/smart_dictionary/factory/` | The factory's server side: hashed client tokens, the queue (`seeds queue`), leases, grounding packs, the V1–V11 checks and the submit step |
| `src/smart_dictionary/cli.py` | `smart-dictionary api \| migrate \| schema \| data \| fixtures \| seeds \| factory \| db`; `worker` arrives with the job queue |
| `Dockerfile` | The production image: runtime dependencies only, run by an unprivileged user; deploying is in `../infra/README.md` |
| `fixtures/` | Hand-written development entries (EntryDoc JSON), also used by the tests |
| `migrations/` | Alembic revisions, written as plain SQL; no autogenerate |
| `tests/` | pytest; database tests run on throwaway databases and skip themselves when no database is reachable |
