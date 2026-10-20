"""The audit routine (design §6.6), and the owner's check of what was approved at once.

A second routine re-reads entries in batches and records what it finds, for the owner to apply
or reject:

- `entries.audited_at` and `audit_version` keep the audit's place: the server leases the
  entries due next (never audited, audited under older instructions, or long ago), so the
  routine needs no memory of its own and a crashed run loses nothing.
- `audit_batches` holds each lease (three hours, like the factory's), with the checks the
  server's rules raised for each entry, so a submission is matched to what was asked.
- `api_clients` takes the scope `audit`: the routine's token can lease entries for an audit
  and submit findings, nothing else.
- `audit_findings` holds each finding: its type, the key it's about, the evidence, the fix it
  proposes, and its decision. The auditor never edits an entry: an applied finding goes
  through the paths that exist (a repair the factory rewrites, a word request, a retirement).

Words approved at once (word_requests.auto_approve) are verified afterwards:
- `word_requests.confirmed_at` and `confirmed_by`: the owner checked a word approved at once,
  by the lists' rule or chosen by AI through a link, and keeps it.
- `word_request_links.target_key`: the key each link named, whose part of speech hints at the
  word's (en:put_up_with:phrv: a phrasal verb).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
alter table api_clients drop constraint api_clients_scopes_check;
alter table api_clients add constraint api_clients_scopes_check
  check (cardinality(scopes) > 0 and scopes <@ array['factory', 'admin', 'lms', 'app', 'audit']);
alter table entries add column audited_at timestamptz, add column audit_version text;
alter table word_requests add column confirmed_at timestamptz, add column confirmed_by text;
alter table word_request_links add column target_key text;

create table audit_batches (
  id            uuid primary key default gen_random_uuid(),
  client        text not null,
  run_label     text,
  audit_version text,
  entry_ids     uuid[] not null,
  rules         jsonb not null default '{}',
  status        text not null default 'leased'
                  check (status in ('leased', 'submitted', 'expired')),
  leased_at     timestamptz not null default now(),
  expires_at    timestamptz not null,
  submitted_at  timestamptz,
  model         text,
  results       jsonb
);
create index audit_batches_leased on audit_batches (expires_at) where status = 'leased';

create table audit_findings (
  id         uuid primary key default gen_random_uuid(),
  batch_id   uuid references audit_batches(id),
  entry_id   uuid not null references entries(id),
  entry_key  text not null,
  target_key text not null,
  type       text not null check (type in ('cefr', 'meaning', 'definition', 'example',
                                           'spanish', 'content', 'part_of_speech', 'kind',
                                           'duplicate', 'remove')),
  source     text not null check (source in ('rules', 'ai')),
  confidence text not null check (confidence in ('high', 'medium', 'low')),
  problem    text not null,
  evidence   jsonb not null default '{}',
  proposed   jsonb not null default '{}',
  status     text not null default 'open'
               check (status in ('open', 'applied', 'rejected', 'dismissed')),
  created_at timestamptz not null default now(),
  decided_at timestamptz,
  decided_by text,
  note       text
);
create index audit_findings_queue on audit_findings (status, created_at desc);
create index audit_findings_entry on audit_findings (entry_id, status);
"""

DOWNGRADE = """
drop table audit_findings, audit_batches;
alter table word_request_links drop column target_key;
alter table word_requests drop column confirmed_at, drop column confirmed_by;
alter table entries drop column audited_at, drop column audit_version;
delete from api_clients where 'audit' = any(scopes);
alter table api_clients drop constraint api_clients_scopes_check;
alter table api_clients add constraint api_clients_scopes_check
  check (cardinality(scopes) > 0 and scopes <@ array['factory', 'admin', 'lms', 'app']);
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
