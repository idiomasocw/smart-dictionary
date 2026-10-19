"""Entries remember the status they were leased from, so an expired lease can return them.

A leased entry is `generating` (design §3.3). If its batch is never submitted, the next
lease puts it back where it was: `queued`, `needs_repair` or `provisional`, keeping repairs
and provisional entries ahead of new ones.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = "alter table entries add column leased_from entry_status;"

DOWNGRADE = "alter table entries drop column leased_from;"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
