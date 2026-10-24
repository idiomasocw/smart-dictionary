"""A retired entry that lives on as a pattern of another remembers it (the policy of 2026-10-24
on categories and headwords): *comply with* retired as "comply (with sth)" of `en:comply:v`
keeps `pattern_of = 'en:comply:v'`, so a search for *comply with* finds *comply* (LK-10).

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
alter table entries add column pattern_of text;
comment on column entries.pattern_of is
  'On a retired entry: the key of the entry that carries it as a pattern, where its searches go';
create index entries_pattern_of on entries (lemma_norm) where pattern_of is not null;
"""

DOWNGRADE = """
drop index if exists entries_pattern_of;
alter table entries drop column pattern_of;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
