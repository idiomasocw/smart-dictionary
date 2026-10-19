# Editorial style guide

How to write a Smart Dictionary entry. The factory's writers and reviewers follow it, and so does anyone who writes or repairs an entry by hand.

**What's binding:**
- The product's content standard (`docs/02-product-spec.md` §6). This guide details it and must stay consistent with it.
- The shape of an entry: `factory/schema/entry.v1.json`, generated from `backend/src/smart_dictionary/core/entrydoc.py`. If this guide and the schema disagree, the schema wins; flag the conflict.
- The server's checks (`docs/03-system-design.md` §6.4, V1–V11). The limits below match them, so an entry that follows this guide passes them.

The entries in `backend/fixtures/entries/` show these rules applied.

## 1. Who reads the entries

**Adults (16 and up) whose first language is Spanish**, mostly from Latin America, learning English in live online classes at levels A1 to C1.
- They look words up **during class**. They need the meaning at a glance: a short gloss, a Spanish equivalent, one example.
- What they see first is a one-line search result; a full entry opens only when they tap it.

A good entry is **accurate, simple, natural, safe and original**. When two rules pull in different directions, choose what a learner in that class needs.

## 2. What you receive, and what you write

For each word you get a **grounding pack**:
- **Fixed facts:** key, headword, part of speech, kind, spelling variants.
- **Priors:** the CEFR level from the open lists, frequency, and which lists include the word.
- **Forms and IPA:** inflected forms, and US and UK pronunciations.
- **Wiktionary hints:** a few short sense hints and Spanish candidates. They only show which meanings exist; never copy them (§17).
- **Context:** related entries already published, and for a repair, the published keys and what failed.

Some fields come from the seed list the words were picked from:
- **`priors.lists`** says where the word was found. `owner:<file>` is one of the owner's own lists; `cefrj` and `octanove-c1c2` give CEFR levels; `ngsl`, `ngsl-spoken` and the like list common words. Use them as evidence for the level (§4).
- **`originals`** are the owner's list lines, as written. They show the meaning or phrase the owner had in mind, such as `(all) at sea (about something)`.
- **`patterns`** are fuller forms of a multiword entry from those lines, with its slots: `break the news to someone`. Use them for the pattern (§10, §14).
- **`flags`** are notes from building the list, such as "listed as phrasal verb, shaped like an idiom" or "dropped leading 'be'". They mark packs worth a second look. If one says the kind or part of speech may be wrong and you agree, leave the word out and say so.

You write **one entry document per word**, valid against the schema:

| Part | Who decides |
|---|---|
| `schema_version` (1), `language` ("en"), `key`, `lemma`, `pos`, `kind`, `variants` | The pack. Copy them unchanged. |
| `pronunciations` | The pack's IPA (§15) |
| `irregular_forms` | Usually empty. The pack's forms already include the irregular ones the server knows (*went*, *gone*, *leaves*), and the server adds those itself; repeating one is harmless. Add a form only if it's irregular and missing from the pack. |
| `cefr`, `senses`, `related`, `pattern` | You |
| Frequency band, audio, inflected forms, embeddings, quality | The server. Don't write them. |

**Repairs:**
- Keep every published key and fix only what the repair lists.
- To drop a sense, set `retired: true`; never delete it or reuse its number.
- A new sense, example or note takes the next unused number.

## 3. Senses

- **Include only senses a learner may meet.** Leave out obsolete, archaic and narrowly technical senses, and senses used in only one small region, unless they're common.
- **Order by frequency,** most common first.
- **One idea per sense.** Split senses when the meaning changes, which usually shows in the Spanish translation (*leave* = *irse* vs *dejar*). Don't split just because the context changes.
- **How many:** 1–4 for most words. Up to 6 for very polysemous A1–B1 verbs (*get*, *make*, *take*), and never more than 6.
- **Phrasal verbs:** give the literal and idiomatic senses separately when both are common (*turn off* the light / *turn* someone *off*).
- **Sensitive senses** (sexual, vulgar, insulting) go in only when they're common and useful. Label them (§9), and keep their examples neutral.
- **Keys:** a new entry's senses are `#1`, `#2` and so on, in order. A sense's examples are `.ex1`, `.ex2`, and its notes `.note1`. Numbers are identities: never renumber them once the entry is published.
  - **Before then** (the pack has no `existing`), you may still insert a sense where it belongs, in a fix round for example, and number the senses again in order.
  - **Once published,** a new sense takes the next unused number, and you place it in the list by frequency. The list's order is the order students see; the numbers only identify the senses.

