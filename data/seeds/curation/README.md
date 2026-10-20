# Curation files

Decisions on seeds and entries that the rules couldn't make, written by a model, reviewed by the owner, and applied by `smart-dictionary seeds curate --file <file> [--dry-run]` (design §6.1 step 7, `backend/src/smart_dictionary/seeds/curate.py`). Each file is the record of what was decided and why: one row per seed or entry, with a confidence and a reason.

## The files of 2026-10-20

Five Opus 5.5 judges worked to one brief, each on a share of the rows; Claude reviewed their files before they were written here.

**`2026-10-20-seeds.csv`:** the 724 seeds that never became entries because they had no part of speech or kind (599 multiword items our Wiktionary copy lacks, 118 single words, 7 clashes).
- **654 accepted:** 539 words (mostly compound nouns, such as *farmer's market* or *bubble gum*), 61 phrases, 47 idioms and 2 phrasal verbs.
- **70 declined:**
  - plain inflections of words the dictionary has (*besieged*, *tucked*);
  - wordings or spellings of another entry (*nip it in the bud* beside *nip something in the bud*);
  - free combinations (*two weeks*).

  Each reason names the entry that covers it. The 20 declined spellings that learners search for (*TV show*, *skin care*, *pyjama*) name it in `variant_of` instead: they join that seed's variants, so the factory lists them and search finds the entry.
- **The review changed 5 rows:** *armaments*, *braised*, *declassified*, *embroiled* and *best-loved* had been declined as forms of words the dictionary doesn't have, which would have lost them. They're accepted as written.

**`2026-10-20-multiword-verbs.csv`:** the 176 multiword verbs stored as plain words. A plain word can't have a pattern, so the app couldn't match *hits the ceiling* in a text.
- **126 change kind:** 62 idioms (*hit the ceiling*, *call it quits*), 61 phrases (*take turns*, *get dressed*), 2 phrasal verbs (*glory in*, *pertain to*), and *tai chi* and *stone wall*, which are nouns.
- **30 retire:** verb readings of compound nouns that nobody uses (*video game*, *pipe bomb*), each covered by its noun.
- **19 keep:** real compound verbs (*roller-skate*, *test drive*, *ad-lib*).
- **One waits for you:** *talk shop* as an idiom would be a twin of the noun entry `en:talk_shop:n`. Retire one of them in the console, then run the file again.

## Reviewing a file

Read the `low` rows first (25 in the seeds, 1 in the verbs), then skim the `medium` ones. To change a decision, edit its row: `decision`, `pos` and `kind`. The command refuses a file whose part of speech and kind can't make an entry, so a mistyped row changes nothing.

## Applying it

From `backend/`, pointed at the database as `infra/README.md` describes:

```bash
uv run smart-dictionary seeds curate --file ../data/seeds/curation/2026-10-20-seeds.csv --dry-run
uv run smart-dictionary seeds curate --file ../data/seeds/curation/2026-10-20-seeds.csv
uv run smart-dictionary seeds curate --file ../data/seeds/curation/2026-10-20-multiword-verbs.csv --dry-run
uv run smart-dictionary seeds curate --file ../data/seeds/curation/2026-10-20-multiword-verbs.csv
uv run smart-dictionary seeds queue --dry-run
uv run smart-dictionary seeds queue
```

- **Seeds:** only seeds still waiting change. `seeds queue` then queues the accepted ones, and links any whose word has an entry already.
- **Unwritten entries:** a change retires the key (keys are never reused) and queues the right one in the same place in the queue, with its seed, its approval and the searches that pointed at it.
- **Written entries** aren't touched: each decision becomes an audit finding in the console's Audit page, for you to apply.
- **What it prints:** every change, anything skipped and why, and what still waits that the file doesn't cover. Running a file twice changes nothing more.
