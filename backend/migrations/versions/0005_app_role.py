"""A least-privilege role for the API: it reads and writes rows, never the schema.

The API and the worker connect as dictionary_app; migrations keep running as the owner
(design §12, integration §4). The role is created without a login, so this revision holds
no secret: `smart-dictionary db app-role` gives it a password. Roles belong to the whole
server, so downgrading revokes this database's grants and leaves the role in place for any
other database that uses it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
do $$
begin
  if not exists (select from pg_roles where rolname = 'dictionary_app') then
    create role dictionary_app nologin;
  end if;
end
$$;
grant usage on schema public, extensions to dictionary_app;
grant select, insert, update, delete on all tables in schema public to dictionary_app;
grant usage, select on all sequences in schema public to dictionary_app;
-- Tables and sequences that later revisions create get the same grants.
alter default privileges in schema public
  grant select, insert, update, delete on tables to dictionary_app;
alter default privileges in schema public
  grant usage, select on sequences to dictionary_app;
"""

DOWNGRADE = """
alter default privileges in schema public
  revoke usage, select on sequences from dictionary_app;
alter default privileges in schema public
  revoke select, insert, update, delete on tables from dictionary_app;
revoke all on all sequences in schema public from dictionary_app;
revoke all on all tables in schema public from dictionary_app;
revoke usage on schema public, extensions from dictionary_app;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
