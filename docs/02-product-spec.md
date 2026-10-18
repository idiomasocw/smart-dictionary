# Smart Video Dictionary: Product Specification (v1)

> **Status:** draft v0.1 for review · **Date:** 2026-10-18 · **Owner:** Diego E
> **Inputs:** `01-audit-and-recommendations.md`, especially the answers in §16.1 · **Companions:** `03-system-design.md`, `04-roadmap.md`
> Requirement IDs (LK-1, EC-2…) are referenced by the system design and the roadmap. Keep them stable.

---

## 1. Vision

An English learner's dictionary for Spanish-, Portuguese- and Arabic-speaking students that lives **inside the academy's BigBlueButton classes**.

- **Pre-populated content.** Every entry already has learner definitions, L1 equivalents, examples with translations and pronunciation audio. An automated AI content factory writes it all.
- **Adaptive help on top.** AI checks a student's sentence, tailors examples, contrasts confusable words, and turns lookups into review.

> *Look up any word from your class in one click, in your own language, with the right meaning for the sentence in front of you, and remember it after class.*

## 2. Goals and non-goals

**v1 goals**

| ID | Goal |
|---|---|
| G1 | A student finds any word or phrase from class in at most 2 interactions, and sees it in their L1 (Spanish first) |
| G2 | **5,000** high-quality entries at launch and **20,000** soon after; missing words answered instantly by provisional entries |
| G3 | Context-aware: the right sense for the sentence, and phrasal verbs and idioms recognized |
| G4 | Adaptive help: use-it coach, tailored examples, explain the difference, review deck |
| G5 | Lean running costs: infrastructure ≈ $30–35/month, runtime AI ≤ $5/month |
| G6 | A fully automated content pipeline with automated QA, where a teacher can hide bad content in one click |

**Not in v1:** clips (postponed); the Portuguese and Arabic layers (later phases); Spanish headwords (the bidirectional phase); a public website or mobile app; the admin dashboard (after v1); any identity other than BBB's; payments.

## 3. Users

| Persona | Who | Needs |
|---|---|---|
| **Student** | An adult today, possibly 16+ later. L1 is Latin American Spanish first, then Brazilian Portuguese and Arabic. CEFR A1–C1. In live BBB classes | Fast lookup in the L1, the right meaning, pronunciation, how to use the word, remembering it |
| **Teacher** | A BBB moderator; not technical | Share a word with the class, see what students looked up, remove a bad example instantly, with no extra workload |
| **Owner (Diego)** | Builds and runs the system | Content that grows automatically, visible quality and costs, minimal operations |

## 4. Scope by release

| Release | Contents |
|---|---|
| **v1 (launch)** | English headwords with the es-419 layer, about **5k entries**; a BBB 4.0 plugin; lookup, search and context lookup; US/UK audio; saved words and review deck; use-it coach; tailored examples; explain the difference; teacher push; class word list; teacher hide/flag; provisional entries; automated QA |
| **v1.x** | **20k entries**; "Words on this slide"; end-of-class recap; stats for the owner |
| **v2** | Portuguese layer (pt-BR, added onto existing entries); admin dashboard |
| **v3** | Clips, once the rights question is decided |
| **v4** | Arabic layer (MSA with dialect tags) |
| **v5** | Spanish headwords (bidirectional) |

The order of v2–v5 is negotiable in the roadmap.

---

## 5. Functional requirements

### 5.1 Lookup and search (LK)

