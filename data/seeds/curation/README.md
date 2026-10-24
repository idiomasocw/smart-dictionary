# Curation files

**What they are.** Each CSV here is a batch of decisions the rules couldn't make: one row per seed or entry, with a decision, a confidence and a reason. A model writes the file, you review it, and one command applies it (design §6.1 step 7, `backend/src/smart_dictionary/seeds/curate.py`). The file stays here as the record of what was decided and why.

**When you use one.** Once per file, after the deploy that brings it. The two files of 2026-10-20 need migration 0014; the file of 2026-10-24 needs 0016, and `seeds functions` run first (step 4). Each is a one-time cleanup, to apply now. Later backlogs get files of their own, applied the same way.

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

## The file of 2026-10-24

**`2026-10-24-categories.csv`** brings 1,773 entries in line with the categories policy the owner adopted that day (`docs/policies/categories-and-headwords.md`). Seven Opus 5.5 judges worked to one brief, and Claude reviewed their files before writing this one. It covers what the rules couldn't decide:
- **The 1,145 idioms and phrases Wiktionary gives no single function.** `seeds functions` sets the other 1,437 by itself (step 4).
- **The 179 multiword entries stored as plain words with a function-word part of speech** (*of course*, *a few*, *upside down*): compound word or phrase?
- **The 448 entries that are another entry plus a preposition** (*comply with*, *comply*): an entry of its own, or a pattern of the shorter one?

**What it does:**
- **865 take their function in place:** same key, the part of speech now says how the expression works (*break the ice*: verb; *as long as*: conjunction). A written one gets it too, as a revision.
- **323 change kind, so they get a new key** (one, *by the way* the idiom, merges into the phrase entry that exists):
  - 155 idioms that are literal become phrases (*at first*, *by far*, *in other words*);
  - 94 multiword words dictionaries show as phrases become phrases (*of course*, *pretty much*);
  - 39 become words, because Oxford gives them their own headword (*fed up*, *carte blanche*, *cutting edge*);
  - 21 phrases or words that are figurative become idioms (*like clockwork*, *under wraps*);
  - the rest move between phrasal verb and the others (*jolly someone along* becomes a phrasal verb, *have to* the modal verb).
- **110 retire:**
  - **57 become patterns of a shorter entry** with the same meaning (*comply with* → "comply (with sth)", *run out of* → "run out (of sth)"). A search for the retired headword now finds the shorter entry (migration 0016), and the factory writes the pattern the next time it writes that entry.
  - **7 merge another use into one phrase entry:** *of course* the interjection becomes a sense of *of course* the phrase, and the same for *a few*, *later on*, *no matter*, *too much* and *early on*.
  - **46 are free combinations** no learner's dictionary lists, most from your own idiom lists (*go to the loo*, *feel fit*, *regain one's composure*, *one hundred*, *this evening*), and *doctor saw*, a parsing error in a source list. **Check these first:** they were your choice, and retiring is one-way.
- **475 stay as they are:** real phrasal verbs (*look for*, *account for*, *pay for*), compounds that stay words (*upside down*, *according to*, *next door*), and whole sentences that keep `phrase` (*mum's the word*, *money talks*).
- **14 wait for a mended headword:** their kind change would make a new key with a misworded headword (*open season for*, *rained off*, *shot-gun wedding*). They take their function now and keep their kind (they're counted in the 865); the kind waits for the audit, whose `headword` findings mend the headword first.

**Review:** 98 rows are `low` (82 changes, 16 keeps) and 619 `medium`, among them the 81 medium retires. Filter `decision` = `retire` first, then `confidence` = `low`.

**Left for the audit,** which the judges noticed but a curation file can't fix: misworded headwords (*rarin' to go*, *last minute* for *last-minute*, *eat out of someone's hands*), headwords cut short (*likes of* for *the likes of*, *long time* for *long time no see*), UK spellings against the headword policy (*practise what you preach*), and near-duplicates (*have an itching palm* / *have an itchy palm*, *off guard* / *off one's guard*). The auditor reads the policy now, so they come back as findings for you to apply or reject.

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

**4. Preview, then apply, each file.** `--dry-run` prints every change and changes nothing; read its last lines, then run the same command without it. Before the file of 2026-10-24, let Wiktionary set the functions it can (about 1,437 idioms and phrases, in place):

```bash
uv run smart-dictionary seeds functions --dry-run
uv run smart-dictionary seeds functions
```

Then the files:

```bash
uv run smart-dictionary seeds curate --file ../data/seeds/curation/2026-10-20-seeds.csv --dry-run
uv run smart-dictionary seeds curate --file ../data/seeds/curation/2026-10-20-seeds.csv
uv run smart-dictionary seeds curate --file ../data/seeds/curation/2026-10-20-multiword-verbs.csv --dry-run
uv run smart-dictionary seeds curate --file ../data/seeds/curation/2026-10-20-multiword-verbs.csv
uv run smart-dictionary seeds curate --file ../data/seeds/curation/2026-10-24-categories.csv --dry-run
uv run smart-dictionary seeds curate --file ../data/seeds/curation/2026-10-24-categories.csv
```

**What to expect:**
- **The seeds file:** about `accepted 654, rejected 70`.
- **The verbs file:** about `requeued 126, retired 30, kept 19, skipped 1` (the skip is *talk shop*).
- **The categories file:** about `function 865, kept 475, requeued 322, retired 111` (one more retired than the file's 110: *by the way* the idiom merges into the phrase entry that exists).
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
