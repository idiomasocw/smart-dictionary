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

| Part                                                                               | Who decides                                                                                                                                                                                                                               |
| ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `schema_version` (1), `language` ("en"), `key`, `lemma`, `pos`, `kind`, `variants` | The pack. Copy them unchanged.                                                                                                                                                                                                            |
| `pronunciations`                                                                   | The pack's IPA (§15)                                                                                                                                                                                                                      |
| `irregular_forms`                                                                  | Usually empty. The pack's forms already include the irregular ones the server knows (_went_, _gone_, _leaves_), and the server adds those itself; repeating one is harmless. Add a form only if it's irregular and missing from the pack. |
| `cefr`, `senses`, `related`, `pattern`                                             | You                                                                                                                                                                                                                                       |
| Frequency band, audio, inflected forms, embeddings, quality                        | The server. Don't write them.                                                                                                                                                                                                             |

**Repairs:**

- Keep every published key and fix only what the repair lists.
- To drop a sense, set `retired: true`; never delete it or reuse its number.
- A new sense, example or note takes the next unused number.

## 3. Senses

- **Include only senses a learner may meet.** Leave out obsolete, archaic and narrowly technical senses, and senses used in only one small region, unless they're common.
- **Order by frequency,** most common first.
- **One idea per sense.** Split senses when the meaning changes, which usually shows in the Spanish translation (_leave_ = _irse_ vs _dejar_). Don't split just because the context changes.
- **How many:** 1–4 for most words. Up to 6 for very polysemous A1–B1 verbs (_get_, _make_, _take_), and never more than 6.
- **Phrasal verbs:** give the literal and idiomatic senses separately when both are common (_turn off_ the light / _turn_ someone _off_).
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

| Part of speech          | Opening                           | Example                                                                                                     |
| ----------------------- | --------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Verb, phrasal verb      | "to …"                            | _leave_: to go away from a person or place · _run into_: to meet someone you know without planning to       |
| Noun                    | "a/an …", "the …", "one of the …" | _leaf_: one of the flat green parts of a plant or tree that grow from its branches                          |
| Adjective               | Describe the quality directly     | _well-known_: known about by a lot of people                                                                |
| Adverb                  | "in a … way", "used to …"         | _actually_: used to say what is really true, especially when it is different from what someone thinks       |
| Idiom used like a verb  | "to …"                            | _break the ice_: to make people who have just met feel more relaxed, for example by starting a conversation |
| Phrase for conversation | "used to …"                       | _by the way_: used to introduce a new subject or extra information in a conversation                        |

## 6. Short gloss

It's what search results show, so make it count:

- at most 5 words, lowercase, with no final period and no headword;
- separate alternatives with semicolons: "move; travel", "in fact; really", "let stay; forget".

## 7. The Spanish layer (es-419)

Every sense needs it. Write **neutral Latin American Spanish**:

- use _tú_ by default, and _usted_ only when the English is formal;
- never use _vos_ or _vosotros_;
- when countries use different words, put the most widely understood first and tag the regional ones.

**Gloss:** the definition's meaning in natural Spanish, at most 160 characters. Explain the meaning; don't translate the English definition word for word.

**Equivalents:** 1–5, ranked best first. The first is the one search results show. Write them in citation form, without articles: verbs in the infinitive (_irse_, _darse cuenta_), adjectives in the masculine singular (_conocido_), nouns in the singular.