| ID | Requirement |
|---|---|
| LK-1 | **Autocomplete** from the 2nd character: up to 8 suggestions, each with headword, POS, CEFR and the first L1 equivalent |
| LK-2 | **Inflected forms** resolve to their entries: *went* → *go*. An ambiguous form shows every lemma (*leaves* → *leaf*, *leave*), ranked by frequency, or by context when there is a sentence |
| LK-3 | **Multiword expressions:** phrasal verbs, separable ones included (*turn it off*); idioms; collocations; fixed phrases (*by the way*); slot patterns (*give sb a hand*) |
| LK-4 | **Typo tolerance:** trigram matching with a phonetic fallback gives "Did you mean…?". When there are 2+ candidates, never silently replace the query |
| LK-5 | **Reverse lookup (L1 → EN):** Spanish input, inflected forms included (*me di cuenta* → *realize*), returns the English senses whose equivalents match, grouped by sense, with the matching equivalent highlighted |
| LK-6 | **Ambiguous language:** input that is valid in both languages (*actual*, *real*) returns two labeled groups; a direction toggle offers Auto / EN→ES / ES→EN |
| LK-7 | **Reverse dictionary:** a description of a meaning, in Spanish or English (*"una persona que deja todo para después"*), returns candidate entries, labeled as suggestions |
| LK-8 | **Context lookup:** a selected word plus its sentence returns entries ranked by fit; the best sense comes first, marked *"in this sentence"*; multiword expressions spanning the selection are detected (clicking *ran* in "she ran into her teacher" gives *run into*) |
| LK-9 | **L1 projection:** cards show only the student's L1 layer (from their profile, falling back to the BBB UI locale), and the student can switch it |
| LK-10 | **Missing word:** first validate the input (a real word or phrase? English? not junk or abuse?). If valid, a **provisional entry** appears within about 5 s, labeled *Beta*, and is queued for full generation. Invalid input gets suggestions or "not found", and nothing is generated |

### 5.2 Entry content (EC)

| ID | Requirement |
|---|---|
| EC-1 | **Header:** headword, POS, kind (word, phrasal verb, idiom, collocation…), CEFR (for the entry and for each sense), frequency band (top 1k / 3k / 5k / 10k / 20k), US and UK IPA with audio, inflections |
| EC-2 | **Senses:** learner definition in simple English; short gloss; L1 gloss; L1 equivalents (ranked, with match type, and region tags when they aren't es-419); register, region and domain labels; grammar patterns |
| EC-3 | **Examples:** 3 per sense (at least 2 for rare senses), each with an L1 translation and US audio, and the headword highlighted |
| EC-4 | **Collocations** (up to ~6 per sense) and common patterns |
| EC-5 | **L1 notes:** false friends, typical mistakes for that L1, pronunciation tips, only where genuinely useful |
| EC-6 | **Related:** synonyms and antonyms (with nuance), word family, confusables ("see also *currently*") |
| EC-7 | **Labels and safety:** offensive, vulgar or slang senses carry warnings; examples are never sexual, violent or hateful (the audience may be 16+) |
| EC-8 | **Provisional entries:** 1–2 senses with one example each, a *Beta* badge, IPA when known, and no audio until the final entry replaces them |

### 5.3 Learning features (LF)

| ID | Requirement |
|---|---|
| LF-1 | **Save** a *sense* (not just a word) to "My words" with one tap; the class it came from is recorded |
| LF-2 | **Review deck:** saved senses become flashcards scheduled with FSRS. Two card types in v1: L1 → English recall (L1 gloss plus an example with the word blanked out) and English → meaning. Students grade themselves (Again / Hard / Good / Easy) in the plugin's "My words" tab |
| LF-3 | **Use-it coach:** the student writes a sentence using the chosen sense and gets a verdict (correct / almost / incorrect), a corrected sentence when needed, and a one-line reason, all in their L1 and in ≤ 25 words. Default quota: 20 per student per day |
| LF-4 | **Tailored examples:** "More examples", optionally by topic (work, travel, health, technology, business, daily life, or free text) and level (A2–C1): 3 examples with L1 translations, cached per (sense, topic, level) and reused for everyone. Good ones get promoted into the entry |
| LF-5 | **Explain the difference:** pick two senses (from the confusables list or a search) and get an L1 contrast of ≤ 80 words with one example each. Cached per pair; the routine pre-generates the frequent pairs |
| LF-6 | **History:** my lookups from the last 30 days |

### 5.4 Teacher features (TF)

| ID | Requirement |
|---|---|
| TF-1 | **Push a word:** a moderator shares an entry or sense, and every participant gets the card in their own L1 (a notification that opens the panel) |
| TF-2 | **Class word list:** a live list of what was looked up in this meeting. Counts are aggregated and anonymous by default, and only moderators see it |
| TF-3 | **Hide / flag** any example, translation, note, pronunciation or whole sense, with a reason from §5.7 and an optional comment. **Hide now** removes the item for every student at once and queues it for repair. Only **verified teachers** can hide (LMS role `teacher` or `staff`), because BBB also makes some students moderators. **Flag for revision** changes nothing on screen and queues the item for repair with priority; any moderator can flag, and flags from moderators who aren't verified teachers are triaged first (CG-8). Both can be undone and have a daily cap per person (default 20), and every action is logged (who, when, why) |
| TF-4 | **Recap** (v1.x): at the end of class, the word list can be shared in chat or as a link |
| TF-5 | **Micro-review** (v1.x, optional): "Review 5 words from your class this week" shows the lowest-confidence entries the class actually looked up. The teacher gives each sense a thumbs up or down, and a thumbs down becomes a flag. Never required |

### 5.5 Content growth and quality (CG)

| ID | Requirement |
|---|---|
| CG-1 | **Seeding:** import word lists (yours plus the open lists) into deduplicated, normalized candidates, split by POS, each with a priority |
| CG-2 | **Factory runs:** each routine run you start takes the next batch by priority (default 40 entries), repairs first |
| CG-3 | **Automated QA:** validators, the in-run reviewer and server spot checks. Entries that pass publish automatically; failures get up to 2 repair attempts, then quarantine |
| CG-4 | **Provisional → final:** provisional entries get priority in the next runs, and the final entry replaces the provisional one under the same key |
| CG-5 | **Reports:** any student or teacher can report a problem with one specific item (a translation, example, meaning, note or pronunciation), picking a reason from §5.7 in their own language, with an optional comment. One report per person per item, a daily cap per student (default 10), and none from inactive accounts. A report never changes content by itself |
| CG-6 | **Golden set:** 50 entries you rate once, re-run on every prompt or model change; the QA thresholds are tuned against it |
| CG-7 | **Audits:** a sample of published entries is re-reviewed regularly, and every published entry is re-validated whenever the validators improve |
| CG-8 | **Triage and repair:** reports, and flags from moderators who aren't verified teachers, are triaged automatically (is it valid, which item, what's the likely fix). A valid one sends the entry to repair with the evidence attached; hides and verified teachers' flags go straight to repair. Three different people reporting the same item within 30 days send it to repair without waiting for triage. Every fix passes the normal QA (CG-3) before it's republished |
| CG-9 | **Reporter feedback:** students see their reports and what happened to each one ("My reports"), and get a notification when a report leads to a fix |
| CG-10 | **Behavior signals:** normal use raises an item's *suspicion score*, and items above a threshold are re-reviewed automatically. The signals: another sense opened right after the dictionary ranked one first; the same word looked up again and again; cards closed within a second; review cards failed unusually often; many coach failures on one sense; teacher hides clustered in one generation batch |

