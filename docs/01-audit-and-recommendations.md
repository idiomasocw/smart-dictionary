# Smart Video Dictionary: Audit & Re-foundation Proposal

> **Status:** draft for discussion · **Date:** 2026-10-18 · **Covers:** current code, product concept, architecture, data model, AI pipeline, infrastructure
> **Next documents:** `02-product-spec.md`, `03-system-design.md`, `04-roadmap.md` (we write these together after you react to this one)

## 0. TL;DR

1. **Verdict.** The current code is a three-day prototype. It has the right instincts (just-in-time generation, clips linked to senses, structured LLM output) on fragile foundations: a thin schema, no quality control, a racy pipeline and inaccurate clip cuts. **Re-found it: keep the ideas, not the code.**
2. **Product.** An **L1-aware, context-aware, in-class dictionary that grows itself.** It beats WordReference and Cambridge on the three things they lack: the learner's L1, the sentence the word came from, and the classroom.
3. **Stack.** Python (FastAPI) + PostgreSQL 18 (pgvector, pg_trgm) + a Postgres job queue, with a React/TS BBB plugin. Drop NestJS, GraphQL, BullMQ/Redis, Supabase, Deepgram and ElevenLabs.
4. **Schema.** One validated JSON document per entry, plus query projections. **Stable keys** mean regenerating an entry never breaks its clips or students' saved words. A reverse index of L1 equivalents gives ES→EN and PT→EN lookup, and the schema is ready for Spanish headwords later.
5. **Content factory.** Grounded generation by Claude, then deterministic validators, a cross-model judge, and automatic publish, repair or quarantine. **Start at 40 headwords per routine run (4 sub-agents × 10)** and calibrate against the usage meter.
6. **Three flags you need to know:**
   - **Oracle's Always Free A1 is now 2 OCPU / 12 GB** (per Oracle's own page). Please confirm what your instance actually has.
   - **Anthropic's terms** say subscription limits assume "ordinary, individual usage". Bulk production for a commercial product is a grey area: confirm with Anthropic, or do the bulk run on the Batch API (≈ $90 with Sonnet 5.5 for the 7.5k-entry core).
   - **"3–4 s clips are fair use" is not a safe assumption**, especially in Spain, Latin America and Brazil. Start clips with public-domain and your own content.
7. **AI at request time.** Lookups need no LLM (spaCy + embeddings + templates). The "use it" coach goes through a capped gateway to Haiku 5.5 or DeepSeek (~$1–2/month). A local model on the free A1 would take 5–20 s per answer and starve the API.
8. **GPU credits are mostly unnecessary.** Kokoro TTS and Parakeet ASR run on CPU, and your PC plus Modal's $30/month credit cover the heavy jobs. Kaggle's terms are non-commercial.

### Straight answers to your questions

| You asked | Short answer | § |
|---|---|---|
| Better stack, architecture, schema? | Yes: Python + Postgres, a document-plus-projections schema, and a Postgres queue | 4, 5 |
| Is `spacy-nlp-service` still useful? | **spaCy yes**, as an in-process library for sentences and transcripts. **The service no**: single words resolve through precomputed form tables | 4.4 |
| Is Supabase a good main DB? | Postgres yes. Supabase no, for this setup: the free tier caps at 500 MB and pauses, and self-hosting takes ~2 GB RAM for features we don't use | 13.2 |
| Bilingual support? | L1 equivalents per sense + a reverse index + L1 form tables + multilingual embeddings. ES↔EN now; PT and AR are additive layers | 5, 6 |
| How do we beat WordReference / Cambridge? | Notes specific to each L1, context-aware senses, phrasal-verb detection, sense-matched clips, in-class features, and a dictionary that grows itself | 3 |
| A local LLM for the AI features? | Offline, yes. In the live path on the free A1, no: use a ~$1–2/month API through a capped gateway | 10 |
| Docker on the Ampere? | Yes: Docker Compose + Caddy with arm64 images, fitting in 12 GB | 13 |
| Clips optional? | Yes: `?include=clips` or a lazy endpoint, gated by rights | 8 |
| Free GPU credits? | Barely needed. Your PC + Modal's $30/month; avoid Kaggle for production (non-commercial terms) | 14 |
| Entries per Claude routine run? | 40 (4 sub-agents × 10), calibrated on usage; or a one-off Batch run | 7.4 |
| LangChain? Jev? | LangChain, no. Jev is real and worth an A/B test for typed decisions (clip senses, QA gates), not for generation | 7.6 |
| Admin dashboard? | Yes, lean, after the plugin MVP; teacher moderation inside the plugin first | 12 |

---

## 1. What I reviewed, and what you told me

**Reviewed:** every tracked file in the repo (39 files, 8 commits between 2026-03-01 and 2026-03-03): the NestJS API and worker, the spaCy service, the Supabase config and migration. I also checked the current state (October 2026) of the tools that matter here: BBB plugins, Claude Code routines, free GPU programs, TTS/ASR/embedding models, lexical datasets and their licenses.

**Your interview answers, and what each one changes:**

| Topic | Your answer | Design consequence |
|---|---|---|
| Direction | English first; Spanish/Portuguese headwords later | Language-neutral schema now, English-centric content first |
| Media library | Mixed: documentaries and TV shows, some public domain, some not; 3–4 s clips | Rights status tracked per source; clips roll out on cleared sources first (§8.2) |
| Server | Oracle Always Free A1. ⚠️ I described that option as 4 OCPU / 24 GB, but Oracle's page now says the free allowance is **2 OCPU / 12 GB** for all tenancies (cut in June 2026). Please confirm your instance's real shape (§16) | Everything must fit in 12 GB; no LLM in the request path; heavy batches run elsewhere (§13, §14) |
| Claude plan | Max 5x | Sized batches with sub-agents, scheduled off-hours (§7.4) |
| AI budget | $0 now; up to ~$5/month (e.g. DeepSeek) if the local model is too slow; ~3 concurrent students; 15–20-word answers | An AI gateway: a cheap API first (~$1–2/month), capped at $5, free tiers as backup, aggressive caching; a local model only offline (§10) |
| Human review | None, fully automated | Automated QA gates, plus one-click flagging by teachers inside classes (§7.5) |
| Students | A stable ID reaches BBB from your portal/LMS | Saved words, history and review decks per student (§11) |
| Backend language | Your call to me | **Python** backend; TypeScript only for the BBB plugin and dashboard (§4) |

---

## 2. Audit of the current implementation

### 2.1 Inventory

| Component | What it does today | Size | State |
|---|---|---|---|
| `smart-dictionary-backend/apps/app` | NestJS + Apollo GraphQL API (`searchWord`, `getWordsBatch`), 7-minute cron dispatcher | ~250 LOC | Exact-lemma lookups work; the just-in-time path has bugs |
| `smart-dictionary-backend/apps/worker` | BullMQ workers: Gemini 2.5 Flash "lexicographer"; Deepgram + ffmpeg video enrichment | ~320 LOC | Lexicographer works; the video pipeline is a stub |
| `spacy-nlp-service` | FastAPI `POST /lemmatize` using spaCy `*_sm` models | ~70 LOC | Works, but the approach is flawed (§2.3 H5) |
| `supabase/` | Local Supabase CLI config + one migration (6 tables) | 111 lines SQL | Thin schema: no indexes, RLS or seed data |
| Tests, CI, Docker, docs | Nest scaffold tests only | — | Missing |

### 2.2 Ideas worth keeping

1. **Just-in-time generation of missing words.** A lookup miss should turn into an entry. Keep the idea, but gate and validate it.
2. **Clips linked to a specific meaning (sense), not just a word.** This is the best modeling insight in the repo (`video_clips.definition_id`).
3. **Schema-constrained LLM output** instead of free text.
4. **API separated from background workers**, with a queue-driven pipeline and a status machine for seeding.

### 2.3 Findings, by severity

**Critical**

