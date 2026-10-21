# Smart Video Dictionary: System Design (v1)

> **Status:** draft v0.1 for review · **Date:** 2026-10-18 · **Owner:** Diego E
> **Implements:** `02-product-spec.md`; requirement IDs appear in brackets, e.g. [LK-8]. **Decisions:** `01-audit-and-recommendations.md` §16.1.
> **Hosting, database and LMS integration:** `05-integration-and-hosting.md` (a dedicated EC2 in AWS us-east-2 plus a dedicated Supabase project) supersedes any Oracle-specific detail.
> Figures marked *(estimate)* are my own and get validated in the pilot. Anything that touches BBB SDK 1.0 must be re-verified in the plugin spike (§9.5).

---

## 1. Overview

### 1.1 Context

```
 students / teachers ── browser: BBB 4.0 client + dictionary plugin
        │  same-origin calls: /dictionary/v1/*  (BBB session token)
        ▼
 BBB server (GCP us-central1) · nginx ── auth_request → BBB session check
        │  adds X-BBB-Meeting / X-BBB-Ext-User / X-BBB-Is-Mod + X-Dict-Secret
        │  HTTPS (~10–15 ms)
        ▼
 AWS us-east-2 · EC2 t4g.medium · Docker Compose              Supabase project (us-east-2, dedicated)
   caddy ─▶ api (FastAPI) ───────────────────────────────────▶ Postgres + pgvector, pg_trgm, unaccent,
               │  spaCy + embedder                              fuzzystrmatch · Data API off
               │  AI gateway ──▶ DeepSeek → Haiku 5.5 → Gemini → Groq (free) ▲
               │  lms-adapter ──▶ LMS /internal/dictionary/learners (L1, level, role)
               │                                                              │
               └── jobs (Procrastinate) ──▶ worker: publish · embeddings · TTS · spot checks · seeds
                                                 │        optional batch node: Oracle A1 (batch queue only)
                                                 ▼
                                           S3 + CloudFront: audio · exports

 Claude Code routine (cloud, Max 5x, started by you) ── HTTPS + token ──▶ api /v1/factory/*
```

### 1.2 Components

| Component | Responsibility | Technology |
|---|---|---|
| **plugin** | In-class UI: lookup, cards, My words and review, coach, reports, teacher tools; data channels | React + TypeScript + Vite, BBB plugin SDK 1.0 (pinned) |
| **bbb-proxy** | Checks the BBB session and injects identity headers plus the shared secret | nginx `auth_request` snippet on the BBB server |
| **api** | REST v1: lookup, search and context; learner data; AI endpoints; factory and admin endpoints | Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2 (async, psycopg 3), spaCy, ONNX Runtime |
| **worker** | Jobs and schedules: the publish pipeline (validate → project → embed → TTS), seed builder, spot checks, report triage, suspicion scores, re-validation sweeps, exports, retention | Procrastinate (Postgres-backed) |
| **database** | Content (documents + projections), search indexes, vectors, jobs, learner data, usage, AI cache and spend | A dedicated Supabase project (us-east-2) used as plain Postgres + pgvector, pg_trgm, unaccent, fuzzystrmatch; Data API off |
| **lms-adapter** | Pulls the learner profile (L1, region, level, role, active) from the LMS and caches it for 24 h | HTTP client + `DICTIONARY_SERVICE_TOKEN` (05 §6.2) |
| **caddy** | TLS, reverse proxy, compression, static hosting for the plugin bundle | Caddy 2 |
| **factory** | Generates and reviews entries in batches, then submits them | Claude Code routine + the repo's `factory/` folder |
| **S3 + CloudFront** | Audio, JSONL exports | A new bucket and distribution in your AWS account |

### 1.3 Quality attributes and how the design meets them

| Attribute | Design response |
|---|---|
| Fast lookups [NF-1] | Precomputed projections and indexes; no LLM in the lookup path; spaCy and the embedder run in-process |
| Low cost [NF-9] | One node; open models on CPU; generation through the subscription routine; a capped AI gateway |
| Quality without human reviewers [G6] | Validators, in-run reviewer, spot checks, teacher hides, golden set |
| Change tolerance | A language-neutral schema; L1 layers added on top; providers behind the gateway; an SDK adapter layer in the plugin |
| Small footprint | API and worker fit a 4 GB t4g.medium; the database is managed (§11.2) |

---

## 2. Architecture decisions

Each decision gets a short ADR file in `docs/adr/` when we implement it.

| ADR | Decision | Rationale |
|---|---|---|
| 001 | Python backend (FastAPI) | NLP, ML and media tooling is Python-native; Pydantic models double as the LLM JSON Schemas |
| 002 | A **dedicated Supabase project** as managed Postgres (Data API off), never the LMS database | One store for everything, with managed backups and isolation from the LMS (05 §4) |
| 003 | REST + OpenAPI 3.1, no GraphQL | HTTP caching and a trivially generated client |
| 004 | Entry documents + projections | Matches how entries are produced (one JSON per entry) and served (one card) |
| 005 | Stable, human-readable keys for entries, senses and examples | Saved words, audio and flags survive regeneration |
| 006 | Procrastinate job queue, no Redis | Jobs enqueue in the same transaction as the data; one service fewer |
| 007 | spaCy in-process, no NLP microservice | Used for sentences only; single words resolve through form tables |
| 008 | Content generated through Claude Code routines only | Your decision (§16.1); the Batch API stays as an escape hatch |
| 009 | AI gateway: DeepSeek → Haiku 5.5 → Gemini → Groq free, with a $5 cap | Resilience and cost control |
| 010 | No Gemini | Its API terms exclude services likely to be used by minors, and students may be 16+ |
| 011 | Target BBB 4.0 / plugin SDK 1.0 | You're upgrading now |
| 012 | Identity via nginx `auth_request` on the BBB server | No secrets in the browser; validated in the spike |
| 013 | Kokoro-82M TTS driven by IPA | Free, runs on CPU, pronunciation exactly as the IPA says |
| 014 | Clips deferred | The rights decision is pending |
| 015 | es-419 as the default Spanish | Your decision |
| 016 | Host on a dedicated Graviton EC2 in AWS us-east-2 (Ohio) | Next to the DB, the LMS and BBB; isolated from the LMS host (05 §5) |
| 017 | Separate repository; the LMS and the plugin integrate through contracts | Different stack and lifecycle; a small AI context; least privilege (05 §3) |
| 018 | Learner data comes from an LMS pull endpoint, pseudonymous and minimal | The LMS owns its data; no shared tables (05 §6) |
| 019 | Multi-tenant-ready core: tenants, auth adapters, API clients | External consumers become an additive change (05 §7) |

---

## 3. Data model

### 3.1 Keys

Keys are stable, human-readable and never reused [ADR-005].

| Object | Format | Examples |
|---|---|---|
| Entry | `{lang}:{slug}:{code}[~{homograph}]` | `en:actually:adv`, `en:run_into:phrv`, `en:break_the_ice:idiom`, `en:bow:n~2` |
| Sense | `{entry_key}#{n}` | `en:actually:adv#1` |
| Example | `{sense_key}.ex{n}` | `en:actually:adv#1.ex2` |
| Note | `{sense_key}.note{n}` | `en:actually:adv#1.note1` |

- **Slug:** the lowercase lemma, with spaces → `_`. Hyphens, apostrophes and periods are kept: *well-known* → `well-known`, *o'clock* → `o'clock`.
- **Code:** a POS code for words (`n v adj adv pron det prep conj interj num`), or the kind for multiword entries (`phrv idiom phr`). Homograph suffixes are rare, e.g. *bow* (bend) vs *bow* (ribbon).
- **Numbering:** sense and example numbers are assigned at first publish and are **not** display order (that's `ord`). Retired numbers are never reused.
- **Regeneration:** the factory receives the existing keys and must keep a key for an unchanged meaning. The submit endpoint rejects a document that silently drops a published sense key: it must be marked retired instead.

### 3.2 Text normalization

`norm(text, lang)` is implemented once in Python (`core/text.py`). Every `*_norm` column is computed by it, and every query is normalized with it before SQL.

1. Unicode NFKC, then casefold.
2. Curly quotes and apostrophes (’ ‘ ʼ ´ \`) → `'`; dashes (– — ‐) → `-`; whitespace collapsed and trimmed; surrounding punctuation removed.
3. Diacritics removed for matching (`papá` → `papa`, `café` → `cafe`). The original `term`/`form` is kept, and exact-accent matches rank higher.
4. Hyphen and space variants become extra forms: `well-known` also yields `well known`.
5. Arabic (later): strip tashkeel and tatweel; أ/إ/آ → ا, ى → ي, ة → ه.

### 3.3 Entry lifecycle

```
candidate ──(seed accepted)──▶ queued ──(batch leased)──▶ generating ──(submitted)──▶ validating
validating ──(passes)───────────────────────────────▶ published
validating ──(fails, attempts < 2)───────────────────▶ needs_repair ──(next batch)──▶ generating
validating ──(fails, attempts = 2)───────────────────▶ quarantined
missing word, seeded ──(gateway)──▶ provisional ──(leased with top priority)──▶ generating …
missing word, not seeded ──(student requests it)──▶ word request ──(owner approves)──▶ queued …
published ──(hide, flag or valid report / failed re-check or audit)──▶ needs_repair   (the live entry stays; hidden parts are filtered)
any ──(owner)──▶ retired
```

**What students see** (`content/visibility.py`, revision 0012): an entry that's **approved, has a published document and isn't retired**. The pipeline status doesn't decide it:
- **An entry being rewritten or repaired** (`generating`, `needs_repair`, `quarantined`) keeps showing the document it last published. A Beta entry never vanishes while the routine replaces it, and a search that points to it never opens a 404.
- **Approved** (`entries.approved_at`, `approved_by`) means the owner chose the word: seeded through `seeds queue`, loaded as a fixture, approved in the console, or approved at once by the owner's rule (`auto:lists`, §6.7). The routine leases only approved entries.
- **A seed whose entry existed first** (a Beta AI wrote for a search, a fixture) is linked to that entry, which is then approved. Before revision 0013, `seeds queue` skipped such a key and left the seed unlinked, so revision 0012 took *raise*, *expect* or *actually* for words nobody chose and sent them to review; 0013 linked them.

**Leases fix the old "stuck in PROCESSING" bug (H3).**
- A leased batch sets `lease_batch_id`, `lease_expires_at` (3 h) and `leased_from`, the status the entry had before (revision 0007).
- Each new lease first returns entries from expired leases to their `leased_from` status and marks their batches `expired`, so no background job is needed for this.
- Leasing uses `FOR UPDATE SKIP LOCKED`, so two runs never get the same entry.
- Provisional entries and repairs come first, then priority.

### 3.4 DDL (v1)

