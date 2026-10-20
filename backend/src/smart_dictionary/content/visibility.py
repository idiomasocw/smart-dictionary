"""Which entries students see (design §3.3): approved ones with a published document, until
they're retired.

The pipeline status doesn't decide it. An entry the routine is rewriting (`generating`) or
sent back for repair (`needs_repair`, then `quarantined`) keeps showing the document it last
published, so a Beta entry never vanishes while it's being replaced. An entry the owner
hasn't approved is never shown (content.word_requests).
"""

from typing import LiteralString


def live(table: LiteralString = "e") -> LiteralString:
    """The SQL condition, for the entries table under the alias `table`."""
    return (
        f"{table}.published_at is not null and {table}.approved_at is not null"
        f" and {table}.status <> 'retired'"
    )
