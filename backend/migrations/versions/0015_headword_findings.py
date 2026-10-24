"""Audit findings can propose a better headword (the policy of 2026-10-24 on categories and
headwords): type `headword`, for one that ends in an object slot (*drop someone*) or spells a
slot another way (*somebody*). Applying one retires the key and adds the mended headword.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TYPES = ("'cefr', 'meaning', 'definition', 'example', 'spanish', 'content', 'part_of_speech',"
         " 'kind', 'duplicate', 'remove'")  # fmt: skip

UPGRADE = f"""
alter table audit_findings drop constraint audit_findings_type_check;
alter table audit_findings add constraint audit_findings_type_check
  check (type in ({TYPES}, 'headword'));
"""

DOWNGRADE = f"""
delete from audit_findings where type = 'headword';
alter table audit_findings drop constraint audit_findings_type_check;
alter table audit_findings add constraint audit_findings_type_check check (type in ({TYPES}));
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