## 4. Levels (CEFR)

- **Every sense gets its own level.** The entry's `cefr` is the level of its most basic sense, usually `#1`.
- **The pack's prior is a starting point,** normally for the main sense. Less common senses are usually higher.
- **With no prior,** estimate from frequency and how hard the meaning is. The pack's priors already show which words had none, so you don't mark the estimate yourself.
- **Each example carries its own level.** Keep its vocabulary at the sense's level, or one level above at most.

## 5. Definitions

- **Learner English:** at most 20 words, one idea, built from basic words. The server checks that at least 85% of the words are in the defining vocabulary (the ~2,800 words of the NGSL).
- **Never use the headword or any of its forms** in its own definition.
- **Write "someone" and "something"** in definitions. Keep the short slots (`sb`, `sth`) for patterns.
- **Don't define with a harder synonym,** don't go in circles, and don't write "etc."
- **Use US spelling.** Start with a lowercase letter and leave off the final period.

How a definition opens depends on the part of speech:

| Part of speech | Opening | Example |
|---|---|---|
| Verb, phrasal verb | "to …" | *leave*: to go away from a person or place · *run into*: to meet someone you know without planning to |
| Noun | "a/an …", "the …", "one of the …" | *leaf*: one of the flat green parts of a plant or tree that grow from its branches |
| Adjective | Describe the quality directly | *well-known*: known about by a lot of people |
| Adverb | "in a … way", "used to …" | *actually*: used to say what is really true, especially when it is different from what someone thinks |
| Idiom used like a verb | "to …" | *break the ice*: to make people who have just met feel more relaxed, for example by starting a conversation |
| Phrase for conversation | "used to …" | *by the way*: used to introduce a new subject or extra information in a conversation |

## 6. Short gloss

It's what search results show, so make it count:
- at most 5 words, lowercase, with no final period and no headword;
- separate alternatives with semicolons: "move; travel", "in fact; really", "let stay; forget".

## 7. The Spanish layer (es-419)

Every sense needs it. Write **neutral Latin American Spanish**:
- use *tú* by default, and *usted* only when the English is formal;
- never use *vos* or *vosotros*;
- when countries use different words, put the most widely understood first and tag the regional ones.

**Gloss:** the definition's meaning in natural Spanish, at most 160 characters. Explain the meaning; don't translate the English definition word for word.

**Equivalents:** 1–5, ranked best first. The first is the one search results show. Write them in citation form, without articles: verbs in the infinitive (*irse*, *darse cuenta*), adjectives in the masculine singular (*conocido*), nouns in the singular.

