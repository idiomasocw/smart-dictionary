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
               │  AI gateway ──▶ Haiku 5.5 → DeepSeek → Groq (free)          ▲
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
| 009 | AI gateway: Haiku 5.5 → DeepSeek → Groq free, with a $5 cap | Resilience and cost control |
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
missing word ──(gateway)──▶ provisional ──(leased with top priority)──▶ generating …
published ──(hide, flag or valid report / failed re-check or audit)──▶ needs_repair   (the live entry stays; hidden parts are filtered)
any ──(owner)──▶ retired
```

**Leases fix the old "stuck in PROCESSING" bug (H3).** A leased batch sets `lease_batch_id` and `lease_expires_at` (default 3 h). A periodic job returns expired leases to their previous status. Leasing uses `FOR UPDATE SKIP LOCKED`, so two runs never get the same entry.

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
    key: str                           # entry or sense key (resolved when it exists)
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

**Filled by the server, not the AI:** frequency band, inflected forms of the lemma and of each spelling variant (UniMorph/LemmInflect: *realise* → *realised*), audio, embeddings, L1 forms, and `quality`. The AI may add irregular forms and spelling variants, which the validators then check.

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
  - add audio URLs and the frequency band.
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
| `admin` | Bearer token, scope `admin` | You |
| `lms` | Bearer token, scope `lms`: server-to-server calls from the LMS (later: `/v1/resolve`, `/v1/demand`) | The LMS backend |
| `jwt` *(later)* | An OIDC JWT verified against the issuer's JWKS (Cognito for your LMS web and app) | Students outside BBB |
| `api_key` *(later)* | Per-client keys with scopes, rate limits and metering | External apps |

Every context resolves to `Principal(tenant, subject, meeting, roles, via)` (`core/principal.py`), so the business logic never knows which adapter authenticated the request.

Caddy accepts `bbb` traffic only from the BBB server's IP. Factory and admin routes are token-only, because routines have no static egress IP.

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
| POST | `/v1/events` `[{type, target_key}]` | bbb | Behavior signals only the plugin sees (sense reselected, card closed quickly); batched and rate-limited | CG-10 |
| GET · POST | `/v1/teacher/micro-review` (v1.x) | bbb (verified teacher) | Five entries to review; thumbs up or down per sense | TF-5 |
| GET | `/v1/admin/quality` | admin | Open reports and flags, the suspicion queue, time to fix | CG-8, OP-1 |
| POST | `/v1/factory/batches` `{size, label}` | factory | Lease the next batch, with grounding packs | CG-2 |
| POST | `/v1/factory/batches/{id}/entries` | factory | Submit EntryDocs and review results | CG-3 |
| GET | `/v1/factory/batches/{id}` | factory | Batch outcome | CG-3 |
| GET | `/v1/factory/golden` | factory | Golden set + rubric | CG-6 |
| POST | `/v1/admin/seeds/import` · GET `/v1/admin/seeds/export` | admin | Seed builder | CG-1 |
| GET | `/v1/admin/stats` | admin | Counts, spend, latency, factory throughput | OP-1 |
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
- Inflected input such as *me di cuenta* needs `l1_forms` (§10.2), which publishing doesn't generate yet; until it does, such queries reach these senses through `did_you_mean`. The response gains a top-level `provisional` object with §6.5.

### 5.4 Conventions

- **Errors:** RFC 9457 `application/problem+json` (`type`, `title`, `status`, `detail`, `instance`, plus a stable `code` such as `quota_exceeded` or `invalid_query`). The framework's own errors use the same shape, with the codes `invalid_request` (422), `not_found`, `method_not_allowed` and `internal_error`.
- **Rate limits:** per student, 120 lookups/min, with daily quotas per AI task (§7.1). A per-IP fallback catches anonymous traffic. `429` responses carry `Retry-After`.
- **Versioning:** `/v1` changes are additive only; breaking changes go to `/v2`. The OpenAPI document is generated by FastAPI and committed, and CI fails if the generated TS client drifts.
- **Limits:** `q` ≤ 100 characters; `context` ≤ 500 characters; `sentence` ≤ 200 characters. Inputs are trimmed and normalized, and control characters are rejected.

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
8. **Export** the ranked list as CSV (`GET /v1/admin/seeds/export`) for you to skim, then **queue** it.

**Built so far (roadmap 2.1):** `smart-dictionary seeds import` runs steps 2–4 and 6 into `seed_candidates`, and `seeds export` writes the ranked CSV of step 8 to `data/raw/seed-candidates.csv`.
- **Parts of speech** come from the CEFR lists, then from each Wiktionary part of speech with at least 3 senses and 40% of the main one's, then from UniMorph. A spelling variant's entry counts its main spelling's senses (*catalog*).
- **Open-list items** take their US spelling and lemma before matching (*centre* → *center*, *were* → *be*).
- **Spacing variants** fold when Wiktionary records one as the other's alternative form (*best seller* → *bestseller*). Other pairs with the same part of speech are flagged.
- **Homographs** are numbered as entry keys are (*pole*, *Pole* → `en:pole:n~2`).
- **Not yet:** the step-5 filters and the curation run (step 7).

A re-run replaces the undecided candidates and keeps the decided ones. Seed candidates and their variants join the Wiktionary keep set, so the full order is `seeds import`, `data import kaikki-en`, then `seeds import` again.

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

When an entry comes back for repair, `existing_keys` and `repair` carry the published keys, the failed checks, hides and flags, valid reports and a signal summary (§6.6), so the fix keeps the keys stable.

### 6.3 The routine [CG-2, ADR-008]

**Files in the repo**

| File | Purpose |
|---|---|
| `factory/ROUTINE.md` | The run instructions (the routine's prompt points here) |
| `factory/STYLE.md` | The editorial style guide (product spec §6) |
| `factory/rubric.md` | The reviewer's checklist |
| `factory/schema/entry.v1.json` | Generated from the Pydantic models |
| `factory/bin/pull_batch.py` · `validate.py` · `submit_batch.py` | Batch I/O and local validation (same validators as the server) |
| `factory/golden/` | Golden-set entries and your ratings |
| `.claude/skills/factory-batch/SKILL.md` | The **local twin**: the same flow in an interactive Claude Code session on your PC |

**Routine configuration**
- **Model:** Sonnet 5.5 or Opus 5.5, decided by the golden-set comparison in the pilot.
- **Network:** *Custom*, with the API host allowlisted (plus PyPI, or vendored wheels, for the validator's dependencies).
- **Secret:** `FACTORY_TOKEN` is configured as a **network secret** for the API host, so it never enters the VM.
- **Setup script:** `pip install -r factory/requirements.txt`.

**Run protocol** (you press *Run now*; each run notifies you when it ends):

1. `pull_batch.py --size 40` calls `POST /v1/factory/batches`, which leases 40 entries (repairs and provisional entries first; 3-hour lease) and writes `work/batch.json`.
2. The orchestrator splits the batch into **4 groups of 10** and spawns 4 **writer** sub-agents. Each writer reads STYLE, the schema and its 10 packs, writes `work/out/group-N.json`, then runs `validate.py` and fixes errors (at most 3 loops).
3. Four **reviewer** sub-agents, each in a fresh context, check group N+1 (in rotation) against `rubric.md`. They produce pass/fail plus issues for each sense. The writers fix what they can; anything else is submitted marked `needs_repair` with the issues attached.
4. `submit_batch.py` calls `POST …/entries`. The server re-validates (it's the authority) and publishes, repairs or quarantines each entry (§6.4).
5. The orchestrator prints a summary (published / repair / quarantined / time) into the run log.

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
| V10 | Safety: blocklists for sexual, violent and hate content in examples; offensive senses labeled | error |
| V11 | Counts: 1–6 senses, 2–3 examples, ≤ 6 collocations, ≤ 3 notes per sense | error |

- **Quality score** = weighted warnings + reviewer verdicts (+ spot-check results when present). An entry **publishes** if there are no errors and the score is ≥ τ, with τ calibrated on the golden set.
- **Spot checks:** Haiku 5.5 re-runs the rubric on a 10% sample, plus every entry the reviewer marked "uncertain", within the AI budget (§7.3).
- **Publish transaction:**
  1. Insert the revision.
  2. Update `entries` (`doc`, `status`, `quality`, `content_version`+1).
  3. Rebuild the projections: forms, patterns, senses (upserted by key; missing ones get `retired_at`), equivalents, L1 forms.
  4. `NOTIFY content_changed`.
  5. Enqueue jobs for embeddings and for TTS of new or changed pronunciations and examples.

### 6.5 Missing words and provisional entries [LK-10, CG-4]

1. **Gate:**
   - no hit in G1–G4;
   - the query is plausible English: in the wordfreq vocabulary (Zipf ≥ 1.0) or in the Wiktionary lemma/form list, or a multiword string whose tokens are all known;
   - not on the blocklist;
   - within the student's quota (10 provisional entries/day).
2. **Dedupe:** an advisory lock on `hash(q_norm)` plus the `misses` row, so concurrent students trigger only one generation.
3. **Generate:** the gateway task `provisional_entry` (Haiku → DeepSeek → Groq) returns 1–2 senses, one example each and the es-419 layer, as structured output.
4. **Validate** with V1–V5, V10 and V11, then **publish** with status `provisional` and top priority. The next routine run leases it, and the final entry replaces it under the same key.

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
- **Audits** [CG-7]: every 10th batch includes 5 published entries for re-review.
- **Re-validation** [CG-7]: when the validators' version changes, a job re-validates every published entry, and failures go to repair.
- **Golden set** [CG-6]: 50 entries chosen across levels and kinds, with your ratings in `factory/golden/ratings.csv`. `validate.py --golden` measures how well the reviewer agrees with your ratings, and CI runs it whenever `factory/` changes. The target is agreement ≥ 90%.
- **Teacher micro-review** [TF-5, v1.x]: each teacher gets the five most suspicious or lowest-quality entries their classes looked up in the last 7 days. A thumbs down becomes a flag, and a thumbs up lowers the suspicion score.

**Schema changes** (a later migration; the §3.4 sketch and revision 0001 predate them)
- **Targets:** `target_key` is a sense, example or note key; `sense_key|variety|term` for an equivalent; `entry_key|ipa|variety` for a pronunciation; or, for a *missing meaning* report only, the entry key.
- **`reports`:** `kind` becomes `reason`, with the list from spec §5.7 (`wrong_translation`, `wrong_meaning`, `unnatural_example`, `wrong_pronunciation`, `typo`, `missing_meaning`, `hard_to_understand`, `offensive`, `other`). It gains `entry_key`, `resolved_at`, `fixed_by_revision` (→ `entry_revisions`) and `notified_at`. The statuses become `open`, `valid`, `invalid`, `duplicate` and `fixed`, and a unique index on `(reporter, target_key)` covers the open and valid ones.
- **`content_flags`:** gains `entry_key`, `comment`, `resolved_at` and `fixed_by_revision`, and its `reason` uses the same list. An active hide has neither `undone_at` nor `resolved_at`; the card filter and the partial index check both.
- **New tables:**
  - `quality_signals (tenant_id, user_id, target_key, entry_key, signal, weight, observed_at)`: append-only, at most one row per user, item, signal and day (aggregate signals have no user);
  - `item_suspicion (target_key primary key, entry_key, score, last_signal_at, last_reviewed_at)`: derived, kept current by a worker job.

---

## 7. AI gateway (runtime) [ADR-009]

### 7.1 Tasks

| Task | Input | Output (structured) | Max output tokens | Cache | Quota per student/day |
|---|---|---|---|---|---|
| `check_sentence` | sense key, sentence (≤ 200 chars) | verdict, corrected sentence?, reason (≤ 25 words, L1) | 200 | (sense, normalized sentence), 30 days | 20 |
| `examples` | sense key, topic?, level? | 3 × {text, translation} | 400 | (sense, topic, level), permanent | 30 |
| `difference` | two sense keys | contrast (≤ 80 words, L1), 2 examples | 400 | (sorted pair, L1), permanent | 20 |
| `provisional_entry` | query | provisional EntryDoc | 900 | (q_norm), permanent | 10 |
| `triage_report` | report or flag + target content | valid?, key, reason, likely fix | 150 | — | — |
| `spot_check` | entry doc + rubric | per-check verdicts | 600 | (key, content_version) | budget-limited |

Every prompt is grounded in the published card (definition, L1 gloss, examples), so answers stay consistent with the dictionary.

### 7.2 Provider chain and resilience

```
Claude Haiku 5.5 (Messages API, structured output)
   └─ on timeout (4 s) / 429 / 5xx / invalid output → DeepSeek (OpenAI-compatible, JSON mode, 6 s)
        └─ on failure → Groq free tier (gpt-oss-120b, JSON mode, 6 s)
             └─ on failure → fail soft: "AI help is busy, try again in a minute" (lookup is unaffected)
