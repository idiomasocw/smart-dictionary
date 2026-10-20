"""The admin API's tables: the owner's setting overrides and the audit of every change.

- `app_settings` holds an override per setting the admin API may change (core.tunables); a
  setting without a row uses the environment's value.
- `admin_audit` records each change: which token made it, the person the LMS says was signed
  in, and the value before and after. The API's role can only add to it.
- The API's role loses `insert`, `update` and `delete` on `api_clients`. Tokens come from the
  CLI, run as the owner, so no request, even one that got past the code, can mint or widen a
  token.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
create table app_settings (
  name       text primary key,
  value      jsonb not null,
  updated_at timestamptz not null default now(),
  updated_by text not null
);

create table admin_audit (
  id         bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  client_id  uuid not null references api_clients(id),
  client     text not null,
  actor      text,
  action     text not null,
  target     text not null,
  before     jsonb,
  after      jsonb
);

revoke update, delete on admin_audit from dictionary_app;
revoke insert, update, delete on api_clients from dictionary_app;
"""

DOWNGRADE = """
grant insert, update, delete on api_clients to dictionary_app;
drop table admin_audit, app_settings;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
