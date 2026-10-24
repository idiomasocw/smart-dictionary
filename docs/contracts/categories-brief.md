# Briefs: categories and parts of speech in the console and on the card

The dictionary adopted a policy on 2026-10-24 (`docs/policies/categories-and-headwords.md`). Two clients show its result, each in its own repository. Paste each part below its line into the agent working on that repository. The API's shapes don't change: only the values an idiom's or a phrase's `pos` can take, and one new finding type.

---

## Part 1, for the LMS (`academic-platform-ocw`): the Smart Dictionary console

**What changed in the dictionary, and why.** Every entry has a `kind` (its category: `word`, `phrasal_verb`, `idiom`, `phrase`) and a `pos` (its part of speech). Until now an idiom's or a phrase's `pos` was always `phrase`, which told a learner nothing about how to use it. Now it's the word class the expression works as: *as long as* is a phrase that works as a `conjunction`, *break the ice* an idiom that works as a `verb`. `phrase` stays as a `pos` only for whole sentences (*how do you do*). A compound such as *bus stop* stays a `word`, a `noun`, as every dictionary lists it. Headwords are bare: *put up with*, never *put up with someone*; the object is a pattern ("put up with sb/sth").

The contract is the dictionary's `docs/contracts/lms-integration.md` §4 (L6). Same client, guard, token and `X-Admin-Actor` rules as before.

**1. Show both labels wherever an entry's category appears** (Entries list, entry page, Word requests, Audit): for `word`, its `pos` alone ("noun"); for the other kinds, the kind then the function, "phrase · conjunction", "idiom · verb", "phrasal verb". Write `phrasal_verb` as "phrasal verb". When an idiom's or a phrase's `pos` is `phrase`, show the kind alone ("idiom").

**2. The approve and add forms** (`POST /v1/admin/requests/{id}/approve`, `POST /v1/admin/entries`):
- For `idiom` and `phrase`, offer every part of speech: `noun`, `verb`, `adjective`, `adverb`, `pronoun`, `determiner`, `preposition`, `conjunction`, `interjection`, `numeral`, and `phrase` (labelled "whole sentence"). Don't force `phrase` any more.
- For `phrasal_verb`, `verb` only. For `word`, every part of speech except `phrase`.
- A headword with an object slot at its end, *somebody* for *someone*, or a leading *to* before a verb answers **422** `invalid_value`. Its `detail` names the headword to use and its pattern, for example `it ends in the object 'someone': write 'put up with', and give the object as a pattern (put up with sb)`. Show `detail` next to the headword field.

**3. The Audit page** gets one finding type and one field:
- **`headword`**: `proposed` is `{"lemma"}`, with `"pattern"` and `"kind"` when they change. Applying it retires the entry and adds the mended headword. Show it as "Headword: *drop someone* → *drop* (pattern: drop sb)".
- **`duplicate`** may carry `"pattern"`: the retired entry is the kept one plus a preposition (*comply with*, *comply*). Show "Retire *comply with*; *comply* gets the pattern 'comply (with sth)'".
- **`kind`** findings now often change only the function (`{"pos": "conjunction", "kind": "phrase"}` on a phrase): applying one keeps the entry's key. Show it as "phrase · phrase → phrase · conjunction".

**Done when** the three points work against a local dictionary (`uv run smart-dictionary api`), and the console never shows `phrase` as the only label of an idiom that has a function.

---

## Part 2, for the BigBlueButton plugin: the entry card

**What changed in the dictionary, and why.** Each card's `pos` and `kind` keep their names and types. Until now an idiom's or a phrase's `pos` was always `phrase`; now it's the word class the expression works as (*as long as*: `conjunction`; *break the ice*: `verb`; *a piece of cake*: `noun`), so a learner sees how to use it in a sentence. Compounds such as *bus stop* are `word`, `noun`, as before.

**On the card, show both labels for a multiword kind:**
- `word`: its `pos` alone, as now ("noun").
- `phrasal_verb`: "phrasal verb".
- `idiom` and `phrase`: the kind, a middle dot, then the function: "idiom · verb", "phrase · conjunction". When `pos` is `phrase` (a whole sentence, *how do you do*), the kind alone: "phrase".

Show the labels in the student's interface language as the card does today ("frase · conjunción", "modismo · verbo" in Spanish). Plain web components only: no OS-native components.

**Done when** the card for *as long as* reads "phrase · conjunction", *break the ice* "idiom · verb", *bus stop* "noun", and *look back* "phrasal verb".
