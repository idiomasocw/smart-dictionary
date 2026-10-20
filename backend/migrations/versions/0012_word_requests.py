"""Only words the owner chose reach students; a real word nobody chose can be requested.

- `entries.approved_at` and `approved_by` mark the words the owner chose: everything seeded
  (the curated lists through `seeds queue`, fixtures) and, from now on, what the owner
  approves in the console. Students see an entry only once it's approved, and the routine
  writes only approved ones.
- The Beta entries AI wrote before this for words nobody seeded stay unapproved: hidden, and
  each waits in the review queue as a request of origin `ai_entry`. Searches that pointed at
  one become `requestable`.
- `word_requests` holds one request per headword, and `word_request_votes` one row per
  student who asked, with the search they made. No names: a vote holds the user's ID.
- `misses` can be `requestable`: a Spanish search whose English word nobody seeded, with that
  word in `headword` and its would-be key in `entry_key`.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
alter table entries add column approved_at timestamptz, add column approved_by text;
update entries e set approved_at = e.created_at, approved_by = 'seeds'
where not ('provisional' = any(e.sources)
           and not exists (select from seed_candidates c where c.entry_id = e.id));

create table word_requests (
  id             uuid primary key default gen_random_uuid(),
  lemma          text not null,
  lemma_norm     text not null unique,
  pos            text,
  kind           entry_kind,
  origin         text not null check (origin in ('student', 'ai_entry')),
  status         text not null default 'pending'
                   check (status in ('pending', 'approved', 'rejected')),
  entry_key      text,
  students       integer not null default 0,
  first_asked_at timestamptz not null default now(),
  last_asked_at  timestamptz not null default now(),
  decided_at     timestamptz,
  decided_by     text,
  note           text
);
create index word_requests_queue on word_requests (status, students desc, last_asked_at desc);

create table word_request_votes (
  request_id uuid not null references word_requests(id) on delete cascade,
  user_id    uuid not null references users(id),
  language   text not null references languages(code),
  query      text not null,
  created_at timestamptz not null default now(),
  primary key (request_id, user_id)
);
create index word_request_votes_user on word_request_votes (user_id, created_at);

insert into word_requests (lemma, lemma_norm, pos, kind, origin, entry_key, first_asked_at,
                           last_asked_at)
select distinct on (e.lemma_norm) e.lemma, e.lemma_norm, e.pos, e.kind, 'ai_entry', e.key,
       e.created_at, e.created_at
from entries e
where e.approved_at is null and e.status <> 'retired'
order by e.lemma_norm, e.created_at;

alter table misses drop constraint misses_status_check;
alter table misses add constraint misses_status_check
  check (status in ('pending', 'invalid', 'provisional', 'queued', 'published', 'requestable'));
alter table misses add column headword text;
update misses m set status = 'requestable', headword = e.lemma
from entries e
where e.key = m.entry_key and e.approved_at is null;
"""

DOWNGRADE = """
update misses set status = 'pending', entry_key = null where status = 'requestable';
alter table misses drop column headword;
alter table misses drop constraint misses_status_check;
alter table misses add constraint misses_status_check
  check (status in ('pending', 'invalid', 'provisional', 'queued', 'published'));
drop table word_request_votes, word_requests;
alter table entries drop column approved_by, drop column approved_at;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
