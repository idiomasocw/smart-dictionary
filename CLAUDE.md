# Smart Dictionary: working notes for Claude

An English learner's dictionary for OneCulture World. Entries are pre-populated by an AI content factory; AI adds adaptive help at request time. Students use it through a BigBlueButton 4.0 plugin (separate repo) that talks to this repo's REST API.

## Where the decisions live

- **Requirements:** `docs/02-product-spec.md`. Requirement IDs (LK-8, EC-2, LF-3…) are stable, so cite them in code and commits where relevant.
- **Architecture:** `docs/03-system-design.md`.
- **Hosting, database, LMS integration:** `docs/05-integration-and-hosting.md`. It wins over 03 where they differ.
- **Plan:** `docs/04-roadmap.md`.
- **Background:** `docs/01-audit-and-recommendations.md` is the original audit; its §16.1 records the owner's decisions.

## Ground rules

- **Stack:** Python 3.13 in `backend/` (uv), FastAPI, SQLAlchemy 2 with psycopg 3. No TypeScript in this repo except the future admin SPA.
- **Migrations:** Alembic revisions written as plain SQL (no autogenerate), one revision per change, always with a downgrade. `tests/test_migrations.py` applies and rolls back everything on a scratch database.
- **IDs:** row IDs are UUIDv7 from `core/ids.py`. The hosted database is Postgres 17, which has no `uuidv7()`.
- **Extensions** live in the `extensions` schema, so schema-qualify their types and operator classes in DDL (`extensions.halfvec`, `extensions.gin_trgm_ops`).
- **Row-level security:** hosted Supabase turns RLS on for every new table in `public` (its `ensure_rls` event trigger), and the local stack doesn't. The API's role `dictionary_app` bypasses RLS (revision 0006), so new tables need no policies. Never rely on RLS for authorization here.
- **Content keys** (entries, senses, examples) are stable forever: never reuse or renumber them.
- **The LMS integrates through HTTP contracts only** (05 §6). Never read LMS tables, and never store student names, emails or phones.
- **No LLM in the lookup path.** Request-time AI goes through the gateway, with its $5/month cap.
- **AI writes entries only for words the owner chose:** seeded, or approved in the console (`entries.approved_at`). A student's search never adds a word; a real one becomes a word request (design §6.5, §6.7).
- **Open datasets are facts only.** `ref_wiktionary.glosses` is CC BY-SA text: use it for internal hints and checks, but never serve it or copy it into entries (`data/README.md`).
- **Secrets** never go in the repo; `backend/.env` is local only.
- **Git:** the owner runs every `git add` and `git commit`. Propose the exact commands, grouped into logical commits, and never add Co-Authored-By or other AI attribution lines.
- **Windows development:** psycopg's async mode needs a selector event loop. The CLI handles it (`core/runtime.py`), and tests that touch the async database use the `anyio_backend_options` fixture.

## Commands

Run these from `backend/`:

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run smart-dictionary migrate            # alembic upgrade head
uv run smart-dictionary data fetch --all   # open datasets into data/raw/ (gitignored)
uv run smart-dictionary data import --all  # load them into the ref_* tables
uv run smart-dictionary fixtures load      # publish the hand-written entries in fixtures/
uv run smart-dictionary seeds import       # seed_candidates from the owner's and open lists
uv run smart-dictionary seeds export       # the ranked candidates, to data/raw/seed-candidates.csv
uv run smart-dictionary seeds queue --dry-run --limit 20   # what would be queued next, by priority
uv run smart-dictionary factory token --name routine       # a factory bearer token, shown once
uv run smart-dictionary admin token --name lms             # the LMS console's admin token, shown once
uv run smart-dictionary factory status     # entries by status, the pace, and the recent runs
uv run --group audio smart-dictionary audio sync   # voice what published entries lack, into the outbox
uv run smart-dictionary db app-role        # new password for the API's role; prints its DATABASE_URL
uv run smart-dictionary api                # http://127.0.0.1:8000
```

The local database runs from the repo root: `supabase start` / `supabase stop`, on ports 34320–34329. The LMS's local stack uses 54320–54329, so never move this project onto those.

Production runs on its own EC2 in us-east-2: `infra/cdk` (Python CDK), `deploy/` (Compose, Caddy, the deploy script) and `.github/workflows/deploy.yml`, which deploys through SSM. `infra/README.md` is the runbook. The API connects as `dictionary_app`, which can't change the schema; migrations run as the owner.
