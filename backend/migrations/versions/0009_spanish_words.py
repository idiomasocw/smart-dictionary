"""Spanish words from Wiktionary's translations: a second lexicon for the equivalents check.

V6 accepts an equivalent's words when the Spanish word lists know them. The frequency list
stops at Zipf 2.35 and UniMorph has gaps, so real words such as ralo, entrecano or
mesabanco were flagged as unknown, and writers dropped natural words to avoid the warning.
English Wiktionary's Spanish translations know them. Only the words are used, folded like
norm() folds Spanish, and only for checking: no translation text is ever served.

A materialized view, so the checker reads an index instead of splitting 38,000 entries'
translations per request. The Wiktionary import refreshes it (importers.run).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
create materialized view ref_spanish_words as
select distinct s.word
from ref_wiktionary w,
     jsonb_array_elements_text(w.translations -> 'es') as t(term),
     regexp_split_to_table(translate(lower(t.term), 'áéíóúüñ', 'aeiouun'), '[^a-z]+') as s(word)
where w.translations ? 'es' and jsonb_typeof(w.translations -> 'es') = 'array'
  and length(s.word) > 1;
create unique index ref_spanish_words_word on ref_spanish_words (word);
"""

DOWNGRADE = "drop materialized view ref_spanish_words;"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE)