| `match` | When | Example |
|---|---|---|
| `exact` | Same meaning and use in this sense | *leave* (go away) → *irse* |
| `near` | Close, with a small difference in use or tone | *leave* → *salir*, *marcharse* |
| `approx` | Covers only part of the meaning or some contexts | *leave* (not take) → *olvidar* (when it's by mistake) |
| `paraphrase` | No single equivalent exists; a short explanation instead | *binge-watch* → *ver muchos episodios seguidos* |

- **When no direct equivalent exists,** give a paraphrase, marked `paraphrase`.
- **`regions`:** a word used mainly in some countries gets their ISO codes (`MX`, `AR`, `CO`, `CL`, `PE`, `VE`, `ES`…). Leave `regions` empty for words understood across Latin America.
- **`note`:** in Spanish, at most 120 characters, only when the equivalent needs it. Typically it shows how the word is used (*darse cuenta de algo*, *fijarse en algo*) or limits it (*cuando es sin querer*).

## 8. Examples

- **Count:** 2–3 per sense. Use 3 when the sense has several common patterns or contexts worth showing; 2 is enough for a narrow sense.
- **Each example is one natural, complete sentence of 6–16 words.** It contains a form of the headword (any inflection) and shows the sense's typical pattern. For a separable phrasal verb, one example can show the split order (*turn it off*).
- **Settings are everyday adult life:** work, study, travel, family, health, home, food, shopping, technology, sport, free time.
- **Use names and places from Latin America, Brazil and the Arab world**, alongside others: Camila, Diego, Mariana, Luis, João, Fernanda, Fatima, Omar, Layla; Bogotá, Lima, Monterrey, São Paulo, Amman. Never use real people, brands or celebrities.
- **Safe for 16 and up:**
  - nothing sexual, violent, cruel or hateful;
  - no drugs, no partisan politics, no religious controversy (neutral sentences about how institutions work are fine: *The bill passed the House last week.*);
  - no stereotypes, and no medical, legal or financial advice.
- **Use US English** (*vacation*, *apartment*, *cell phone*), unless the sense is used only in the UK.
- **Write plain text,** with no markup and no quotation marks around the headword. The app highlights it.
- **Avoid textbook sentences.** No "This is a leaf." and no definition dressed up as an example.

**The Spanish translation** must be natural, faithful in meaning and tense, and not word for word. Keep the names as they are. It follows the same Spanish rules as §7.

## 9. Labels

**`registers`:** add one only when it applies.

| Register | Meaning | Example |
|---|---|---|
| `formal` | Mainly in formal writing or speech | *commence* |
| `informal` | Mainly in relaxed conversation | *run into*, *by the way* |
| `slang` | Very informal, often with a group or age | *ghost* (stop answering someone) |
| `offensive` | Can insult or upset people; always label it | Insults, slurs used as words |
| `literary` | Mainly in literature | *slumber* |
| `old-fashioned` | Still understood, but sounds dated | *wireless* (radio) |
| `technical` | Used by specialists | *hypothesis* in science |

**`domains`:** set one only when the sense belongs to a field. Use lowercase English from this list: business, finance, law, medicine, technology, computing, science, education, sport, music, art, cooking, travel, politics, religion, military.

**`regions`** (English): `US`, `UK`, `AU`, `CA`, `IE`, `NZ`, only for a sense used mainly in that region. *flat* meaning an apartment gets `UK`.

## 10. Patterns and grammar

**`patterns`** show how the word combines:
- Write the headword with the closed slots: `sb`, `sth`, `sb/sth`, `sb's`, `one's`, `oneself`, `somewhere`, `doing sth`.
- Add `+ clause` and `to do sth` where needed.
- Use a slash for alternatives and parentheses for optional words.
- Examples: "depend on sb/sth", "realize (that) + clause", "go by bus/car/train", "leave sth somewhere".

**`grammar`:** countability for nouns (`countable`, `uncountable`, `both`) and transitivity for verbs (`transitive`, `intransitive`, `both`). Set it when you're sure, and leave it out otherwise.

## 11. Collocations

- At most 6 per sense, most useful first.
- Each is a short phrase containing the headword, never a sentence: "make a decision", "heavy rain", "a well-known fact".
- No final period. Use US usage.

## 12. Notes

Add a note only when it truly helps: at most 3 per sense, each at most 300 characters.

- **For Spanish speakers** (`"l1": "es"`), written **in Spanish**:
  - **When to write one:** for false friends, typical mistakes and real pronunciation traps.
  - **How to write it:** quote words with «», and always give the correct alternative.
  - **Examples:** «Realize» casi nunca significa «realizar». Para «realizar» (hacer) di «carry out» o «do». · Se dice «go home», sin «to».
- **For everyone** (`"l1": null`), written **in English**:
  - **When to write one:** rarely, for usage, grammar or culture.
  - **Example:** the literal meaning of an idiom, when it helps memory.
- **`see`:** keys of the entries the note points to, such as `["en:current:adj"]`.

| `kind` | Use |
|---|---|
| `false_friend` | Looks like a Spanish word but means something else (*actual*, *realize*, *assist*, *embarrassed*) |
| `common_mistake` | A mistake Spanish speakers typically make (*go to home*, *explain me*) |
| `pronunciation` | A real trap: silent letters, stress shifts, *-ed* endings |
| `grammar` | A structure that causes trouble (no *-s* after *people*) |
| `usage` | When or how to use it (politeness, register in context) |
| `culture` | Background needed to understand it |

## 13. Related entries

| `type` | Use | Example |
|---|---|---|
| `synonym` | Same meaning. Add a `note` of at most 120 characters, in English, when they differ. | *beautiful* → *pretty* ("pretty is less strong") |
| `antonym` | Opposite meaning | *leave* → *arrive* |
| `family` | Same word family | *actual* → *actually*, *decide* → *decision* |
| `confusable` | Learners mix them up | *realize* ↔ *notice*, *actual* ↔ *current* |
| `see_also` | Worth looking at next | *by the way* → *anyway* |

- **Link only what helps,** usually no more than 6.
- **The target can be an entry that doesn't exist yet.** It's resolved when it's published.
- **Build keys as `en:<headword>:<code>`:** headword lowercase, spaces as `_`, then the part-of-speech code.
  - Codes: `n`, `v`, `adj`, `adv`, `pron`, `det`, `prep`, `conj`, `interj`, `num`, `phrv` (phrasal verb), `idiom`, `phr` (phrase).
  - Examples: `en:run_into:phrv`, `en:well-known:adj`.

## 14. Multiword entries

Phrasal verbs, idioms and phrases need a `pattern`, the sequence the app matches in text:
- **`{"lemma": …}`** marks a word that changes form inside the expression, typically the verb (*ran into*, *broke the ice*).
- **`{"text": …}`** marks a word that never changes: particles, articles, fixed nouns.
- **`{"slot": …}`** marks an open position, using the closed slots in §10 (*pull sb's leg*).
- **Separable phrasal verbs** get `separable: true`, and `max_gap` gives the most words allowed between the parts. *turn the TV off* has a gap of 2, so `max_gap` should be 3 to cover longer objects.
- **Fixed phrases** use only `text` tokens (*by the way*).

| Kind | Pattern |
|---|---|
| `phrasal_verb`, *run into* | `[{"lemma": "run"}, {"text": "into"}, {"slot": "sb"}]` |
| `idiom`, *break the ice* | `[{"lemma": "break"}, {"text": "the"}, {"text": "ice"}]` |
| `phrase`, *by the way* | `[{"text": "by"}, {"text": "the"}, {"text": "way"}]` |

Define what the whole expression means, not its literal meaning.

## 15. Pronunciation

- **One pronunciation per variety** (`en-US`, `en-GB`), written the way a learner's dictionary shows it.
- **Start from the pack's IPA,** which comes from CMUdict (US) and Britfone (UK), and stay close to it. The server warns when a pronunciation strays too far from those dictionaries.
  - **Smooth out reference forms that read oddly for learners.** CMUdict gives *actual* as `ˈæktʃəwəl`; write `ˈæktʃuəl`.
  - **Several options:** when the pack gives more than one, write the most common.
- **Characters:**
  - keep the stress marks `ˈ` and `ˌ`, except on one-syllable words: `haʊs`, not `ˈhaʊs`;
  - write `r`, not Britfone's `ɹ`;
  - add no syllable dots, slashes or brackets.
- **Homographs with the same part of speech:** when the pronunciation changes with the meaning, give one pronunciation per sense with `sense_keys`. *bass* (fish) is `bæs`; *bass* (music) is `beɪs`.
- **A different part of speech, a different sound:** the pack's IPA belongs to the spelling, not the part of speech, so for *house*, *use*, *close*, *live*, *record* or *present* it may be the other one's. The verb *house* is `haʊz`, though the pack gives the noun's `haʊs`. Write this part of speech's pronunciation; a V7 warning is expected then.
- **Missing IPA:** if the pack has none (common for multiword entries), leave `pronunciations` empty.

## 16. Spelling and characters

- **US spelling everywhere.** UK spellings appear only as `variants`, with `regions: ["UK"]`, taken from the pack. Never change the headword.
- **Apostrophes** are the plain ASCII `'` (*don't*, *one's*). Use no curly quotes in English text.
- **Spanish text** uses ¿ ¡ and accents, and quotes words with «».
- **Plain text only:** no HTML, no Markdown, no emoji.
- **Punctuation:** sentences end with a period. Glosses, short glosses, collocations and patterns don't.

## 17. Originality

- **Write everything yourself.** Never copy or closely paraphrase any dictionary, including the Wiktionary hints in the pack. They only tell you which meanings exist, and the server rejects 8 identical words in a row.
- **Don't invent usage.** If you aren't sure a sense or an equivalent is real and current, leave it out.

## 18. Before you submit

| Check | Server check |
|---|---|
| The document matches the schema; keys are well formed and published keys kept | V1, V2 |
| Every example contains a form of the headword; a multiword pattern matches its examples | V3 |
| Definitions: 20 words or fewer, no headword, basic words | V4 |
| Spanish gloss, equivalents and translations read as natural Spanish | V5, V6 |
| IPA close to the pack's, with stress marks and no syllable dots | V7 |
| No two senses mean the same thing | V8 |
| Nothing copied from the hints or any dictionary | V9 |
| Examples are safe; offensive senses are labeled | V10 |
| 1–6 senses, 2–3 examples, at most 6 collocations and 3 notes per sense | V11 |
| Short gloss of 5 words or fewer; Spanish gloss of 160 characters or fewer; notes of 300 or fewer | Length checks |

## A complete entry

```json
{
  "schema_version": 1,
  "key": "en:realize:v",
  "language": "en",
  "lemma": "realize",
  "pos": "verb",
  "kind": "word",
  "cefr": "B1",
  "pronunciations": [
    { "variety": "en-US", "ipa": "ˈriəˌlaɪz" },
    { "variety": "en-GB", "ipa": "ˈrɪəˌlaɪz" }
  ],
  "variants": [{ "form": "realise", "regions": ["UK"] }],
  "senses": [
    {
      "key": "en:realize:v#1",
      "cefr": "B1",
      "definition": "to understand or become aware of something that you did not know before",
      "short_gloss": "understand suddenly",
      "patterns": ["realize (that) + clause", "realize sth"],
      "grammar": { "transitivity": "transitive" },
      "l1": {
        "es-419": {
          "gloss": "entender algo o tomar conciencia de algo que antes no se sabía",
          "equivalents": [
            { "term": "darse cuenta", "match": "exact", "note": "darse cuenta de algo" },
            { "term": "caer en la cuenta", "match": "near" }
          ]
        }
      },
      "examples": [
        {
          "key": "en:realize:v#1.ex1",
          "text": "I didn't realize it was so late.",
          "cefr": "B1",
          "translations": { "es-419": "No me di cuenta de que era tan tarde." }
        },
        {
          "key": "en:realize:v#1.ex2",
          "text": "Camila suddenly realized that she had left her keys at home.",
          "cefr": "B1",
          "translations": { "es-419": "De pronto, Camila se dio cuenta de que había dejado las llaves en casa." }
        },
        {
          "key": "en:realize:v#1.ex3",
          "text": "We soon realized the job was harder than it looked.",
          "cefr": "B1",
          "translations": { "es-419": "Pronto nos dimos cuenta de que el trabajo era más difícil de lo que parecía." }
        }
      ],
      "collocations": ["suddenly realize", "soon realize", "fail to realize"],
      "notes": [
        {
          "key": "en:realize:v#1.note1",
          "kind": "false_friend",
          "l1": "es",
          "text": "«Realize» casi nunca significa «realizar». Para «realizar» (hacer) di «carry out» o «do».",
          "see": ["en:carry_out:phrv"]
        }
      ]
    },
    {
      "key": "en:realize:v#2",
      "cefr": "C1",
      "definition": "to make a hope, plan or idea become real",
      "short_gloss": "make happen",
      "registers": ["formal"],
      "patterns": ["realize sth"],
      "grammar": { "transitivity": "transitive" },
      "l1": {
        "es-419": {
          "gloss": "hacer realidad un sueño, un plan o una idea",
          "equivalents": [
            { "term": "hacer realidad", "match": "exact" },
            { "term": "realizar", "match": "near" },
            { "term": "cumplir", "match": "approx", "note": "con sueños o metas" }
          ]
        }
      },
      "examples": [
        {
          "key": "en:realize:v#2.ex1",
          "text": "Opening her own restaurant helped Mariana realize a lifelong dream.",
          "cefr": "C1",
          "translations": { "es-419": "Abrir su propio restaurante ayudó a Mariana a hacer realidad el sueño de su vida." }
        },
        {
          "key": "en:realize:v#2.ex2",
          "text": "The company needs more funding to realize its plans for expansion.",
          "cefr": "C1",
          "translations": { "es-419": "La empresa necesita más financiamiento para hacer realidad sus planes de expansión." }
        }
      ]
    }
  ],
  "related": [
    { "type": "confusable", "key": "en:notice:v" },
    { "type": "family", "key": "en:reality:n" }
  ]
}
```
