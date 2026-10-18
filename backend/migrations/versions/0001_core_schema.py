"""Core schema: reference data, entries and projections, learners, factory, AI.

Implements docs/03-system-design.md §3.4 with the amendments from
docs/05-integration-and-hosting.md §7 (tenants, api_clients, learner profile cache).

Extensions live in the `extensions` schema (the Supabase convention), so their
types and operator classes are schema-qualified here. Row IDs are UUIDv7 from
the app (core/ids.py); gen_random_uuid() defaults are only a safety net.

Revision ID: 0001
Revises:
Create Date: 2026-10-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
create schema if not exists extensions;
create extension if not exists vector with schema extensions;
create extension if not exists pg_trgm with schema extensions;
create extension if not exists unaccent with schema extensions;
create extension if not exists fuzzystrmatch with schema extensions;

create function public.set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end
$$;

-- Reference -------------------------------------------------------------------

create table tenants (
  id         text primary key,
  name       text not null,
  created_at timestamptz not null default now()
);
insert into tenants (id, name) values ('ocw', 'OneCulture World');

create table languages (
  code      text primary key,
  name      text not null,
  direction text not null default 'ltr' check (direction in ('ltr', 'rtl'))
);
insert into languages (code, name, direction) values
  ('en', 'English', 'ltr'), ('es', 'Spanish', 'ltr'), ('pt', 'Portuguese', 'ltr'), ('ar', 'Arabic', 'rtl');

create table varieties (
  tag      text primary key,
  language text not null references languages(code),
  name     text not null
);
insert into varieties (tag, language, name) values
  ('en-US', 'en', 'English (United States)'),
  ('en-GB', 'en', 'English (United Kingdom)'),
  ('es-419', 'es', 'Spanish (Latin America)'),
  ('es-ES', 'es', 'Spanish (Spain)'),
  ('pt-BR', 'pt', 'Portuguese (Brazil)'),
  ('ar', 'ar', 'Arabic (Modern Standard)');

-- Entries ---------------------------------------------------------------------

create type entry_status as enum ('candidate', 'queued', 'generating', 'validating', 'published',
                                  'provisional', 'needs_repair', 'quarantined', 'retired');
create type entry_kind as enum ('word', 'phrasal_verb', 'idiom', 'phrase');

create table entries (
  id                  uuid primary key default gen_random_uuid(),
  key                 text not null unique,
  language            text not null references languages(code),
  lemma               text not null,
  lemma_norm          text not null,
  pos                 text not null check (pos in ('noun', 'verb', 'adjective', 'adverb', 'pronoun',
                        'determiner', 'preposition', 'conjunction', 'interjection', 'numeral', 'phrase')),
  kind                entry_kind not null default 'word',
  homograph           smallint not null default 1 check (homograph >= 1),
  cefr                text check (cefr in ('A1', 'A2', 'B1', 'B2', 'C1', 'C2')),
  freq_zipf           numeric(3, 2),
  priority            integer not null default 0,
  status              entry_status not null default 'candidate',
  attempts            smallint not null default 0,
  lease_batch_id      uuid,
  lease_expires_at    timestamptz,
  quality             numeric(4, 3),
  content_version     integer not null default 0,
  doc                 jsonb,
  current_revision_id uuid,
  sources             text[] not null default '{}',
  published_at        timestamptz,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (language, lemma_norm, pos, kind, homograph)
);
create index entries_queue  on entries (status, priority desc) where status in ('queued', 'needs_repair', 'provisional');
create index entries_prefix on entries (language, lemma_norm text_pattern_ops);
create index entries_trgm   on entries using gin (lemma_norm extensions.gin_trgm_ops);
create index entries_lease  on entries (lease_expires_at) where lease_batch_id is not null;
create trigger entries_updated_at before update on entries
  for each row execute function public.set_updated_at();

create table entry_revisions (
  id          uuid primary key default gen_random_uuid(),
  entry_id    uuid not null references entries(id),
  doc         jsonb not null,
  produced_by jsonb not null,
  qa          jsonb not null,
  outcome     text not null check (outcome in ('published', 'needs_repair', 'quarantined')),
  created_at  timestamptz not null default now()
);
create index entry_revisions_entry on entry_revisions (entry_id, created_at desc);
alter table entries add constraint entries_current_revision_fk
  foreign key (current_revision_id) references entry_revisions(id) deferrable initially deferred;

-- Projections: rebuilt from entries.doc on publish -----------------------------

create table entry_forms (
  entry_id  uuid not null references entries(id) on delete cascade,
  form      text not null,
  form_norm text not null,
  phonetic  text,                -- Double Metaphone key, computed by the app (LK-4)
  features  text,                -- UniMorph style: 'V;PST', 'N;PL'
  source    text not null,       -- unimorph | lemminflect | variant | doc
  primary key (entry_id, form_norm)
);
create index entry_forms_norm     on entry_forms (form_norm);
create index entry_forms_trgm     on entry_forms using gin (form_norm extensions.gin_trgm_ops);
create index entry_forms_phonetic on entry_forms (phonetic) where phonetic is not null;

create table expression_patterns (
  entry_id  uuid primary key references entries(id) on delete cascade,
  tokens    jsonb not null,
  separable boolean not null default false,
  max_gap   smallint not null default 0
);

create table senses (
  key         text primary key,
  entry_id    uuid not null references entries(id),
  ord         smallint not null,
  cefr        text check (cefr in ('A1', 'A2', 'B1', 'B2', 'C1', 'C2')),
  registers   text[] not null default '{}',
  domains     text[] not null default '{}',
  short_gloss text not null,
  retired_at  timestamptz,         -- soft retirement, never cascade-deleted
  embedding   extensions.halfvec(768)  -- dimension confirmed by the embedding bake-off
);
create index senses_entry     on senses (entry_id) where retired_at is null;
create index senses_embedding on senses using hnsw (embedding extensions.halfvec_cosine_ops);

create table equivalents (
  sense_key        text not null references senses(key),
  variety          text not null references varieties(tag),
  term             text not null,
  term_norm        text not null,
  rank             smallint not null,
  match            text not null check (match in ('exact', 'near', 'approx', 'paraphrase')),
  regions          text[] not null default '{}',
  target_entry_key text,
  primary key (sense_key, variety, term_norm)
);
create index equivalents_norm   on equivalents (term_norm);
create index equivalents_prefix on equivalents (term_norm text_pattern_ops);
create index equivalents_trgm   on equivalents using gin (term_norm extensions.gin_trgm_ops);

create table l1_forms (
  variety   text not null references varieties(tag),
  form_norm text not null,
  term_norm text not null,
  primary key (variety, form_norm, term_norm)
);

create table sense_links (
  from_sense text not null references senses(key),
  to_sense   text not null references senses(key),
  kind       text not null check (kind in ('equivalent', 'near_equivalent')),
  primary key (from_sense, to_sense)
);

create table audio_assets (
  id          uuid primary key default gen_random_uuid(),
  owner_key   text not null,
  variety     text not null references varieties(tag),
  voice       text not null,
  ipa         text,
  engine      text not null,
  s3_key      text not null,
  duration_ms integer,
  created_at  timestamptz not null default now(),
  unique (owner_key, variety, voice)
);

-- Learners and usage (tenant-scoped) -------------------------------------------

create table users (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         text not null references tenants(id),
  external_id       text not null,          -- for tenant ocw: the LMS persons.person_id
  native_language   text,                   -- synced from the LMS
  native_region     text,                   -- synced from the LMS
  level             text check (level in ('A1', 'A2', 'B1', 'B2', 'C1', 'C2')),
  role              text,
  is_active         boolean,
  is_minor          boolean,
  variety           text references varieties(tag),  -- the student's own choice; null = derived
  profile_synced_at timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  deleted_at        timestamptz,
  unique (tenant_id, external_id)
);
create trigger users_updated_at before update on users
  for each row execute function public.set_updated_at();

create table lookups (
  id          bigint generated always as identity,
  created_at  timestamptz not null default now(),
  tenant_id   text not null references tenants(id),
  user_id     uuid,
  meeting_id  text,
  query       text not null,
  direction   text,
  entry_key   text,
  sense_key   text,
  had_context boolean not null default false,
  result      text not null check (result in ('hit', 'miss', 'provisional', 'invalid')),
  latency_ms  integer,
  primary key (id, created_at)
) partition by range (created_at);
create table lookups_default partition of lookups default;
create index lookups_user    on lookups (user_id, created_at desc);
create index lookups_meeting on lookups (meeting_id, created_at desc) where meeting_id is not null;

-- Monthly partitions, created ahead of time; a periodic job keeps adding them and
-- drops those older than 12 months (NF-7).
do $$
declare
  first_month date := date_trunc('month', now())::date;
  m date;
begin
  for i in 0..6 loop
    m := (first_month + make_interval(months => i))::date;
    execute 'create table ' || quote_ident('lookups_' || to_char(m, 'YYYY_MM'))
         || ' partition of lookups for values from (' || quote_literal(m::text)
         || ') to (' || quote_literal(((m + interval '1 month')::date)::text) || ')';
  end loop;
end
$$;

create table saved_items (
  tenant_id  text not null references tenants(id),
  user_id    uuid not null references users(id) on delete cascade,
  sense_key  text not null references senses(key),
  meeting_id text,
  created_at timestamptz not null default now(),
  primary key (user_id, sense_key)
);

create table review_cards (
  tenant_id text not null references tenants(id),
  user_id   uuid not null references users(id) on delete cascade,
  sense_key text not null references senses(key),
  card_type text not null check (card_type in ('l1_to_en', 'en_to_meaning')),
  fsrs      jsonb not null,
  due_at    timestamptz not null,
  primary key (user_id, sense_key, card_type)
);
create index review_due on review_cards (user_id, due_at);

create table review_logs (
  id             bigint generated always as identity primary key,
  tenant_id      text not null references tenants(id),
  user_id        uuid not null references users(id) on delete cascade,
  sense_key      text not null,
  card_type      text not null,
  rating         smallint not null check (rating between 1 and 4),
  reviewed_at    timestamptz not null default now(),
  elapsed_days   numeric,
  scheduled_days numeric
);

create table pushes (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  text not null references tenants(id),
  meeting_id text not null,
  target_key text not null,
  by_user    uuid references users(id) on delete set null,
  created_at timestamptz not null default now()
);
create index pushes_meeting on pushes (meeting_id, created_at desc);

create table content_flags (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  text not null references tenants(id),
  target_key text not null,
  action     text not null check (action in ('hide', 'flag')),
  reason     text not null,
  by_user    uuid references users(id) on delete set null,
  meeting_id text,
  created_at timestamptz not null default now(),
  undone_at  timestamptz
);
create index content_flags_active on content_flags (target_key) where undone_at is null;

create table reports (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  text not null references tenants(id),
  reporter   uuid references users(id) on delete set null,
  target_key text not null,
  kind       text not null check (kind in ('wrong_translation', 'bad_example', 'wrong_meaning', 'offensive', 'other')),
  comment    text,
  status     text not null default 'open' check (status in ('open', 'valid', 'invalid', 'fixed')),
  triage     jsonb,
  created_at timestamptz not null default now()
);

create table misses (
  tenant_id  text not null references tenants(id),
  language   text not null references languages(code),
  query_norm text not null,
  hits       integer not null default 1,
  status     text not null default 'pending'
               check (status in ('pending', 'invalid', 'provisional', 'queued', 'published')),
  entry_key  text,
  first_seen timestamptz not null default now(),
  last_seen  timestamptz not null default now(),
  primary key (tenant_id, language, query_norm)
);

-- Factory and seeds -------------------------------------------------------------

create table seed_candidates (
  id         uuid primary key default gen_random_uuid(),
  lemma      text not null,
  lemma_norm text not null,
  pos        text,
  kind       entry_kind,
  sources    text[] not null,
  levels     text[] not null default '{}',
  zipf       numeric(3, 2),
  tier       smallint,
  decision   text check (decision in ('accept', 'reject', 'merge')),
  reason     text,
  entry_id   uuid references entries(id),
  unique nulls not distinct (lemma_norm, pos, kind)
);

create table factory_batches (
  id             uuid primary key default gen_random_uuid(),
  requested_size smallint not null,
  entry_ids      uuid[] not null,
  status         text not null default 'leased' check (status in ('leased', 'submitted', 'expired')),
  run_label      text,
  leased_at      timestamptz not null default now(),
  submitted_at   timestamptz,
  results        jsonb
);

create table api_clients (
  id                 uuid primary key default gen_random_uuid(),
  tenant_id          text not null references tenants(id),
  name               text not null,
  scopes             text[] not null
                       check (cardinality(scopes) > 0 and scopes <@ array['factory', 'admin', 'lms', 'app']),
  token_hash         bytea not null unique,
  rate_limit_per_min integer not null default 600,
  created_at         timestamptz not null default now(),
  revoked_at         timestamptz
);

-- AI ----------------------------------------------------------------------------

create table ai_cache (
  task       text not null,
  input_hash bytea not null,
  output     jsonb not null,
  provider   text not null,
  model      text not null,
  created_at timestamptz not null default now(),
  expires_at timestamptz,
  primary key (task, input_hash)
);

create table llm_calls (
  id             bigint generated always as identity primary key,
  created_at     timestamptz not null default now(),
  tenant_id      text references tenants(id),  -- null for factory and system calls
  task           text not null,
  provider       text not null,
  model          text not null,
  prompt_version text not null,
  in_tokens      integer,
  out_tokens     integer,
  cost_usd       numeric(10, 6) not null default 0,
  latency_ms     integer,
  outcome        text not null check (outcome in ('ok', 'fallback', 'error', 'timeout', 'refused')),
  user_id        uuid,
  ref_key        text
);
create index llm_calls_time on llm_calls (created_at);

-- Supabase grants new public objects to its API roles by default. The Data API is
-- off for this project, but nothing here should ever be reachable through it.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on all tables in schema public from anon, authenticated';
    execute 'revoke all on all sequences in schema public from anon, authenticated';
    execute 'revoke all on all functions in schema public from anon, authenticated';
    execute 'alter default privileges in schema public revoke all on tables from anon, authenticated';
    execute 'alter default privileges in schema public revoke all on sequences from anon, authenticated';
    execute 'alter default privileges in schema public revoke all on functions from anon, authenticated';
  end if;
end
$$;
"""

DOWNGRADE = """
drop table if exists llm_calls, ai_cache, api_clients, factory_batches, seed_candidates, misses,
  reports, content_flags, pushes, review_logs, review_cards, saved_items, lookups, users,
  audio_assets, sense_links, l1_forms, equivalents, senses, expression_patterns, entry_forms;
alter table if exists entries drop constraint if exists entries_current_revision_fk;
drop table if exists entry_revisions, entries, varieties, languages, tenants;
drop type if exists entry_kind, entry_status;
drop function if exists public.set_updated_at();
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