### 5.6 Operations (OP)

| ID | Requirement |
|---|---|
| OP-1 | **Stats endpoint:** counts by status and CEFR, misses, reports, AI spend, latency (the dashboard comes after v1) |
| OP-2 | **AI spend cap:** a hard monthly cap on paid providers ($5 by default), with automatic fallback to the free tier and an alert at 80% |
| OP-3 | **Backups** nightly, with a restore tested monthly |

### 5.7 Report reasons

Reports (CG-5), flags and hides (TF-3) all use one short list, shown in the reporter's language:

| Reason | Example |
|---|---|
| Wrong translation | The Spanish equivalent doesn't mean this |
| Wrong meaning | The definition doesn't match the word |
| Unnatural example | No one would say this sentence |
| Wrong pronunciation or audio | The IPA or the recording is off |
| Typo | A spelling mistake anywhere in the entry |
| Missing meaning | The meaning I needed isn't there (reported on the whole entry) |
| Hard to understand | The definition or note is too difficult for its level |
| Offensive or inappropriate | Unsuitable for the 16+ audience |
| Other | Anything else; a comment is required |

---

## 6. Content standard (editorial policy)

This is the product-level contract. The detailed style guide lives in `factory/STYLE.md` and must stay consistent with it.

### 6.1 Entry types and what each requires

An entry is one **lemma + part of speech** (*run* the verb and *run* the noun are 2 entries). The 20k target counts entries, not headwords.

| Kind | Example | Required beyond the base fields |
|---|---|---|
| word | *actually* (adv.) | Inflections, IPA, word family |
| phrasal_verb | *run into*, *turn off* | A pattern (separable? object position?) and its literal vs idiomatic senses |
| idiom | *break the ice* | A pattern with its fixed and variable parts; register; a literal-meaning note when it helps |
| collocation / fixed phrase | *make a decision*, *by the way* | The pattern, plus the most common variants |

