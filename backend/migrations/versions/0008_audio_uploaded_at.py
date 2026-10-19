"""Audio rows record when their file reached the bucket, so cards link only files that exist.

The audio job writes each file to an outbox, and an uploader on the host sends it to S3
(design §10.4). A row is recorded as soon as its file is written; `uploaded_at` is set
once the uploader confirms the upload.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
alter table audio_assets add column uploaded_at timestamptz;
create index audio_assets_waiting on audio_assets (created_at) where uploaded_at is null;
-- A card reads its entry's files by the entry key: the owner key up to its '#'.
create index audio_assets_entry on audio_assets (split_part(owner_key, '#', 1));
"""

DOWNGRADE = """
drop index audio_assets_entry;
drop index audio_assets_waiting;
alter table audio_assets drop column uploaded_at;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
