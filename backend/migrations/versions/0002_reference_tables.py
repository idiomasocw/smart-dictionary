"""Reference tables for the open datasets that ground and check the content factory.

Read-only facts loaded by `smart-dictionary data import` (docs/03 §3.4 "Reference
data"). Each row points at its dataset, whose license and attribution are recorded
in ref_datasets. ref_wiktionary.glosses is Wiktionary text (CC BY-SA): it is used
internally for sense hints and the overlap check, and is never served by the API.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
create table ref_datasets (
  id          text primary key,
  name        text not null,
  version     text not null,
  license     text not null,
  attribution text not null,
  source_url  text not null,
  sha256      text,
  row_count   integer not null default 0,
  imported_at timestamptz not null default now()
);

-- Headword lists with levels or ranks: CEFR-J, Octanove, later NGSL and the owner's lists.
create table ref_lists (
  id         bigint generated always as identity primary key,
  dataset_id text not null references ref_datasets(id) on delete cascade,
  lemma      text not null,
  lemma_norm text not null,
  pos        text,             -- mapped to the entries.pos vocabulary; null when unmappable
  pos_raw    text,
  level      text check (level in ('A1', 'A2', 'B1', 'B2', 'C1', 'C2')),
  rank       integer,
  extra      jsonb
);
create index ref_lists_lemma   on ref_lists (lemma_norm);
create index ref_lists_dataset on ref_lists (dataset_id);

-- Zipf frequencies (wordfreq): ranking for English, a lexicon check for Spanish.
create table ref_freq (
  dataset_id text not null references ref_datasets(id) on delete cascade,
  language   text not null,
  word       text not null,     -- normalized with core.text.norm
  zipf       numeric(4, 2) not null,
  primary key (dataset_id, language, word)
);
create index ref_freq_word on ref_freq (language, word);

-- Lemma <-> inflected form (UniMorph): English forms and the Spanish lexicon.
create table ref_forms (
  id         bigint generated always as identity primary key,
  dataset_id text not null references ref_datasets(id) on delete cascade,
  language   text not null,
  lemma      text not null,
  lemma_norm text not null,
  form       text not null,
  form_norm  text not null,
  features   text
);
create index ref_forms_form  on ref_forms (language, form_norm);
create index ref_forms_lemma on ref_forms (language, lemma_norm);

-- Reference pronunciations (CMUdict for en-US, Britfone for en-GB).
create table ref_ipa (
  id         bigint generated always as identity primary key,
  dataset_id text not null references ref_datasets(id) on delete cascade,
  word       text not null,
  word_norm  text not null,
  variety    text not null check (variety in ('en-US', 'en-GB')),
  ipa        text not null,
  raw        text               -- the source notation, e.g. ARPAbet
);
create index ref_ipa_word on ref_ipa (word_norm, variety);

-- Wiktionary facts (kaikki.org extract), filtered to frequent words and multiword entries.
create table ref_wiktionary (
  id           bigint generated always as identity primary key,
  dataset_id   text not null references ref_datasets(id) on delete cascade,
  word         text not null,
  word_norm    text not null,
  pos          text,
  pos_raw      text not null,
  kind         text not null check (kind in ('word', 'phrasal_verb', 'idiom', 'phrase')),
  glosses      text[] not null default '{}',   -- internal only: never served (CC BY-SA)
  translations jsonb not null default '{}'::jsonb,
  ipa          jsonb not null default '{}'::jsonb,
  forms        text[] not null default '{}',
  tags         text[] not null default '{}'
);
create index ref_wiktionary_word on ref_wiktionary (word_norm);
create index ref_wiktionary_kind on ref_wiktionary (kind) where kind <> 'word';

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on ref_datasets, ref_lists, ref_freq, ref_forms, ref_ipa, ref_wiktionary from anon, authenticated';
  end if;
end
$$;
"""

DOWNGRADE = """
drop table if exists ref_wiktionary, ref_ipa, ref_forms, ref_freq, ref_lists, ref_datasets;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
