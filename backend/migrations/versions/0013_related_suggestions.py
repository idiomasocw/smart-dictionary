"""Related words become suggestions, one expression has one entry, and seeds find their entries.

- `word_requests.origin` can be `related`: a word an entry links to (a synonym, a see-also)
  that the dictionary lacks. `word_request_links` holds which entries link to it, and how.
- `expression_of(lemma_norm)` is a multiword headword without its leading article, while two
  or more words remain: *the last straw* and *last straw* are one expression, *a few* stays
  itself. An index on entries finds an expression's entries under any part of speech or kind.
- Seeds whose entry existed before `seeds queue` reached them (a Beta AI wrote for a search, a
  fixture) were left unlinked, so 0012 took their entries for words nobody chose and sent them
  to review. Each is linked to its entry now, the entry is approved, and a review still
  pending for it is closed as approved.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = r"""
alter table word_requests drop constraint word_requests_origin_check;
alter table word_requests add constraint word_requests_origin_check
  check (origin in ('student', 'ai_entry', 'related'));

create table word_request_links (
  request_id uuid not null references word_requests(id) on delete cascade,
  entry_key  text not null,
  type       text not null,
  created_at timestamptz not null default now(),
  primary key (request_id, entry_key, type)
);

create function expression_of(lemma_norm text) returns text
  language sql immutable parallel safe set search_path = ''
  as $$ select pg_catalog.regexp_replace(lemma_norm, '^(a|an|the) (?=\S+ \S)', '') $$;
create index entries_expression on entries (expression_of(lemma_norm)) where language = 'en';

create temporary table adopted as
select c.id as candidate_id, e.id as entry_id
from seed_candidates c
join entries e on e.language = 'en' and e.lemma_norm = c.lemma_norm and e.pos = c.pos
              and e.kind = c.kind and e.homograph = c.homograph and e.status <> 'retired'
where c.entry_id is null and coalesce(c.decision, 'accept') = 'accept';

update seed_candidates c set entry_id = a.entry_id, decision = 'accept', updated_at = now()
from adopted a where c.id = a.candidate_id;
update entries e set approved_at = now(), approved_by = 'seeds', updated_at = now()
from adopted a where e.id = a.entry_id and e.approved_at is null;
update word_requests r
set status = 'approved', decided_at = now(), decided_by = 'seeds',
    note = coalesce(r.note, 'seeded: the seed had not found its entry')
from adopted a join entries e on e.id = a.entry_id
where r.status = 'pending' and r.entry_key = e.key;
drop table adopted;
"""

# The seeds linked on the way up stay linked, and their entries approved: they were the
# owner's choice all along.
DOWNGRADE = """
delete from word_requests where origin = 'related';
drop index entries_expression;
drop function expression_of(text);
drop table word_request_links;
alter table word_requests drop constraint word_requests_origin_check;
alter table word_requests add constraint word_requests_origin_check
  check (origin in ('student', 'ai_entry'));
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
