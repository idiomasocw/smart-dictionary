# Smart Dictionary

An AI-built, L1-aware English learner's dictionary for OneCulture World's BigBlueButton classes. An automated content factory (Claude Code routines with automated QA) writes every entry in advance: definitions, Latin American Spanish equivalents, examples and audio. AI adds adaptive help on top: a sentence coach, tailored examples, "explain the difference" and a review deck.

## Repository map

| Path | Contents |
|---|---|
| [`docs/`](docs/) | The audit, product spec, system design, roadmap and integration decisions. Start with `02`–`05` |
| [`backend/`](backend/) | Python 3.13 API, database migrations and dataset importers; workers arrive with the content factory. See [`backend/README.md`](backend/README.md) |
| [`data/`](data/) | The open-dataset manifest (`datasets.toml`) and its licensing notes. Downloads land in `data/raw/`, which git ignores |
| [`factory/`](factory/) | The content factory's contract: the EntryDoc JSON Schema. The routines arrive in Phase 2 |
| [`supabase/`](supabase/) | Local Supabase CLI config: database and Studio only, on ports 34320–34329 |

`infra/` (CDK and deploy) arrives later in Phase 1. The BBB plugin lives in its own repository.

## Local development

```bash
supabase start            # repo root: Postgres on 127.0.0.1:34322, Studio on http://127.0.0.1:34323
cd backend
cp .env.example .env
uv sync
uv run smart-dictionary migrate
uv run smart-dictionary data fetch --all    # the open datasets, about 0.6 GB, into data/raw/
uv run smart-dictionary data import --all   # load them into the ref_* tables
uv run smart-dictionary fixtures load       # hand-written development entries
uv run smart-dictionary api
```

Stop the database with `supabase stop` from the repo root. The ports don't collide with the LMS's local stack (54320–54329).

## Status

Phase 1 (foundation) is in progress; see [`docs/04-roadmap.md`](docs/04-roadmap.md).
