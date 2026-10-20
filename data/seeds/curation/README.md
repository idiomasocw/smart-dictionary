# Curation files

**What they are.** Each CSV here is a batch of decisions the rules couldn't make: one row per seed or entry, with a decision, a confidence and a reason. A model writes the file, you review it, and one command applies it (design §6.1 step 7, `backend/src/smart_dictionary/seeds/curate.py`). The file stays here as the record of what was decided and why.

**When you use one.** Once per file, after the deploy that brings it (it needs migration 0014). The two files of 2026-10-20 are a one-time cleanup, to apply now. Later backlogs get files of their own, applied the same way.

**Why it matters.** Without them, 724 words you listed never reach the factory, and 176 idioms and phrases stay stored as plain verbs, which the app can't match in a text (*hits the ceiling*).

## The files of 2026-10-20

Five Opus 5.5 judges worked to one brief, each on a share of the rows, and Claude reviewed their files before they were written here.

**`2026-10-20-seeds.csv`:** the 724 seeds that never became entries because they had no part of speech or kind (599 multiword items our Wiktionary copy lacks, 118 single words, 7 clashes).
- **654 accepted:** 539 words (mostly compound nouns, such as *farmer's market* or *bubble gum*), 61 phrases, 47 idioms and 2 phrasal verbs.
- **70 declined:** plain inflections of words the dictionary has (*besieged*, *tucked*), other wordings or spellings of an entry (*nip it in the bud* beside *nip something in the bud*), and free combinations (*two weeks*). Each reason names the entry that covers it.
- **20 of the declined become spellings of the entry they name** (`variant_of`): *TV show*, *skin care*, *pyjama*. The factory lists them in that entry, so a search for *TV show* finds *television show*.
- **The review changed 5 rows:** *armaments*, *braised*, *declassified*, *embroiled* and *best-loved* had been declined as forms of words the dictionary doesn't have, which would have lost them. They're accepted as written.

**`2026-10-20-multiword-verbs.csv`:** the 176 multiword verbs stored as plain words.
- **126 change kind:** 62 idioms (*hit the ceiling*, *call it quits*), 61 phrases (*take turns*, *get dressed*), 2 phrasal verbs (*glory in*, *pertain to*), and *tai chi* and *stone wall*, which are nouns.
- **30 retire:** verb readings of compound nouns that nobody uses (*video game*, *pipe bomb*), each covered by its noun.
- **19 keep:** real compound verbs (*roller-skate*, *test drive*, *ad-lib*).
- **One waits for you:** *talk shop* as an idiom would be a twin of the noun entry `en:talk_shop:n`. Retire one of the two in the console, then run the file again.

## How to use them

**1. Review.** Open each file in Excel or Google Sheets and filter the `confidence` column. Read the `low` rows (25 in the seeds, 1 in the verbs), then skim the `medium` ones. To change a decision, edit its `decision`, `pos` and `kind`, and save it as CSV (UTF-8). The command refuses a file with a pair that can't make an entry (a word marked `phrase`), so a mistyped row changes nothing.

Review before you apply, because applying is one-way. A retired key is never reused, and an accepted seed stops waiting, so running the file again doesn't change it back. To undo one word afterwards, retire it in the console.

**2. Get the latest code.** The command runs on your PC, so pull first:

```bash
git pull
```

**3. Point the shell at production.** In Git Bash, from `backend/`. This asks for the owner's session-pooler URL without showing it or saving it in your history, as in `infra/README.md`:

```bash
read -rsp "Owner DATABASE_URL (session pooler): " DATABASE_URL && echo && export DATABASE_URL
```

**4. Preview, then apply, each file.** `--dry-run` prints every change and changes nothing; read its last lines, then run the same command without it:

```bash
uv run smart-dictionary seeds curate --file ../data/seeds/curation/2026-10-20-seeds.csv --dry-run
uv run smart-dictionary seeds curate --file ../data/seeds/curation/2026-10-20-seeds.csv
uv run smart-dictionary seeds curate --file ../data/seeds/curation/2026-10-20-multiword-verbs.csv --dry-run
uv run smart-dictionary seeds curate --file ../data/seeds/curation/2026-10-20-multiword-verbs.csv
```

**What to expect:**
- **The seeds file:** about `accepted 654, rejected 70`.
- **The verbs file:** about `requeued 126, retired 30, kept 19, skipped 1` (the skip is *talk shop*).
- **An entry the factory wrote already** isn't changed. It prints "written already: a kind finding waits in the console", and you apply it on the Audit page.
- **A different count** means production changed since the files were made. Every line says what happened to each word.

**5. Queue the accepted seeds.** This turns them into entries for the factory. Any whose word already has an entry is linked to it instead:

```bash
uv run smart-dictionary seeds queue --dry-run
uv run smart-dictionary seeds queue
```

**6. Disconnect,** so later commands use your local database again:

```bash
unset DATABASE_URL
```
