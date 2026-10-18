"""Spelling variants in ref_wiktionary: variant_of names the main spelling.

Wiktionary files many US spellings and alternative forms (catalog, favorite, okay,
healthcare) as pointers to a main entry (catalogue, favourite, OK, health care). The
importer keeps those entries, with their own IPA, forms and tags, and records the
normalized main spellings here so the seed builder can join to the full entry.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
alter table ref_wiktionary add column variant_of text[] not null default '{}';
-- The variants of a main spelling: where variant_of @> '{catalogue}'.
create index ref_wiktionary_variant_of on ref_wiktionary using gin (variant_of);
"""

DOWNGRADE = """
drop index if exists ref_wiktionary_variant_of;
alter table ref_wiktionary drop column if exists variant_of;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