```sql
create extension if not exists vector;
create extension if not exists pg_trgm;
create extension if not exists unaccent;
create extension if not exists fuzzystrmatch;

-- Reference ------------------------------------------------------------------
create table languages (code text primary key, name text not null,
                        direction text not null default 'ltr' check (direction in ('ltr','rtl')));
create table varieties (tag text primary key,                          -- en-US, en-GB, es-419, es-ES, pt-BR, ar
                        language text not null references languages(code), name text not null);

-- Entries ----------------------------------------------------------------------
create type entry_status as enum ('candidate','queued','generating','validating','published',
                                  'provisional','needs_repair','quarantined','retired');
create type entry_kind as enum ('word','phrasal_verb','idiom','phrase');

create table entries (
  id               uuid primary key default uuidv7(),
  key              text not null unique,
  language         text not null references languages(code),
  lemma            text not null,
  lemma_norm       text not null,
  pos              text not null check (pos in ('noun','verb','adjective','adverb','pronoun','determiner',
                                                 'preposition','conjunction','interjection','numeral','phrase')),
  kind             entry_kind not null default 'word',
  homograph        smallint not null default 1,
  cefr             text check (cefr in ('A1','A2','B1','B2','C1','C2')),
  freq_zipf        numeric(3,2),
  priority         integer not null default 0,
  status           entry_status not null default 'candidate',
  attempts         smallint not null default 0,
  lease_batch_id   uuid,
  lease_expires_at timestamptz,
  quality          numeric(4,3),
  content_version  integer not null default 0,           -- bumps on every publish; part of the ETag
  doc              jsonb,                                 -- validated EntryDoc (§3.5)
  current_revision_id uuid,
  sources          text[] not null default '{}',          -- owner, cefr-j, octanove, ngsl, wiktionary, miss
  published_at     timestamptz,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (language, lemma_norm, pos, kind, homograph)
);
create index entries_queue  on entries (status, priority desc) where status in ('queued','needs_repair','provisional');
create index entries_prefix on entries (language, lemma_norm text_pattern_ops);
create index entries_trgm   on entries using gin (lemma_norm gin_trgm_ops);
create index entries_lease  on entries (lease_expires_at) where lease_batch_id is not null;

create table entry_revisions (
  id          uuid primary key default uuidv7(),
  entry_id    uuid not null references entries(id),
  doc         jsonb not null,
  produced_by jsonb not null,    -- {source:'routine'|'gateway', model, prompt_version, batch_id, run_label}
  qa          jsonb not null,    -- validator, reviewer and spot-check results; quality score
  outcome     text not null check (outcome in ('published','needs_repair','quarantined')),
  created_at  timestamptz not null default now()
);

-- Projections: rebuilt from doc on publish, in the same transaction -----------
create table entry_forms (
  entry_id  uuid not null references entries(id) on delete cascade,
  form text not null, form_norm text not null,
  features  text,                                -- UniMorph style: 'V;PST', 'N;PL'; null for variants
  source    text not null,                       -- unimorph | lemminflect | variant | doc
  primary key (entry_id, form_norm));
create index entry_forms_norm  on entry_forms (form_norm);
create index entry_forms_trgm  on entry_forms using gin (form_norm gin_trgm_ops);
create index entry_forms_dmeta on entry_forms (dmetaphone(form_norm));

create table expression_patterns (
  entry_id  uuid primary key references entries(id) on delete cascade,
  tokens    jsonb not null,                      -- [{"lemma":"run"},{"lemma":"into"}], {"slot":"sb"}
  separable boolean not null default false,
  max_gap   smallint not null default 0);

create table senses (
  key         text primary key,
  entry_id    uuid not null references entries(id),
  ord         smallint not null,
  cefr        text,
  registers   text[] not null default '{}',
  domains     text[] not null default '{}',
  short_gloss text not null,
  retired_at  timestamptz,                       -- soft retirement: never cascade-deleted
  embedding   halfvec(768));                     -- dimension fixed by the bake-off (§10.3)
create index senses_entry     on senses (entry_id) where retired_at is null;
create index senses_embedding on senses using hnsw (embedding halfvec_cosine_ops);

create table equivalents (
  sense_key text not null references senses(key),
  variety   text not null references varieties(tag),
  term text not null, term_norm text not null,
  rank      smallint not null,
  match     text not null check (match in ('exact','near','approx','paraphrase')),
  regions   text[] not null default '{}',
  target_entry_key text,                         -- bidirectional phase
  primary key (sense_key, variety, term_norm));
create index equivalents_norm   on equivalents (term_norm);
create index equivalents_prefix on equivalents (term_norm text_pattern_ops);
create index equivalents_trgm   on equivalents using gin (term_norm gin_trgm_ops);

create table l1_forms (                          -- inflected L1 input → equivalent ("me di cuenta" → "darse cuenta")
  variety text not null, form_norm text not null, term_norm text not null,
  primary key (variety, form_norm, term_norm));

create table sense_links (                       -- created now, filled in the bidirectional phase
  from_sense text not null references senses(key), to_sense text not null references senses(key),
  kind text not null check (kind in ('equivalent','near_equivalent')), primary key (from_sense, to_sense));

create table audio_assets (
  id uuid primary key default uuidv7(),
  owner_key text not null,                       -- entry key + pronunciation index, or an example key
  variety text not null, voice text not null, ipa text,
  engine text not null,                          -- e.g. 'kokoro-82m@1.0'
  s3_key text not null,                          -- content-addressed (§10.4)
  duration_ms integer, created_at timestamptz not null default now(),
  unique (owner_key, variety, voice));

-- Learners and usage -----------------------------------------------------------
create table users (
  id          uuid primary key default uuidv7(),
  external_id text not null unique,              -- your portal's student ID, via BBB
  l1          text,
  variety     text references varieties(tag),
  level       text check (level in ('A1','A2','B1','B2','C1','C2')),
  is_minor    boolean,                           -- unknown until your portal provides it
  created_at  timestamptz not null default now(),
  deleted_at  timestamptz);

create table lookups (
  id bigint generated always as identity, created_at timestamptz not null default now(),
  user_id uuid, meeting_id text, query text not null, direction text,
  entry_key text, sense_key text, had_context boolean not null default false,
  result text not null check (result in ('hit','miss','provisional','invalid')), latency_ms integer,
  primary key (id, created_at)) partition by range (created_at);   -- monthly; dropped after 12 months [NF-7]

create table saved_items (
  user_id uuid not null references users(id), sense_key text not null references senses(key),
  meeting_id text, created_at timestamptz not null default now(), primary key (user_id, sense_key));

create table review_cards (
  user_id uuid not null references users(id), sense_key text not null references senses(key),
  card_type text not null check (card_type in ('l1_to_en','en_to_meaning')),
  fsrs jsonb not null,                           -- stability, difficulty, state, reps, lapses, last_review
  due_at timestamptz not null, primary key (user_id, sense_key, card_type));
create index review_due on review_cards (user_id, due_at);

create table review_logs (
  id bigint generated always as identity primary key, user_id uuid not null, sense_key text not null,
  card_type text not null, rating smallint not null check (rating between 1 and 4),
  reviewed_at timestamptz not null default now(), elapsed_days numeric, scheduled_days numeric);

create table pushes (id uuid primary key default uuidv7(), meeting_id text not null, target_key text not null,
                     by_user uuid references users(id), created_at timestamptz not null default now());

create table content_flags (
  id uuid primary key default uuidv7(),
  target_key text not null,                      -- sense, example or note key; 'sense_key|variety|term' for an equivalent
  action text not null check (action in ('hide','flag')), reason text not null,
  by_user uuid references users(id), meeting_id text,
  created_at timestamptz not null default now(), undone_at timestamptz);
create index content_flags_active on content_flags (target_key) where undone_at is null;

create table reports (
  id uuid primary key default uuidv7(), reporter uuid references users(id), target_key text not null,
  kind text not null check (kind in ('wrong_translation','bad_example','wrong_meaning','offensive','other')),
  comment text, status text not null default 'open' check (status in ('open','valid','invalid','fixed')),
  triage jsonb, created_at timestamptz not null default now());

create table misses (
  language text not null, query_norm text not null, hits integer not null default 1,
  status text not null default 'pending' check (status in ('pending','invalid','provisional','queued','published')),
  entry_key text, first_seen timestamptz not null default now(), last_seen timestamptz not null default now(),
  primary key (language, query_norm));

-- Factory and seeds --------------------------------------------------------------
create table seed_candidates (
  id uuid primary key default uuidv7(), lemma text not null, lemma_norm text not null,
  pos text, kind entry_kind, sources text[] not null, levels text[] not null default '{}',
  zipf numeric(3,2), tier smallint,
  decision text check (decision in ('accept','reject','merge')), reason text,
  entry_id uuid references entries(id),
  unique nulls not distinct (lemma_norm, pos, kind));

create table factory_batches (
  id uuid primary key default uuidv7(), requested_size smallint not null, entry_ids uuid[] not null,
  status text not null default 'leased' check (status in ('leased','submitted','expired')),
  run_label text, leased_at timestamptz not null default now(), submitted_at timestamptz, results jsonb);

create table api_tokens (
  id uuid primary key default uuidv7(), name text not null,
  scope text not null check (scope in ('factory','admin')), token_hash bytea not null unique,
  created_at timestamptz not null default now(), revoked_at timestamptz);

-- AI -----------------------------------------------------------------------------
create table ai_cache (
  task text not null, input_hash bytea not null, output jsonb not null,
  provider text not null, model text not null,
  created_at timestamptz not null default now(), expires_at timestamptz,
  primary key (task, input_hash));

create table llm_calls (
  id bigint generated always as identity primary key, created_at timestamptz not null default now(),
  task text not null, provider text not null, model text not null, prompt_version text not null,
  in_tokens integer, out_tokens integer, cost_usd numeric(10,6) not null default 0, latency_ms integer,
  outcome text not null check (outcome in ('ok','fallback','error','timeout','refused')),
  user_id uuid, ref_key text);
create index llm_calls_time on llm_calls (created_at);
```

**Amendments**, implemented in `backend/migrations/versions/0001_core_schema.py` (that file is authoritative where it differs from the sketch above):
- **Tenancy (05 §7):**
  - `tenants (id text primary key, name text not null)`, seeded with `('ocw', 'OneCulture World')`;
  - `tenant_id text not null references tenants(id)` on `users`, `lookups`, `saved_items`, `review_cards`, `review_logs`, `pushes`, `content_flags`, `reports` and `misses`. On `llm_calls` it's nullable, because factory and system calls have no tenant;
  - `users` is `unique (tenant_id, external_id)`.
- **API clients:** `api_tokens` became `api_clients (id, tenant_id, name, scopes text[], token_hash, rate_limit_per_min, created_at, revoked_at)`, with scopes `factory`, `admin`, `lms` and, later, `app`.
- **Learner profile:** `users` holds `native_language`, `native_region`, `level`, `role`, `is_active` and `profile_synced_at`, synced by the lms-adapter (05 §6.2 L1). `variety` is the student's own choice; when it's null, the variety is derived from native language and region. The separate `l1` column was dropped.
- **Phonetic matching:** `entry_forms.phonetic` holds a Double Metaphone key computed by the app, instead of an index on `dmetaphone()`.
- **UUIDv7:** the hosted project runs PostgreSQL 17, so IDs are generated in the app (`core/ids.py`), with `gen_random_uuid()` defaults as a safety net.
- **API-role lockdown:** the migration revokes Supabase's default grants to `anon` and `authenticated`, even though the Data API is off.
- **Seed candidates (revision 0004):** `seed_candidates` also holds the seed file's `variants`, `patterns` and `flags`, the `originals` lines and a `homograph` number. Uniqueness is `(lemma_norm, pos, kind, homograph)`, as for entries, so *Pole* and *pole* can both be noun candidates.

**Reference data:** `smart-dictionary data fetch` and `data import` load the open datasets listed in `data/datasets.toml` into read-only `ref_*` tables. Revisions `0002_reference_tables.py` and `0003_wiktionary_variants.py` are authoritative for their columns.

| Table | Contents |
|---|---|
| `ref_datasets` | One row per dataset: version, license, attribution, source URL, checksum and row count |
| `ref_lists` | Headword lists with levels or ranks: CEFR-J, Octanove C1/C2 and the NGSL family (NGSL and its supplement, NGSL-Spoken, NAWL, TSL, BSL); the owner's lists later |
| `ref_freq` | wordfreq Zipf values: English for ranking, Spanish as a lexicon check |
| `ref_forms` | UniMorph lemma–form pairs, English and Spanish |
| `ref_ipa` | CMUdict (en-US) and Britfone (en-GB), converted to IPA |
| `ref_wiktionary` | kaikki.org Wiktionary facts for frequent or listed words, frequent multiword entries (thresholds in `datasets.toml`) and proper nouns whose exact spelling is listed (April, Monday): kind, POS, IPA, forms, tags and translation candidates. US spellings and alternative forms that Wiktionary files as pointers (catalog, okay) keep their own IPA and forms, and `variant_of` names the main spelling. It's recorded per entry, so a word is a variant only where its entries for that part of speech have no glosses of their own. `glosses` is CC BY-SA text: it feeds sense hints and the overlap check and is never served |

Every row points at its dataset, and `ref_datasets` also feeds the attribution page. LemmInflect needs no table: it inflects headwords when an entry is published (`entry_forms`).

**Deferred tables:** `media_sources`, `utterances`, `clips` and `clip_coverage` arrive with the clips phase (sketched in audit §5.3).

### 3.5 The `EntryDoc` schema (v1)

This is the single source of truth. The JSON Schema given to the factory, the validators and the API types are all generated from these Pydantic models. Below is an abridged outline; length limits are enforced by the validators, because structured-output schemas can't express min/max lengths.

```python
CEFR    = Literal["A1", "A2", "B1", "B2", "C1", "C2"]
Variety = Literal["es-419"]            # v1; later "pt-BR", "ar"

class Pronunciation(BaseModel):
    variety: Literal["en-US", "en-GB"]
    ipa: str                           # valid IPA charset, with stress marks
    sense_keys: list[str] = []         # only for homographs with different pronunciations

class Equivalent(BaseModel):
    term: str                          # "en realidad"
    match: Literal["exact", "near", "approx", "paraphrase"]
    regions: list[str] = []            # ISO 3166 codes ("ES", "MX", "AR"); empty = neutral es-419
    note: str | None = None            # ≤ 120 chars, in the L1

class L1Layer(BaseModel):
    gloss: str                         # ≤ 160 chars, in the L1
    equivalents: list[Equivalent]      # 1–5, ranked

class Example(BaseModel):
    key: str
    text: str                          # 6–16 words; must contain a form of the headword
    cefr: CEFR | None = None
    translations: dict[Variety, str]

class Note(BaseModel):
    key: str
    kind: Literal["false_friend", "common_mistake", "pronunciation", "grammar", "usage", "culture"]
    l1: Literal["es", "pt", "ar"] | None = None    # None = for everyone (written in English)
    text: str                          # ≤ 300 chars
    see: list[str] = []                # entry or sense keys

class Sense(BaseModel):
    key: str
    cefr: CEFR
    definition: str                    # learner English, ≤ 20 words, without the headword
    short_gloss: str                   # ≤ 5 words
    registers: list[Literal["formal", "informal", "slang", "offensive", "literary",
                            "old-fashioned", "technical"]] = []
    domains: list[str] = []            # business, medicine, law, technology, sport…
    regions: list[Literal["US", "UK", "AU", "CA", "IE", "NZ"]] = []
    patterns: list[str] = []           # "depend on sb/sth"
    grammar: Grammar | None = None     # countability, transitivity, comparatives…
    l1: dict[Variety, L1Layer]         # "es-419" required in v1
    examples: list[Example]            # 2–3
    collocations: list[str] = []       # ≤ 6
    notes: list[Note] = []             # ≤ 3
    retired: bool = False              # keep the key; drop from display

class Related(BaseModel):
    type: Literal["synonym", "antonym", "family", "confusable", "see_also"]
    key: str                           # an entry key; the target may not exist yet
    note: str | None = None            # nuance, ≤ 120 chars

class Pattern(BaseModel):              # required for phrasal verbs, idioms and phrases
    tokens: list[dict]                 # {"lemma": "run"} | {"text": "the"} | {"slot": "sb"}
                                       # slots: sb, sth, sb/sth, sb's, one's, oneself, somewhere, doing sth
    separable: bool = False
    max_gap: int = 0

class EntryDoc(BaseModel):
    schema_version: Literal[1]
    key: str
    language: Literal["en"]
    lemma: str
    pos: POS
    kind: Literal["word", "phrasal_verb", "idiom", "phrase"]
    cefr: CEFR
    pronunciations: list[Pronunciation]
    variants: list[Variant] = []       # spelling variants: colour (UK) / color (US)
    pattern: Pattern | None = None
    senses: list[Sense]                # 1–6
    related: list[Related] = []
    provisional: bool = False
```

**Related links** (`content/related.py`) name entries by key, and the routine may link a word the dictionary lacks or name it under another key:
- **A card shows a link** only when an entry students see stands for it: the key itself; else the same word and part of speech under another homograph (`en:tv:n` opens `en:tv:n~2`); else, for a multiword expression, its entry of any kind, with or without its article (`en:by_the_way:phr` opens the idiom); else the entry that lists it as a spelling variant under the same part of speech (`en:colour:n` opens *color*). Other links stay in the document, hidden, and appear once their entry is shown. The card's ETag covers the links, so a cached card picks up a new one.
- **A link to a word with no entry at all** becomes a suggestion for the owner when the entry publishes (§6.7).

**Filled by the server, not the AI:** frequency band, inflected forms of the lemma and of each spelling variant (UniMorph/LemmInflect: *realise* → *realised*), audio, embeddings, L1 forms, and `quality`. The AI may add irregular forms and spelling variants, which the validators then check.

### 3.6 Duplicates: what's expected and what isn't

