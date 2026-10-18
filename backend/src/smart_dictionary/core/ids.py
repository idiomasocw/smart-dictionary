"""Row IDs are UUIDv7: time-ordered, so new rows land at the end of their indexes.

They are generated here rather than in the database because the hosted project
runs PostgreSQL 17, which has no built-in uuidv7(). The columns keep a
gen_random_uuid() default only as a safety net.
"""

import uuid

import uuid_utils


def new_id() -> uuid.UUID:
    return uuid.UUID(str(uuid_utils.uuid7()))
