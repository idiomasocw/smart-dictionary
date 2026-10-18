"""Seed candidates carry what the factory needs from the seed file, and allow homographs.

The owner's seed file adds variants (UK spellings and optional parts), usage patterns,
review flags and the original list lines; they stay with the candidate until it is
queued as an entry. Uniqueness now includes a homograph number, as it does for entries,
so Pole and pole can both be noun candidates. Downgrading drops the extra homographs,
which the old uniqueness rule can't hold.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
alter table seed_candidates
  add column homograph  smallint    not null default 1 check (homograph >= 1),
  add column variants   jsonb       not null default '[]'::jsonb,  -- [{"form": "colour", "regions": ["UK"]}]
  add column patterns   text[]      not null default '{}',
  add column flags      text[]      not null default '{}',         -- notes for the curation run
  add column originals  text[]      not null default '{}',         -- the list lines it came from
  add column created_at timestamptz not null default now(),
  add column updated_at timestamptz not null default now();
alter table seed_candidates drop constraint seed_candidates_lemma_norm_pos_kind_key;
alter table seed_candidates add constraint seed_candidates_identity
  unique nulls not distinct (lemma_norm, pos, kind, homograph);
create index seed_candidates_order on seed_candidates (tier, zipf desc nulls last)
  where decision is null;
"""

DOWNGRADE = """
drop index if exists seed_candidates_order;
alter table seed_candidates drop constraint if exists seed_candidates_identity;
delete from seed_candidates where homograph > 1;
alter table seed_candidates add constraint seed_candidates_lemma_norm_pos_kind_key
  unique nulls not distinct (lemma_norm, pos, kind);
alter table seed_candidates
  drop column homograph, drop column variants, drop column patterns, drop column flags,
  drop column originals, drop column created_at, drop column updated_at;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
