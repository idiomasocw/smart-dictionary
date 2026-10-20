"""A missed word remembers its AI attempts, so a failed one stops looking like "generating".

Before, a failed provisional generation left the miss `pending`: every lookup answered
"generating" and started another AI job, so students couldn't tell "being written" from
"failed" (design §6.5). Now:
- `running_since` marks a job at work. Lookups answer "generating" without starting another,
  and the mark expires after a few minutes, so a crashed job can't hold the word.
- `attempts`, `last_attempt_at` and `last_error` record each failure. Lookups answer
  "unavailable" during a cool-down, and after the last attempt the word goes to the factory.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
alter table misses
  add column attempts smallint not null default 0,
  add column running_since timestamptz,
  add column last_attempt_at timestamptz,
  add column last_error text;
"""

DOWNGRADE = """
alter table misses
  drop column last_error,
  drop column last_attempt_at,
  drop column running_since,
  drop column attempts;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
