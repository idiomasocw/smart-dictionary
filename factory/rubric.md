# Review rubric

You review entries someone else wrote. Software has already checked what it can:
- the document's shape, lengths and counts;
- that every example contains the headword;
- that the Spanish reads as Spanish;
- that nothing was copied from Wiktionary;
- basic safety.

Your job is what software can't judge: whether the entry is **right, natural and useful** for an adult learner whose first language is Spanish.

Read every active sense of every entry, and check the six points below.

## 1. Meaning

- **Each sense is real and current** for this part of speech.
- **The common senses are there,** most common first.
  - The pack's Wiktionary hints show which meanings exist.
  - A missing common sense fails; a missing rare one doesn't.
  - A sense found mostly inside compounds (*opera house*, *guest house*) isn't missing, because compounds get entries of their own.
- **No two senses mean the same thing.**

## 2. Definition and short gloss

- **The definition** is accurate and simple, uses easier words than the headword, and holds one idea.
- **The short gloss** says the same thing in a few words, and works as a one-line search result.

## 3. Levels

The CEFR levels are plausible:
- the main sense is near the pack's prior;
- rarer senses are higher;
- each example is at its sense's level, or one above.

## 4. Examples

- **Each example shows this sense,** not another one, in a sentence a native speaker would really say.
- **Together they show the typical pattern** and vary in form.
- **They fit adult life,** with nothing unsuitable for a 16-year-old.

## 5. Spanish

- **The gloss** explains the same meaning in natural Latin American Spanish.
- **The equivalents:**
  - are real Spanish words used for this meaning, ranked best first;
  - carry the right match type (`exact`, `near`, `approx`, `paraphrase`);
  - carry region tags when they're regional.
- **Example translations** are natural and faithful in meaning and tense, and use *tú*.
- **Notes for Spanish speakers** are in Spanish, correct, and give the right alternative.

## 6. Everything else

- **Notes** are correct and worth reading.
- **Labels** (registers, domains, regions) are right.
- **Patterns, collocations and related entries** are correct and useful.
- **Pronunciations** are right for this part of speech, and close to the pack's IPA.
  - The pack's IPA belongs to the spelling, so it can be the other part of speech's (STYLE §15). Then the right one wins: the verb *house* is `haʊz`.
  - Dropping the pack's stress mark on a one-syllable word, or a stray secondary stress (`ˌdɪˈsaɪd`), is expected.
- **Nothing reads as copied** from a dictionary.

## Verdicts

- **`pass`:** ready for students as it is.
- **`fail`:** a student could learn something wrong from it:
  - a wrong meaning;
  - a wrong or unnatural translation;
  - an unnatural example;
  - a misleading note;
  - a level more than one step off.

  A missing common sense also fails, as does anything that breaks STYLE.md in a way a student would notice.
- **`uncertain`:** you can't tell, for example whether a regional Spanish word is right. Say what you're unsure of.

Small points that don't affect the student, such as a comma, belong in the notes of a `pass`, not in a `fail`.

How verdicts count: the server lowers an entry's score by 0.15 for `uncertain` and by 0.5 for `fail`. So a `fail` never publishes, and the entry goes back for repair with your notes.

## Notes

Write one line per problem, naming the sense, example or note, and saying what to change:

- `en:leave:v#2.ex1: unnatural ("leave the keys at the table"); use "on the table"`
- `en:leave:v#1 equivalents: "salir" should rank above "marcharse"`

Notes on a `pass` count too: the writer applies the ones it agrees with in the fix round, and nobody checks them again. So a point that must change belongs in a `fail`, not in a pass's notes.

A line saying something unusual is deliberate is welcome, because it stops a later audit from undoing it: `en:house:v pronunciations: haʊz, not the pack's haʊs, is right; keep it`.

## Output

Save a JSON list in `factory/work/review/group-M.json`, one item per entry:

```json
[{ "key": "en:leave:v", "verdict": "fail", "notes": ["..."], "needs_repair": false }]
```

Leave `needs_repair` false. The orchestrator sets it after the fix round.