One entry per headword, part of speech and meaning (the owner's headword policy, 2026-10-18), because many forms point to one entry, and a second entry splits saved words and the factory's work.

**Expected:**
- **One entry per part of speech:** *a few* as a determiner and as a pronoun, *house* as a noun and a verb. A key holds the part of speech, and so does the card.
- **Homographs in other capitals that are other words:** *Pole* and *pole*, *March* and *march*, *IT* and *it*. The second takes `~2` in its key.
- **Expressions that share words but not meaning:** *few* and *a few*, *make a point* and *make a point of*, *go out* and *go out with*.

**Duplicates, to retire:**
- **The same word in other capitals:** *cd* and *CD*.
- **One multiword expression in two entries:** *by the way* as an idiom and as a phrase, *miss the boat* as an idiom and as a verb, *the last straw* and *last straw*. `expression_of(lemma_norm)` (revision 0013) is the headword without a leading article while two words remain, and one expression takes one entry under any kind. `seeds queue` links a seed to the expression's entry instead of queuing a second one, and the console's Approve and Add answer 409 naming the entry that exists. One headword of one kind under two parts of speech, or as two homographs, stays two entries: *a few* as a determiner and as a pronoun, *big brother* and *Big Brother*.
- **Hyphen against space:** *face to face* and *face-to-face*. Keep the hyphenated one: its forms include the spaced spelling (`spacing_variants`).
- **A preposition that's only a complement:** *brush up* and *brush up on*. Keep one entry, and let the preposition be a pattern.
- **A UK spelling seeded on its own:** *computerise* beside *computerize*. Retire it, and list it as the US seed's variant.

**Audit:** `docs/queries/duplicates.sql` lists each kind, for the owner to judge. The owner retires duplicates in the console (`POST /v1/admin/entries/{key}/retire`, §5.5), or in bulk with a reviewed script such as `docs/queries/duplicates-fix-2026-10-20.sql`. A retired key is never reused.

---

## 4. Search and lookup

### 4.1 Query analysis (Python, before any SQL)

1. Compute `q_norm = norm(q)`, the token count and the script (Latin now, Arabic later).
2. Direction: if the student forced EN→ES or ES→EN, run only that side. Otherwise run both sides; the generators are cheap [LK-6].
3. If a `context` sentence is present, take the context path (§4.4).

### 4.2 Candidate generators

Each generator is one SQL statement returning `(entry_key, sense_key?, via, raw_score)`. All of them run within a single round trip.

| # | Generator | Matches | Used for |
|---|---|---|---|
| G1 | Exact lemma | `entries.lemma_norm = :q` (status published or provisional) | LK-1, LK-2 |
| G2 | Inflected form | `entry_forms.form_norm = :q` → all lemmas (*leaves* → *leaf*, *leave*) | LK-2 |
| G3 | Multiword | spaCy lemmatizes the query (*gave it up* → *give it up*); the in-memory `Matcher` runs `expression_patterns` → *give up* | LK-3 |
| G4 | L1 equivalent | `l1_forms` (*me di cuenta* → *darse cuenta*), then `equivalents.term_norm`, then the sense | LK-5 |
| G5 | Prefix (autocomplete) | `lemma_norm like :q || '%'` (text_pattern_ops) ∪ the same on `equivalents.term_norm` | LK-1 |
| G6 | Fuzzy | trigram `form_norm % :q` (similarity ≥ 0.4), ordered by similarity; `dmetaphone` fallback for single words | LK-4 |
| G7 | Semantic | Only when G1–G4 find nothing, or the query has ≥ 3 tokens: embed the query, then `senses.embedding <=> :qvec` limit 10 | LK-7 |

Example: G2 + G4 together.

```sql
select e.key, null::text as sense_key, 'form' as via, 1.0 as raw
from entry_forms f join entries e on e.id = f.entry_id
where f.form_norm = :q and e.status in ('published','provisional')
union all
select s.entry_key, q.sense_key, 'equivalent', 1.0 - (q.rank - 1) * 0.05
from (select eq.sense_key, eq.rank from equivalents eq
      where eq.variety = :variety
        and eq.term_norm in (select term_norm from l1_forms where variety = :variety and form_norm = :q
                             union select :q)) q
join lateral (select e.key as entry_key from senses s2 join entries e on e.id = s2.entry_id
              where s2.key = q.sense_key and s2.retired_at is null
                and e.status in ('published','provisional')) s on true;
```

### 4.3 Ranking

- **Fusion:** reciprocal-rank fusion across generator lists (k = 60), weighted: exact 1.0 · form 0.9 · multiword 0.9 · equivalent 0.85 · fuzzy 0.5 · semantic 0.4.
- **Boosts:** frequency (`freq_zipf`); closeness to the student's CEFR level; a small penalty for provisional entries.
- **Grouping:** English-side and L1-side hits are returned as **two labeled groups** when both exist [LK-6].
- **"Did you mean":** returned only from G6, and only when G1–G5 find nothing [LK-4].
- **Misses:** if nothing qualifies, the request goes through the missing-word flow (§6.5) and is upserted into `misses`.

### 4.4 Context lookup [LK-8]

Input: the selected text, its sentence (≤ 500 characters) and the selection offsets.

1. **Parse** the sentence with spaCy `en_core_web_md` (~5–10 ms, an estimate).
2. **Detect multiword expressions:** run the pattern `Matcher` on the parsed sentence and keep matches that overlap the selection, longest first. Separable patterns use the dependency parse (particle attached to the verb).
3. **Candidates:** the matched multiword entries first, then the selected token's lemmas (via `entry_forms`).
4. **Rank senses:** embed the sentence with the same model as the senses, take the cosine similarity to each candidate sense, then a softmax. The top sense is marked *"in this sentence"* when its margin over the next is ≥ a threshold (tuned on the search regression set, §14).
5. **Budget:** spaCy + embedding + SQL < 300 ms at p95 [NF-1]. The embedding is cached per sentence hash for 10 minutes.

### 4.5 Card projection and caching

- `GET /v1/entries/{key}?variety=es-419` builds an **EntryCard** from `doc`:
  - keep only the requested L1 layer [LK-9];
  - remove content hidden in `content_flags`;
  - add the frequency band and the audio links: `pronunciations[].audio_url` and `senses[].examples[].audio_url`, null until the file is uploaded (§10.4). The ETag also changes when audio arrives, which doesn't change the content version.
- Cards are **not personalized**. "Saved" state comes from `/v1/me/saved`, which the plugin caches, so cards are shareable and cacheable.
- **Caching:** an in-process LRU keyed by `(key, variety, content_version, flags_version)`. `ETag = "{content_version}.{flags_version}"`, with `Cache-Control: private, max-age=300` and `If-None-Match` answered with a 304.
- **Pattern refresh:** the `Matcher` and other in-memory indexes reload on publish via Postgres `LISTEN/NOTIFY` (channel `content_changed`).

---

## 5. API (REST v1)

### 5.1 Authentication contexts

| Context | How | Who |
|---|---|---|
| `bbb` | Requests arrive through the BBB nginx proxy carrying `X-Dict-Secret` (constant-time compare) plus `X-BBB-Meeting`, `X-BBB-Ext-User` and `X-BBB-Is-Mod`. A FastAPI dependency builds a `Principal`; `users` rows are created lazily on first use | Students and teachers |
| `factory` | `Authorization: Bearer <token>`, scope `factory` (hashed in `api_clients`) | The routine |
| `audit` | `Authorization: Bearer <token>`, scope `audit` (hashed in `api_clients`, created only by the CLI): lease entries for an audit and submit findings, nothing else | The audit routine (§6.6) |
| `admin` | `Authorization: Bearer <token>`, scope `admin` (hashed in `api_clients`, created only by the CLI), optionally only from the LMS server's address (§5.5) | You, through the LMS's console |
| `lms` | Bearer token, scope `lms`: server-to-server calls from the LMS (later: `/v1/resolve`, `/v1/demand`) | The LMS backend |
| `jwt` *(later)* | An OIDC JWT verified against the issuer's JWKS (Cognito for your LMS web and app) | Students outside BBB |
| `api_key` *(later)* | Per-client keys with scopes, rate limits and metering | External apps |

Every context resolves to `Principal(tenant, subject, meeting, roles, via)` (`core/principal.py`), so the business logic never knows which adapter authenticated the request.

Caddy accepts `bbb` traffic only from the BBB server's IP. Factory routes are token-only, because routines have no static egress IP. Admin routes need their token too, and Caddy can also hold them to the LMS server's address (`ADMIN_ALLOWED_IPS`, §5.5).

### 5.2 Endpoints

| Method | Path | Auth | Purpose | Req |
|---|---|---|---|---|
| GET | `/v1/suggest?q=&dir=auto\|en\|l1&limit=8` | bbb | Autocomplete | LK-1 |
| GET | `/v1/lookup?q=&dir=&context=&sel_start=&sel_end=` | bbb | Search (+ context); grouped results; misses → provisional | LK-2…10 |
| GET | `/v1/entries/{key}?variety=es-419` | bbb | Entry card | EC-* |
| GET | `/v1/me` · PATCH `/v1/me` | bbb | Profile: L1, variety, level | LK-9 |
| GET · PUT · DELETE | `/v1/me/saved[/{sense_key}]` | bbb | Saved senses | LF-1 |
| GET | `/v1/me/review/due?limit=20` | bbb | Due review cards | LF-2 |
| POST | `/v1/me/review/{sense_key}` `{card_type, rating}` | bbb | Grade a card (FSRS) | LF-2 |
| GET | `/v1/me/history` | bbb | Lookups from the last 30 days | LF-6 |
| POST | `/v1/ai/check-sentence` `{sense_key, sentence}` | bbb | Use-it coach | LF-3 |
| POST | `/v1/ai/examples` `{sense_key, topic?, level?}` | bbb | Tailored examples | LF-4 |
| POST | `/v1/ai/difference` `{a, b}` | bbb | Explain the difference | LF-5 |
| GET | `/v1/meetings/current/words` | bbb (moderator) | Class word list | TF-2 |
| POST | `/v1/meetings/current/pushes` `{target_key}` | bbb (moderator) | Log a push; the plugin broadcasts it over a data channel | TF-1 |
| POST | `/v1/flags` `{target_key, action, reason, comment?}` | bbb: `flag` needs a moderator; `hide` needs a verified teacher (LMS role `teacher` or `staff`) | Hide or flag one item | TF-3 |
| DELETE | `/v1/flags/{id}` | bbb: its author, or staff | Undo a hide or flag | TF-3 |
| POST | `/v1/reports` `{target_key, reason, comment?}` | bbb (any active user) | Report a problem; answers 202 | CG-5 |
| GET | `/v1/me/reports` | bbb | My reports and what happened to each | CG-9 |
| POST | `/v1/requests` `{q, dir?, variety?}` | bbb (a signed-in student) | Request a word the lookup called `requestable`; answers 202 `requested`, or 200 with `exists`, `quota_exceeded`, `not_requestable` or `no_student` | §6.7 |
| POST | `/v1/events` `[{type, target_key}]` | bbb | Behavior signals only the plugin sees (sense reselected, card closed quickly); batched and rate-limited | CG-10 |
| GET · POST | `/v1/teacher/micro-review` (v1.x) | bbb (verified teacher) | Five entries to review; thumbs up or down per sense | TF-5 |
| GET | `/v1/admin/quality` | admin | Open reports and flags, the suspicion queue, time to fix | CG-8, OP-1 |
| POST | `/v1/factory/batches` `{size, label}` | factory | Lease the next batch, with grounding packs | CG-2 |
| POST | `/v1/factory/batches/{id}/entries` | factory | Submit EntryDocs and review results | CG-3 |
| GET | `/v1/factory/batches/{id}` | factory | Batch outcome | CG-3 |
| GET | `/v1/factory/golden` | factory | Golden set + rubric | CG-6 |
| POST | `/v1/audit/batches` `{size, label, audit_version}` · `/v1/audit/batches/{id}/results` | audit | Lease the entries due for an audit, with facts and the rules' findings; submit the auditor's answers (§6.6) | CG-7 |
| POST | `/v1/admin/seeds/import` · GET `/v1/admin/seeds/export` | admin | Seed builder | CG-1 |
| GET | `/v1/admin/stats` | admin | Entries by status, missed searches, AI use and spend, audio, recent runs (§5.5) | OP-1 |
| GET | `/v1/admin/entries?letter=&status=&q=&sort=&page=&per_page=` · `/v1/admin/entries/letters` · `/v1/admin/entries/{key}` | admin | Browse entries, with why a repair was needed (§5.5) | OP-1 |
| GET | `/v1/admin/misses?status=&stuck=&q=&page=&per_page=` | admin | Missed searches and their AI attempts (§5.5) | LK-10, OP-1 |
| GET | `/v1/admin/requests?status=&origin=&q=&to_verify=&page=&per_page=` · POST `/v1/admin/requests/{id}/approve` `{lemma, pos, kind, note?}` · POST `/v1/admin/requests/{id}/reject` `{note?}` · POST `/v1/admin/requests/confirm` `{ids, note?}` | admin | The word-request queue, with its evidence; the owner's decisions, and confirmations of what was approved at once, audited (§6.7) | OP-1 |
| GET | `/v1/admin/findings?status=&type=&source=&confidence=&q=&key=&page=&per_page=` · POST `/v1/admin/findings/apply` · `/v1/admin/findings/reject` `{ids, note?}` | admin | The audit's findings and the owner's decisions, audited (§6.6) | CG-7, OP-1 |
| GET · PUT · DELETE | `/v1/admin/settings[/{name}]` · GET `/v1/admin/audit` | admin | The owner's settings, audited (§5.5) | OP-1, OP-2 |
| GET | `/healthz` · `/readyz` | none | Health checks | — |

### 5.3 Example response: `GET /v1/lookup?q=darse cuenta`

```json
{
  "query": { "q": "darse cuenta", "norm": "darse cuenta", "direction": "auto", "variety": "es-419" },
  "groups": [
    { "side": "l1", "items": [
      { "entry_key": "en:realize:v", "lemma": "realize", "pos": "verb", "kind": "word", "cefr": "B1",
        "sense_key": "en:realize:v#1", "short_gloss": "understand something suddenly",
        "equivalent": "darse cuenta", "matched": { "via": "equivalent", "text": "darse cuenta" },
        "score": 0.92, "provisional": false },
      { "entry_key": "en:notice:v", "lemma": "notice", "pos": "verb", "kind": "word", "cefr": "B1",
        "sense_key": "en:notice:v#1", "short_gloss": "see or become aware of",
        "equivalent": "notar", "matched": { "via": "equivalent", "text": "darse cuenta" },
        "score": 0.89, "provisional": false }
    ] }
  ],
  "did_you_mean": []
}
```

- `side` is `en` when the query matched an English headword or form, and `l1` when it matched an equivalent in the student's language. `dir=auto` searches both sides and lists `en` first.
- `matched.via` is `exact`, `form` or `equivalent`. `equivalent` is the sense's first equivalent in the student's variety.
- `did_you_mean` (trigram and phonetic matches) is filled only when no group matched.
- Inflected input such as *me di cuenta* needs `l1_forms` (§10.2), which publishing doesn't generate yet; until it does, such queries reach these senses through `did_you_mean`.
- `provisional` says what happens about the missing word when nothing matched (§6.5) [LK-10], for example `{"status": "generating", "entry_key": null, "retry_after_ms": 1500, "headword": null}`. A search in Spanish gets the same object: the dictionary finds the English word it means.
- **When something matched,** `provisional` is null, unless the owner chose an entry for exactly this headword that nobody has written yet. *absorbing* matches the verb *absorb* through its form, while the adjective *absorbing* waits in the queue. Then it's `{"status": "generating", "entry_key": "en:absorbing:adj", …}`: AI is writing that entry, and searching again shows it first, as the exact match.

| `status` | Meaning | What the plugin does |
|---|---|---|
| `generating` | AI is writing a provisional entry for a word the owner chose, or finding the English word for a Spanish search | Shows that it's working, and asks the same lookup again after `retry_after_ms`, for up to about 10 s. The entry then arrives as an ordinary item with `"provisional": true`, shown as *Beta*; for a Spanish search, on the `l1` side |
| `ready` | An entry exists under `entry_key`, though the query doesn't reach it: for example a Spanish search whose English word was already in the dictionary | Opens `entry_key` |
| `queued` | The routine is writing this word, under `entry_key`; or AI failed it three times and it's next in the factory's queue | Says the word is coming soon |
| `requestable` | A real headword nobody chose, `headword` (§6.7): no AI writes it | "Not in the dictionary yet", with a button to request `headword` (`POST /v1/requests`) |
| `requested` | This student requested `headword` already | "You asked for this word; it's waiting for review" |
| `invalid` | Nothing to generate or request: a word the search's language doesn't know, a misspelling, a word no dictionary has, one the owner rejected, blocked, or longer than 10 words (8 in Spanish) | "Not found", with `did_you_mean` |
| `quota_exceeded` | The student sent 30 missed searches to AI in the last 24 hours (the owner can change the cap, §5.5) | Asks them to try again tomorrow |
| `unavailable` | AI is off, or its last attempt at this word failed (it tries again 10 minutes later, 3 times at most) | "Not found yet" |

### 5.4 Conventions

- **Errors:** RFC 9457 `application/problem+json` (`type`, `title`, `status`, `detail`, `instance`, plus a stable `code` such as `quota_exceeded` or `invalid_query`). The framework's own errors use the same shape, with the codes `invalid_request` (422), `not_found`, `method_not_allowed` and `internal_error`.
- **Rate limits:** per student, 120 lookups/min, with daily quotas per AI task (§7.1). A per-IP fallback catches anonymous traffic. `429` responses carry `Retry-After`.
- **Versioning:** `/v1` changes are additive only; breaking changes go to `/v2`. The OpenAPI document is generated by FastAPI and committed, and CI fails if the generated TS client drifts.
- **Limits:** `q` ≤ 100 characters; `context` ≤ 500 characters; `sentence` ≤ 200 characters. Inputs are trimmed and normalized, and control characters are rejected.

### 5.5 Admin API: the owner's console in the LMS [OP-1]

The audit's admin dashboard (§12 there) arrives as a console inside the LMS, which the owner also runs. The LMS backend calls `/v1/admin/*` server to server and renders the pages. The contract is `docs/contracts/lms-integration.md` §4 (L6).

**Who gets in.** The owner owns both systems, but later integrations won't, so the console gets a credential of its own:
- **An `admin` token, not the BBB proxy secret.** The proxy secret lives on the BBB server and vouches for students. An admin token is separate, revocable alone, and opens nothing else. Its `sda_` prefix tells it apart from a factory token (`sdf_`) in logs and secret scanners.
- **Only the CLI creates one** (`smart-dictionary admin token --name lms`), as the database owner. The API's role can read `api_clients` but not write it (revision 0011), so no request can mint or widen a token, even one that got past the code. Other integrations get other scopes, never `admin`.
- **The token stays on the LMS server.** The browser talks to the LMS, which checks that the signed-in person is the owner, then calls the dictionary. The API sets no CORS headers, so a browser page can't call it directly.
- **Optionally, only from the LMS server's address.** With `ADMIN_ALLOWED_IPS` stored (SSM `/smart-dictionary/prod/caddy/`), Caddy answers `/v1/admin/*` from any other address with a 404, so a leaked token is useless elsewhere.

**What it reads.**
- `GET /v1/admin/stats`: entries by status with their meanings, the pace, missed searches, AI use and spend, audio, and the last 10 runs.
- `GET /v1/admin/entries`: entries filtered by first letter (`a`–`z`, or `other`), status and prefix, sorted by headword or by last change, with pages of up to 200. Each row carries the first definition and, for `needs_repair` or `quarantined`, the problems behind it.
- `GET /v1/admin/entries/letters`: how many entries start with each letter.
- `GET /v1/admin/entries/{key}`: the document and its last 20 revisions, each with its checks.
- `GET /v1/admin/misses`: missed searches and their AI attempts; `stuck=true` lists those AI gave up on that no seed covers.
- `GET /v1/admin/audit`: every setting change.

- `GET /v1/admin/requests`, with `POST …/approve` and `…/reject`: the word-request queue (§6.7), students' requests and related words, each with its evidence and its `twins`. `to_verify=true` lists the words approved at once that the owner hasn't confirmed, and `POST /v1/admin/requests/confirm` keeps them (to undo one, retire its entry).
- `GET /v1/admin/findings`, with `POST …/apply` and `…/reject`: the audit's findings (§6.6), open ones first, the most confident first.

**What it does to single entries** (`admin/curation.py`), each audited (`entry.add`, `entry.retire`, `entry.requeue`, `entry.publish`, `entry.repair`):
- **`POST /v1/admin/entries`** `{lemma, pos, kind, note?}`: add a word, queued and approved, first in the factory's queue. A pending request for it is approved with it. It refuses a key that exists, or another entry for the same multiword expression (§3.6).
- **`POST /v1/admin/entries/{key}/retire`** `{note?}`: take a duplicate or an unwanted word out of the dictionary. Its key is kept forever, searches that pointed to it answer "not found", and students can't request it. Not while the routine is writing it.
- **`POST /v1/admin/entries/{key}/requeue`** `{note?}`: give a quarantined entry another go once its cause is fixed. Its attempts reset, and it goes back to the queue, or to the Beta rewrites when it has a Beta.
- **The owner has the last word over the AI** (2026-10-20). An entry's page shows the `draft` the server sent back (the routine's newest submission, when nothing was published after it), beside the published `doc`, so the owner can judge the reviewer's verdict.
  - **`POST /v1/admin/entries/{key}/publish`** `{note?}`: publish that draft, overruling its review. The server's checks still apply: a draft with an error answers 422 with each one. A human can overrule an AI's judgment, not the checks that guard structure, originality and safety.
  - **`POST /v1/admin/entries/{key}/repair`** `{notes, replace?}`: the owner's own critique. The entry goes to the factory with the owner's notes, prefixed `owner:` and placed before any other; `replace` drops the reviewer's and the audit's. It works on a published entry (shown until the rewrite publishes), a draft that was sent back, and a word nobody has written yet (the notes guide its first writing). The writer follows the owner's notes even over a reviewer's, the reviewer never fails an entry for following them, and the audit never raises a finding against them (`owner_decisions`, §6.6).

