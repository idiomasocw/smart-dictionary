"""The API's role bypasses row-level security, which hosted Supabase turns on for every table.

Supabase's `ensure_rls` event trigger enables RLS on each table created in public. With no
policies, a role other than the owner reads nothing and can't write, so the first production
lookup failed inserting into lookups. RLS here only guards Supabase's Data API roles (anon,
authenticated), which this project doesn't use; the API enforces tenancy itself. So
dictionary_app bypasses RLS, as the owner does, on today's tables and every later one. It
still can't change the schema (revision 0005).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = "alter role dictionary_app bypassrls;"

DOWNGRADE = "alter role dictionary_app nobypassrls;"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