**Target mix for 20k (to refine with your lists):** ~14k words, ~3k phrasal verbs, ~2k idioms, ~1k collocations and fixed phrases.

### 6.2 Rules

- **Senses:** only learner-relevant ones, ordered by frequency. At most 4 for most words; up to 6 for very polysemous A1–B1 verbs (*get*, *make*, *take*).
- **Definitions:** simple English from a ~2,000–3,000-word defining vocabulary, ≤ 20 words, never using the headword itself, one idea per sense.
- **Examples:**
  - 3 per sense, 6–16 words each, showing the typical pattern.
  - Natural adult contexts (work, study, travel, family, health); names and settings that include Latin America, Brazil and the Arab world.
  - **Suitable for ages 16+.**
- **L1 (es-419):**
  - Neutral Latin American Spanish, with regionalisms tagged (ES, MX, AR, CO…).
  - Equivalents are ranked and typed (exact / near / approx / paraphrase).
  - When there's no direct equivalent, say so and give a paraphrase.
- **L1 notes:** written in the L1 and short; only for false friends, typical mistakes and real pronunciation traps.
- **CEFR:** CEFR-J and Octanove levels are priors; the level is set per sense; levels estimated for long-tail words are flagged as estimates.
- **Pronunciation:** US and UK IPA, given per sense for homographs (*record*, *read*, *lead*).
- **Sensitive vocabulary:** included only when common and useful, always labeled, with neutral examples.
- **Originality:** nothing is copied or closely paraphrased from other dictionaries.

---

## 7. Non-functional requirements (NF)

| ID | Area | Requirement |
|---|---|---|
| NF-1 | Performance | Server time at p95: lookup < 150 ms; autocomplete < 80 ms; context lookup < 300 ms. Provisional entry < 8 s; AI features < 5 s |
| NF-2 | Capacity | ≥ 50 concurrent students on the t4g.medium API host + Supabase Micro, with a one-step resize path for each |
| NF-3 | Availability | A single node, targeting ≥ 99% per month during class hours. AI features fail soft: a lookup never depends on an LLM |
| NF-4 | Payload | An entry card is ≤ 15 KB gzipped; images and clips are never inline |
| NF-5 | Accessibility | Plugin meets WCAG 2.2 AA: keyboard operable, screen-reader labels, visible focus; layout ready for RTL (Arabic later) |
| NF-6 | i18n | Plugin UI in English and Spanish at v1, with Portuguese and Arabic later; all strings externalized |
| NF-7 | Privacy | Data minimization and pseudonymous external IDs. Lookups are kept 12 months; use-it coach inputs 30 days; reports 12 months after they're resolved; behavior signals 90 days; deletion on request. Before enrolling anyone under 18: a privacy check of the age rules where students live, plus each AI provider's terms |
| NF-8 | Security | Identity only from the BBB proxy; no secrets in the browser; rate limits per student and per IP; moderator actions logged |
| NF-9 | Cost | Infrastructure ≈ $30–35/month (a dedicated EC2 + a Supabase project; see `05` §9); runtime AI ≤ $5/month (hard cap); content generated through the Max 5x routine |
| NF-10 | Maintainability | One backend language; typed contracts (OpenAPI → TS); automated tests and content evals in CI |

---

## 8. UX outline (BBB plugin)

### 8.1 Surfaces

1. **Entry points:** a *Dictionary* button in the actions bar; a *Look up* popover when text is selected in chat; a keyboard shortcut.
2. **Floating panel** (movable, resizable):
   - a search box with a direction toggle (Auto / EN→ES / ES→EN);
   - results;
   - the entry card;
   - tabs: Senses · Notes · My words (and Clips later).
3. **Entry card:**
   - **Header:** word, POS, CEFR, US/UK audio buttons.
   - **L1 equivalents** shown prominently.
   - **Senses** as an accordion, each with ★ *Save*, *Use it*, *More examples* and *Compare*.
   - **⚑ Report** on every meaning, example, translation, note and pronunciation, and once for the whole entry (for a missing meaning). It opens the reason list (§5.7): two taps, plus an optional comment.