It never names a student: AI use is counted per search, and the console sees how many students reached the daily cap, never which. A request shows how many students asked and what they searched, never who.

**What it changes:** the decision on a word request (§6.7), and the settings in `core/tunables.py`, each with tighter bounds than the environment allows. A leaked token can turn AI off or move the cap, but can't run up a bill or read a key.

| Setting | Default (environment) | Bounds |
|---|---|---|
| `ai_provider_order` | `AI_PROVIDER_ORDER` | Known providers, once each; empty turns request-time AI off |
| `ai_searches_per_day` | `AI_SEARCHES_PER_DAY`, 30 | 0–200 |
| `ai_monthly_budget_usd` | `AI_MONTHLY_BUDGET_USD`, 5 | 0–50 |
| `word_requests_per_day` | `WORD_REQUESTS_PER_DAY`, 10 | 0–50 |
| `auto_approve_listed` | `AUTO_APPROVE_LISTED`, true | On or off |
| `auto_approve_related` | `AUTO_APPROVE_RELATED`, true | On or off |
| `audit_auto_repair` | `AUDIT_AUTO_REPAIR`, true | On or off |
| `factory_publish_threshold` | `FACTORY_PUBLISH_THRESHOLD`, 0.7 | 0.5–1 |

- **Where they live:** an override is a row in `app_settings` and wins over the environment; `DELETE` removes it, and the environment's value applies again. Each API process rereads them every 15 seconds, so a change needs no deploy.
- **The audit:** every `PUT` and `DELETE` writes `admin_audit`, with the token's name, `X-Admin-Actor` (the LMS person ID of whoever was signed in, which the LMS vouches for) and the value before and after. The API's role can add rows but not change or delete them.
- **Responses** carry `Cache-Control: no-store`, and each request's statements stop after 15 seconds.

---

## 6. Content factory

### 6.1 Seed builder: how we reach 20k entries [CG-1]

You asked whether there's a better way to reach 20k than your own lists. My answer: **use your lists as the top tier, and merge in the open lists** to fill the gaps by level and frequency. Every candidate keeps its provenance.