```

- One retry per provider, on 429/5xx only.
- A **circuit breaker** per provider opens after 5 failures in a minute and stays open for 2 minutes.
- Outputs are always validated with Pydantic, including from providers that only have a JSON mode.

### 7.3 Budget [OP-2]

- The cost of every call = token usage × a price table in config. It's written to `llm_calls`.
- Monthly spend = the sum since the start of the month. At **80% of $5** the owner is alerted. At **100%**, paid providers are skipped until the month rolls over, and Groq serves alone.
- Expected spend at ~1,000 AI calls a day is **about $1–2/month** *(estimate)*.

### 7.4 Prompts, safety and privacy

- Prompts are versioned files (`backend/ai/prompts/{task}.v{n}.md`); the version is logged with each call.
- **16+ safety rules** are built into every system prompt. Outputs pass the V10 blocklist and language detection. Refusals map to a friendly message.
- **Inputs:** sentences are capped at 200 characters; emails and phone numbers are stripped before sending; nothing else about the student is sent.
- **Retention:** `llm_calls` stores no raw student text. The cache key for `check_sentence` is a hash, and its cached output expires after 30 days [NF-7].
- **Before enrolling under-18 students:** re-check the Anthropic, DeepSeek and Groq terms for 16–17-year-old users, and add any safeguards they require.

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

- **Input:** the IPA from the document, converted to Kokoro's phoneme set and validated per variety, so audio follows the IPA exactly [ADR-013].
- **Voices:** en-US (one female, one male) and en-GB (one voice).
  - Headwords: every pronunciation.
  - Examples: en-US.
- **Encoding:** 24 kHz output → AAC 48 kbps mono `.m4a` via ffmpeg.
- **Storage key:** `audio/{sha256(engine|voice|ipa_or_text)}.m4a`. Content addressing dedupes files and allows `Cache-Control: public, max-age=31536000, immutable`.
- **Scheduling:** worker queue `batch` with concurrency 1, preferring night hours. For scale: 5k entries is about 10k headword files plus about 15k example files *(estimate)*.

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
- **On tag:** build linux/arm64 (prod) + linux/amd64 (dev) images with buildx and push them to **ECR**, as the LMS does.
- **Deploy** (your LMS's proven pattern: ECR, a wait for SSH, health-gated cutover, never cancelled mid-run):
  1. Run a manual workflow that connects to the dictionary EC2 over SSH.
  2. `docker compose pull`.
  3. Run the migrations as a one-off container (`alembic upgrade head`).
  4. `up -d`.
  - **Rollback:** redeploy the previous tag.

### 11.5 Backups
- **Supabase daily backups** (Pro plan; 7-day retention).
- **Nightly at 03:00:** `pg_dump -Fc` of the dictionary database to S3 `backups/db/`, kept as 30 daily + 12 monthly through a lifecycle rule. This is our own long-term, portable copy.
- **Nightly:** a JSONL export of published entries to S3 `exports/`.
- **Monthly:** an automated restore test (restore into a scratch Postgres container, compare row counts, report).
- **The EC2 itself is disposable:** the CDK stack and images recreate it, so it needs no backup.

### 11.6 Secrets
- **On the EC2:** secrets live in **SSM Parameter Store**, and the instance role reads them at deploy time to write `.env` (mode 600):
  - Supabase connection strings;
  - the Anthropic, DeepSeek and Groq keys (separate keys from the LMS's, for cost attribution and revocation);
  - the proxy secret;
  - `DICTIONARY_SERVICE_TOKEN`.
  The instance role also grants access to only this service's S3 prefixes, so there are no static AWS keys on the host.
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
| Abuse of AI or provisional endpoints | Per-student quotas, the global cap, gate checks, advisory locks |
| Report spam or vandalism by hiding | Reports: one open report per person per item, 10 a day, none from inactive accounts, and they never change content directly. Hides: verified teachers only (LMS role). Hides and flags: 20 a day per person, logged, undoable. Signals: one per user, item, signal and day |
| Prompt injection through student text | Strict output schemas, no tools, no secrets in prompts, output validation |
| Injection through grounding data | Wiktionary text is treated as data; a factory token can only reach `/v1/factory/*` |
| XSS through content | The plugin renders text only, never HTML from the API; CSP on the plugin assets |
| Token leakage | The factory token is a routine network secret; all tokens are hashed, with a documented rotation |
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
- **Metrics:** `/v1/admin/stats` plus SQL views:
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