4. **My words:** saved senses, *Review now (N due)*, and *My reports* with the status of each report.
5. **Teacher tools:** *Push to class* on every card, a *Class words* list, and *Flag* on every example, translation, note, pronunciation and sense (moderators). *Hide* also appears for verified teachers.
6. **Notifications** for students: a pushed word (tap to open the card), and "your report fixed *actually*".

### 8.2 Key flows

| Flow | Steps |
|---|---|
| **F1. Chat lookup** | Select a word in a chat message → *Look up* → the panel opens with context-ranked senses (LK-8) and the matching L1 equivalent highlighted |
| **F2. Teacher push** | The teacher searches a word → *Push to class* → each student's notification opens the card in their L1 → the word lands in *Class words* |
| **F3. Use-it coach** | On a sense, tap *Use it* → write a sentence → get a verdict, a correction and a reason in L1 → optionally save the sense |
| **F4. Review** | *My words* → *Review now* → cards in the FSRS order → grade each → next due date shown |
| **F5. Missing word** | Search a word with no entry → a *Beta* card appears within seconds → the full entry replaces it after a later routine run |
| **F6. Bad content** | A verified teacher taps *Hide* on an example and picks a reason → it disappears for everyone → a later routine run repairs it → it's republished after QA |
| **F7. Student report** | A student taps ⚑ on a translation → picks *Wrong translation* → automatic triage → if it's valid, a later run fixes it → the student gets "your report fixed *…*" |

---

## 9. Success metrics (measured from logs)

| Metric | v1 target |
|---|---|
| Miss rate (lookups with no entry) | < 10% at 5k entries; < 4% at 20k |
| Lookup latency | p95 < 150 ms on the server; < 1 s from opening the panel to seeing a result |
| Content quality | Reports < 2 and teacher hides < 1 per 1,000 lookups; golden-set agreement ≥ 90% |
| Quality response | Median ≤ 3 days from a valid report or flag to the fix being republished; ≥ 50% of student reports triaged as valid (fewer suggests the report flow confuses people) |
| Engagement | ≥ 30% of students in a class look something up; ≥ 20% save at least one word per week |
| Learning | ≥ 80% recall at 7 days among students who review |
| Cost | AI ≤ $5/month; infrastructure ≈ $30–35/month |
| Content | 5k published at launch; 20k afterwards, at a pace the pilot will measure (§3 of the roadmap) |

---

## 10. Assumptions and constraints

- BBB 4.0, with root access to its nginx (for the identity proxy).
- The LMS passes `persons.person_id` as BBB's `userID` (verified in `05` §2.1).
- Hosting is AWS us-east-2 (a dedicated EC2) plus a dedicated Supabase project; the Oracle A1 is only an optional batch node (`05`).
- Claude Max 5x, with routine runs started manually during the day.
- Clips are postponed, so the media library isn't used in v1.
- Students are adults today, 16+ possible later.

## 11. Open product questions

1. Product name and branding.
2. Whether the review deck should also live outside BBB (a portal page) after v1.
3. How the recap is delivered (chat message, link or email), for v1.x.
4. Whether students may see the class word list. The default is moderators only.

## Glossary

| Term | Meaning |
|---|---|
| **Entry** | One lemma + part of speech (e.g. *run*, verb) with all its senses |
| **Sense** | One meaning of an entry; sense keys never change once published |
| **Equivalent** | An L1 word or phrase that translates one sense |
| **L1** | The student's first language (es-419 first) |
| **MWE** | Multiword expression: phrasal verb, idiom, collocation, fixed phrase |
| **Provisional entry** | A quick *Beta* card for a missing word, replaced later by the full entry |
| **Routine** | A Claude Code cloud run of the content factory, started by you |
| **Golden set** | The 50 entries you rate, used to calibrate and regression-test quality |
| **FSRS** | Free Spaced Repetition Scheduler, the algorithm behind the review deck |
| **Hide / flag** | A teacher's action on one item: a hide (verified teachers only) removes it at once; a flag (any moderator) only queues it for repair |
| **Report** | A student's or teacher's note that one item is wrong; it never changes content by itself |
| **Suspicion score** | How likely an item is to be wrong, from reports, flags and behavior signals; above a threshold the item is re-reviewed automatically |
| **Verified teacher** | Someone whose LMS role is `teacher` or `staff`, not just a BBB moderator |