| `match`      | When                                                     | Example                                               |
| ------------ | -------------------------------------------------------- | ----------------------------------------------------- |
| `exact`      | Same meaning and use in this sense                       | _leave_ (go away) → _irse_                            |
| `near`       | Close, with a small difference in use or tone            | _leave_ → _salir_, _marcharse_                        |
| `approx`     | Covers only part of the meaning or some contexts         | _leave_ (not take) → _olvidar_ (when it's by mistake) |
| `paraphrase` | No single equivalent exists; a short explanation instead | _binge-watch_ → _ver muchos episodios seguidos_       |

- **When no direct equivalent exists,** give a paraphrase, marked `paraphrase`.
- **`regions`:** a word used mainly in some countries gets their ISO codes (`MX`, `AR`, `CO`, `CL`, `PE`, `VE`, `ES`…). Leave `regions` empty for words understood across Latin America.
- **`note`:** in Spanish, at most 120 characters, only when the equivalent needs it. Typically it shows how the word is used (_darse cuenta de algo_, _fijarse en algo_) or limits it (_cuando es sin querer_).

## 8. Examples

- **Count:** 2–3 per sense. Use 3 when the sense has several common patterns or contexts worth showing; 2 is enough for a narrow sense.
- **Each example is one natural, complete sentence of 6–16 words.** It contains a form of the headword (any inflection) and shows the sense's typical pattern. For a separable phrasal verb, one example can show the split order (_turn it off_).
- **Settings are everyday adult life:** work, study, travel, family, health, home, food, shopping, technology, sport, free time.
- **Use names and places from the US, Latin America, Brazil and the Arab world**, alongside others: Camila, Diego, Juan, John, Robert, Daniel, Karen, Sarah, Jessica, Elizabeth, Mariana, Luis, João, Fernanda, Fatima, Omar, Layla; Bogotá, Lima, Monterrey, São Paulo, Amman. Never use real people, brands or celebrities.
- **Safe for 16 and up:**
  - nothing sexual, graphic, cruel or hateful, and no violence shown as good;
  - **a headword on a hard topic** (_kill_, _bomb_, _drunk_, _terrorism_) is in the dictionary because learners meet it in the news, in history and in idioms. Write it in calm, factual, non-graphic sentences: a news report, a history class, a TV series, an idiom (_kill time_, _my feet are killing me_). Never describe injuries or show harm as exciting. The headword's own forms pass the server's safety check (V10); other blocked words still fail it;
  - no drugs, no partisan politics, no religious controversy (neutral sentences about how institutions work are fine: _The bill passed the House last week._);
  - no stereotypes, and no medical, legal or financial advice.
- **Use US English** (_vacation_, _apartment_, _cell phone_), unless the sense is used only in the UK.
- **Write plain text,** with no markup and no quotation marks around the headword. The app highlights it.
- **Avoid textbook sentences.** No "This is a leaf." and no definition dressed up as an example.

**The Spanish translation** must be natural, faithful in meaning and tense, and not word for word. Keep the names as they are. It follows the same Spanish rules as §7.

## 9. Labels

**`registers`:** add one only when it applies.

| Register        | Meaning                                     | Example                          |
| --------------- | ------------------------------------------- | -------------------------------- |
| `formal`        | Mainly in formal writing or speech          | _commence_                       |
| `informal`      | Mainly in relaxed conversation              | _run into_, _by the way_         |
| `slang`         | Very informal, often with a group or age    | _ghost_ (stop answering someone) |
| `offensive`     | Can insult or upset people; always label it | Insults, slurs used as words     |
| `literary`      | Mainly in literature                        | _slumber_                        |
| `old-fashioned` | Still understood, but sounds dated          | _wireless_ (radio)               |
| `technical`     | Used by specialists                         | _hypothesis_ in science          |

**`domains`:** set one only when the sense belongs to a field. Use lowercase English from this list: business, finance, law, medicine, technology, computing, science, education, sport, music, art, cooking, travel, politics, religion, military.

**`regions`** (English): `US`, `UK`, `AU`, `CA`, `IE`, `NZ`, only for a sense used mainly in that region. _flat_ meaning an apartment gets `UK`.

**Labels are never categories.** Slang, informal and offensive are registers on a sense; a region, a field, countable or transitive are labels too. None of them is a `kind` or a part of speech, which describe the whole entry (§14).

## 10. Patterns and grammar

**`patterns`** show how the word combines:

- Write the headword with the closed slots: `sb`, `sth`, `sb/sth`, `sb's`, `one's`, `oneself`, `somewhere`, `doing sth`.
- Add `+ clause` and `to do sth` where needed.
- Use a slash for alternatives and parentheses for optional words.
- Examples: "depend on sb/sth", "realize (that) + clause", "go by bus/car/train", "leave sth somewhere".
- **A preposition that completes a word's meaning is a pattern of that word, not an entry of its own:** the verb _long_ gets "long for sb/sth", _comply_ gets "comply (with sth)", _look back_ gets "look back (on sth)". The pack's `patterns` carry such forms when an entry was folded into this one; give each a pattern.
- **Patterns use the short slots above; headwords spell them out** (§14).

**`grammar`:** countability for nouns (`countable`, `uncountable`, `both`) and transitivity for verbs (`transitive`, `intransitive`, `both`). Set it when you're sure, and leave it out otherwise.

## 11. Collocations

- At most 6 per sense, most useful first.
- Each is a short phrase containing the headword, never a sentence: "make a decision", "heavy rain", "a well-known fact".
- No final period. Use US usage.
- **Collocations are never headwords.** _heavy rain_ and _make a decision_ belong here and in examples. If a pack's headword is a free collocation rather than a fixed expression, leave the word out and say so.

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

| `kind`           | Use                                                                                               |
| ---------------- | ------------------------------------------------------------------------------------------------- |
| `false_friend`   | Looks like a Spanish word but means something else (_actual_, _realize_, _assist_, _embarrassed_) |
| `common_mistake` | A mistake Spanish speakers typically make (_go to home_, _explain me_)                            |
| `pronunciation`  | A real trap: silent letters, stress shifts, _-ed_ endings                                         |
| `grammar`        | A structure that causes trouble (no _-s_ after _people_)                                          |
| `usage`          | When or how to use it (politeness, register in context)                                           |
| `culture`        | Background needed to understand it                                                                |

## 13. Related entries

| `type`       | Use                                                                                 | Example                                          |
| ------------ | ----------------------------------------------------------------------------------- | ------------------------------------------------ |
| `synonym`    | Same meaning. Add a `note` of at most 120 characters, in English, when they differ. | _beautiful_ → _pretty_ ("pretty is less strong") |
| `antonym`    | Opposite meaning                                                                    | _leave_ → _arrive_                               |
| `family`     | Same word family                                                                    | _actual_ → _actually_, _decide_ → _decision_     |
| `confusable` | Learners mix them up                                                                | _realize_ ↔ _notice_, _actual_ ↔ _current_       |
| `see_also`   | Worth looking at next                                                               | _by the way_ → _anyway_                          |

- **Link only what helps,** usually no more than 6.
- **The target can be an entry that doesn't exist yet.** It's resolved when it's published.
- **Build keys as `en:<headword>:<code>`:** headword lowercase, spaces as `_`, then the part-of-speech code.
  - Codes: `n`, `v`, `adj`, `adv`, `pron`, `det`, `prep`, `conj`, `interj`, `num`, `phrv` (phrasal verb), `idiom`, `phr` (phrase).
  - Examples: `en:run_into:phrv`, `en:well-known:adj`.

## 14. Multiword entries

**Kinds and parts of speech** (the policy of 2026-10-24, `docs/policies/categories-and-headwords.md`). The pack fixes both; this is what they mean, so you write senses that fit them:

| `kind`         | What it is                                                                                    | `pos`                                                    | Examples                                                       |
| -------------- | --------------------------------------------------------------------------------------------- | -------------------------------------------------------- | -------------------------------------------------------------- |
| `word`         | One word, or a compound that dictionaries list as its own headword with a word class          | Its word class                                           | _house_, _bus stop_, _well-known_, _upside down_              |
| `phrasal_verb` | A verb and particle(s) whose meaning is the combination's                                     | `verb`                                                   | _look back_, _put up with_                                     |
| `idiom`        | A figurative expression: knowing every word doesn't give its meaning                         | What it works as in a sentence                           | _break the ice_ (verb), _a piece of cake_ (noun)               |
| `phrase`       | Any other fixed expression: complex conjunctions and prepositions, discourse markers, sayings | What it works as; `phrase` only for a whole sentence     | _as long as_ (conjunction), _of course_ (adverb), _how do you do_ (phrase) |

- **One entry per expression.** It takes its main function; another use is a sense of the same entry ("Of course!" is a sense of _of course_, the adverb).
- **The card shows both:** "idiom · verb", "phrase · conjunction". Write definitions that fit the function: a verb idiom is defined as a verb ("to start a conversation…").
- **Headwords are bare:** no object slot at the end (_put up with_, never _put up with someone_; the object is a pattern), no _to_ before a verb, no _be_ before an adjective (_fed up_). A slot inside an expression is spelled out: _someone_, _something_, _someone's_, _one's_, _oneself_ (_pull someone's leg_, _keep one's word_).
- **If the pack's kind, part of speech or headword looks wrong,** leave the word out and say so (§2). Don't write around it.

Phrasal verbs, idioms and phrases need a `pattern`, the sequence the app matches in text:

- **`{"lemma": …}`** marks a word that changes form inside the expression, typically the verb (_ran into_, _broke the ice_).
- **`{"text": …}`** marks a word that never changes: particles, articles, fixed nouns.
- **`{"slot": …}`** marks an open position, using the closed slots in §10 (_pull sb's leg_).
- **Separable phrasal verbs** get `separable: true`, and `max_gap` gives the most words allowed between the parts. _turn the TV off_ has a gap of 2, so `max_gap` should be 3 to cover longer objects.
- **Fixed phrases** use only `text` tokens (_by the way_).

| Kind                       | Pattern                                                  |
| -------------------------- | -------------------------------------------------------- |
| `phrasal_verb`, _run into_ | `[{"lemma": "run"}, {"text": "into"}, {"slot": "sb"}]`   |
| `idiom`, _break the ice_   | `[{"lemma": "break"}, {"text": "the"}, {"text": "ice"}]` |
| `phrase`, _by the way_     | `[{"text": "by"}, {"text": "the"}, {"text": "way"}]`     |

Define what the whole expression means, not its literal meaning.

## 15. Pronunciation

- **One pronunciation per variety** (`en-US`, `en-GB`), written the way a learner's dictionary shows it.
- **Start from the pack's IPA,** which comes from CMUdict (US) and Britfone (UK), and stay close to it. The server warns when a pronunciation strays too far from those dictionaries.
  - **Smooth out reference forms that read oddly for learners.** CMUdict gives _actual_ as `ˈæktʃəwəl`; write `ˈæktʃuəl`.
  - **Several options:** when the pack gives more than one, write the most common.
- **Characters:**
  - keep the stress marks `ˈ` and `ˌ`, except on one-syllable words: `haʊs`, not `ˈhaʊs`;
  - write `r`, not Britfone's `ɹ`;
  - add no syllable dots, slashes or brackets.
- **Homographs with the same part of speech:** when the pronunciation changes with the meaning, give one pronunciation per sense with `sense_keys`. _bass_ (fish) is `bæs`; _bass_ (music) is `beɪs`.
- **A different part of speech, a different sound:** the pack's IPA belongs to the spelling, not the part of speech, so for _house_, _use_, _close_, _live_, _record_ or _present_ it may be the other one's. The verb _house_ is `haʊz`, though the pack gives the noun's `haʊs`. Write this part of speech's pronunciation; a V7 warning is expected then.
- **Missing IPA:** if the pack has none (common for multiword entries), leave `pronunciations` empty.

## 16. Spelling and characters

- **US spelling everywhere.** UK spellings appear only as `variants`, with `regions: ["UK"]`, taken from the pack. Never change the headword.
- **Apostrophes** are the plain ASCII `'` (_don't_, _one's_). Use no curly quotes in English text.
- **Spanish text** uses ¿ ¡ and accents, and quotes words with «».
- **Plain text only:** no HTML, no Markdown, no emoji.
- **Punctuation:** sentences end with a period. Glosses, short glosses, collocations and patterns don't.

## 17. Originality

- **Write everything yourself.** Never copy or closely paraphrase any dictionary, including the Wiktionary hints in the pack. They only tell you which meanings exist, and the server rejects 8 identical words in a row.
- **Don't invent usage.** If you aren't sure a sense or an equivalent is real and current, leave it out.

## 18. Before you submit

| Check                                                                                            | Server check  |
| ------------------------------------------------------------------------------------------------ | ------------- |
| The document matches the schema; keys are well formed and published keys kept                    | V1, V2        |
| Every example contains a form of the headword; a multiword pattern matches its examples          | V3            |
| Definitions: 20 words or fewer, no headword, basic words                                         | V4            |
| Spanish gloss, equivalents and translations read as natural Spanish                              | V5, V6        |
| IPA close to the pack's, with stress marks and no syllable dots                                  | V7            |
| No two senses mean the same thing                                                                | V8            |
| Nothing copied from the hints or any dictionary                                                  | V9            |
| Examples are safe; offensive senses are labeled                                                  | V10           |
| 1–6 senses, 2–3 examples, at most 6 collocations and 3 notes per sense                           | V11           |
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
            {
              "term": "darse cuenta",
              "match": "exact",
              "note": "darse cuenta de algo"
            },
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
          "translations": {
            "es-419": "De pronto, Camila se dio cuenta de que había dejado las llaves en casa."
          }
        },
        {
          "key": "en:realize:v#1.ex3",
          "text": "We soon realized the job was harder than it looked.",
          "cefr": "B1",
          "translations": {
            "es-419": "Pronto nos dimos cuenta de que el trabajo era más difícil de lo que parecía."
          }
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
            {
              "term": "cumplir",
              "match": "approx",
              "note": "con sueños o metas"
            }
          ]
        }
      },
      "examples": [
        {
          "key": "en:realize:v#2.ex1",
          "text": "Opening her own restaurant helped Mariana realize a lifelong dream.",
          "cefr": "C1",
          "translations": {
            "es-419": "Abrir su propio restaurante ayudó a Mariana a hacer realidad el sueño de su vida."
          }
        },
        {
          "key": "en:realize:v#2.ex2",
          "text": "The company needs more funding to realize its plans for expansion.",
          "cefr": "C1",
          "translations": {
            "es-419": "La empresa necesita más financiamiento para hacer realidad sus planes de expansión."
          }
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
