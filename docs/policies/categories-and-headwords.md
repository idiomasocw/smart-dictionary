# Entry categories and headwords: policy

**Status:** adopted by the owner on 2026-10-24. Its rules are in `factory/STYLE.md` (§9, §10, §11, §14) and `docs/03-system-design.md` (§3.5, §3.6, §6.6, §6.7); the code enforces them (`core/headwords.py`, `core/entrydoc.valid_pos`), and the audit raises what slipped in before.

## 1. What professional dictionaries do

Read on 2026-10-24, live, except where marked:
- **Collins:** its site blocks automated reading, so its evidence comes from Wayback Machine captures (2016–2023).
- **Macmillan:** it closed in June 2023, so its evidence comes from captures (2014–2018).
- **WordReference:** read through a browser.

| Item | Cambridge | Oxford Learner's | Longman | Merriam-Webster | Wiktionary | English Vocabulary Profile |
|---|---|---|---|---|---|---|
| *as long as* | phrase, "as/so long as" (American dictionary: idiom); its grammar panel calls it a conjunction | idiom "as/so long as", unlabelled, under *long* (adverb) | "as/so long as", unlabelled | conjunction | conjunction, labelled idiomatic | phrase, B1 |
| *the day before yesterday* | phrase | not an entry | not an entry | noun phrase | adverb; "day before yesterday" is a noun | not listed |
| *in front of* | phrase, "in front of someone/something" | preposition (under *front*) | "in front of somebody/something", unlabelled | phrase | preposition | phrase, A2, "in front of sb/sth" |
| *of course* | idiom (learner's dictionary: adverb) | idiom under *course*, unlabelled | adverb | phrase | adverb, interjection | phrase, A1 |
| *look back* / *look back on* | one phrasal verb, *look back*; *on* is a pattern | one phrasal verb, *look back*: "look back (at/on something)" | one phrasal verb; *on/to* is a pattern | "look back" verb; no *look back on* | *look back* only | phrasal verb *look back*, B2 |
| *long for* | phrase "long for something" | a pattern of the verb *long*: "long for somebody/something" | a pattern of the verb *long* | a pattern of the verb *long* | verb, including "to miss (someone)" | phrase, C2, "long for sth" |
| *put up with* | phrasal verb "put up with something/someone" | phrasal verb *put up with*, pattern "…somebody/something" | phrasal verb "put up with somebody/something" | phrase | verb, idiomatic | phrasal verb, B2 |
| *pull someone's leg* | idiom "pull someone's leg" | idiom "pull somebody's leg" | "pull somebody's leg", unlabelled | phrase | verb, idiomatic, "pull someone's leg" | not listed |
| *get rid of* | phrase, two headwords: "…something", "…someone" | idiom "get rid of somebody/something" | "get rid of somebody/something", unlabelled | phrase | verb, idiomatic | phrase, B1 |

Collins COBUILD and Macmillan label *as long as*, *in front of* and *by the way* "phrase", and file *look back on* and *long for* as patterns. WordReference's Random House files idioms under their keyword; its English–Spanish dictionary labels them *expr* or *v expr*. It is the only source with a separate *look back on* entry.

**What follows from it:**
- **There's no official list of parts of speech.** Every dictionary uses the word classes: noun, verb, adjective, adverb, pronoun, determiner, preposition, conjunction, interjection, numeral. Some add article, auxiliary or modal verb, or exclamation (another name for interjection).
  - The Cambridge Grammar of the English Language regroups them for linguists.
  - Universal Dependencies' 17 tags are a standard for software.
  - CEFR-J uses word classes only, giving multiword items their function (*according to*: preposition, *each other*: pronoun).
- **Slang is never a part of speech.** Every dictionary treats it as a register label on a sense, beside informal, formal and offensive: Merriam-Webster's "stylistic status label", Oxford's register labels, Cambridge's "lit adjective slang". Our model already works this way: `registers` on each sense, with `slang` among them (STYLE §9).
- **Multiword items get a category on top of, or instead of, the word class.**
  - The category labels are *phrasal verb*, *phrase* and, in Cambridge only, *idiom*. Cambridge is inconsistent with itself: *of course* is an idiom in one of its dictionaries and an adverb in another. Wiktionary bans "Idiom" as a part of speech and marks idioms with a label.
  - Merriam-Webster, Oxford (*in front of*: preposition), CEFR-J and Wiktionary give the grammatical function instead.
  - Learners need both. The category says what kind of thing it is; the function says how to use it in a sentence.
- **Headwords:**
  - **Slots are spelled differently by each dictionary.** *someone/something* is used by Cambridge, Merriam-Webster, Macmillan and Wiktionary; *somebody/something* by Oxford and Longman.
  - **Phrasal verbs keep their objects out of the searchable headword.** Oxford and Macmillan show them only in patterns ("put up with somebody/something").
  - **Idioms keep a slot that sits inside them** ("pull someone's leg").
  - **Wiktionary's naming policy:** "one's" for actions that refer back to the subject, "someone's" otherwise; a bare infinitive; no initial article unless it changes the meaning.
- **A preposition that only completes the verb is a pattern, not an entry.**
  - **The owner is right about *look back on*.** No major dictionary gives it an entry; all show it as a pattern of *look back*.
  - **A three-part verb gets an entry when the two-part verb can't carry the meaning:** *put up with* (tolerate), *get on with* (continue doing), *fall back on*.
  - ***long for* is a pattern of the verb *long*** in Oxford, Longman, Merriam-Webster, Collins and Macmillan. And yes, you can long for someone: Oxford's example is "Lucy had always longed for a brother".
- **Collocations are never headwords** in the dictionaries. They show them in patterns, examples and collocation boxes. The English Vocabulary Profile, a level list rather than a dictionary, is the exception. Our `collocations` field on each sense is the place (STYLE §11).

## 2. Where our data stands

Measured on the local copy of the seeds on 2026-10-24. Production should be close.

- **Kinds:** 2,376 idioms, 1,453 phrasal verbs, 206 phrases, and 33,954 words, of which 4,841 are multiword compound nouns (*bus stop*).
- **Idioms and phrases lose their function.** The model forces their part of speech to `phrase` (`KIND_POS`), so *as long as* can't say it's a conjunction, or *break the ice* that it's a verb. Wiktionary gives the function for 1,437 of the 2,582 (verb 783, noun 479, adjective 96, adverb 65, preposition 9, interjection 3, conjunction 2). 100 have several functions, and 1,045 none.
- **The same kind of item is stored two ways.** *by the way* is a phrase (part of speech `phrase`), while *of course*, *according to*, *in case*, *each other* and *half a dozen* are plain words with their function as part of speech. There are 179 such multiword plain words (17 interjections, 134 adverbs, 13 prepositions, 5 conjunctions, and others). Some are compounds dictionaries list as headwords (*according to*, *upside down*), which stay words (§3.1); the others are phrases.
- **Object slots are mostly right.** The seed builder keeps headwords bare (*jump on*, pattern "jump on someone"). 517 headwords hold a slot inside the expression, which is right (*pull someone's leg*, *keep one's word*). Among the entries, 3 end with an object slot, which is wrong (*drop someone*, *slate something*, *down to someone*); a few more seeds do, and a few use *somebody*.
- **436 entries pair a word with the same word plus a preposition** (*look* / *look for*, *comply* / *comply with*, *get along* / *get along with*). Some are real phrasal verbs with their own meaning (*look for*, *come by*, *call on*). Others are the base word with its usual preposition, which should be a pattern (*comply with*, *disagree with*, *get along with*). Wiktionary lists 355 of the longer forms as headwords, so it can't decide this.
- **The console's suggestion for *as long as* came from Wiktionary, not from AI.** Wiktionary has it as a conjunction marked idiomatic. Our importer reads "idiomatic" as the kind *idiom*, and the console offers multiword kinds first.

## 3. The policy

### 3.1 Category (`kind`): what the student sees first

| Kind | What it is | Examples |
|---|---|---|
| `word` | One word, or a compound that dictionaries list as its own headword with a word class: compound nouns and adjectives, compound verbs, and compounds such as *upside down* | *house*, *bus stop*, *well-known*, *roller-skate*, *upside down*, *according to* |
| `phrasal_verb` | A verb plus particle(s) whose meaning is the combination's, not the base verb's plus a complement | *look back*, *put up with*, *get on with*, *look for* |
| `idiom` | A figurative expression: its meaning isn't the sum of its words | *break the ice*, *hit the ceiling*, *a piece of cake*, *pull someone's leg* |
| `phrase` | Any other fixed multiword expression that dictionaries show as a phrase: literal set phrases, complex conjunctions, discourse markers, greetings, sayings | *as long as*, *in front of*, *the day before yesterday*, *of course*, *by the way* |

**Compound word or phrase?** The owner asked whether *bus stop* belongs under `word`. It does, and that is the standard practice:
- **Dictionaries don't have a "compound" category.** Cambridge, Oxford Learner's, Longman and Merriam-Webster all give *bus stop* its own headword labelled simply **noun**, like *house*. The same goes for *ice cream*, *well-known* (adjective) and *upside down* (adverb).
- **What a learner sees is the part of speech.** The card says "noun" for both *house* and *bus stop*. `kind` doesn't add a word class; it says which kind of vocabulary item this is, and `word` means "one lexical unit, however it's spelled". Whether it's written solid, hyphenated or with a space (*bookcase*, *well-known*, *bus stop*) is spelling, not category.
- **A separate `compound` kind would cost a lot and give learners nothing:** new keys for about 4,800 entries, changes to the schema, the factory, the console and the plugin, and every compound's audit history to move. It would also force a fuzzy line between *bookcase* and *bus stop*.

So the test is the dictionaries' own: **if Oxford Learner's (or Cambridge) gives the item its own headword with a word class, it's a `word`; if it shows it as a phrase or an idiom under one of its words, it's a `phrase` or an `idiom`.** *according to* has its own headword (preposition), so it's a word; *of course* is shown under *course*, so it's a phrase. Because one expression takes one kind (§3.6 of the design), an expression with entries in several word classes that dictionaries list as headwords (*next door*: adverb and adjective) stays a word, all of it.

**Idiom or phrase?** The test is figurative meaning. A learner who knows every word would misunderstand an idiom, but not a phrase. *as long as* is a phrase, as Cambridge, Collins, Macmillan and the English Vocabulary Profile file it, and its function is conjunction, as Merriam-Webster labels it.

### 3.2 Part of speech (`pos`): its grammatical function, always

- **A word:** its word class.
- **A phrasal verb:** `verb`.
- **An idiom or a phrase:** the word class it works as in a sentence:
  - *as long as*: conjunction
  - *in front of*, *according to*: preposition
  - *the day before yesterday*: adverb
  - *of course*: adverb
  - *break the ice*: verb
  - *a piece of cake*: noun
- **`phrase` as a part of speech** stays only for whole-sentence expressions with no single function: proverbs and social formulas (*every cloud has a silver lining*, *how do you do*).
- **One entry per expression** (design §3.6). It takes its main function. Another use goes in its senses: *of course* is an adverb, and "Of course!" is a sense of the same entry.
- **The card shows both:** "phrase · conjunction", "idiom · verb".

Keys don't change. An idiom's or a phrase's key carries its kind (`en:as_long_as:phr`), not its part of speech.

### 3.3 The headword

1. **The dictionary form:** a verb's base form, a singular noun (plural-only nouns stay plural: *jeans*), no *to* before a verb (*break the ice*, not *to break the ice*) and no *be* before an adjective (*fed up*, not *be fed up*). The preposition *to* stays where it belongs to the phrase (*to date*, *to a fault*), and so does *be* in phrasal verbs and idioms that Oxford Learner's lists with it (*be into*, *be on the ball*).
2. **A leading article stays only when the expression is always said with it, or it changes the meaning:** *the day before yesterday*, *the last straw*. Otherwise it goes, as in Wiktionary's policy. A search with or without it finds the entry either way, because the twin and expression rules treat both forms as one.
3. **No object slot at the end.** The headword is *long for*, *put up with*, *get rid of*, *look forward to*, never with *something* or *someone* after it. The full form is a pattern: "put up with sb/sth".
4. **A slot inside the expression stays,** written as Wiktionary, Cambridge and Merriam-Webster write it, so our checks match Wiktionary's headwords: `someone`, `something`, `someone's`, `one's` (referring back to the subject: *keep one's word*), `oneself`. Never *somebody*, *sb* or *sth* in a headword. Examples: *pull someone's leg*, *keep one's word*, *pull oneself together*. *yourself* stays only in imperative formulas, where it's the actual word said (*make yourself at home*, *help yourself*), as Wiktionary has them. (Patterns keep their own short slots: `sb`, `sth`, `sb's`.)
5. **A preposition that only completes the meaning is a pattern of the shorter entry, not an entry.** *look back* with "look back (on sth)"; *comply* with "comply (with sth)"; *long* with "long for sb/sth". The longer form gets its own entry only when:
   - **its meaning differs:** *get on with*, *put up with*, *look for*; or
   - **the shorter form isn't used alone:** *rely on*, *depend on*.

   When in doubt, Oxford Learner's is the reference.
6. **Collocations are never headwords.** *heavy rain* and *make a decision* belong in the sense's `collocations` and examples, unless the combination is idiomatic.

### 3.4 Labels are not categories

These go on senses, never in `kind` or `pos`:
- **register:** formal, informal, slang, offensive, literary, old-fashioned, technical;
- **region:** UK, US and others;
- **domain;**
- **grammar:** countable or uncountable, transitive or intransitive.

## 4. Bringing the data in line

Built on 2026-10-24; the owner applies the data steps against production after the deploy (`data/seeds/curation/README.md`).

1. **The model:** idioms and phrases take a word class as part of speech (`valid_pos`; `KIND_POS` forces only a phrasal verb's `verb`). Keys stay as they are, so a function changes in place (`curation.set_pos`).
   - Validation, the seed queue, approvals, curation files and the console's approve form all accept the new pairs. The entry schema needed no change: it never tied a part of speech to a kind.
   - STYLE.md §9, §10, §11 and §14, and the audit's brief, teach the rules.
2. **Functions, automatically:** `seeds functions` gives the 1,437 idioms and phrases that Wiktionary gives exactly one function that function, in place, and their waiting seeds too.
3. **The rest, by judgment:** `data/seeds/curation/2026-10-24-categories.csv`, judged by seven Opus 5.5 judges and reviewed, covers 1,773 entries:
   - the 1,145 idioms and phrases with no single function: 863 take their function in place, 211 change kind (between idiom, phrase and word), 45 retire as free combinations, and 26 whole sentences keep `phrase`;
   - the 179 multiword plain words: 99 become phrases or idioms (*of course*, *a few*), 70 stay compound words (*upside down*, *according to*), 10 retire;
   - the 448 preposition pairs: 52 retire as patterns of the shorter entry (*comply with* → "comply (with sth)"), 15 change kind, 2 retire outright, and 379 keep their entry (*look for*, *pay for*).

   Unwritten entries change at once. A written one takes a function in place, as a revision; any other change to it becomes an audit finding for the owner, as before.
4. **Headwords:** the 3 entries that end in an object slot, and any older headword the judges found misworded, reach the owner as the audit's `headword` findings, which mend them under a new key with the slot as a pattern. A kind change on such a headword waits for that (14 rows in the file).
5. **Searches follow a pattern:** a headword retired as a pattern of another entry keeps `pattern_of` (migration 0016), so a search for *comply with* finds *comply*.

## 5. Keeping it in line

**The headword rules** (`backend/src/smart_dictionary/core/headwords.py`) catch a headword that:
- ends in an object slot (*put up with someone*);
- spells a slot another way (*somebody*, *sb*, *sth*);
- starts with *to* before a verb, or *be* before an adjective.

**Where they apply:**
- **The seed builder and curation files mend it:** the headword loses the slot, the slot becomes a pattern ("put up with sb"), and a headword left with one word becomes a plain word (*drop someone*, a phrasal verb, becomes the verb *drop*).
- **The console's Add and Approve refuse it** (422), and the message names the headword to use and its pattern.
- **The factory's style guide teaches the rules** (STYLE §14), so the writer never invents such a headword.

**The audit's free checks** (design §6.6) raise a finding, which the auditor confirms or dismisses before the owner sees it, for:
- a headword that breaks the rules above (type `headword`, proposing the mended headword and its pattern);
- an idiom or a phrase whose part of speech is still `phrase` while Wiktionary gives it one function (type `kind`; applying it changes the function in place);
- an entry that's a shorter entry of the same part of speech plus a preposition (type `duplicate`, proposing the pattern; *look for* is dismissed, *comply with* confirmed).

**The auditor's judgment** adds the calls data can't make: idiom or phrase, compound word or phrase, preposition pairs the rules didn't pair, and collocations stored as headwords. The owner applies or rejects them in the console.

**A query for the owner,** block 7 of `docs/queries/review.sql`, lists every entry against each rule at once.