- **C1. An unauthenticated search writes to the dictionary and spends money.** `searchWord` inserts any query string as a new lemma and enqueues a paid LLM job ([words.service.ts:92-108](../smart-dictionary-backend/apps/app/src/words/words.service.ts#L92)). Typos, gibberish, other languages, prompt-injection text and scripted abuse all become "words" and LLM calls. There is no auth, rate limit or validation, and queries over 255 characters fail the `VARCHAR(255)` column.
- **C2. Regenerating a definition destroys its clips.** `video_clips.definition_id … ON DELETE CASCADE` ([migration:96](../supabase/migrations/20260302020640_initial_dictionary_schema.sql#L96)), combined with AI-regenerated definitions that get new UUIDs, means any regeneration silently deletes the most expensive asset in the system. Senses need **stable keys** that survive regeneration.
- **C3. Writes are neither transactional nor idempotent.** The lexicographer marks the word `COMPLETED` *before* inserting its definitions ([lexicographer.processor.ts:140-146](../smart-dictionary-backend/apps/worker/src/processors/lexicographer.processor.ts#L140)), skips failed definition inserts (164-167) and never checks the examples insert (177). A crash or a retry leaves partial or duplicated entries. Multi-table writes through PostgREST (supabase-js) can't be wrapped in a transaction without writing SQL RPC functions.
- **C4. Clip cutting is inaccurate by design.** `-ss` before `-i` combined with `-c copy` ([video.processor.ts:122-127](../smart-dictionary-backend/apps/worker/src/processors/video.processor.ts#L122)) snaps cuts to keyframes, so clips can start or end seconds away from the target word, often on frozen frames. Word-accurate clips require re-encoding.

**High**

- **H1. Two dispatch paths race each other.** The JIT path enqueues a job but leaves the word `PENDING`; the cron also picks up `PENDING` rows ([cron.service.ts:29-33](../smart-dictionary-backend/apps/app/src/cron/cron.service.ts#L29)), so the same word can be generated twice. The `UNIQUE(…, meaning)` constraint only blocks *identical* wording.
- **H2. The user-facing job has the lowest priority.** JIT jobs get `priority: 1` ([words.service.ts:104](../smart-dictionary-backend/apps/app/src/words/words.service.ts#L104)) while cron jobs get none. BullMQ's docs are explicit: *"Jobs without a priority assigned will get the highest priority, being processed before jobs with priorities assigned to them."* The student who triggered the job waits behind the whole seeding backlog, while the frontend is promised "check back in 10 seconds".
- **H3. The cron dispatcher can't scale or recover.** It runs inside the API process and selects rows without locking (no `FOR UPDATE SKIP LOCKED`), so a second API replica double-dispatches. Rows stuck in `PROCESSING` are never recovered, and `FAILED` is terminal because no retries are configured.
- **H4. Translations are one string per language** ([migration:46](../supabase/migrations/20260302020640_initial_dictionary_schema.sql#L46), 64). That leaves no room for several equivalents, regional variants (*computadora* vs *ordenador*), usage notes, or Spanish→English lookup, because nothing indexes JSON values. The prompt is also ambiguous about whether `translations` means the word's equivalent or a translation of the definition text, so outputs will be inconsistent.
- **H5. The lemmatization approach is unreliable.** Context-free lemmatization of a single word returns one guess ("leaves" → *leave* or *leaf*? "saw" → *see* or *saw*?). Lemmatizing phrases token by token corrupts fixed expressions ("best of both worlds" → "good of both world"), hyphenated words ("well-known" → "well - know") and contractions ([main.py:56](../spacy-nlp-service/main.py#L56)). Lowercasing breaks proper nouns and acronyms (*Polish*/*polish*, *US*/*us*). `sm` models are the least accurate, and the `async` endpoint runs blocking CPU work on the event loop (main.py:45).
- **H6. Generation is ungrounded and unchecked.** The prompt ([lexicographer.processor.ts:73-84](../smart-dictionary-backend/apps/worker/src/processors/lexicographer.processor.ts#L73)) asks the model to invent senses, CEFR levels and inflections from nothing: no frequency data, no reference sense inventory, no CEFR lists, no corpus evidence. Nothing is validated beyond `JSON.parse`, so hallucinated senses and arbitrary CEFR levels go straight to production.
- **H7. Generation wastes tokens.** Every call asks for translations into en/es/fr/de/it/pt (lines 35-42, 99): three languages you don't need, and no Arabic. Roughly half the output is waste, and longer outputs fail more often. `temperature: 0.1` (line 111) also makes example sentences repetitive and bland.
- **H8. There is no seed data.** The `languages` table is never populated (`config.toml:65` references a `seed.sql` that doesn't exist), so on a fresh database every word insert fails its foreign key.

**Medium**

- **M1.** `languages.code VARCHAR(2)` can't express varieties (es-419/es-ES, pt-BR/pt-PT, en-US/en-GB, ar/ar-EG). Use BCP 47 tags.
- **M2.** Multiword expressions (phrasal verbs, idioms, collocations, slot patterns like *give sb a hand*, separable verbs like *turn it off*) are not first-class.
- **M3.** `words.conjugations` is unindexed JSONB and unused by lookup, even though the comment calls it "lightning-fast". Forms need their own indexed table, with grammatical features.
- **M4.** Audio hangs off definitions, with no IPA and no US/UK variants. Homographs with different pronunciations (*record* n. /ˈrek-/ vs v. /rɪˈk-/) aren't modeled.
- **M5.** Pipeline state is mixed into the content table, with no provenance (model, prompt version, cost), no revisions, no QA scores and no publish states.
- **M6.** `UNIQUE(word_id, part_of_speech, meaning)` dedupes on free text, which is fragile and can exceed btree's maximum entry size on long definitions. `part_of_speech` and `cefr_level` are free text with no constraints.
- **M7.** `video_status` has no `READY` value, but the code sets it ([video.processor.ts:61](../smart-dictionary-backend/apps/worker/src/processors/video.processor.ts#L61)). supabase-js returns errors rather than throwing them, so this fails silently.
- **M8.** GraphQL is a poor fit for a read-heavy lookup API. POST queries bypass HTTP/CDN caching, the playground is enabled in production ([app.module.ts:21](../smart-dictionary-backend/apps/app/src/app.module.ts#L21)), the `lemmas` list is unbounded, and `nativeLanguage` must be repeated on every nested field.
- **M9.** Search is exact lemma equality only: no autocomplete, typo tolerance, inflected-form lookup, reverse (L1→EN) lookup or meaning-based search.
- **M10.** The video pipeline is mostly a stub. Nothing produces `video-jobs`, matching and saving are placeholders (lines 108-136), the whole WAV file is loaded into memory (line 89), and temporary clips are never deleted. Deepgram nova-2 is paid and an older model, while free local options now match it (§8).
- **M11.** Redis + BullMQ are there only to move about 20 jobs every 7 minutes: one more stateful service with no real benefit at this scale.

**Low**

- **L1.** Config fails open: there are four copy-pasted Supabase clients, each falling back to `'mock-key'` and localhost, so a misconfiguration shows up as runtime errors instead of failing at startup.
- **L2.** The worker boots an HTTP server it never uses ([worker/main.ts:5-6](../smart-dictionary-backend/apps/worker/src/main.ts#L5)).
- **L3.** Hygiene: `spacy-nlp-service/.env` (no secrets in it) and `__pycache__` are committed; there's no root `.gitignore`; the README is Nest boilerplate; the worker e2e test asserts a route that doesn't exist.
- **L4.** There are no Dockerfiles, compose file, CI or health checks, and nothing accounts for ARM64 builds, which matters because the target server is ARM.

### 2.4 Verdict

This is a three-day prototype of about 600 lines. Its instincts are right, but its foundations aren't ready: the content model can't express what a learner's dictionary needs, and the pipeline has no quality control. Because so little code exists, **a clean re-foundation is cheaper than a retrofit. Keep the ideas, not the code.**

---

## 3. Product concept: an in-class vocabulary companion

**Positioning:** *an L1-aware, context-aware English learner's dictionary that lives inside your BBB classes and builds itself.*

WordReference is a bilingual glossary with forums; Cambridge is a monolingual learner's dictionary with a bilingual add-on. Both are static websites you leave the class to visit. Our advantages come from three things they don't have: the **learner's L1**, the **sentence the word came from**, and the **classroom**.

| Capability | WordReference | Cambridge | This dictionary |
|---|---|---|---|
| CEFR level per sense | — | ✓ | ✓ every sense |
| L1 equivalents with regional variants | ✓ (forum quality) | partial | ✓ ranked, region-tagged |
| False friends, typical mistakes, pronunciation pitfalls **for your L1** | forums only | rare | ✓ built in, per L1 |
| Picks the right sense for the sentence you're reading | — | — | ✓ |
| Spots phrasal verbs and idioms in context (*ran into* → *run into*) | — | — | ✓ |
| Real-world clips matched to the sense | — | — | ✓ (optional) |
| Examples tailored to level and topic (e.g. "for a nurse") | — | — | ✓ on demand, cached |
| In-class: teacher pushes a word, class word list, post-class review | — | — | ✓ |
| Grows itself: a miss becomes an entry within minutes | — | — | ✓ |

### 3.1 Surfaces and features

**Student, inside BBB (plugin):**
1. Lookup panel with autocomplete; their L1 shows automatically, taken from their profile.
2. Click any word in chat (or captions, if available) for a context-aware result: the right sense first, phrasal verbs detected.
3. Entry card: CEFR, IPA + audio (US/UK), learner definition, L1 gloss and equivalents, examples with translations, collocations, notes for their L1 (false friends, typical errors), clips on demand.
4. Reverse lookup: type Spanish or Portuguese and get English equivalents. A **reverse dictionary** too: describe the meaning ("una persona que deja todo para después") and get *procrastinator*.
5. **Save** a word to a personal list, and get a spaced-repetition review deck after class.
6. **"Use it" coach**: write a sentence with the word and get a 15–20-word verdict in their L1.

**Teacher (BBB moderator):**
1. **Push a word to everyone.** Each student sees the card in their own L1.
2. **Live class word list** of what students looked up, ending in a recap at the end of class.
3. **One-click hide/flag** of a bad example, translation or clip. This is the human feedback loop, without any review workload.

**Academy (admin):** coverage, quality, pipeline health, top lookups and misses, by level and by class.

### 3.2 Precompute first, generate at request time only when needed

The 4-core server and the $0 budget make this the central design rule. The table also answers "can a local LLM help?": yes, but mostly **offline**.

| Feature | Computed when | Engine |
|---|---|---|
| Definitions, equivalents, examples, notes, collocations | Offline (content factory) | Claude Opus via scheduled routine |
| Audio (headwords + examples) | Offline | Kokoro TTS on CPU: your PC or the server (§9) |
| Clips | Offline | Parakeet ASR on your PC's CPU or Modal credits; ffmpeg cutting (§8) |
| Embeddings (senses, examples) | Offline | small multilingual embedding model on CPU |
| Sense ranking for a clicked word in a sentence | Request time | spaCy + embeddings (no LLM, ~20 ms) |
| "Here it means…" one-liner | Request time | **template** from the ranked sense; no LLM needed |
| Sentence check ("use it" coach) | Request time, rate-limited | AI gateway: Haiku 5.5 or DeepSeek (~$1–2/month), Groq free tier as backup (§10) |
| More or tailored examples | Request time, cached permanently (good ones get promoted into the entry) | AI gateway |
| Missing word | Request time → provisional entry in seconds, final entry later | cheap model now, Claude in the next routine run |

### 3.3 The self-growing loop

```
student lookup misses
  → candidate check: real word/phrase? which language? (word lists, Wiktionary forms, cheap LLM for phrases)
      ├─ rejected (typo → "did you mean…", gibberish, out of scope) → logged, never generated
      └─ accepted → provisional entry in seconds (cheap model, labeled "beta")
                    → queued for the next Claude routine run, priority = demand
                    → full entry replaces it after automated QA
```

---

## 4. Target architecture and stack

### 4.1 Principles

1. **Precompute everything that can be precomputed.** The request path does lookups, not generation.
2. **One database** (PostgreSQL) for content, search, vectors, jobs, learners and usage.
3. **One backend language** (Python). TypeScript only runs in browsers.
4. **Deterministic first, AI second.** Every AI artifact carries its provenance and a QA score.
5. **Stable keys** for everything that users and media point at.
6. **Heavy batch work runs where compute is free** (your PC, Modal credits), not on the small server.
7. **Every external AI provider is replaceable** through config, because free tiers and models change monthly. GitHub Models was retired in July and Cerebras's free tier became a trial this year.

### 4.2 System overview

```
                ┌──────────────────── your BBB server ─────────────────────┐
 student /      │ BBB client + dictionary plugin (React/TS, plugin SDK)    │
 teacher  ──────┼─▶ nginx /dictionary/*  ─ auth_request ─▶ BBB session check│
 browser        │    (adds verified meeting / user / ext-id / role headers)│
                └───────────────────────────┬──────────────────────────────┘
                                            │ HTTPS + shared secret
┌──────────────────────────── Oracle A1 · Docker Compose ─────────────────────────────┐
│ Caddy (TLS, static admin SPA)                                                       │
│   └▶ api (FastAPI) ── lookup · suggest · entry · reverse · context · learner data   │
│        ├ spaCy + embedding model, in-process on CPU                                 │
│        ├ AI gateway: cache · per-student quotas · $ cap ──────────▶ Haiku 5.5 / DeepSeek / Groq (free)
│        └ factory endpoints: batches out, entries in ◀─────────────── Claude routine or Batch API
│ worker (Procrastinate): ingest · validate · judge · project · embed · TTS · clip jobs│
│ postgres 18: entries(doc) · projections · pgvector · jobs · learners · usage        │
└───────────────────────────────────────────┬─────────────────────────────────────────┘
                                            │
        S3: media library · clips · audio · nightly backups
        batch nodes: your PC (ASR, TTS, clip cutting) · Modal credits (GPU bursts)
```

### 4.3 Recommended stack

You left the backend language to me. I chose **Python** because nearly all the backend work is NLP, ML and media (spaCy, ONNX embeddings, Kokoro, Parakeet, ffmpeg), and Pydantic models double as the JSON Schemas we hand to the LLMs. That keeps one language on the server. TypeScript stays where it's mandatory (the BBB plugin) and natural (the dashboard), with clients generated from OpenAPI.

| Layer | Choice | Why | Replaces |
|---|---|---|---|
| API | Python 3.13, **FastAPI**, Pydantic v2 | Typed models shared with the pipeline; OpenAPI for free | NestJS + Apollo |
| API style | **REST + OpenAPI 3.1**, `?include=` expansion, ETags | Cacheable GETs and a trivial plugin client | GraphQL |
| Database | **PostgreSQL 18** + pgvector, pg_trgm, unaccent, fuzzystrmatch | One store for content, search, vectors, queue and analytics | Supabase |
| Jobs | **Procrastinate** (Postgres-backed: periodic tasks, retries, locks) | No Redis; jobs enqueue inside the same transaction as the data | BullMQ + Redis + Nest cron |
| NLP | **spaCy 3.8** (`en_core_web_md`), in-process | Context lookups, transcript indexing (§4.4) | `spacy-nlp-service` |
| Morphology | UniMorph + LemmInflect (EN), mlconjug3/UniMorph (ES/PT), CAMeL Tools (AR, later) | Precomputed form tables | LLM-invented "conjugations" |
| Embeddings | Bake-off between **EmbeddingGemma 2** (Apache-2.0) and **bge-m3** (MIT), ONNX on CPU | Cross-lingual search, sense ranking | — |
| Generation | **Claude** (routine or Batch API, §7.4) | Best quality for definitions, examples and L1 notes | Gemini 2.5 Flash |
| QA judge | A different model family (Gemini free tier or Jev, §7.6), with Haiku 5.5 as fallback | Automated review | none |
| Runtime AI | Gateway → Haiku 5.5 / DeepSeek / Groq free / optional local model (§10) | Cheap, fast, capped | — |
| TTS | **Kokoro-82M** (Apache-2.0, CPU, IPA control) | Free; pronunciation guaranteed by IPA | ElevenLabs |
| ASR | **Parakeet TDT 0.6B v3** (CC-BY-4.0) on your PC's CPU or Modal; aligners for existing subtitles | Free, word timestamps | Deepgram nova-2 |
| Media | System ffmpeg, re-encoded cuts | Accurate clips | `@ffmpeg-installer` + copy cuts |
| Frontends | React + TypeScript + Vite; BBB plugin SDK; dashboard with TanStack + shadcn/ui | — | — |
| Contracts | OpenAPI → TS client (openapi-typescript + openapi-fetch) | One source of truth for types | hand-written models |
| Infra | Docker Compose, Caddy, GitHub Actions, S3 backups, Uptime Kuma | Simple single-node operations | none |
| Tooling | uv, ruff, pytest, pyright · pnpm, vitest | Fast, current | — |

**Dropped, and why:** NestJS and GraphQL (no caching benefit and more ceremony for a lookup API); BullMQ and Redis (a Postgres queue covers this scale); Supabase (§13.2); Deepgram and ElevenLabs (paid, and free local models now match them for this job); the standalone spaCy service (§4.4); LangChain (§7.6).

### 4.4 Is `spacy-nlp-service` still useful?

**Keep spaCy, retire the service.** It solves a real problem, mapping what people type or say to dictionary forms, but it uses the wrong tool for half of it:

| Problem | Best tool | Why |
|---|---|---|
| A single typed word (`went`, `leaves`) | **Precomputed form tables** (UniMorph, LemmInflect) | Exact, about 1 ms, and returns **every** possible lemma (*leaves* → *leaf* and *leave*) instead of one guess |
| A typed L1 word (`me di cuenta`) | Precomputed L1 form tables (mlconjug3/UniMorph), with embeddings as fallback | Same reasons; no Spanish parser needed at request time |
| A sentence (chat context, transcripts) | **spaCy as an in-process library**: lemmas + dependency parse, `Matcher` patterns compiled from `expression_patterns` | Finds *gave it up* → *give up*; fast enough to scan millions of transcript tokens on CPU, which no LLM is |
| Meaning and the long tail | Embeddings, with an LLM as the last resort | — |

**Never** lemmatize idioms token by token or lowercase everything. **Don't** pay a network hop per keystroke. Alternatives I considered and rejected: Stanza (slower; its Arabic models are trained on non-commercially licensed data), UDPipe (non-commercial models), and LLM lemmatization (too slow and costly at transcript scale).

### 4.5 Repository layout

```
smart-video-dictionary/
├─ backend/      Python (uv): core/ (EntryDoc models, search, projections), api/, worker/, cli/
├─ factory/      content factory: style guide, prompts, JSON Schema, validators, routine instructions
├─ plugin/       BBB plugin (React + TS + Vite)
├─ admin/        dashboard SPA (later phase)
├─ data/         importers for open datasets (scripts and checksums, not the data)
├─ infra/        docker-compose, Caddyfile, backup scripts, GitHub Actions
└─ docs/         this audit, specs, ADRs
```

---

## 5. Data model v2

### 5.1 Design choice: entry documents plus query projections

An entry is **produced** as a document (the AI emits one JSON per headword), **served** as a document (the API returns one card), and **regenerated** as a whole. So the source of truth is a **validated JSON document per entry**, with a revision history. Only the parts we actually *query* get projected into relational tables: inflected forms, multiword patterns, senses with their embeddings, and L1 equivalents. Projections are rebuilt from the document on every publish and never edited by hand.

This beats fully normalizing every nested field (glosses, example translations, notes, collocations): we never query inside those, and normalizing them would multiply tables, joins and ORM code for no benefit.

**Stable keys are the rule that makes it safe.** Every entry, sense and example has a human-readable key that never changes once published (`en:actually:adv`, `en:actually:adv#1`, `en:actually:adv#1.ex2`). Clips, audio, saved words, review cards and reports point at **keys**, not row IDs. Regenerating an entry keeps the keys of senses whose meaning didn't change; senses that disappear are soft-retired, never cascade-deleted. That fixes C2.

### 5.2 Entity overview

```
languages ─< varieties                          (en, es, pt, ar · en-US, en-GB, es-419, es-ES, pt-BR, ar …)

entries (doc JSONB, status, quality, priority) ─< entry_revisions (doc snapshot + provenance + QA)
   │
   ├─< entry_forms           went → go · leaves → leave/leaf      (lookup of inflected input)
   ├─< expression_patterns   run + into (separable? gap?)         (phrasal verbs/idioms in sentences & transcripts)
   └─< senses (key, CEFR, domains, embedding)
          ├─< equivalents (variety, term, rank, match type)  ← reverse index: "darse cuenta" → realize#1
          ├─< sense_links  (EN sense ↔ ES/PT sense, once those entries exist: bidirectional phase)
          ├─< clips ── media_sources (rights_status) ─< utterances (word timings, lemmas)
          └─< audio_assets (also for entries/examples)

users (external_id from your portal) ─< lookups · saved_items · review_cards · reports
misses · ai_cache · llm_calls · jobs (queue tables)
```

### 5.3 DDL sketch (PostgreSQL 18)

Abridged: the full DDL goes in the system design document.

```sql
create extension if not exists vector;        -- pgvector: sense/example embeddings
create extension if not exists pg_trgm;       -- typo tolerance, fuzzy autocomplete
create extension if not exists unaccent;      -- accent-insensitive Spanish/Portuguese
create extension if not exists fuzzystrmatch; -- phonetic fallback (dmetaphone) for misspellings

create table languages (code text primary key, name text not null,
                        direction text not null default 'ltr' check (direction in ('ltr','rtl')));
create table varieties (tag text primary key,             -- BCP 47: en-US, en-GB, es-419, es-ES, pt-BR, ar
                        language text not null references languages(code), name text not null);

create type entry_status as enum ('candidate','queued','generating','validating',
                                  'published','provisional','needs_repair','quarantined','retired');
create type entry_kind   as enum ('word','phrasal_verb','idiom','collocation','compound','abbreviation','proper_noun');

create table entries (
  id            uuid primary key default uuidv7(),
  key           text not null unique,                 -- 'en:run-into:phrv' (stable forever)
  language      text not null references languages(code),
  lemma         text not null,                        -- display form: 'run into', 'give sb a hand'
  lemma_norm    text not null,                        -- casefolded, unaccented, apostrophes normalized
  pos           text not null,                        -- closed set (UD-based), enforced by check constraint
  kind          entry_kind not null default 'word',
  homograph     smallint not null default 1,
  cefr          text check (cefr in ('A1','A2','B1','B2','C1','C2')),
  freq_zipf     numeric(3,2),                         -- from SUBTLEX/wordfreq: drives priority
  status        entry_status not null default 'candidate',
  priority      int not null default 0,               -- frequency + demand (misses) + curriculum lists
  quality       numeric(4,3),                         -- automated QA score
  doc           jsonb,                                -- validated EntryDoc (schema_version inside)
  current_revision_id uuid,
  published_at  timestamptz,
  updated_at    timestamptz not null default now(),
  unique (language, lemma_norm, pos, homograph)
);

create table entry_revisions (
  id uuid primary key default uuidv7(),
  entry_id uuid not null references entries(id),
  doc jsonb not null,
  produced_by jsonb not null,   -- {agent:'claude-routine', model:'claude-opus-5-5', prompt:'lex-v3', run:'…', tokens:{…}}
  qa jsonb,                     -- validator + judge results
  created_at timestamptz not null default now()
);

-- Projections (rebuilt from doc on publish)
create table entry_forms (entry_id uuid references entries(id) on delete cascade,
                          form text not null, form_norm text not null, features text,  -- 'V;PST', 'N;PL'
                          primary key (entry_id, form_norm));
create index on entry_forms (form_norm);
create index on entry_forms using gin (form_norm gin_trgm_ops);

create table expression_patterns (entry_id uuid primary key references entries(id) on delete cascade,
                                  tokens jsonb not null,          -- [{"lemma":"run"},{"lemma":"into"}]
                                  separable boolean not null default false, max_gap smallint not null default 0);

create table senses (
  key text primary key,                       -- 'en:actually:adv#1'
  entry_id uuid not null references entries(id),
  ord smallint not null, cefr text, domains text[], registers text[], regions text[],
  short_gloss text,
  retired_at timestamptz,                     -- soft retirement, never cascade-deleted
  embedding halfvec(768)
);
create index on senses using hnsw (embedding halfvec_cosine_ops);

create table equivalents (                    -- the reverse index: L1 term → English sense
  sense_key text not null references senses(key),
  variety   text not null references varieties(tag),   -- es-419, es-ES, pt-BR, ar
  term text not null, term_norm text not null,
  rank smallint not null,
  match text not null check (match in ('exact','near','approx','paraphrase')),
  regions text[],                             -- e.g. {MX,AR} or {ES}
  target_entry_key text,                      -- filled once the L1 side has its own entry
  primary key (sense_key, variety, term_norm)
);
create index on equivalents (term_norm);
create index on equivalents using gin (term_norm gin_trgm_ops);
create table l1_forms (variety text, form_norm text, term_norm text,   -- "me di cuenta" → "darse cuenta"
                       primary key (variety, form_norm, term_norm));

create table sense_links (from_sense text references senses(key), to_sense text references senses(key),
                          kind text check (kind in ('equivalent','near_equivalent')),
                          primary key (from_sense, to_sense));                 -- bidirectional phase

-- Media
create table audio_assets (id uuid primary key default uuidv7(), owner_key text not null,  -- entry/sense/example key
                           variety text not null, ipa text, engine text, voice text,
                           s3_key text not null, duration_ms int, unique (owner_key, variety, voice));
create table media_sources (id uuid primary key default uuidv7(), s3_key text unique, title text, kind text,
                            language text, rights_status text not null
                              check (rights_status in ('owned','licensed','public_domain','cc','third_party_unlicensed')),
                            attribution text, duration_ms int, status text, transcript_s3_key text);
create table utterances (id bigint generated always as identity primary key,
                         source_id uuid references media_sources(id), start_ms int, end_ms int,
                         text text, words jsonb,                 -- [{w,start,end,conf}]
                         lemmas text[]);                         -- spaCy lemmas: candidate retrieval
create index on utterances using gin (lemmas);
create table clips (id uuid primary key default uuidv7(),
                    sense_key text not null references senses(key) on delete restrict,
                    source_id uuid not null references media_sources(id), utterance_id bigint,
                    start_ms int not null, end_ms int not null, text text not null, highlight int4range,
                    video_s3_key text, vtt_s3_key text, poster_s3_key text,
                    accent text, scores jsonb, rank smallint, status text not null default 'active');
create table clip_coverage (sense_key text primary key references senses(key),
                            status text check (status in ('pending','found','not_found')),
                            clips int not null default 0, scanned_library_version int, scanned_at timestamptz);

-- Learners & usage
create table users (id uuid primary key default uuidv7(), external_id text unique not null,
                    l1 text, variety text, level text, created_at timestamptz default now());
create table lookups (id bigint generated always as identity, user_id uuid, meeting_id text,
                      query text, l1 text, entry_key text, sense_key text, context text, latency_ms int,
                      created_at timestamptz not null default now()) partition by range (created_at);
create table saved_items  (user_id uuid, sense_key text, meeting_id text, created_at timestamptz default now(),
                           primary key (user_id, sense_key));
create table review_cards (user_id uuid, sense_key text, due_at timestamptz, fsrs jsonb,
                           primary key (user_id, sense_key));
create table misses  (language text, query_norm text, hits int default 1, status text,
                      first_seen timestamptz, last_seen timestamptz, primary key (language, query_norm));
create table reports (id uuid primary key default uuidv7(), reporter uuid, role text, target_key text,
                      kind text, comment text, status text default 'open', created_at timestamptz default now());

-- Ops
create table llm_calls (id bigint generated always as identity, task text, provider text, model text,
                        prompt_version text, in_tokens int, out_tokens int, cost_usd numeric(10,6),
                        latency_ms int, ok boolean, ref_key text, created_at timestamptz default now());
create table ai_cache  (task text, input_hash bytea, output jsonb, model text, created_at timestamptz default now(),
                        primary key (task, input_hash));
```

### 5.4 The entry document

One `EntryDoc` per headword, defined once as a Pydantic model. The JSON Schema for the AI, the validator and the API types are all generated from it. A complete example is in **Appendix A**. Its main shape:

```
EntryDoc
├─ key, lemma, language, pos, kind, cefr, frequency
├─ pronunciations[]  {variety, ipa}
├─ senses[]
│   ├─ key, cefr, definition (learner English), short_gloss, registers, domains, regions, patterns
│   ├─ l1 { "es": {gloss, equivalents[]}, "pt-BR": {…}, "ar": {…} }
│   ├─ examples[]  {key, text, cefr, translations{es, pt-BR, ar}}
│   ├─ collocations[]
│   └─ notes[]  {kind: false_friend|common_mistake|pronunciation|grammar|usage|culture, l1?, text}
└─ related[]  {type: synonym|antonym|family|confusable|see_also, key}
```

Languages are **additive**: a Portuguese pass adds `l1["pt-BR"]` and example translations to existing documents without touching the English content. Arabic works the same way. That is how we stay "Spanish first" without repainting later.

---

## 6. Bilingual lookup: how each direction works

| What the student types | How it resolves |
|---|---|
| English base form `actually` | `entries.lemma_norm` exact match; the card is projected into the student's L1 |
| English inflected form `went`, `leaves` | `entry_forms` → *go*; *leaf* **and** *leave* (both shown, ranked by frequency, or by context if there is a sentence) |
| English phrase `gave it up` | spaCy lemmas + `expression_patterns` (separable, gap ≤ 2) → *give up* |
| Typo `beatiful`, `recieve` | trigram similarity on forms, plus `dmetaphone` fallback → "did you mean *beautiful*?" |
| Spanish `darse cuenta`, `me di cuenta` | `l1_forms` normalizes (*me di cuenta* → *darse cuenta*) → `equivalents` → *realize#1*, *notice#1* |
| Portuguese `na verdade` | `equivalents` (variety pt-BR) → *actually#1*, *in fact* |
| A description, in any language: "persona que deja todo para después" | multilingual embedding search over senses → *procrastinator*, *put off* |
| A word clicked in a chat sentence | candidates as above, then senses re-ranked by embedding similarity to the sentence; phrasal verbs detected first |

**Ambiguous input** (*actual*, *real*, *once*, *pie* exist in both languages): the API runs both the English and the L1 search and returns two labeled groups ("English word *actual*" / "Spanish *actual* → *current*"). The plugin can also offer an EN→L1 / L1→EN toggle.

**Ranking:** fuse the lexical hits (exact, forms, equivalents, trigram) and the semantic hits (embeddings) with reciprocal-rank fusion, then boost by frequency and the student's level. All of this is one SQL round trip plus one embedding call: no LLM in the lookup path.

**Arabic (later):** RTL rendering; orthographic normalization (diacritics, alef/yaa/taa-marbuta variants, tatweel) into `term_norm`; clitic segmentation (و ف ب ل ال) with CAMeL Tools when we build `l1_forms` for Arabic; MSA by default, with dialect tags as data.

**Bidirectional phase (Spanish/Portuguese headwords):** Spanish entries are simply `entries` rows with `language = 'es'` and Spanish definitions. `sense_links` connects *realize#1* ↔ *darse cuenta#1*, and `equivalents.target_entry_key` starts pointing at real Spanish entries. No schema change needed.

---

## 7. The AI content factory (the core idea, rescued and upgraded)

The goal stays the same: the dictionary comes into existence almost by itself. What changes is that generation is **grounded** in real data, **checked** automatically, and **repaired** automatically.

### 7.1 Pipeline

```
S0 seed → S1 ground → S2 generate → S3 validate → S4 judge → S5 decide → S6 enrich → S7 publish → S8 feedback & audit
                                          ▲                       │ repair (≤ 2 attempts)
                                          └───────────────────────┘
```

| Stage | What happens | Runs on |
|---|---|---|
| **S0 Seed** | Build the headword queue with a priority: frequency + curriculum level + demand (misses) + clip availability | server |
| **S1 Ground** | Assemble a *grounding pack* per headword: POS candidates, inflections, IPA (US/UK), sense hints and ES/PT translation candidates from Wiktionary data (facts only), frequency, CEFR priors, 3–5 real sentences from your transcripts, keys of related entries already published | server |
| **S2 Generate** | Claude writes one `EntryDoc` per headword (all senses together, so sense splits stay consistent), against the JSON Schema and the style guide | routine / Batch API |
| **S3 Validate** | Deterministic checks (below): free, milliseconds | server |
| **S4 Judge** | A rubric of specific yes/no checks per sense, run by a *different* model family | free tier / Jev / Haiku |
| **S5 Decide** | Auto-publish above the threshold; send the judge's reasons back for repair; quarantine after two failed repairs | server |
| **S6 Enrich** | Projections (forms, equivalents, L1 forms), embeddings, audio | server / your PC |
| **S7 Publish** | Status `published`, ETag bump, cache purge | server |
| **S8 Feedback & audit** | Teacher hides and student reports feed the repair queue; a weekly audit re-checks a 2% sample; the golden set is re-run on every prompt or model change | server + routine |

**S0 seed sources** (all usable commercially, per the licensing research):

| Source | Gives | License |
|---|---|---|
| CEFR-J Wordlist 1.6 | 7,801 A1–B2 entries with POS and level | Free for commercial use, with citation |
| Octanove C1/C2 | C1/C2 headwords with POS and level | CC BY-SA 4.0 |
| NGSL, NGSL-Spoken, BSL, NAWL, TSL | Core, spoken, business, academic and TOEIC lists | CC BY-SA 4.0 (commercial use explicitly allowed) |
| wordfreq | Zipf frequencies for en/es/pt/ar (data frozen around 2021; fine for ranking) | Apache-2.0 code, CC BY-SA data |
| Wiktionary via kaikki.org | Phrasal-verb and idiom inventories, forms, IPA, translation candidates | CC BY-SA (we use facts only) |
| **Your own transcripts** | Frequent multiword expressions; candidates that are *guaranteed* to have clips | yours |
| Lookup misses | Real demand | yours |

**Avoid** the English Vocabulary Profile, the Oxford 3000/5000 and Pearson's Academic Collocation List: there's no reuse license for any of them.

**Licensing hygiene:** inflections, IPA, POS and translation candidates are facts, so share-alike doesn't reach our original text. Wiktionary *definition text* is different: copying or closely paraphrasing it would make our text CC BY-SA. So prompts carry facts and short sense hints, the validator runs an n-gram overlap check against Wiktionary glosses, and an attribution page credits Wiktionary, UniMorph, CEFR-J and NGSL.

**S3 deterministic validators:**
- JSON Schema conformance; closed vocabularies for POS, CEFR, registers and note kinds; key format.
- Every example contains a form of the headword (forms table + spaCy), with the right pattern for phrasal verbs.
- Definitions don't use the headword, stay within the defining vocabulary (share of tokens outside NGSL below a threshold), and respect length bounds.
- Each translation is detected as the right language (catches Spanish/Portuguese mix-ups).
- Every L1 equivalent exists in that language's lexicon (catches hallucinated words).
- IPA uses valid symbols and is close to CMUdict/Britfone.
- No two senses are near-duplicates (embedding similarity).
- Overlap with Wiktionary glosses stays below the threshold.
- A sensitive-content screen runs on every example.

### 7.2 Who does what

| Task | Engine | Cost |
|---|---|---|
| Core entries: about 7.5k (CEFR-J A1–B2 + ~1.5k phrasal verbs and idioms) | Claude Sonnet 5.5 or Opus 5.5 | Subscription, or a one-off Batch run (§7.4) |
| Long tail and provisional entries | Haiku 5.5 (Batch) or DeepSeek | Cents per thousand (my estimate) |
| QA judge | Gemini Flash free tier (offline QA only), Jev, or Haiku 5.5 | ≈ $0–6 per 20k entries (my estimate) |
| Repairs and weekly audits | Claude (routine) | Subscription |

### 7.3 Style guide highlights (full version in `factory/STYLE.md`)

- **Definitions:** learner English drawn from a ~2,000–3,000-word defining vocabulary, at most ~20 words, one idea per sense, never the headword itself.
- **Senses:** only learner-relevant ones (≤ 4 for most words), ordered by frequency, split by meaning.
- **Examples:** 3 per sense, 6–16 words, natural adult contexts (work, travel, family, study), each showing its pattern (*depend **on***). Names and settings from Latin America, Brazil and the Arab world as well as the US/UK; no stereotypes or brands. `temperature` around 0.7 for examples, lower for definitions.
- **Equivalents:** ranked and typed (`exact`, `near`, `approx`, `paraphrase`); es-419 by default, with Spain variants tagged. When there's no direct equivalent, say so and give a paraphrase.
- **L1 notes:** only when they are true and genuinely useful (false friends, typical errors, pronunciation traps), written in the L1 for A1–B1 learners, and short.
- **CEFR:** list levels are priors, adjusted per sense; common senses never get an inflated level.

### 7.4 Running it with Claude Code: how many entries per run?

**What the docs say today (2026-10-18):**
- Routines are a research preview. Each run is a fresh cloud session on a clone of a **GitHub** repo; this repo has no remote yet, so it needs one.
- The schedule interval is at least 1 hour. **No per-plan daily cap is documented**, only hourly caps (e.g. 100 scheduled runs per account per hour).
- Runs draw on your plan's usage: rolling 5-hour session windows plus weekly limits. No fixed message counts are published.
- Network access can be None, Trusted, Custom or Full. *Network secrets* are injected by a proxy, so the factory token never enters the VM.
- Subagents work, and each routine can pick its model.

**My recommendation: 40 headwords per run, as 4 sub-agents × 10.**
- **Why 10 per sub-agent:** each context stays around 30–50k tokens. That leaves room for the style guide, schema and grounding packs, so quality doesn't drift late in a long context. Output is about 20k tokens per sub-agent.
- **Why 4 in parallel:** a run takes roughly 20–30 minutes. Bigger fan-outs burn the weekly allowance fast, which the docs warn about explicitly.
- **Run flow:**
  1. `pull_batch`: HTTPS GET, authenticated by the network secret. Repairs come first.
  2. The sub-agents generate their entries.
  3. Each sub-agent runs the validator locally and fixes what it flags.
  4. The orchestrator submits everything: HTTPS POST.
  5. The server judges and publishes.
- **Calibrate before scaling:** run a 10-entry pilot, read the usage meter (share of a 5-hour window and of the week), then set runs per day so that at least ~40% of your weekly allowance stays free for development. Schedule at nights and on weekends.
- **Model:** compare Sonnet 5.5 with Opus 5.5 on the golden set. If Sonnet passes, use it for bulk (it stretches your allowance) and keep Opus for editorial and audit runs.
- **Rough expectation, to be replaced by pilot data:** 100–250 entries/day, so the 7.5k core takes about 4–10 weeks.

**⚠️ A terms question you should know about.**
- Claude Code's legal page says Pro/Max limits "assume ordinary, individual usage" and points developers who are building products to API keys.
- The Consumer Terms restrict automated access to API keys "or where we otherwise explicitly permit it". Routines are an official scheduling feature, but whether *bulk content production for a commercial product* counts as ordinary individual use isn't spelled out.
- One thing is clear-cut: **student-facing runtime calls must use API keys, never your subscription.**
- For certainty on the bulk work, ask Anthropic, or use the Batch API for the bulk run.

**The Batch API alternative, priced from Anthropic's pricing page today.** Batch is 50% off: Opus 5.5 $2/$10, Sonnet 5.5 $1/$5, Haiku 5.5 $0.05/$0.25 per million input/output tokens, and cache discounts stack on top. My estimate assumes about 7.5k input tokens per entry (a 6k shared prefix that gets cached) and about 2k output tokens:

| Model (Batch) | ≈ per entry | Core 7.5k entries | All 20k |
|---|---|---|---|
| Opus 5.5 | $0.024 | ≈ $180 | ≈ $470 |
| Sonnet 5.5 | $0.012 | ≈ $90 | ≈ $235 |
| Haiku 5.5 (quality to be tested) | $0.0006 | ≈ $5 | ≈ $12 |

Turnaround is usually under an hour per batch, so the whole core is a one-day job. **My recommendation is a hybrid:**
- Use Claude Code interactively to build the style guide, the golden set and the calibration. That is ordinary use.
- Produce the core with the routine (free, slower) **or** with a one-off Sonnet batch (≈ $90, done in a day, clean terms). This is your call (§16).
- The factory is portable, so switching between the two is a config change.

### 7.5 Fully automated QA (since there is no human review)

| Layer | Catches | Cost |
|---|---|---|
| Grounding (S1) | Invented senses, forms and IPA | free |
| Deterministic validators (S3) | Schema errors, missing headwords in examples, wrong languages, invented L1 words, duplicate senses, IPA mismatches, copied text, sensitive content | free, milliseconds |
| Cross-model judge (S4) | Wrong meanings, bad equivalents, unnatural examples, wrong CEFR, misleading notes | ≈ $0–6 per 20k entries (my estimate) |
| Decision (S5) | Publish / repair (≤ 2 attempts) / quarantine | — |
| In-class signals | A teacher's one-click hide (instant), student reports | free |
| Audits (S8) | Drift: a weekly 2% re-check by Claude, plus golden-set regression on every prompt or model change | subscription |

**The highest-leverage hour you can spend** is rating 50 golden entries once, so the judge's thresholds are calibrated to *your* standard. After that the system runs on its own.

### 7.6 LangChain, Jev and other frameworks

- **LangChain: not needed.** Our LLM calls are single structured generations plus simple routing. Provider SDKs with JSON-schema structured outputs, validated by Pydantic, are simpler and easier to debug. The agentic part (research, write, validate, fix) is done by Claude Code itself, a much stronger harness than a LangChain agent.
- **Jev is real and worth a trial for decisions, not generation.**
  - **What it is:** a hosted typed decision model from TypeSafe AI, in limited early access since 2026-09-15. You send a state plus typed questions (Choice, Score, yes/no "Noul", Box), and it returns only the values you declared, with probabilities.
  - **Speed and price:** 70–500 ms; $0.042 per million input tokens, output free; no self-hosting.
  - **Fits:** picking the sense of a clip candidate, QA gates (pass/fail with a confidence value), CEFR cross-checks, "is this n-gram an idiom?", routing decisions.
  - **Caveats:** early access and proprietary. Its own docs list weaknesses (counting, dates, noisy input, a bias toward the first option). A published attack breaks it by placing fabricated evidence in the input, and our transcripts are untrusted input.
  - **Plan:** put every decision step behind one interface, then A/B test Jev against embeddings alone and a Haiku judge on a labeled set during the clips phase. For scale: 200k clip decisions at 600 tokens each comes to about $5 (my arithmetic).
- **Optional helpers:** Pydantic AI or Instructor (validation and retries around structured outputs); DSPy later, to tune judge prompts against a labeled set.

---

## 8. Clips pipeline: from the media library to sense-matched clips

### 8.1 Flow

```
S3 library → inventory + rights tag → audio → transcript (align existing subtitles | ASR) → utterances with word timings
  → spaCy lemmas + multiword matches → candidates per sense → sense assignment → scoring → cut → publish → coverage marking
```

| Step | How | Where |
|---|---|---|
| Inventory | List objects and register them in `media_sources`; set `rights_status` from a folder convention (`owned/`, `public-domain/`, `cc/`, `licensed/`, `third-party/`) | server |
| Audio | ffmpeg → 16 kHz mono FLAC | your PC or server |
| Transcript | **Existing SRT/VTT:** forced alignment (WhisperX `align`, or Qwen3-ForcedAligner for en/es/pt). **None:** Parakeet TDT 0.6B v3 with word timestamps; benchmarks show ~36× real time on a desktop x86 CPU and ~57× on a T4 | your PC or Modal (not the ARM server: WhisperX/pyannote dependencies lack aarch64 wheels) |
| Utterances | Split on punctuation and pauses; store the text and word timings | server |
| Index | spaCy lemmas go into `utterances.lemmas` (GIN index); a multiword matcher is compiled from `expression_patterns` | server or PC |
| Candidates | Per sense: utterances containing the lemma or pattern, 2–8 s long, good ASR confidence, single speaker, no overlapping speech | server |
| Sense assignment | Embed the utterance (± its neighbor) and compare it with sense embeddings. A low margin between senses goes to the judge (Jev / Haiku / Gemini) | server + API |
| Scoring | Sense confidence, clarity (speech rate, ASR confidence), headword not at the edges, variety (sources, speakers, accents), rights preference | server |
| Cut | ffmpeg with accurate seeking (fix for C4), re-encoded to H.264/AAC at 480p or 720p with `faststart`, ±300 ms padding, a poster frame, and WebVTT with word timings for karaoke highlighting | your PC or server |
| Publish | S3 keys, CDN, `clips` rows: up to 3 per sense | server |
| Coverage | `clip_coverage` marks each sense `found` or `not_found` against a library version, so new media triggers re-scans of `not_found` senses only. This is your "skip and mark" requirement | server |

**Clip length:** cut on utterance boundaries (a whole sentence), typically 2–8 s with a hard cap around 10 s. A fixed 3–4 s window often cuts off exactly the context a learner needs.

### 8.2 Rights: an honest look at the "fair use" assumption

You mentioned that 3–4 s clips fall under fair use. I'd be careful with that:

- **Fair use is a US doctrine.** It is decided case by case on four factors (purpose, nature, amount, market effect), not by duration, and commercial use weighs against it.
- **Your academy and students are in countries with closed lists of exceptions.**
  - Spain's quotation exception (TRLPI art. 32) covers fragments used for analysis, commentary or critique in teaching or research, with credit. A dictionary clip feature is a stretch.
  - Spain's teaching exceptions are limited to formal education and non-commercial use.
  - Brazil (Lei 9.610/98 art. 46) allows small excerpts only when they aren't the main purpose of the new work and don't harm the original's exploitation.
  - A commercial academy is unlikely to qualify.
- **What comparable sites do:**
  - PlayPhrase.me hosts film/TV clips of up to 8 s, claims US fair use, is non-commercial, and removes clips within about 24 h of a notice.
  - Yarn's own terms ban commercial use.
  - YouGlish avoids hosting entirely by playing YouTube segments through YouTube's API.

This is general information, not legal advice. **My recommendations:**
1. Keep `rights_status` per source (already in the schema). By default the API serves only `owned`, `public_domain`, `cc` (BY/BY-SA) and `licensed`.
2. **Launch clips on public-domain and your own content first:** Internet Archive public-domain films and documentaries, Wikimedia Commons, Blender open movies, and your recorded lessons (with signed releases).
3. Make third-party clips a **conscious decision after a short consultation with a lawyer** where you operate. If you go ahead, mitigate:
   - one utterance per clip, at 480p;
   - visible attribution (title, year, rights holder);
   - no downloads and no access to the full video;
   - a fast takedown process and a rights-holder opt-out.
4. **YouTube embeds** of official channels, with start/end parameters, are a safer third channel. YouTube's policies forbid downloading or caching and charging users to watch embeds, so keep embeds out of paywalled features. Avoid NC and ND sources (TED talks, for example, are BY-NC-ND).

### 8.3 How much coverage to expect (my estimate)

Conversational speech runs about 9,000 words per hour, so 100 h is roughly 0.9M tokens and 1,000 h roughly 9M. A word at 3 occurrences per million (around rank 5,000) appears about 3 times in 100 h and about 27 times in 1,000 h. We also need the *right sense* in a *clean* clip.

| Library size | Core 5k words with ≥ 1 good clip | Phrasal verbs and idioms |
|---|---|---|
| ~100 h | ~40–60% | ~15–30% |
| ~1,000 h | ~85–95% | ~50–70% |

Documentaries skew formal; TV shows give more conversational coverage. These are guesses; S8 will report real coverage per CEFR band.

### 8.4 Clips are optional in the API

- In the entry call: `GET /v1/entries/{key}?include=clips`.
- Or lazily: `GET /v1/senses/{key}/clips?limit=3`.

The plugin fetches clips only when the student opens the clips tab: a poster image plus a short MP4 of roughly 0.3–1 MB.

---

## 9. Pronunciation and audio

- **IPA:**
  - US from CMUdict (ARPAbet converted to IPA), UK from Britfone, with Wiktionary/WikiPron as a cross-check (facts).
  - Claude reconciles phrases, new words and **per-sense homographs**: *record* n./v., *read* present/past, *lead* the metal vs the verb.
- **Synthesis:**
  - **Kokoro-82M with phoneme input**, so the audio follows the IPA exactly, in US and UK voices, for headwords and examples.
  - Slow playback is done in the browser (`playbackRate`, pitch preserved), so no extra files are needed.
- **Compute:** CPU is enough. My estimate for 20k headwords × 2 voices plus ~60k examples is 2–3 days of your PC's CPU time, or a few nights on the server. **No GPU credits needed.**
- **Format:** AAC (`.m4a`), mono, 48–64 kbps, about 10–20 KB per headword; content-addressed S3 keys, cacheable on a CDN.
- **Real voices:** a "hear it in the wild" option plays audio-only snippets from cleared clips, giving authentic accents.
- **Later languages:**
  - **Latin American Spanish** (bidirectional phase): Kokoro's Spanish is Spain-based, so use Chatterbox Multilingual's es-LatAm pack (MIT; output is watermarked) on Modal, Supertonic 3 (OpenRAIL-M, fast on CPU), or Google Cloud TTS's free tier (1M Neural2 characters/month).
  - **Brazilian Portuguese:** Kokoro pt-BR.
  - **Arabic:** Supertonic 3, NVIDIA Magpie TTS (open model license, IPA input, GPU), or Azure's F0 tier (0.5M neural characters/month, with many Arabic dialect voices).
- **Not licensed for commercial use:** F5-TTS, XTTS-v2 (Coqui has shut down), Fish S2 Pro, Voxtral TTS. ElevenLabs is no longer needed.

---

## 10. Runtime AI on a small server: can a local LLM do it?

### 10.1 What the numbers say

Measured on OCI A1 with **4 OCPUs** (benchmark from Nov 2025):

| Model / runtime | Generation speed |
|---|---|
| Qwen2.5-3B Q4, upstream llama.cpp | 10.6 tokens/s |
| Qwen2.5-3B Q4, Ampere's optimized llama.cpp build | 13.4–15.5 tokens/s |
| Qwen2.5-3B Q4, stock Ollama | 7.8 tokens/s |
| 7B model, Ollama | ~6.1 tokens/s |
| 14B model, Ollama | ~3.3 tokens/s |

No prompt-processing figures were published. **2 OCPUs**, which is the free shape now, roughly halve all of these.

**Your task, estimated:** a 15–20-word answer is about 25–30 output tokens, plus a ~300-token prompt.
- **4 OCPUs:** about 2.5–4 s to generate, plus several seconds of prompt processing (CPUs are slow at that). That makes **~5–10 s for one student** and **15–25 s** when three ask at once.
- **2 OCPUs:** roughly double.
- **Side effect:** while the model runs, it takes the CPU away from lookups for everyone.

Your PC is much faster per core, which is why `qwen3:4b` felt quick there. One tip for any test: Qwen3's default *thinking* mode silently generates hundreds of hidden tokens, so use the non-thinking/instruct variant for short answers.

### 10.2 Verdict

- **Live features go through an API.** With your ≤ $5/month, either **Claude Haiku 5.5** ($0.10/$0.50 per million tokens on today's pricing page) or **DeepSeek** costs about **$1–2/month** at your load. My estimate assumes ~1,000 requests/day of ~600 input and ~40 output tokens.
  - I lean to Haiku 5.5: the same vendor as the content factory, and API traffic falls under Anthropic's commercial terms.
  - DeepSeek works too. Its data is processed in China, which is worth weighing for student-written text.
  - **Free backup:** Groq's free tier (gpt-oss-120b, ~1K requests/day, not retained by default).
- **A local model is useful offline, if your instance has 4 OCPU / 24 GB:**
  - **Uses:** night batches such as judge pre-filtering and example variants.
  - **Runtime:** Ampere's llama.cpp build (1.3–2× faster than stock Ollama on A1) with Q4_0 or Q4_K_4 files.
  - **Candidates:** a ~4B instruct model, or Gemma 4 26B-A4B if RAM allows (14.4 GB, 3.8B active parameters: big-model quality at small-model speed).
  - **On 2 OCPU / 12 GB:** skip it.
- **Most lookup-time "AI" needs no LLM at all.** Sense ranking is embeddings plus spaCy, and "here it means…" is a template.

### 10.3 The AI gateway

- **Tasks:** `sentence_check`, `more_examples` (topic and level), `explain_difference` (*make* vs *do*), `provisional_entry`, `query_normalize` (fallback only).
- **Routing:** an ordered list of providers per task, with timeouts (~3 s) and automatic fallback.
- **Cache:** `ai_cache` keyed by task + normalized input. Examples and "difference" answers are cached permanently, and good ones get **promoted into the entry**, so the dictionary learns from usage.
- **Quotas:** per student (e.g. 30 AI requests/day), per meeting, and a **global monthly dollar cap** (default $5) that automatically downgrades to free providers when reached.
- **Safety and privacy:**
  - Filter outputs, and send nothing beyond the sentence itself.
  - Log every call to `llm_calls`.
  - Never send student text to free tiers that train on it; the Gemini free tier does. Gemini's API terms also require users to be 18+.

---

## 11. BBB plugin integration

### 11.1 Which BBB version

BBB **4.0.0 went stable on 2026-10-14** and bundles plugin SDK 1.0.0-beta.3. The latest 3.0.x is 3.0.39 (SDK 0.0.105), which is still supported.

We build against **your server's** version (§16). The plugin stays thin, with all logic on the server, so moving between SDK versions stays cheap.

### 11.2 Extension points (SDK 0.0.105) and what we use them for

| SDK capability | Our feature |
|---|---|
| Floating window | The lookup panel: search, card, clips |
| Actions bar / nav bar button | Open the dictionary; "My words" |
| `useLoadedChatMessages` + `useChatMessageDomElements` | Click-to-look-up in chat: a selection or overlay around words (the docs say not to alter message text) |
| Presentation text (`currentPage.urlsJson.text`) | **"Words on this slide"**: vocabulary pre-highlighted for the current slide |
| Captions (readable only through a GraphQL custom subscription, which is fragile across versions) | Later, optional |
| Data channels (`pushEntry` to everyone or chosen receivers, with role permissions) | Teacher pushes a word; the class word list updates live |
| `useCurrentUser` (userId, **extId**, role, presenter) + UI locale (`useUiData`) | Identify the student, enable teacher mode, set the default L1 |
| Plugin settings (manifest `settingsSchema`, `bbb-html5.yml`) | Non-secret config only: **every participant's browser can read these** |

### 11.3 Identity and API access

This is the critical design question. `remoteDataSources` can't pass parameters (such as the looked-up word), and the research found no server-side handler for it in BBB's source, so we shouldn't build on it.

**Proposed approach, to validate in a one-day spike:**
1. Add an nginx location on your BBB server that runs `auth_request` against BBB's own session check, `/bigbluebutton/connection/checkGraphqlAuthorization`. BBB uses the same pattern for `/api/rest/userMetadata`.
2. That check returns the verified meeting ID, user ID, **external user ID** and moderator flag as headers.
3. nginx forwards the request to the dictionary API with those headers plus a shared secret.

The browser never holds a secret, and the API trusts identity only when it comes from the BBB server.

```nginx
# Sketch: confirm the exact header names in the spike
location /dictionary/ {
  auth_request /bigbluebutton/connection/checkGraphqlAuthorization;   # validates the BBB session token
  auth_request_set $meeting  $upstream_http_meeting_id;
  auth_request_set $ext_user $upstream_http_user_external_id;
  auth_request_set $is_mod   $upstream_http_user_is_moderator;
  proxy_set_header X-BBB-Meeting  $meeting;
  proxy_set_header X-BBB-Ext-User $ext_user;
  proxy_set_header X-BBB-Is-Mod   $is_mod;
  proxy_set_header X-Dict-Secret  "<loaded from a file, never the repo>";
  proxy_pass https://dict.example.com/v1/;
}
```

- The external user ID is your portal's stable student ID, stored as `users.external_id`. The default L1 comes from the BBB UI locale, and each student can override it.
- **Fallback if the spike fails:** your portal mints a short-lived signed token at join time. We'd still need to confirm how the plugin can read it.

### 11.4 Targets

- Lookups: p95 < 150 ms at the server.
- Payloads are projected to the student's L1 only: a typical card is about 5–15 KB.
- Clips load lazily; GET responses are HTTP-cached with ETags.

---

## 12. Admin dashboard: yes, but lean and later

With no human review, the dashboard is an **operations console, not a CMS**. Build it after the plugin MVP.

**Until then:** SQL views, a database GUI over an SSH tunnel, a `/admin/stats` endpoint and Uptime Kuma.

**Teacher moderation lives inside the plugin** (hide and flag buttons), because that's where teachers already are.

**Screens, when we build them:**
1. **Pipeline:** queue depth by stage; routine and Batch runs with tokens and cost; failures; the repair queue.
2. **Coverage and quality:** entries by CEFR band and status, judge scores, clip coverage by band.
3. **Demand:** top lookups and misses (accept/reject), by class and level.
4. **Entry inspector:** the rendered card next to its JSON, revision diffs, regenerate and hide buttons.
5. **Clip gallery:** quick reject, re-rank.
6. **Reports inbox:** teacher hides and student reports, with the auto-triage result.
7. **Settings:** API clients, the AI budget cap, provider routing.

**Tech:** React + Vite + TanStack Router/Query + shadcn/ui, served by Caddy, behind your portal's SSO or a single admin login.

---

## 13. Infrastructure on the Oracle A1

### 13.1 Services and memory budget

| Service | Image | Notes |
|---|---|---|
| `caddy` | caddy:2 | TLS, reverse proxy, static admin SPA |
| `api` | backend image (python:3.13-slim, arm64) | uvicorn; spaCy and the embedding model loaded once |
| `worker` | same image, different command | Procrastinate worker + periodic tasks |
| `postgres` | postgres:18 + pgvector | Never exposed publicly |
| `uptime-kuma` | optional | Uptime alerts |
| `llama-server` | optional, **only on 4 OCPU / 24 GB** | Ampere llama.cpp build |

**Memory on the free 12 GB shape (estimates):**

| Component | RAM |
|---|---|
| Postgres 18 (`shared_buffers` ≈ 2 GB) | ~2.5–3 GB |
| api (spaCy `md` + ONNX embedding model) | ~1–1.5 GB |
| worker (spaCy, Kokoro, embeddings, ffmpeg at peak) | ~1–2 GB |
| Caddy + Uptime Kuma | ~0.3 GB |
| OS + page cache | the rest (~5 GB) |

That fits, without a local LLM. On 24 GB you could add a local model (3–15 GB).

**CPU:** with 2 OCPUs, the API and Postgres get priority during class hours. Worker concurrency is 1 by day and higher at night, and heavy batches run on your PC or Modal.

**Disk:** 200 GB of block storage in total (each boot volume takes at least ~50 GB). It holds the database, models and temporary files; media stays in S3 and is streamed during processing.

### 13.2 Is Supabase a good main database?

**PostgreSQL is exactly the right engine. Supabase, for this project, is not the right product.**

- **Hosted free tier:** 500 MB of database, and projects pause after a week of inactivity. Our database (embeddings, form tables, the utterance index) outgrows 500 MB early, and Pro costs $25/month, while your budget is $0.
- **Self-hosted:** 11 containers. The official minimum is 4 GB RAM (8 GB+ recommended), and one measurement put it at about 2 GB idle. That's a sixth of the 12 GB server, spent on features we don't use:
  - Auth: identity comes from BBB.
  - Storage: we already have S3.
  - Also unused: Realtime and Edge Functions.
- **Its main draws don't fit our design.** PostgREST, client access controlled by RLS, and Auth all assume clients talk to the database. Here, the plugin only ever talks to our API.
- **Your `PGRST301` errors** are part of Supabase's API-key transition: the new `sb_` keys versus legacy JWT keys, which Supabase says are being deprecated by the end of 2026. With plain Postgres, that whole class of problem disappears.

**Plain Postgres in Docker is the simplest choice, and the schema stays portable.** Moving to Supabase, Neon or RDS later is one `pg_dump` away.

### 13.3 Operations

- **Deploy:**
  - At first, `git pull && docker compose up -d --build` on the server.
  - CI (GitHub Actions) runs lint, tests and validators, and re-runs the golden set on any factory change.
  - Later, build arm64 images and publish them to GHCR.
- **Backups:**
  - Nightly `pg_dump` to a versioned S3 bucket, plus Oracle's five free volume backups.
  - A nightly JSONL export of published entries, for portability.
  - A monthly restore drill.
- **Security:**
  - Only ports 80 and 443 open (OCI security list + ufw); SSH keys only; Postgres internal only.
  - Secrets in root-only env files.
  - The API rejects any request that lacks the BBB proxy secret or an admin/factory token.
  - Rate limits per IP and per student; CORS limited to your BBB domain.
- **Monitoring:** Uptime Kuma, error tracking (Sentry's free tier, or self-hosted GlitchTip later) and `/admin/stats`.
- **Idle reclamation:** Oracle may reclaim an Always Free instance if, over 7 days, its CPU (95th percentile), network *and* memory all stay below 20%. Our steady memory use (~50% of 12 GB) keeps us above that line, but monitor it.
- **Exit plan:** everything is Compose + S3 + `pg_dump`, so moving to another ARM VPS takes an afternoon.

---

## 14. Free compute and free tiers: what to use, and the fine print

| Resource | What you get | Use it for | Fine print |
|---|---|---|---|
| **Your PC** | x86, 32 GB RAM (plus a GPU if it has an NVIDIA card) | ASR (Parakeet benchmarks at ~36× real time on a desktop CPU), TTS (Kokoro), clip cutting, embedding backfills | Must be on; run jobs overnight |
| **Modal Starter** | $30/month of credits; GPUs billed per second (T4 ≈ $0.59/h, A100-80GB ≈ $2.50/h) | GPU bursts: ASR at ~57× real time on a T4, big-model judging | The best fit for unattended batch jobs |
| AMD Developer Cloud | $100 for 30 days (for approved members); an MI300X 192 GB VM | A one-off giant batch, e.g. a 100B+ open model as judge | ROCm, not CUDA; needs approval |
| Kaggle | ~30 GPU hours/week (2×T4) | Experiments only | Its terms allow only "internal, personal, non-commercial use", so not production content |
| Colab free, Lightning, HF ZeroGPU, Paperspace | Small or interactive quotas | Prototyping | Poor fit for unattended batches |
| Gemini API free tier | Flash/Flash-Lite, Embedding 2, Gemma 4 | Offline QA judge (dictionary content isn't sensitive) | Data is used to improve products, with human review; users must be 18+; serving users in the EEA/UK/CH requires the paid tier |
| Groq free | gpt-oss-120b at ~1K requests/day and 200K tokens/day; Whisper | Runtime backup | Daily caps |
| OpenRouter `:free` | 50 requests/day (1,000 after buying $10 of credit) | Backup | Some free hosts may train on your data |
| Mistral free | Large monthly token allowance (the published figure may be out of date) | Offline batch | Trains on data unless you opt out |
| Cloudflare Workers AI | 10K "neurons"/day | Small tasks | — |
| Google Cloud TTS free | 4M standard/WaveNet + 1M Neural2/Chirp HD characters/month | Latin American Spanish and Arabic audio, later | — |
| Azure Speech F0 | 0.5M neural characters/month | Arabic dialect voices, later | — |
| Gone or closed in 2026 | GitHub Models (retired 2026-07-30); Cerebras's free tier (now a $5 trial); SageMaker Studio Lab (no new users) | — | — |

**Bottom line:** you don't strictly need GPU credits. CPU is enough for TTS and ASR at your scale, and **your PC plus Modal's monthly credit** cover every heavy job.

---

## 15. Risks and mitigations

| # | Risk | Likelihood / impact | Mitigation |
|---|---|---|---|
| 1 | AI errors at scale with no human review | High / High | Grounding, validators, cross-model judge, "provisional" labels, teachers' one-click hide, weekly audits, golden-set regression |
| 2 | Copyright on clips | Medium / High | Gate on `rights_status`; cleared sources first; legal advice before third-party clips; a takedown process |
| 3 | Oracle's free tier changes again (already halved) or reclaims the instance | Medium / Medium | Fit in 12 GB, portable Compose, backups, an exit plan |
| 4 | Subscription terms for bulk generation | Medium / Medium | Confirm with Anthropic, or use the Batch API (≈ $90 with Sonnet for the core); runtime calls always use API keys |
| 5 | Free tiers and models change monthly | High / Low | Gateway with pluggable providers; model IDs in config; golden-set evals before any switch |
| 6 | BBB 4.0 has just shipped and its SDK is in beta | Medium / Medium | Thin plugin with server-side logic, pinned SDK, an adapter layer |
| 7 | Students spoofing someone else's identity | Medium / Medium | Identity verified by the BBB server (spike in §11.3); never trust IDs sent by the browser |
| 8 | Student privacy | Medium / Medium | Pseudonymous external IDs, minimal data, retention limits (e.g. 12 months for lookups), a deletion endpoint; student text never goes to providers that train on it |
| 9 | Dataset licensing | Low / Medium | Facts-only grounding, overlap checks, an attribution page; avoid EVP, Oxford lists and the ACL |
| 10 | Single server = single point of failure | Medium / Medium | Backups and restore drills; `stale-if-error` HTTP caching; optionally Cloudflare in front |
| 11 | Jev is early-access and proprietary | Low / Low | Optional, behind an interface |
| 12 | Scope creep (4 languages, clips, SRS, dashboard) | High / Medium | Phased roadmap: ES↔EN first, clips only after the core works |

---

## 16. Decisions and open questions

**Proposed decisions** (approve or challenge any of them):
1. Re-found the project: keep the ideas, not the code.
2. Python backend (FastAPI); TypeScript for the plugin and dashboard.
3. Self-hosted PostgreSQL 18. No Supabase, no Redis, no GraphQL.
4. Entry documents + projections; stable keys; BCP 47 varieties; English-centric content on a language-neutral schema.
5. spaCy as a library; form tables for lookups; embeddings for meaning.
6. Claude as the lexicographer; automated QA; auto-publish with quarantine.
7. Kokoro for audio; Parakeet and aligners for transcripts; clips optional and gated by rights.
8. An AI gateway with a $5 cap; no LLM in the lookup path.
9. Admin dashboard after the plugin MVP; teacher moderation inside BBB.

**Open questions for you:**
1. **Server shape and billing:** what does your A1 instance actually have (OCPU/RAM), and do you pay anything for it? Oracle now says the free allowance is 2 OCPU / 12 GB.
2. **BBB version:** 3.0.x or 4.0? And can you edit nginx on the BBB server? The identity pattern in §11.3 needs it.
3. **Content production path:** the routine on Max 5x (free, slower, a terms grey area), a one-off Batch run (≈ $90 with Sonnet or ≈ $180 with Opus for the core, done in a day), or a hybrid?
4. **Clip rights:** start with public-domain and your own content only? Will you get a quick legal opinion before using third-party clips?
5. **Learners' ages:** are any students minors? It affects Gemini use and privacy.
6. **Varieties:** Spanish defaulting to es-419 (Latin American neutral) with Spain variants tagged? Portuguese as pt-BR? Which Arabic dialect(s), later?
7. **First milestone:** the ~7.5k core (CEFR-J A1–B2 + ~1.5k phrasal verbs and idioms), or something smaller first (e.g. the top 3k)?
8. **Media library:** roughly how many hours, and do the videos already have subtitles or transcripts?
9. **Your PC as a batch node:** does it have an NVIDIA GPU, and can it run jobs overnight?
10. **GitHub:** OK to push this repo to a private GitHub repository? Routines and CI need it.
11. **Golden set:** will you rate ~50 entries once (about an hour) to calibrate the automated judge?

### 16.1 Your answers (2026-10-18)

Where these differ from the body of this audit, **these answers win**. The specs (`02`–`04`) are the source of truth from here on.

| # | Topic | Your answer | Consequence |
|---|---|---|---|
| 1 | Server | 4 OCPU / 24 GB, created before the cut, not billed so far | Design for 24 GB, but keep the core stack within a 12 GB fallback profile; check the OCI console for limits and billing |
| 2 | BBB | Upgrading from 3.0 to **4.0** today | Target BBB 4.0 and plugin SDK 1.0 (beta). The SDK research covered 0.0.105, so its capabilities get re-verified in the plugin spike |
| 3 | Content production | **Routine only**: you start runs manually during the day and chain them as each one finishes | The factory is built for routines, plus a local skill with the same instructions. The Batch API stays only as an escape hatch |
| 4 | Clips | **Postponed** | Clips are out of v1; their tables and pipeline get designed when we schedule that phase |
| 5 | Ages | All adults today; students from 16 possible | **Gemini is removed** from the stack; content must suit 16+; privacy rules cover minors; each provider's terms get checked before 16–17-year-olds enroll |
| 5b | Role of AI | Entries are fully pre-populated; AI adds adaptive help on top | Confirms the precompute-first design |
| 6 | Spanish | es-419 (Latin American neutral) by default, with variants tagged | — |
| 7 | Size | **20k entries**, launching at ~5k; your own word lists plus the open lists | A seed builder merges every list; stages: pilot → 5k (v1 launch) → 20k |
| 8 | Missing words | Provisional entry immediately | The gateway writes provisional cards, and the routine replaces them |
| 9 | Repository | Private GitHub repo | Routines and CI become possible |
| 10 | Golden set | You'll rate ~50 entries | Calibrates the reviewer rubric |
| 11 | Live AI | Haiku 5.5 → DeepSeek → Groq free tier | The gateway's fallback chain |
| 12 | v1 AI features | Use-it coach, tailored examples, explain the difference, review deck | All four are in v1 |

**Changed by these answers:** with Gemini out and generation done through routines only, the QA judge becomes a **reviewer sub-agent inside each routine run** (a fresh context with a rubric), plus Haiku spot checks on the server within the $5 cap. Jev stays optional.

**Deferred:** media library size and subtitles (until the clips phase), your PC as a batch node (optional), and Arabic dialects (until the Arabic phase).

**Revised later the same day, after reviewing the LMS:** hosting moves to a dedicated EC2 in AWS us-east-2 with a dedicated Supabase project; the dictionary keeps its own repo; the LMS integrates only through contracts. This replaces row 1 above. See `05-integration-and-hosting.md`.

---

## 17. Roadmap outline (draft for `04-roadmap.md`)

Effort is a rough guess, assuming you build with Claude Code a few hours a day.

| Phase | Goal | Main deliverables | Rough effort |
|---|---|---|---|
| **0. Decide & specify** | Agree on direction | Answers to §16; product spec; system design; EntryDoc v1 + style guide; GitHub repo | days |
| **1. Foundation** | A deployable skeleton | Monorepo; Postgres schema + migrations; FastAPI lookup/suggest/entry; ingest and validators; Compose on the A1 with Caddy and backups; dataset importers | 1–2 weeks |
| **2. Content factory MVP** | Entries flowing automatically | Grounding packs; prompts and golden set; judge; publish flow; a 500-entry pilot; calibration; then the core (routine or Batch) | 1–2 weeks, then continuous |
| **3. BBB plugin MVP** | Students using it in class | Identity spike; floating lookup panel; L1 from profile; entry card with audio; chat lookup; teacher push | ~2 weeks (overlaps phase 2) |
| **4. Context & AI** | The features WordReference and Cambridge can't match | Context-aware senses; phrasal-verb detection; provisional entries; "use it" coach through the gateway; class word list; saved words and review deck | 2–3 weeks |
| **5. Clips** | Sense-matched clips | Inventory and rights tags; ASR/alignment; utterance index; sense assignment; scoring; cutting; coverage marking; clip player | 2–4 weeks |
| **6. Scale out** | More languages, better operations | Admin dashboard; analytics; Portuguese pass; Arabic pass; Spanish headwords (bidirectional) | ongoing |

---

## Appendix A: a sample `EntryDoc`

The sample is abridged to one sense. It shows the L1 layers and a false-friend note: *actually* ≠ *actualmente* / *atualmente*.

```json
{
  "schema_version": 1,
  "key": "en:actually:adv",
  "language": "en",
  "lemma": "actually",
  "pos": "adverb",
  "kind": "word",
  "cefr": "A2",
  "pronunciations": [
    { "variety": "en-US", "ipa": "ˈæk.tʃu.ə.li" },
    { "variety": "en-GB", "ipa": "ˈæk.tʃu.ə.li" }
  ],
  "senses": [
    {
      "key": "en:actually:adv#1",
      "cefr": "A2",
      "definition": "used to say what is really true, especially when it is different from what someone thinks",
      "short_gloss": "in fact; really",
      "registers": ["neutral"],
      "patterns": ["actually + verb", "Actually, + clause"],
      "l1": {
        "es": {
          "gloss": "se usa para decir lo que es verdad, sobre todo cuando es distinto de lo que alguien cree",
          "equivalents": [
            { "term": "en realidad", "match": "exact" },
            { "term": "de hecho", "match": "near" },
            { "term": "la verdad es que", "match": "near" }
          ]
        },
        "pt-BR": {
          "gloss": "usado para dizer o que é verdade, principalmente quando é diferente do que alguém pensa",
          "equivalents": [
            { "term": "na verdade", "match": "exact" },
            { "term": "na realidade", "match": "near" }
          ]
        }
      },
      "examples": [
        {
          "key": "en:actually:adv#1.ex1",
          "text": "He looks young, but he's actually 45.",
          "cefr": "A2",
          "translations": {
            "es": "Parece joven, pero en realidad tiene 45 años.",
            "pt-BR": "Ele parece jovem, mas na verdade tem 45 anos."
          }
        },
        {
          "key": "en:actually:adv#1.ex2",
          "text": "Actually, I don't agree with you.",
          "cefr": "A2",
          "translations": {
            "es": "La verdad es que no estoy de acuerdo contigo.",
            "pt-BR": "Na verdade, eu não concordo com você."
          }
        }
      ],
      "collocations": ["what actually happened", "actually quite good"],
      "notes": [
        {
          "kind": "false_friend",
          "l1": "es",
          "text": "«Actually» no significa «actualmente». Para «actualmente» usa «currently» o «nowadays».",
          "see": ["en:currently:adv"]
        },
        {
          "kind": "false_friend",
          "l1": "pt",
          "text": "«Actually» não significa «atualmente». Para «atualmente», use «currently» ou «nowadays».",
          "see": ["en:currently:adv"]
        }
      ]
    }
  ],
  "related": [
    { "type": "confusable", "key": "en:currently:adv" },
    { "type": "family", "key": "en:actual:adj" }
  ]
}
```

A phrasal verb adds a matching pattern, which is projected into `expression_patterns`:

```json
{ "key": "en:run-into:phrv", "lemma": "run into", "kind": "phrasal_verb",
  "pattern": { "tokens": [{ "lemma": "run" }, { "lemma": "into" }], "separable": false, "max_gap": 0 } }
```

---

## Appendix B: sources (accessed 2026-10-18)

**Platforms, terms and pricing**
- Oracle Always Free resources: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm · InfoQ on the June cut: https://infoq.com/news/2026/07/oracle-cloud-free-tier-limits/
- A1 CPU inference benchmark (Nov 2025): https://tiffena.me/blog/ai-infrastructure/benchmark-cpu-only-llm-inference-oracle-ampere-a1-llama.cpp-ollama-docker/ · Ampere llama.cpp builds: https://github.com/AmpereComputingAI/llama.cpp/releases
- Claude Code routines: https://code.claude.com/docs/en/routines · cloud environments: https://code.claude.com/docs/en/cloud-environments · desktop scheduled tasks: https://code.claude.com/docs/en/desktop-scheduled-tasks · legal and compliance: https://code.claude.com/docs/en/legal-and-compliance · Consumer Terms: https://www.anthropic.com/legal/consumer-terms
- Claude API pricing: https://platform.claude.com/docs/en/about-claude/pricing · Batch API: https://platform.claude.com/docs/en/build-with-claude/batch-processing · structured outputs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- Supabase pricing: https://supabase.com/pricing · API keys: https://supabase.com/docs/guides/api/api-keys
- BullMQ priorities: https://docs.bullmq.io/guide/jobs/prioritized
- Kaggle terms: https://www.kaggle.com/terms · Gemini API terms: https://ai.google.dev/gemini-api/terms · Modal pricing: https://modal.com/pricing · Groq rate limits: https://console.groq.com/docs/rate-limits

**BBB**
- Releases: https://github.com/bigbluebutton/bigbluebutton/releases · plugin docs: https://docs.bigbluebutton.org/plugins/ · SDK 0.0.105 README: https://cdn.jsdelivr.net/npm/bigbluebutton-html-plugin-sdk@0.0.105/README.md

**Jev**
- InfoQ (2026-10-01): https://www.infoq.com/news/2026/10/typesafe-ai-jev-released/ · models: https://docs.typesafe.ai/models.md · known weaknesses: https://docs.typesafe.ai/model-jaggedness/jev-1.13.md · Check Point injection study: https://blog.checkpoint.com/ai-security/jev-is-not-a-language-model-but-it-breaks-like-one-prompt-injection-against-a-typed-decision-model/

**Models**
- Kokoro: https://huggingface.co/hexgrad/Kokoro-82M · Parakeet TDT v3: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 · CPU ASR benchmarks: https://github.com/istupakov/onnx-asr · WhisperX: https://github.com/m-bain/whisperX · Qwen3-ForcedAligner: https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B
- Chatterbox: https://github.com/resemble-ai/chatterbox · Supertonic 3: https://huggingface.co/Supertone/supertonic-3 · Magpie TTS: https://huggingface.co/nvidia/magpie_tts_multilingual_357m
- bge-m3: https://arxiv.org/abs/2402.03216 · EmbeddingGemma 2: https://huggingface.co/google/embeddinggemma-2 · Gemma 4 26B-A4B: https://huggingface.co/google/gemma-4-26B-A4B-it

**Lexical data**
- kaikki.org: https://kaikki.org/dictionary/rawdata.html · CEFR-J: https://cefr-j.org/download.html · NGSL family: https://newgeneralservicelist.com · Octanove lists: https://github.com/openlanguageprofiles/olp-en-cefrj · wordfreq: https://github.com/rspeer/wordfreq
- UniMorph: https://unimorph.github.io · LemmInflect: https://github.com/bjascob/LemmInflect · CMUdict: https://github.com/cmusphinx/cmudict · Britfone: https://github.com/JoseLlarena/Britfone · WikiPron: https://github.com/CUNY-CL/wikipron · Open English WordNet: https://github.com/globalwordnet/english-wordnet · CAMeL Tools: https://github.com/CAMeL-Lab/camel_tools

**Clips and copyright**
- PlayPhrase fair-use policy: https://playphrase.me/fair-use-policy.html · YouGlish terms: https://youglish.com/terms · YouTube developer policies: https://developers.google.com/youtube/terms/developer-policies · Spain's TRLPI: https://www.boe.es/buscar/act.php?id=BOE-A-1996-8930 · Creative Commons on database rights: https://wiki.creativecommons.org/4.0/Sui_generis_database_rights

Some figures come from secondary sources, and they are labeled where they're used. Treat every price and quota as a snapshot from 2026-10-18.