**Inputs.**
- Your lists go in `data/seeds/owner/`, in any of three formats:
  - TXT: one item per line;
  - CSV: `lemma[,pos][,kind][,level][,tags]`, optionally followed by `variants`, `patterns`, `sources`, `original` and `flags` (multiple values separated by ` | `);
  - XLSX: same columns as the CSV.

  `scripts/normalize_owner_lists.py` turns the raw lists in `dictionary-entries/` into `data/seeds/owner/owner-seeds.csv`. Its rules:
  - **Characters.** Apostrophes are stored as ASCII `'`.
  - **Citation form.** No infinitive `to`. No leading `be` unless only one word would remain (*be into*).
  - **Optional parts become variants of one entry.** *(sitting) on top of the world* is `on top of the world` with the variant `sitting on top of the world`, never two entries.
  - **Complements become patterns.** *(with someone)* goes to `patterns`.
  - **Placeholders are spelled out** (someone, something, one's). A trailing one is dropped from the lemma, as in *run into*.
  - **The US spelling is the headword.** The UK spelling becomes a variant tagged UK, written `colour [UK]` in the CSV. Pairs come from Wiktionary's regional-spelling pointers, plus regular endings (-our, -re, -ise, -ence) where both spellings are listed, plus a short list of regional pairs Wiktionary marks only as alternatives (*plough*, *plow*; *on second thoughts*, *on second thought*). A UK spelling that matches only some meanings of the US word (cheque, tyre, metre, licence) stays a separate entry.
  - **One word, one entry.** A capitalized and a lowercase row merge only when they're the same word (*internet*, *OK*); *March* and *march* stay apart. A row that another row lists as a variant folds into it. A word Wiktionary records only as an inflection is flagged with its lemma rather than folded, because many such plurals and participles are headwords in their own right (*savings*, *terms*, *located*).
- The open lists:
  - CEFR-J 1.6 (7,801 A1–B2 lemma+POS rows);
  - Octanove C1/C2;
  - the NGSL family (NGSL, NGSL-Spoken, BSL, NAWL, TSL);
  - wordfreq ranks;
  - Wiktionary phrasal verbs and idioms (kaikki extract; facts only).
- Later, `misses` adds real demand.

**Steps.**
1. **Parse and normalize.** Multiword items are split, and kinds are guessed: verb + particle → phrasal verb; Wiktionary idiom tag → idiom.
2. **Lemmatize single words** (LemmInflect/UniMorph). An inflected list item maps to its lemma, and the original is kept as evidence.
3. **Assign POS.** POS comes from the source when it has one, otherwise from CEFR-J or Wiktionary. A lemma with several learner-relevant POS gives one candidate per POS.
4. **Deduplicate** on `(lemma_norm, pos, kind)`, merge sources, and fold US/UK spelling pairs into one entry: the US spelling is the lemma, and the UK one is a variant with `regions: ["UK"]`.
5. **Filter out:** proper nouns, archaic or offensive-only items, tokens with digits, and words with Zipf < 2.0. Your own lists override every filter.
6. **Tier and order.**

   | Tier | Contents |
   |---|---|
   | T0 | Your lists and misses |
   | T1 | CEFR-J A1–A2 |
   | T2 | CEFR-J B1–B2, plus the most common phrasal verbs and idioms |
   | T3 | Octanove C1/C2 |
   | T4 | The long tail by Zipf |

   Within a tier, items are ordered by Zipf. Multiword items have no unigram frequency, so they're ordered by how many lists contain them plus the curation score (step 7).
7. **Curation run** (a special routine batch, about 500 candidates per run). Claude marks each candidate keep/drop, confirms kind and POS, flags offensive or archaic items, scores learner usefulness from 1 to 5 (for multiword items), and **proposes missing high-value phrasal verbs and idioms**.
8. **Export** the ranked list as CSV (`GET /v1/admin/seeds/export`) for you to skim, then **queue** it. `smart-dictionary seeds queue` turns candidates into `queued` entries.
   - **What it takes:** only candidates with a part of speech and kind that form a valid entry and aren't rejected. `--lemma`, `--level` and `--limit` narrow the selection, and `--dry-run` previews it.
   - **A candidate whose word has an entry already** is linked to that entry, and the entry is approved, instead of queuing it: its own key, or for a multiword expression its entry of another kind or article (§3.6). A pending review for that entry closes as approved by `seeds`.
   - **Priority within the queue:** the level comes first (A1 to C2, then words with no level), then your lists ahead of the open lists, then Zipf. The first milestone is A1–B2 coverage, so this departs from tier order. Tier order would put CEFR-J's basic words behind your long tail.

**Built so far (roadmap 2.1):** `smart-dictionary seeds import` runs steps 2–4 and 6 into `seed_candidates`, and `seeds export` writes the ranked CSV of step 8 to `data/raw/seed-candidates.csv`.
- **Parts of speech** come from the CEFR lists, then from each Wiktionary part of speech with at least 3 senses and 40% of the main one's, then from UniMorph. A spelling variant's entry counts its main spelling's senses (*catalog*).
- **Open-list items** take their US spelling and lemma before matching (*centre* → *center*, *were* → *be*).
- **Spacing variants** fold when Wiktionary records one as the other's alternative form (*best seller* → *bestseller*). Other pairs with the same part of speech are flagged.
- **Homographs** are numbered as entry keys are (*pole*, *Pole* → `en:pole:n~2`).
- **The curation run (step 7), as files:** a model judges the seeds and entries the rules couldn't decide, writing one CSV per backlog under `data/seeds/curation/`. The owner reviews the file, then `smart-dictionary seeds curate --file <csv>` applies it (`--dry-run` first; `seeds.curate`):
  - **seeds** (`lemma, pos, kind, decision, confidence, reason`): a seed that waited with no part of speech or kind gets them, or is declined; `seeds queue` then queues it, or links it to its expression's entry;
  - **entries** (`key, decision, pos, kind, confidence, reason`): an unwritten entry keeps its kind, moves to the right one (its key retired, the right key queued in the same place with its seed, approval and searches; an entry of that kind that exists takes the seed instead), or is retired. A written entry isn't touched: its decision becomes an audit finding (§6.6) for the owner to apply.

  The first files (2026-10-20, Opus 5.5) cover the 724 seeds that waited with no part of speech or kind, most of them multiword items Wiktionary lacks, and the 176 multiword verbs stored as plain words. A plain word can't have a pattern (`entrydoc.py`), so the app couldn't match *hits the ceiling* in a text; most are idioms or phrases.
- **Not yet:** the step-5 filters.

A re-run replaces the undecided candidates and keeps the decided ones. Seed candidates and their variants join the Wiktionary keep set, and the builder reads Wiktionary, so a new database needs `data import --all`, `seeds import`, `data import kaikki-en`, then `seeds import` again. Starting with `seeds import` leaves a few rows out: with no Wiktionary rows, the first build folds some list words into others (*chiselled* into *chisel*).

**Target mix (to tune with your lists):** ~14k words, ~3k phrasal verbs, ~2k idioms, ~1k collocations and fixed phrases.

### 6.2 Grounding pack (one per leased entry, about 1–1.5k tokens)

```json
{
  "key": "en:actually:adv", "lemma": "actually", "pos": "adverb", "kind": "word",
  "priors": { "cefr": "A2", "zipf": 5.6, "lists": ["owner", "cefr-j:A2", "ngsl"] },
  "forms": [],
  "ipa": { "en-US": ["ˈæktʃuəli"], "en-GB": ["ˈæktʃuəli", "ˈæktʃəli"] },
  "wiktionary_hints": { "senses": ["in fact; really…", "used to correct…"],
                        "es_candidates": ["en realidad", "realmente", "de hecho"] },
  "existing_keys": { "senses": [], "examples": [] },
  "related_published": ["en:actual:adj", "en:currently:adv"],
  "repair": null
}
```

Wiktionary sense hints are cut to about 12 words each. They only signal which meanings exist; validator V9 enforces originality.

**Built (`factory/packs.py`):**
- **`forms`:** these come from LemmInflect, as search's do, mapped to the entry notation (`N;PL`, `V;PST`), never including the headword. A phrasal verb inflects its verb (*gave up*). UniMorph adds only irregular forms beyond those, for the entry's part of speech. UniMorph alone isn't reliable: it has no plural for *house*, only the annotation "uncountable" in its place. An extra must be one plain lowercase word of one part of speech. That drops UniMorph's slips: *graying* tagged as a comparative of *gray*, the annotation "American spelling", and "Child.".
- **The pack also carries the seed candidate's variants, patterns, flags and original list lines.**

`POST /v1/factory/batches` returns one pack per entry, and `GET /v1/factory/batches/{id}` reports a batch's state. Both take a bearer token with the `factory` scope, created with `smart-dictionary factory token --name <label>`.

When an entry comes back for repair, `existing_keys` and `repair` carry the published keys, the failed checks, hides and flags, valid reports and a signal summary (§6.6), so the fix keeps the keys stable.

### 6.3 The routine [CG-2, ADR-008]

**Files in the repo**

| File | Purpose |
|---|---|
| `factory/ROUTINE.md` | The run instructions, pasted whole into the routine's form |
| `factory/STYLE.md` | The editorial style guide (product spec §6) |
| `factory/rubric.md` | The reviewer's checklist |
| `factory/schema/entry.v1.json` | Generated from the Pydantic models |
| `factory/bin/pull_batch.py` · `validate.py` · `submit_batch.py` | Batch I/O, and checking drafts with the server's own checks (`POST /v1/factory/validate`) |
| `factory/golden/` | Golden-set entries and your ratings (Pilot A) |
| `.claude/skills/factory-batch/SKILL.md` | The **local twin**: the same flow in an interactive Claude Code session on your PC |

**Routine configuration** (the setup steps are in `factory/README.md`)
- **Model:** Sonnet 5.5 or Opus 5.5, decided by the golden-set comparison in the pilot.
- **Prompt:** the whole of `factory/ROUTINE.md`, pasted into the routine's form so the routine stands alone. It's versioned in the repo and pasted again when it changes. STYLE.md, the rubric, the schema and the scripts need no pasting: every run reads them from its fresh clone.
- **Network:** *Custom*, with only the API host allowlisted. No package registries: the scripts use only Python's standard library, and the checking happens on the server.
- **Secret:** the factory token is a **network secret** (Bearer) for the API host. Anthropic's proxy adds the header after each request leaves the VM, so the token never enters it and `FACTORY_TOKEN` stays unset in the cloud. The local twin sets it in the shell instead.
- **Setup script:** none.
- **Connectors:** none. A routine can use every tool of an included connector without asking.

**Run protocol** (you press *Run now*; each run notifies you when it ends):

1. `pull_batch.py --size 40 --groups 4` calls `POST /v1/factory/batches`, which leases 40 entries (provisional entries first, then repairs; 3-hour lease). It writes `work/batch.json` and splits the packs, in queue order, into **4 groups of 10**.
2. The orchestrator spawns 4 **writer** sub-agents. Each writer reads STYLE, the schema and its 10 packs, writes `work/out/group-N.json`, then runs `validate.py` and fixes errors (at most 3 rounds).
3. Four **reviewer** sub-agents, each in a fresh context, check group N+1 (in rotation) against `rubric.md`. Each entry gets `pass`, `fail` or `uncertain`, with notes.
4. **One fix round:** the writer fixes the failed and uncertain entries, and applies the notes on passed ones that it agrees with. The same reviewer judges only the changed failed and uncertain entries again. Whatever still fails is submitted marked `needs_repair`, with the notes attached.
5. `submit_batch.py --model <id>` calls `POST …/entries` with the verdicts and a `prompt_version`, a hash of ROUTINE, STYLE, the rubric and the schema. The server re-validates (it's the authority) and publishes, repairs or quarantines each entry (§6.4).
6. The orchestrator reports the summary (published / repair / quarantined / not written), the time taken and anything to look at.

**Batch size** defaults to 40 (`--size`, `--groups` configurable). It gets tuned from the pilot's usage meter, keeping about 40% of the weekly allowance free for your development work.

**Throughput (estimate, to be measured in the pilot).** If a run takes 20–30 minutes and the 5-hour windows allow 6–12 runs a day, that's 240–480 entries/day. **5k takes about 2–3 weeks, 20k about 6–10 weeks.** The pilot replaces these guesses with real numbers.

### 6.4 Server-side validation and publishing [CG-3]

| ID | Check | Severity |
|---|---|---|
| V1 | Conforms to the JSON Schema; closed vocabularies | error |
| V2 | Key format; published keys preserved or explicitly retired | error |
| V3 | Every example contains a form of the headword (forms table + spaCy); phrasal-verb patterns match | error |
| V4 | Definition ≤ 20 words, no headword, ≥ 85% of tokens in the defining vocabulary | error / warn |
| V5 | L1 text detected as Spanish with confidence ≥ 0.8 | error |
| V6 | Every equivalent's tokens exist in the Spanish lexicon (wordfreq es + UniMorph es) | warn → error after 2 unknown tokens |
| V7 | Valid IPA charset; normalized edit distance to CMUdict/Britfone ≤ 0.35 | warn |
| V8 | No duplicate senses (sense-embedding cosine < 0.92) | warn |
| V9 | No 8-gram overlap with Wiktionary glosses | error |
| V10 | Safety: blocklists for sexual, violent and hate content in examples, except the headword's own forms (*kill* in the entry for *kill*); offensive senses labeled | error |
| V11 | Counts: 1–6 senses, 2–3 examples, ≤ 6 collocations, ≤ 3 notes per sense | error |

- **Quality score** = weighted warnings + reviewer verdicts (+ spot-check results when present). An entry **publishes** if there are no errors and the score is ≥ τ, with τ calibrated on the golden set.

**Built (`factory/validate.py`, `factory/submit.py`):**
- **Every check from V1 to V11, plus the style guide's length limits.** Three use stand-ins until their tools exist:
  - **V5** lets each word vote by frequency instead of using a detector. A word counts as Spanish evidence when wordfreq finds it at least as common in Spanish as in English, and as English evidence only when it's over ten times as common in English. Words in between are shared (*hotel*, *plan*, *real*) and don't vote, and neither do words neither language knows, nor a few everyday Spanish words that English data swamps (*¿has visto?*, *come*, *once*). Lexicon membership alone failed on real data, because the Spanish web lists everyday English words. The first frequency rule flagged every one of 12 correct sentences like *¿Has comido en el bar del hotel?*. With the margin, all 87 Spanish texts in the fixtures and the first dry run score 100%, and their English at most 27%.
  - **V8** compares the words of two definitions until sense embeddings exist.
  - **V3** matches inflected forms and multiword patterns without spaCy, including split phrasal verbs within `max_gap`.
  - **V3 and V4 read a possessive as its word:** *the company's profits* shows *company*, and *someone's* is as basic as *someone*.
  - **A word keeps its inner periods and digits** (*p.m.*, *e.g.*, *MP3*), as its headword does. Splitting them made every example of *a.m.*, *p.m.*, *e.g.*, *i.e.*, *a.k.a.*, *B.C.*, *MP3 player* and *omega-3 fatty acid* fail V3, so those entries could never publish.
  - **V6's Spanish lexicon** is the frequency list (Zipf ≥ 2.35), UniMorph, and the Spanish words in Wiktionary's translations (`ref_spanish_words`, revision 0009). The last one adds real words the others lack: *ralo*, *entrecano*, *mesabanco*. A reflexive verb counts as its verb (*asearse* → *asear*).
- **V10's blocklist is only a floor.** The reviewer and the spot checks catch the rest.
- **The checks were calibrated against the real reference data.** The 12 hand-written entries and the style guide's example pass with no issues. An English gloss in the Spanish layer, an invented equivalent, a definition in rare words and the headword in its own definition are all caught.
- **The score starts at 1:**
  - each warning subtracts its weight (0.1, or 0.05 for V7 and 0.15 for V8);
  - the reviewer's verdict subtracts 0, 0.15 or 0.5 (pass, uncertain, fail);
  - τ is `FACTORY_PUBLISH_THRESHOLD`, 0.7 until the golden set calibrates it.
- **Repairs:** a failing entry goes to `needs_repair` with its issues in the revision's `qa`, which the next pack carries with the draft they judged (`repair.draft`). The writer starts from that draft, so what the review didn't question survives: before 2026-10-20 a turned-down first draft reached the next writer as notes alone, and a note such as "keep ex2 and ex3" pointed at text the writer never saw. After 2 failed repairs it's quarantined. Every submission is a revision.
- **`POST /v1/factory/validate`** runs the same checks without saving anything. The routine uses it while writing.
- **Related links to missing words** become suggestions once their entry publishes (§6.7). That step runs in a savepoint: a failure there is logged and never holds the entry back.
- **`POST /v1/factory/batches/{id}/entries`** reports each entry's outcome:
  - `published`, `needs_repair` or `quarantined`;
  - `not_leased` when the batch no longer holds that entry;
  - a closed or expired batch gets 409.
- **Not yet:** the repair flow for published entries (§6.6). A published entry sent to repair leaves search while it's `needs_repair`, and a final failure would quarantine it. Today's flow covers new entries.
- **Spot checks:** Haiku 5.5 re-runs the rubric on a 10% sample, plus every entry the reviewer marked "uncertain", within the AI budget (§7.3).
- **Publish transaction:**
  1. Insert the revision.
  2. Update `entries` (`doc`, `status`, `quality`, `content_version`+1).
  3. Rebuild the projections: forms, patterns, senses (upserted by key; missing ones get `retired_at`), equivalents, L1 forms.
  4. `NOTIFY content_changed`.
  5. Enqueue jobs for embeddings and for TTS of new or changed pronunciations and examples.

### 6.5 Missing words and provisional entries [LK-10, CG-4]

**AI writes a Beta entry only for a word the owner chose** (the owner's rule, 2026-10-20). A word is chosen when it's seeded (the curated lists, through `seeds queue`) or approved in the console (§6.7). Every other word a student searches for gets no AI entry: a real headword becomes requestable, and the rest is "not found". A student's search can never add a word to the dictionary on its own.

**The lookup decides with SQL only** and answers at once (`ai/provisional.py`, `resolve()`):
1. **The gate:** at most 10 words (8 in Spanish), each known to the reference data in the search's language (`ref_freq` Zipf ≥ 1.0, `ref_forms`, or English Wiktionary). Anything else is `invalid`. A blocked word passes only when the owner chose it (*kill*, an A1 verb); nobody can request one.
2. **The entries it may stand for:** the search and its dictionary forms (*geese* → *goose*, *gave up* → *give up*, lemminflect), with or without a leading article (*last straw* ↔ *the last straw*), matched against `entries.lemma_norm`.
   - **The closest match decides first:** the headword exactly as searched, then its other forms in that order. *absorbing* is a form of the verb *absorb* and an adjective of its own, and a search for it stands for the adjective, whatever their places in the queue. Before 2026-10-21 queue priority decided, so AI wrote *absorb* for a student who searched *absorbing*. About 1,840 approved headwords are also forms of another entry (*affected*, *loaded*, *amazing*, *worse*).
   - **Within the closest match the owner chose, the best one decides:**
     - **shown already** under another form: `ready`, with its key;
     - **approved, and nobody has written it** (`candidate`, `queued`): `generating`, and one AI job writes it after the response;
     - **approved, and the factory is on it:** `queued`, with its key.
   - **None approved:** the request check (§6.7) answers `requestable` or `requested` with the headword, or `invalid`.
3. **A search that found entries can still start one job.** When the owner chose an entry for exactly the search that nobody has written yet, AI writes it after the response, with the same quota, claim and cool-down (`also_writing`). The answer keeps its results and adds `generating` with that entry's key. Without this, *absorbing* found the verb *absorb* through its form for good, and the adjective waited for the factory at the end of the queue; *light* the noun hid *light* the adjective the same way (4,972 headwords have several parts of speech).
4. **The quota** counts each student's searches that reached AI over the last 24 hours (distinct `ref_key`s of successful calls), 30 by default (`AI_SEARCHES_PER_DAY`, or the console), so a Spanish search that takes two calls counts once.

**Generating** (`provisional_entry.v3`):
- **The headword comes from the seed, never from the model.** The model is told the lemma, part of speech and kind, and writes only the senses: 1–2, one example each, with the es-419 layer. A model can't drift to another word, correct a typo into a headword, or invent one.
- **Hard topics get entries.** A headword like *kill*, *bomb* or *drunk* is written in calm, factual, non-graphic sentences. Only a sexual or vulgar headword whose main meaning can't be shown within the content rules is declined (`unsuitable`). A refusal can't improve with retries, so the word goes straight to the factory.
- **The server builds the document** around the senses: the seed's key, pronunciations from CMUdict and Britfone, a multiword entry's pattern, and `provisional: true`.
- **Checks:** all of V1–V11, with V11's counts for a short entry. Errors block; warnings lower `quality`. The search must be one of the document's forms.
- **A sense that fails is dropped, not the draft.** When every error lies inside some senses and another sense is clean, the clean senses publish, and the revision's `qa.dropped_senses` records why. Before this, *give me a break* failed its first attempt on its second sense's gloss, over the 5-word limit, and waited out the 10-minute cool-down.
- **It never replaces the routine's work.** It publishes only while the entry is still approved and unwritten, keeping its queue priority and seed sources. Provisional entries are leased first (`QUEUE_ORDER`).
- **One job at a time:** the lookup that starts a job claims the miss (`misses.running_since`), so other lookups answer `generating` without starting another. A claim older than 3 minutes expires; an advisory lock per query backs it up.

**A failed generation** counts an attempt on the miss (`attempts`, `last_attempt_at`, `last_error`; revision 0010):
- **What counts:** no provider answered, a draft that fails its checks, or any error.
- **For 10 minutes after each failure,** lookups answer `unavailable` at once, with no AI call. After that, one lookup starts one new attempt.
- **After 3 failed attempts, or a refusal,** the entry moves to the front of the factory's queue, and lookups answer `queued` with its key.
- Cool-down, attempts and hand-off are the owner's choices (2026-10-20).

**Searches in Spanish** take their own path [LK-5, LK-6]:
- **Routing:** a miss is Spanish when it was searched in Spanish (`dir=l1`), or with `dir=auto` when every word is known in Spanish, at least as common there as in English, **and the search isn't an English headword** (Wiktionary or an approved entry). So *pie*, *son*, *actual* or *solo* stay English: this is an English dictionary, and `dir=l1` asks for the Spanish reading. Other student languages have no path yet.
- **The gate** is the English one, in Spanish (`ref_freq` or `ref_forms`), with the same quota and lock. The miss is a `misses` row with language `es`.
- **`translate_query`** names the 1–3 English headwords the student most likely means, or `none`, which settles the miss as `invalid`. Translating writes nothing, so it's allowed for any word (the owner's choice, 2026-10-20).
- **Each headword then follows the English rules:** shown (`ready`), approved and unwritten (AI writes it, told what the student typed), with the factory (`queued`), or, when none is chosen, `requestable` with the English headword (`misses.headword`).
- **The student's Spanish joins the equivalents only when it's spelled right:** every word must be in Wiktionary's translations or UniMorph, alone or under attached pronouns. The web-frequency list isn't enough, since it holds common typos (*aveces*, *xq*, *hize*). A misspelled search tells the model so, and asks for correct spelling; the next search still finds the entry, through the miss.
- **The lookup log** records the miss under the language it was routed to.

### 6.6 The quality loop: flags, reports, signals, audits and the golden set

Every route ends in the same repair path, and every item is addressed by its key (`en:house:n#1.ex2`), so a fix rewrites one piece and leaves the rest of the entry, saved words and review cards untouched.

```
teacher hide / flag ──┐
student report ───────┼─▶ triage ─▶ needs_repair + evidence ─▶ routine run ─▶ QA (CG-3) ─▶ republish ─▶ close + notify
behavior signals ─────┤             (suspicion score)
audits, golden set ───┘
```

**Intake**
- **Hide** [TF-3]: verified teachers only, checked against `users.role` from the LMS profile, since a BBB moderator may be a student. It writes `content_flags` and sends `NOTIFY flags_changed`, so the item leaves every card at once.
- **Flag** [TF-3]: any moderator. Nothing changes on screen.
- **Caps:** at most 20 hides and flags per person per day. Both are logged and can be undone.
- **Report** [CG-5]:
  - the server rejects a second open report from the same person on the same item, more than 10 a day, and inactive accounts;
  - the answer is 202, with the report's status visible later in `/v1/me/reports`.
- **Signals** [CG-10] are written to `quality_signals`:
  - from data the server already has: repeated lookups (`lookups`), failure rates (`review_logs`), "incorrect" coach verdicts;
  - from the plugin, through `/v1/events`: another sense opened right after a ranked one, a card closed within a second;
  - each user counts at most once per item, signal and day, so one student can't move a score alone.

**Triage** [CG-8]
- **Hides, and flags from verified teachers, skip triage:** the entry goes straight to `needs_repair`. Flags from other moderators are triaged like reports.
- **Reports get an AI triage job** (Haiku, within the AI budget): valid or not, which key, which issue, a suggested fix. Duplicates are merged.
- **Three different reporters** on one item within 30 days send it to repair without waiting for triage.
- **When the budget is spent,** triage waits for the next routine run, which can triage a batch of reports itself.

**Suspicion score** (`item_suspicion`)
- The score is a weighted sum with a 30-day half-life. Starting weights, to be tuned with real data:

  | Signal | Weight |
  |---|---|
  | Hide | 5 |
  | Flag | 3 |
  | Valid report | 3 |
  | Open report | 1 |
  | Sense reselected | 0.2 |
  | Repeat lookup | 0.1 |
  | Quick close | 0.05 |
  | Review-card failure rate above twice its level's median (weekly) | 1 |
  | Coach-failure anomaly (weekly) | 1 |

- Above the threshold (start at 5), the item is re-reviewed. First the validators and a server spot check (`spot_check`, §7.1) run again. If they find a problem, the entry goes to repair with the evidence attached. If they don't, the score resets and the item is left alone for 30 days.
- **Clusters:** hides clustered in one factory batch trigger a re-check of the whole batch, because they usually point to a prompt or model problem.

**Repair and close**
- **The repair pack** carries the reports, flags and a signal summary. The routine fixes the named item. Its key stays when the meaning is unchanged; a sense whose meaning was wrong is retired and replaced under a new key (§3.1).
- **Validators and QA** run as for any entry, and the fix is republished.
- **Closing:**
  - The reports become `fixed` and the flags resolved, both with `fixed_by_revision` set. Reporters are notified the next time they open the plugin [CG-9].
  - Reports the routine finds groundless become `invalid`, and their reporters see that too.
  - A hide is lifted when the repaired item is published. If the routine finds nothing wrong and leaves the item unchanged, the hide stays until its teacher or the owner lifts it.

**Proactive checks**
- **Audits** [CG-7]: a second routine, below.
- **Re-validation** [CG-7]: when the validators' version changes, a job re-validates every published entry, and failures go to repair.
- **Golden set** [CG-6]: 50 entries chosen across levels and kinds, with your ratings in `factory/golden/ratings.csv`. `validate.py --golden` measures how well the reviewer agrees with your ratings, and CI runs it whenever `factory/` changes. The target is agreement ≥ 90%.
- **Teacher micro-review** [TF-5, v1.x]: each teacher gets the five most suspicious or lowest-quality entries their classes looked up in the last 7 days. A thumbs down becomes a flag, and a thumbs up lowers the suspicion score.

**The audit routine** [CG-7] (`factory/audit.py`, `factory/AUDIT.md`, revision 0014, the owner's choice of 2026-10-20: "trust Opus, but verify"). A second Claude Code routine, Opus 5.5, once or twice a day, re-reads entries in batches of about 100 and records what it finds. It never edits an entry.
- **The server keeps the audit's place.** Each entry records when it was audited and under which instructions (`audit_version`, a hash of AUDIT.md, STYLE.md and the rubric). A lease (`POST /v1/audit/batches`, three hours) hands out the entries due next, so the routine has no memory to lose: a crashed run's entries are simply due again, and changed instructions make every entry due again.
- **What's due first:**
  1. words approved at once (`ai:related`, `auto:lists`), before anything else;
  2. written entries never audited, the most looked-up in 30 days first;
  3. unwritten entries: their facts only (a real learner word, the right part of speech and kind, no twin), before the factory spends a run on them;
  4. entries audited under older instructions, then those audited over 180 days ago.

  Betas (rewritten soon anyway), entries in repair, and entries with an open finding wait.
- **Two layers.** The server's rules are free and give the same answer every time. They raise what data alone can show:
  - a level two or more steps from CEFR-J's or Octanove's (one step is a judgment call);
  - a part of speech those lists give, or Wiktionary gives with at least 3 senses and 40% of the main one's, that has no entry and no seed;
  - a multiword entry stored as a plain word while Wiktionary has it only as an idiom, phrasal verb or phrase;
  - a twin (§3.6), raised once per pair;
  - a headword Wiktionary lacks;
  - a related link that names no dictionary word.

  The auditor confirms or dismisses each, with a confidence, and adds what needs judgment: missing or wrong meanings, definitions, levels with no list behind them, examples, Spanish. A rule finding the owner rejected, or the auditor dismissed, is never raised again (its `fingerprint`), and each item carries the entry's past decisions so the auditor doesn't repeat them.
- **Each finding** (`audit_findings`) has a type (`cefr`, `meaning`, `definition`, `example`, `spanish`, `content`, `part_of_speech`, `kind`, `duplicate`, `remove`), the key it's about, the evidence, a proposed fix, a confidence and a source (`rules` or `ai`). The server refuses one that doesn't fit: a key that isn't in the entry, a content finding on an unwritten entry, a part of speech the entry model lacks.
- **Applying one goes through the paths that exist:**
  - **content** (`cefr` to `content`) sends the entry to repair: a `needs_repair` revision whose `qa` holds the findings (`audit`, and as review notes), so the factory leases it next, rewrites it with every check and keeps its keys, while students keep seeing the published document;
  - **`part_of_speech`** adds the word, as the owner's (curation `add`, so the twin rule applies);
  - **`kind`** retires the entry and adds it again under the right kind;
  - **`duplicate`** retires the twin it names, and **`remove`** retires the entry.
- **Trust, verified.** With `audit_auto_repair` on (the default), a high-confidence content finding is applied when it's submitted (`auto:audit`). A checker sub-agent first re-reads every high-confidence answer and lowers the ones it doubts. Everything else waits for the owner in the console, who applies or rejects in bulk. For each type, the console shows how many findings the owner applied and rejected over 90 days (`your_decisions_90d`): when one type is nearly always applied, its findings can be trusted with more.
- **Overruling the audit.** Rejecting an open finding closes it for good. Rejecting one applied at once calls its repair off while the factory hasn't started it: the entry is published again as it was (`undone`); once the factory is rewriting it, or has, the owner sends it to repair with notes instead (`kept` says why). The owner's own calls on an entry (a draft published over its review, repair notes) travel with each audit item as `owner_decisions`, and the auditor never raises a finding against them.
- **What it audits.** Every approved entry, written or not: a written one for its content and facts, an unwritten one for its facts (a real learner word, the right part of speech and kind, no twin). It skips entries the factory is busy with (`generating`, Betas about to be replaced, `needs_repair`, `quarantined`): a draft in repair already has its notes, and the owner judges it on the entry's page.

**Schema changes** (a later migration; the §3.4 sketch and revision 0001 predate them)
- **Targets:** `target_key` is a sense, example or note key; `sense_key|variety|term` for an equivalent; `entry_key|ipa|variety` for a pronunciation; or, for a *missing meaning* report only, the entry key.
- **`reports`:** `kind` becomes `reason`, with the list from spec §5.7 (`wrong_translation`, `wrong_meaning`, `unnatural_example`, `wrong_pronunciation`, `typo`, `missing_meaning`, `hard_to_understand`, `offensive`, `other`). It gains `entry_key`, `resolved_at`, `fixed_by_revision` (→ `entry_revisions`) and `notified_at`. The statuses become `open`, `valid`, `invalid`, `duplicate` and `fixed`, and a unique index on `(reporter, target_key)` covers the open and valid ones.
- **`content_flags`:** gains `entry_key`, `comment`, `resolved_at` and `fixed_by_revision`, and its `reason` uses the same list. An active hide has neither `undone_at` nor `resolved_at`; the card filter and the partial index check both.
- **New tables:**
  - `quality_signals (tenant_id, user_id, target_key, entry_key, signal, weight, observed_at)`: append-only, at most one row per user, item, signal and day (aggregate signals have no user);
  - `item_suspicion (target_key primary key, entry_key, score, last_signal_at, last_reviewed_at)`: derived, kept current by a worker job.

### 6.7 Word requests: the owner approves what isn't seeded

A real word nobody seeded waits for the owner (`content/word_requests.py`, revisions 0012 to 0014). It comes from a student's request (origin `student`), a link in a published entry (`related`), or a Beta AI wrote before reviews (`ai_entry`).

**The word limit** is 10 words, the longest seeded expression, so *hit the nail on the head* (6) can be requested. Until 2026-10-20 it was 4, and every longer idiom answered "not found" with no button.

**Requestable** means a missed search whose headword:
- **is a Wiktionary headword.** The import leaves misspellings out (§3.4), so *recieve* or *definately* never are, and neither is a word no dictionary has;
- **wasn't rejected,** in the console or while curating the seeds (a seed candidate `reject` or `merge`, with no accepted one);
- **isn't chosen already** (then §6.5 writes it).

The lookup then answers `requestable` with the headword, and the plugin offers a button.

**A request** (`POST /v1/requests`):
- **The server decides again.** A request can't add a typo, a word that exists already, or one the owner rejected.
- **One request per headword** (`word_requests`) gathers every student who asked. Each vote (`word_request_votes`) holds a user ID, the language and the search: never a name.
- **Each student has 10 requests a day** (`WORD_REQUESTS_PER_DAY`, or the console's `word_requests_per_day`). Asking again for the same word costs nothing.
- **Later lookups by that student** answer `requested`.
- **A refusal is an answer, not an error:** only `requested` is 202, and every other outcome is 200 with its `status` (`exists`, `quota_exceeded`, `not_requestable`, `no_student`). The student did nothing wrong, and a browser logs every 4xx as a red console error in class. A refusal writes no request and no vote. A control character in the query (400) and a malformed body (422) stay errors.
- **A leading article doesn't hide a headword:** a missed search of three or more words also tries itself without *a*, *an* or *the*, so *the last straw* finds *last straw*, and one of two or more words tries itself with *the* and *a* or *an*, so *last straw* finds *the last straw*.

**The owner decides in the console** (§5.5): `GET /v1/admin/requests` lists pending requests, most asked first. Each comes with its evidence: students, searches, example queries, Zipf frequency, the open lists that name it, its seed candidates' decisions, and Wiktionary's parts of speech, likeliest first.
- **Approve** with the headword, part of speech and kind:
  - the entry is queued (or kept), approved, and moved to the front of the factory's queue;
  - AI writes its Beta right away, and the searches that led to it find it;
  - the routine writes the full entry in its next run;
  - other unapproved entries AI made for the same headword are retired.
- **Reject:** the headword is closed for good. Students see "not found" and can't request it again, and any unapproved entry for it is retired, its key kept forever.
- **Every decision** is audited (`request.approve`, `request.reject`) with the token's name and the person signed in.

**Related words** (`content/related.py`): when an entry publishes, each link to a word with no entry at all, retired ones included, becomes a request of origin `related`. `word_request_links` keeps which entries link to it and how (`linked_from`). Typos, blocked words and words the owner declined are skipped, as for students. Nobody waits for one, so once approved it takes its natural place in the queue (level, then frequency), and AI writes no Beta for it until a student searches. `smart-dictionary requests sync` does the same for every published entry, for links written before this.

**Approved at once** (`auto_approve`, the owner's choices of 2026-10-20). A student's request or a related word is approved without waiting when the dictionary has no entry for it under any part of speech, retired ones included (it fills gaps and never adds a part of speech to a word that has one), its part of speech and kind are clear, and one of two rules allows it:
- **The lists' rule** (`auto:lists`, the console's `auto_approve_listed`, on by default): a curated list or a seed names it (`ref_lists`, or a seed candidate that wasn't rejected). The owner's lists and the open learner lists are the owner's choice of sources, so such a word is a gap, not a novelty.
- **Chosen by AI** (`ai:related`, `auto_approve_related`, on by default: trust the routine's model, and verify). An entry the owner chose links to it, and its rarest word is common enough (wordfreq Zipf ≥ 3, about once per million words), so links can't bring in rarities. **One hop only:** a link from an entry AI chose doesn't count, so the dictionary can't grow by chains of links past the owner's lists.

**A clear part of speech and kind** is the seed's; else the links', when Wiktionary has it (`word_request_links.target_key` keeps the key each link named); else Wiktionary's one option under the part of speech CEFR-J or Octanove gives; else Wiktionary's only option. A link to another word's spelling variant (*colour*, listed under *color*) never becomes a request.

Anything else waits for the owner: *hit the nail on the head* is in no list and no entry links to it, *perspicacious* is too rare, and *light* is a noun and an adjective. The request's `note` records why it was approved (`auto: on ngsl; noun/word from Wiktionary, its only one`; `ai: linked from en:go:v see_also; verb/phrasal_verb from the link, as Wiktionary has it; Zipf 4.0`), and the console counts each rule's approvals (`auto_approved`, `ai_chosen`). A student's request is decided once the answer has gone, and when approved, AI writes its Beta at once. A related word waits its turn in the factory. `requests sync` applies both rules to what was pending before.

**Verified afterwards.** Every word approved at once stays on the console's to-verify list (`to_verify`) until the owner confirms it, alone or in bulk; to undo one, the owner retires its entry. The audit routine (§6.6) reads these words first and reports any doubt as a finding, so the owner can confirm the rest without reading each one.

**One multiword expression, one entry:** Approve refuses a key while an approved entry has the same expression under another kind or article (409, naming it; §3.6). Each request row lists the entries for its headword (`twins`), so the owner sees the clash before deciding.

**Entries from before reviews:** revision 0012 left unapproved the Beta entries AI wrote for words nobody seeded. They're hidden (the owner's choice, 2026-10-20) and wait in the queue as requests of origin `ai_entry`, with their first definition shown. Approving shows them again at once; rejecting retires them.

---

## 7. AI gateway (runtime) [ADR-009]

### 7.1 Tasks

| Task | Input | Output (structured) | Max output tokens | Cache | Quota per student/day |
|---|---|---|---|---|---|
| `check_sentence` | sense key, sentence (≤ 200 chars) | verdict, corrected sentence?, reason (≤ 25 words, L1) | 200 | (sense, normalized sentence), 30 days | 20 |
| `examples` | sense key, topic?, level? | 3 × {text, translation} | 400 | (sense, topic, level), permanent | 30 |
| `difference` | two sense keys | contrast (≤ 80 words, L1), 2 examples | 400 | (sorted pair, L1), permanent | 20 |
| `provisional_entry` | query | provisional EntryDoc | 900 | (q_norm), permanent | 30 searches (§6.5) |
| `translate_query` | Spanish query, variety | 1–3 English headwords, or none | 200 | (variety, q_norm), permanent | shares `provisional_entry`'s 30 |
| `triage_report` | report or flag + target content | valid?, key, reason, likely fix | 150 | — | — |
| `spot_check` | entry doc + rubric | per-check verdicts | 600 | (key, content_version) | budget-limited |

Every prompt is grounded in the published card (definition, L1 gloss, examples), so answers stay consistent with the dictionary.

### 7.2 Provider chain and resilience

```
DeepSeek (OpenAI-compatible, JSON mode, 8 s)
   └─ on timeout / 429 / 5xx / invalid output → Claude Haiku 5.5 (Messages API, structured output, 8 s)
        └─ on failure → Gemini 3.5 Flash-Lite (OpenAI-compatible, JSON mode, 8 s)
             └─ on failure → Groq free tier (gpt-oss-120b, JSON mode, 8 s)
                  └─ on failure → fail soft: "AI help is busy, try again in a minute" (lookup is unaffected)
```

- **Fastest first** (the owner's choice, 2026-10-20). Request-time answers are stand-ins and short helps; the quality comes from the routine, which writes every entry with Opus 5.5. DeepSeek leads because it answers fastest in the owner's experience. Claude Haiku first was the original plan.
- **The order is a setting,** `AI_PROVIDER_ORDER` (default `deepseek,anthropic,gemini,groq`), so reordering after measuring needs no code change. A provider left out of it isn't used. `llm_calls` logs each call's `latency_ms` and outcome per provider, which is the measurement.
- **The owner's console can override it** without a deploy (§5.5), and can bring in any provider that has a key; an empty order turns request-time AI off.

- One retry per provider, on 429/5xx only.
- A **circuit breaker** per provider opens after 5 failures in a minute and stays open for 2 minutes.
- Outputs are always validated with Pydantic, including from providers that only have a JSON mode.

### 7.3 Budget [OP-2]

- The cost of every call = token usage × a price table in config. It's written to `llm_calls`.
- Monthly spend = the sum since the start of the month. At **80% of $5** the owner is alerted. At **100%**, paid providers are skipped until the month rolls over, and Groq serves alone.
- The owner's console can move the cap between $0 and $50 (§5.5); the ceiling is in code, so a leaked admin token can't run up a large bill.
- Expected spend at ~1,000 AI calls a day is **about $1–2/month** *(estimate)*.

### 7.4 Prompts, safety and privacy

- Prompts are versioned files (`backend/src/smart_dictionary/ai/prompts/{task}.v{n}.md`); the version is logged with each call.
- **16+ safety rules** are built into every system prompt. Outputs pass the V10 blocklist and language detection. Refusals map to a friendly message.
- **Inputs:** sentences are capped at 200 characters; emails and phone numbers are stripped before sending; nothing else about the student is sent.
- **Retention:** `llm_calls` stores no raw student text. The cache key for `check_sentence` is a hash, and its cached output expires after 30 days [NF-7].
- **Before enrolling under-18 students:** re-check the Anthropic, DeepSeek, Google (Gemini) and Groq terms for 16–17-year-old users, and add any safeguards they require.

**Built (`backend/src/smart_dictionary/ai/`):** the gateway, and the `provisional_entry` task (§6.5). The other tasks arrive with their features.
- **Claude** goes through the official `anthropic` SDK with structured outputs (`output_config.format`) at effort `low`. DeepSeek, Gemini and Groq go over HTTPS through their OpenAI-compatible endpoints in JSON mode, and get the schema in the prompt.
- **Keys:** a provider without its key (`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`) is skipped. With none, AI features answer `unavailable`.
- **Gemini** is `gemini-3.5-flash-lite`: the newest Flash-Lite, stable, with no shutdown date announced (Google's pages, 2026-10-16).
  - Flash-Lite 3.1 costs a little less but may retire from May 2027. Flash 3.8 costs $0.75 / $3.75, doubling in January 2027.
  - Gemini 3 models always think. The gateway asks for low reasoning effort, and counts the thinking, which Google bills as output, from the usage total.
- **Every provider's timeout is 8 s,** not the 4 and 6 s planned above. A whole provisional entry takes longer than a sentence check, and the student isn't waiting on the call. One limit for all also keeps the speed comparison fair. Re-measure it with real traffic.
- **The schema sent** is the task's Pydantic schema, with every object closed and every property required (null when unused), and without the length limits structured outputs don't take. The gateway checks those limits when it validates the answer.
- **Prices** are in `PRICES` (`ai/gateway.py`). Claude Haiku 5.5 costs $0.10 / $0.50 per million tokens (prompts up to 100K), and Gemini 3.5 Flash-Lite $0.30 / $2.50. A model missing from the table is priced as the dearest one, so a gap only makes the cap stricter.
- **The cap** is `AI_MONTHLY_BUDGET_USD`, 5 by default. From 80% each call logs a warning; an alarm on that log line is still to do.
- **The cache** keys on the prompt version and the input, so a new prompt never serves old answers.
- **Privacy:** `llm_calls` keeps the student's `users` ID, for the quota, and the looked-up word as `ref_key`, which the gate has limited to known English words.

### 7.5 Promotion

A generated example that passes the validators and has gathered no reports after N views is **promoted** into the entry's document by a weekly job, which publishes a new revision. Audits re-check promoted items. This is how the dictionary learns from use [LF-4].

---

## 8. Learner features

- **Identity and profile:**
  - A `users` row is created on the first request that arrives with `X-BBB-Ext-User`.
  - If the profile has no L1, the plugin sends the BBB UI locale, so `es` maps to `es-419` [LK-9]. The student can change it with `PATCH /v1/me`.
- **Saved words → review deck** [LF-1, LF-2]:
  - Saving a sense creates two cards:
    - **l1_to_en:** the L1 gloss plus an example with the headword masked (found through its forms), answered by the headword and its audio;
    - **en_to_meaning:** the headword plus an example, answered by the equivalents and the definition.
  - Scheduling uses **FSRS** (py-fsrs) on the server: desired retention 0.9, at most 20 new cards per day.
  - Cards are built from the entry, so no AI is involved.
- **History** [LF-6]: from `lookups`; the last 30 days are shown, and rows are kept for 12 months.
- **Class word list** [TF-2]:
  - An aggregate of this meeting's lookups from the last 4 hours, plus its pushes: the top 50 with counts, no names.
  - Moderators only.
- **Teacher push** [TF-1]:
  - The plugin broadcasts `{target_key}` on the `dictionary-push` data channel, where moderators can push and everyone receives.
  - Receivers fetch the card in their own L1.
  - The push is logged through `POST /v1/meetings/current/pushes`.
- **Hide / flag** [TF-3]:
  - Hiding needs a verified teacher: `users.role` is `teacher` or `staff`, from the LMS profile. A BBB moderator who isn't one can only flag.
  - Both write `content_flags`; a hide also sends `NOTIFY flags_changed`, so the item disappears from every card at once.
  - The entry moves to `needs_repair` (§6.6). The author or staff can undo, and every action stays in the log.
- **Reports and feedback** [CG-5, CG-9]:
  - ⚑ on any item opens the reason list.
  - `/v1/me/reports` shows each report's status.
  - When a report leads to a fix, the plugin shows "your report fixed *…*" the next time the student opens it.

---

## 9. BBB plugin

### 9.1 Stack
- React + TypeScript + Vite, with the BBB plugin SDK **1.0.0-beta.x pinned to an exact version**.
- TanStack Query for server state and a small Zustand store for UI state.
- i18next (EN, ES) and CSS modules, so our styles never leak into BBB's.
- Target bundle: < 250 KB gzipped.

### 9.2 Manifest (field names to verify against SDK 1.0)

```json
{
  "requiredSdkVersion": "~1.0.0",
  "name": "SmartDictionary",
  "javascriptEntrypointUrl": "https://dict.<domain>/plugin/SmartDictionary.js",
  "localesBaseUrl": "https://dict.<domain>/plugin/locales",
  "dataChannels": [
    { "name": "dictionary-push", "pushPermission": ["moderator"], "replaceOrDeletePermission": ["moderator"] }
  ],
  "settingsSchema": [{ "name": "apiBasePath", "type": "string", "required": true }]
}
```

Caddy serves the manifest and bundle from the API host. For the pilot, the LMS sends `pluginManifests` on `create` only for flagged groups (05 §6.2 L2); later they're registered for all meetings in `/etc/bigbluebutton/bbb-web.properties`. The BBB server must be able to reach that URL. Plugin settings are readable by every browser, so they never hold secrets.

### 9.3 Code structure

| Module | Role |
|---|---|
| `sdk-adapter/` | **The only code that imports the SDK.** It wraps the floating window, actions-bar button, chat message DOM, current user, UI locale and data channel, so SDK changes stay in one folder |
| `api/` | Client generated from OpenAPI (openapi-typescript + openapi-fetch); base path `/dictionary/v1` (same origin as BBB, so no CORS) |
| `features/lookup` · `card` · `mywords` · `coach` · `teacher` | One folder per product feature |

### 9.4 Identity proxy (on the BBB server)

This is the nginx `auth_request` pattern from audit §11.3:
- the location `/dictionary/` validates the BBB session;
- it copies the verified meeting ID, external user ID and moderator flag into `X-BBB-*` headers;
- it adds `X-Dict-Secret` from a root-only include file;
- it proxies to `https://dict.<domain>/v1/`.

On the API host, Caddy accepts `bbb` traffic only from the BBB server's static external IP (GCP us-central1).

**Fallback if this pattern fails on 4.0:** your portal mints a short-lived signed token (HMAC or JWT, containing the external ID, meeting ID, role and expiry) when it builds the join URL, and the API verifies it. The spike must then also find a supported way for the plugin to read that token.

### 9.5 Spike: the first task after your 4.0 upgrade

Exit criteria: all four work on BBB 4.0, or a fallback is documented.
1. A hello-world plugin loads and opens a floating window.
2. A round trip through the `auth_request` proxy works: confirm the header names, and how the plugin gets the session token on 4.0.
3. Chat message DOM access works, including a selection popover that doesn't alter the message text.
4. A data-channel push reaches a second browser.

---

## 10. NLP, embeddings and audio

### 10.1 NLP

- spaCy `en_core_web_md` (MIT) is loaded once per API process and once in the worker.
- The `Matcher` is compiled from `expression_patterns` at startup and recompiled on `content_changed`.
- It serves G3 (§4.2), context lookup (§4.4) and validator V3.

### 10.2 Forms

| Side | Sources |
|---|---|
| English | UniMorph `en` (facts) + LemmInflect (MIT) for gaps, plus spelling variants |
| Spanish (L1 forms) | mlconjug3 for verbs and UniMorph `es` for nouns and adjectives |

- **Pronominal verbs** get clitic variants (*darse cuenta* → *me di cuenta*, *se dio cuenta*, *nos dimos cuenta*…).
- In **multiword equivalents**, only the head verb is inflected.
- All forms are generated at publish into `l1_forms`.

### 10.3 Embeddings: bake-off in Phase 2

- **Candidates:**
  - EmbeddingGemma 2 (Apache-2.0, 768 dimensions);
  - bge-m3 (MIT, 1024);
  - harrier-oss-v1-0.6b (MIT, 1024).
- **Test set:**
  - 200 Spanish queries, descriptions included, each with its expected English senses;
  - 200 context sentences, each with its expected sense.
- **Metrics:** recall@10, MRR, and p95 CPU latency on the API host (t4g.medium), running on ONNX Runtime (int8 if quality holds).
- **Sense text** embedded: `"{lemma} ({pos}): {definition} | {es gloss} | {equivalents} | {examples}"`.
- **Model choice** is stored in settings. Changing the model triggers a batch re-embedding, and the winner fixes the `halfvec` dimension in the migration.

### 10.4 Audio (Kokoro-82M, Apache-2.0)

- **Input:**
  - **Headwords:** the document's IPA, converted to Kokoro's phoneme alphabet (misaki's 45 English symbols per variety) and validated, so audio follows the IPA exactly [ADR-013]. The converter (`audio/phonemes.py`) handled every one of the 135,166 US and 16,205 UK reference pronunciations. A symbol it doesn't know is reported, never guessed.
    - **Stress marks move from the syllable's start to its vowel** (`ˈkʌmfərtəbəl` → `kˈʌmfəɹtəbəl`), as misaki writes them, and a word with no mark gets one before its first vowel. A mark left in front of a consonant made Kokoro add a stray vowel ("e-comfortable"). The waveforms showed it, and showed it gone after the fix.
  - **Examples:** they have no IPA. espeak-ng phonemizes the text, and misaki's own conversion rules turn its symbols into Kokoro's. misaki itself doesn't run on Python 3.13.
- **Voices:** Kokoro's best-rated voice per accent: `af_heart` (en-US) and `bf_emma` (en-GB).
  - Headwords: every pronunciation, in its variety's voice.
  - Examples: `af_heart`, since examples are US English.
- **Engine and encoding (built, `backend/src/smart_dictionary/audio/`):**
  - Kokoro v1.0 through ONNX Runtime on the CPU. The model files come from kokoro-onnx's release, and are checked against their SHA-256.
  - The 24 kHz output gets 0.15 s of silence before and 0.25 s after, is peak-normalized to −1 dBFS, then encoded with PyAV to AAC 48 kbps mono `.m4a` with faststart.
  - A word is about 6–8 KB, an example about 20 KB.
- **Storage layout:** one folder per entry, and one file per thing said, with the content's hash in its name:

  ```
  v1/                                     the layout's version; a new format goes to v2/ beside it
    en/                                   the entry's language
      house/                              the entry key's slug
        n/                                its code: n, v, phrv… (a homograph's n~2 becomes n-2)
          pron.en-us.af_heart.3f9a2c1d0b7e.m4a        the headword, per variety and voice
          s2-pron.en-gb.bf_emma.9c0d4e7a1b2f.m4a      a pronunciation for sense 2 only
          s1-ex2.en-us.af_heart.71aa03bc94d2.m4a      sense 1, example 2
  ```

  - **The hash** is the first 12 hex characters of `sha256(engine|voice|variety|IPA or text)`. Anything that changes the sound changes the name, so a file is never overwritten, and every copy can be cached for a year.
  - **Path characters:** only `a-z 0-9 - _ .`. A slug's accents are dropped, and any other character becomes `-`, so no path needs URL encoding. Two slugs that fold to the same folder (*résumé*, *resume*) still get different files, because the hash names them.
  - **The database is the source of truth.** `audio_assets.s3_key` holds each file's path, and the API builds links as `AUDIO_BASE_URL` + `/` + `s3_key`; nothing guesses a path. The folders are for people browsing the bucket and for handling one entry's files as a group.
  - **Upload headers:** `Content-Type: audio/mp4` and `Cache-Control: public, max-age=31536000, immutable`.
  - **Replaced files** stay in place. A cleanup job will delete the files no `audio_assets` row points to, 30 days after they're replaced. Versioning keeps a deleted file for another 90 days.
- **Delivery (built, `infra/cdk/stack.py`):**
  - A private, versioned S3 bucket, read only by a CloudFront distribution through Origin Access Control. The distribution answers on its default `*.cloudfront.net` address, and SSM holds it as `AUDIO_BASE_URL`, with the bucket as `AUDIO_BUCKET`.
  - **CORS for any origin, on every response.** A CloudFront response-headers policy adds the CORS headers, so a copy cached from a plain `<audio>` request still works for a later `fetch()`. S3's own CORS rule answers preflights.
  - **Delivery settings:** HTTPS only, HTTP/2 and 3, IPv6. The full price class, because only it includes the South American edge locations. A missing file is a 403, cached for 30 seconds.
- **The job (built, `audio/sync.py`, `smart-dictionary audio sync | loop`):**
  - **What it compares:** each published entry against its `audio_assets` rows. A file is made when its row is missing or names another file, that is, when the IPA, the text, the voice or the engine changed. Provisional entries wait, because the routine rewrites them within the hour.
  - **Production:** the `audio` container (`deploy/compose.yaml`) runs `audio loop`. It takes at most one CPU core and 1.5 GB, so lookups keep the other core. The model lives in the `kokoro_models` volume.
  - **Pace:** the factory publishes about 40 entries an hour, which is roughly 15 minutes of audio. One core keeps pace, so no separate backfill machine is needed. A big backlog can still be split across processes with `--shard i/n`. The container logs each pass's real-time factor.
- **Uploads (built, `deploy/audio-upload.sh`):**
  - **No AWS credentials in containers:** the API's containers can't use the host's AWS role, by design (IMDSv2 hop limit 1). So the container writes files to an outbox folder, and a host timer uploads them every minute with the host's role. That role may only add files under `v1/`.
  - **Receipts:** each upload leaves a receipt, `<file>.done`. The next pass of the job turns it into the row's `uploaded_at`, and only then does the card link the file.
  - **Lost files:** a file neither uploaded nor waiting after a day is forgotten, so it's made again.

---

## 11. Infrastructure and deployment

### 11.1 Environments
- **dev:** your PC, running Docker Compose with the same images (x86) against a local Postgres container or a Supabase branch.
- **prod:** a dedicated **EC2 t4g.medium** (arm64, 2 vCPU / 4 GB) in AWS us-east-2, defined in its own CDK stack (`infra/cdk`), plus the **dedicated Supabase project** in us-east-2 (05 §4–5).

### 11.2 Memory budget (t4g.medium, estimates)

| Service | Limit | Notes |
|---|---|---|
| api | 1.5 CPU / 2 GB | 1–2 uvicorn workers; spaCy `md` + the ONNX embedder loaded once per process |
| worker | 1 CPU / 1.5 GB | Publish pipeline by day; Kokoro TTS and backfills at night (or on the optional batch node) |
| caddy, uptime-kuma | 0.25 CPU / 256 MB each | — |
| OS + page cache | the rest | — |

If memory or CPU-credit alarms fire, the next step is t4g.large (8 GB), which is a resize, not a redesign.

### 11.3 Compose sketch

```yaml
services:
  caddy:
    image: caddy:2
    ports: ["80:80", "443:443"]
    volumes: ["./Caddyfile:/etc/caddy/Caddyfile:ro", "caddy_data:/data", "./plugin-dist:/srv/plugin:ro"]
  api:
    image: <account>.dkr.ecr.us-east-2.amazonaws.com/smart-dictionary:${TAG}
    command: ["api"]
    env_file: .env                       # DATABASE_URL points at the Supabase session pooler
  worker:
    image: <account>.dkr.ecr.us-east-2.amazonaws.com/smart-dictionary:${TAG}
    command: ["worker"]
    env_file: .env
volumes: { caddy_data: {} }
```

### 11.4 CI/CD (GitHub Actions)

- **On push and PR:**
  - ruff, pyright, and pytest against a Postgres service;
  - plugin lint, tests and build;
  - checks that the OpenAPI → TS client and the EntryDoc schema are up to date;
  - the golden-set check when `factory/` changes.
- **Deploy** (`.github/workflows/deploy.yml`, run by hand; health-gated and never cancelled mid-run, like the LMS's, but through **SSM instead of SSH**):
  1. The job assumes the stack's deploy role through **GitHub OIDC**. The role trusts only this repository's `production` environment, so no AWS keys are stored in GitHub.
  2. It builds the linux/arm64 image with buildx and pushes it to **ECR**, tagged with the commit. A commit that already has an image skips the build.
  3. It copies `deploy/` to S3 and runs `deploy.sh` on the host with **SSM Run Command**. The host has no SSH port; people connect with SSM Session Manager.
  4. `deploy.sh` writes the env files from Parameter Store, pulls the image, and runs the migrations as a one-off container, as the owner. Then `up --wait` gates on the API's health check, and the previous image goes back up if it fails.
  - **Rollback:** run the workflow with an earlier commit; its image is reused.
  - `infra/README.md` is the runbook.

### 11.5 Backups
- **Supabase daily backups** (Pro plan; 7-day retention).
- **Nightly at 03:00:** `pg_dump -Fc` of the dictionary database to S3 `backups/db/`, kept as 30 daily + 12 monthly through a lifecycle rule. This is our own long-term, portable copy.
- **Nightly:** a JSONL export of published entries to S3 `exports/`.
- **Monthly:** an automated restore test (restore into a scratch Postgres container, compare row counts, report).
- **The EC2 itself is disposable:** the CDK stack and images recreate it, so it needs no backup.

### 11.6 Secrets
- **On the EC2:** secrets live in **SSM Parameter Store** as SecureStrings. At each deploy the instance role reads them and writes one env file per path (mode 600):
  - `/smart-dictionary/prod/app/` for the API:
    - `DATABASE_URL`, as `dictionary_app` through the session pooler. That role reads and writes rows but can't change the schema (revision 0005). It bypasses the row-level security that hosted Supabase turns on for every table (revision 0006); RLS stays on as the guard against Supabase's Data API roles, which this project doesn't use. The pooler is IPv4, which Docker's default network supports; the direct connection is IPv6 only.
    - `BBB_PROXY_SECRET`;
    - later the Anthropic, DeepSeek, Gemini and Groq keys (separate from the LMS's, for cost attribution and revocation) and `DICTIONARY_SERVICE_TOKEN`.
  - `/smart-dictionary/prod/migrate/` holds the owner's `DATABASE_URL`, which only the one-off migration container gets.
  - `/smart-dictionary/prod/caddy/` holds `SITE_ADDRESS`.
  - The metadata service answers the host only (IMDSv2, hop limit 1), so a container can't borrow the instance role to read the owner's URL.
  - The instance role also reaches only this service's S3 prefixes, so there are no static AWS keys on the host.
- **In the database:** factory and admin tokens, hashed.
- **On the BBB server:** the proxy secret, in a root-only nginx include.
- **In the repo:** never any secret.

### 11.7 Optional batch node (Oracle A1, Bogotá)

- **What it runs:** the same worker image, with `--queues batch` only: TTS, embedding backfills and seed imports. It connects to the Supabase project; ~60–80 ms per query doesn't matter for batch work.
- **Nothing student-facing depends on it.** If it's reclaimed or resized, the EC2 worker takes the batch queue at night.
- **Housekeeping:** check *Governance → Limits* and *Billing → Cost Analysis* in the OCI console monthly. Its free allowance is now 2 OCPU / 12 GB.

---

## 12. Security and privacy

| Threat | Control |
|---|---|
| Spoofed identity | `bbb` context accepted only with the proxy secret, from the BBB server's IP; identity comes from BBB's own session check |
| Abuse of AI or provisional endpoints | Per-student quotas, the global cap, gate checks, advisory locks; AI writes only words the owner chose |
| Junk or unwanted headwords from student searches | No search adds a word: a real headword becomes a request (Wiktionary only, so no typos), capped per student, and only the owner's approval queues it (§6.7) |
| Report spam or vandalism by hiding | Reports: one open report per person per item, 10 a day, none from inactive accounts, and they never change content directly. Hides: verified teachers only (LMS role). Hides and flags: 20 a day per person, logged, undoable. Signals: one per user, item, signal and day |
| Prompt injection through student text | Strict output schemas, no tools, no secrets in prompts, output validation |
| Injection through grounding data | Wiktionary text is treated as data; a factory token can only reach `/v1/factory/*` |
| XSS through content | The plugin renders text only, never HTML from the API; CSP on the plugin assets |
| Token leakage | The factory token is a routine network secret; all tokens are hashed, with a documented rotation |
| A leaked or misused admin token | Its own `admin` scope (`sda_` prefix), held only by the LMS backend; optionally accepted only from the LMS server's IP; it reads aggregates and content, never student identities or secrets; it changes only four bounded settings, each audited; the API's role can't write `api_clients` or rewrite the audit (§5.5) |
| SQL injection | Parameterized queries only |
| Data exposure | Pseudonymous external IDs; no names or emails stored; logs never contain student sentences |

**Data inventory [NF-7]**

| Data | Purpose | Retention |
|---|---|---|
| `users` (external ID, L1, level) | Personalization | Until deletion |
| Learner profile cache from the LMS (L1, region, level, role, active; **no names, email or phone**) | L1 projection, level fit, access | Refreshed every 24 h; deleted with the user |
| `lookups` | History, class lists, metrics | 12 months (monthly partitions) |
| `saved_items`, `review_*` | Learning | Until deletion |
| `check_sentence` cache | Performance | 30 days |
| `reports` (reporter, reason, comment), `content_flags` | Quality control | 12 months after the report or flag is resolved; comments are never logged |
| `quality_signals` | Suspicion scores | 90 days; `item_suspicion` keeps only the aggregate |
| `llm_calls` (no raw text) | Cost and reliability | 12 months |

- **Deletion:** an admin CLI/endpoint deletes a student's rows by external ID and anonymizes their lookups.
- **Minors:** `users.is_minor`, once your portal can supply it, can gate features if the provider or legal checks require it.

---

## 13. Observability

- **Logs:** JSON with request ID, route, status, latency, a hashed user ID and a hashed meeting ID; Caddy access logs; rotation enabled.
- **Metrics:** `/v1/admin/stats` (§5.5; the LMS's console shows it) plus SQL views:
  - usage: lookups per day, miss rate, p50/p95 latency;
  - AI: calls, spend and fallback rate by provider;
  - factory: entries per batch, pass rate, repairs, quarantines;
  - pipeline: queue depth by status;
  - quality: the quality-score distribution; reports and hides per 1,000 lookups; median time from a valid report or flag to the republished fix; the share of reports triaged valid; the size of the suspicion queue; hides per teacher.
- **Alerts (Uptime Kuma):** `/readyz` down, disk > 80%, a missing backup heartbeat, AI spend ≥ 80%, a spike in 5xx errors.

---

## 14. Testing

| Layer | What | When |
|---|---|---|
| Unit | `norm()`, keys, each validator rule (with fixtures), ranking fusion, the FSRS wrapper, gateway fallback (fake providers) | Every push |
| Integration | API against a real Postgres, using a 200-entry fixture dictionary | Every push |
| Search regression | ~300 queries (forms, typos, multiword, ES reverse lookup, descriptions, context sentences) with their expected top 3; MRR tracked over time | Every push touching search |
| Content | Golden-set agreement ≥ 90%; schema drift | When `factory/` changes |
| Contract | OpenAPI snapshot; TS client regeneration | Every push |
| Load | Locust or k6: 50 students, one lookup per 5 s plus autocomplete bursts, against the NF-1 targets | Before launch and after big changes |
| Plugin | vitest + testing-library; a manual checklist in a test meeting; Playwright later | Every push / before releases |

---

## 15. Deferred designs

| Topic | Starting point |
|---|---|
| Clips | Audit §8 (pipeline, rights, coverage) and §5.3 (tables) |
| Portuguese (pt-BR) | Add `pt-BR` to `Variety`; a factory "layer pass" that writes only `l1["pt-BR"]` and the translations; L1 forms via mlconjug3 (pt) + UniMorph `pt` |
| Arabic | Normalization (§3.2); CAMeL Tools for L1 forms; RTL in the plugin; TTS options in audit §9 |
| Spanish headwords | `entries` rows with `language = 'es'`, Spanish definitions, `sense_links` |
| Admin dashboard | The screens in audit §12 |
| Jev | An A/B test against the Haiku spot checks, as typed QA gates |
| Local LLM | On the optional Oracle batch node, for offline tasks |
| LMS vocabulary linking (L3–L5) | 05 §6.2 |
