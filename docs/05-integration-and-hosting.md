# Smart Video Dictionary: Integration & Hosting Decisions

> **Status:** draft v0.1 for review · **Date:** 2026-10-18 · **Owner:** Diego E
> **Supersedes** the hosting and database parts of `03-system-design.md` §1, §2 and §11; those sections now point here.
> **Based on** a read-only review of the LMS repo (`academic-platform-ocw`, commit `0e31082a`) and your answers about where each piece runs.
> Prices and latencies are **estimates** from public list prices and typical inter-region round trips. Check prices in the AWS and Supabase calculators before committing.

---

## 1. Decisions at a glance

| Question | Decision | In one line |
|---|---|---|
| Which repository? | **Its own repo**: this directory, pushed as a private `idiomasocw/smart-dictionary` | Different stack and lifecycle, a small AI context, and it can become a product of its own |
| Code inside the LMS? | **No shared code.** The LMS and the dictionary talk only through versioned HTTP contracts | Loose coupling; each side can change without breaking the other |
| Database | **A dedicated Supabase project in us-east-2 (Ohio), Micro** (the legacy one is deleted), used as plain Postgres with the Data API off | Managed backups and Studio, isolated from the LMS, next to the API |
| Same DB as the LMS, separate schema? | **No** | Shared blast radius, project-wide restores, competing load, mixed migration tooling |
| Region | **us-east-2 (Ohio)** for the DB, EC2, ECR and S3 | The closest AWS region to BBB (GCP us-central1, Iowa), and where your LMS database already lives |
| Where does the API run? | **A dedicated small Graviton EC2 in us-east-2** (t4g.medium), Docker Compose + Caddy, deployed with the same pattern as your LMS | Close to the DB, the LMS and BBB; isolated from the LMS |
| On the LMS EC2, scaled up? | **No** | The t3a.small is already at its memory limit; burstable CPU; one incident would hit both |
| Serverless on AWS? | **Not for v1** | Cold starts with in-process NLP and embedding models; Fargate + ALB costs more at this scale |
| The Oracle A1 (Bogotá)? | **Off the critical path.** Optional free **batch node** for TTS and embedding backfills | Free capacity where latency doesn't matter |
| Audio storage | **A new S3 bucket + CloudFront distribution** in your AWS account | Effectively free at this scale; proven in your LMS |
| Student data from the LMS | **Pseudonymous and minimal:** person ID, native language and region, interface language, CEFR level, role, active status | Everything L1 projection and level fit need, and no PII |
| External consumers later? | **Ready by design:** tenants, auth adapters, API clients | Opening the API becomes an additive change |

---

## 2. What I verified in the LMS

### 2.1 BBB passes a stable user ID: confirmed

- Every join URL passes `userID` = **`persons.person_id`**, a UUID that never changes:
  - students: `sessions.service.ts` `joinSession()` resolves the Cognito login to the person, then `userID: studentId`, where `studentId = student.personId`;
  - flex bookings: `flex-booking.service.ts:1307` (`userID: studentPersonId`);
  - scheduled lessons: `scheduled-lessons.service.ts:1789`;
  - teachers: `sessions.service.ts:444` (`teacher.personId`).
- BBB exposes this value to plugins as the user's **external ID**, so the plugin and the identity proxy receive your person ID directly.
- Meetings are created with `meta_session_id` plus the webhook and analytics callback URLs. No `pluginManifests` and no `userdata-*` parameters are sent yet.
- Roles use the attendee and moderator passwords: teachers join as moderators.

### 2.2 Student data that exists

`persons` has:
- `native_language` and `native_region` (a catalog code plus region, via `resolveNativeLanguageInput`);
- `interface_language`;
- `country` (ISO alpha-2);
- `timezone`;
- names, email, phone, consents and signup provenance.

The CEFR level comes from the student's enrollment (group → course → level). **There is no birthdate anywhere**, so age can't be derived. If you open enrollment to 16–17-year-olds, the LMS needs an "is minor" signal first.

### 2.3 Vocabulary already flows through the LMS

- Class transcripts produce **`session_vocabulary_items`**: term, definition, example sentence and practice question per class. These are delivered as micro-lessons over WhatsApp.
- The AI tutor keeps per-student vocabulary state (mastered, in progress, struggling).

This is the most valuable integration point after launch (§6.3).

### 2.4 Hosting

| Piece | Where it runs |
|---|---|
| LMS backend | One **t3a.small (2 vCPU, 2 GB)** in us-east-1, managed by CDK. It runs blue/green backend containers (1 GB each during the overlap, with a 2 GB swapfile as "insurance"), the AI tutor agent (512 MB) and two Redis containers, behind Caddy |
| Deploys | GitHub Actions → ECR → SSH, health-gated |
| Database | A hosted Supabase project, accessed through Drizzle |
| Auth | Cognito |
| Frontend | Amplify |
| BBB (`live1`) | GCP us-central1 |

You also already run **Claude Code routines in production**, each with its own token behind a `RoutineTokenGuard` and with routine-health warnings. The dictionary's content factory uses exactly that pattern.

---

## 3. Repository strategy

| Option | Verdict |
|---|---|
| Inside the LMS monorepo | ✗ |
| **Its own repo (recommended)** | ✓ |

Why not the LMS monorepo:
- The two use different languages and toolchains (Python vs Node/TS) and need separate CI and deploy targets.
- **AI context:** the LMS repo has about 3,000 files, 200 migrations and 100+ docs. Every AI session and every routine run would start inside that context, and routines clone the whole repo on each run.
- **Least privilege:** the content factory would gain read access to all the LMS code.
- A product you may open to others later shouldn't be entangled with the LMS.

**The setup:**
- **`smart-dictionary`** (this directory): backend, factory, infra, docs. Keep the folder as it is, or rename it between sessions. Tag the prototype as `prototype-v0` and push to a new private repo in the `idiomasocw` org.
- **`smart-dictionary-bbb-plugin`** (later, its own session): it consumes the dictionary's **published OpenAPI** through a generated client, and releases independently.
- **The LMS repo** gets only the small integration changes in §6.2, done in an LMS session against the contract doc this repo publishes (`docs/contracts/lms-integration.md`, written in Phase 1).

Each repo keeps a short, focused `CLAUDE.md`. Cross-repo work moves through **contract docs and the versioned API**, the way separate teams (and separate AI sessions) work safely.

---

## 4. Database

| Option | For | Against | Verdict |
|---|---|---|---|
| Same LMS project, `dictionary` schema | No extra cost; same region | Shares compute and connections with a revenue-critical app (HNSW index builds and bulk publishes would compete with live classes and payments); backups and PITR are project-wide, so you can't restore one without rolling back the other; two migration systems on one DB (Supabase CLI/Drizzle and Alembic); a standing temptation to join LMS tables; much harder to spin off | ✗ |
| **Dedicated Supabase project, us-east-2 (Ohio)** | Managed backups; Studio for browsing entries and queues (an early admin view for free); isolated compute and restores; ~1–2 ms from the API; pgvector, pg_trgm, unaccent and fuzzystrmatch all available | Compute costs about $10/month (Micro), roughly offset by deleting the billed legacy project | **✓** |
| Self-hosted Postgres on the API host | No managed-DB fee | You own backups and upgrades, and need a bigger instance | Fallback |
| Supabase in Ohio + API in Bogotá | — | ~60–80 ms per query round trip: a lookup with 2–3 queries misses the 150 ms target | ✗ |

**Setup notes:**
1. **Create the project in us-east-2 (Ohio) on Micro** in the academy's org. (The legacy project is already deleted.) New projects start on Postgres 17.
2. **Postgres only:**
   - **disable the Data API** (no PostgREST, no anon or service keys, none of the PGRST301 errors you hit before);
   - create a dedicated `dictionary_app` role plus a migration role;
   - Alembic owns the schema.
3. **Connections:**
   - the API uses the **session pooler** (IPv4) or a direct IPv6 connection;
   - Procrastinate's `LISTEN/NOTIFY` needs session mode, never the transaction pooler (verify in Phase 1).
4. **Version gaps:** if the project runs PG 17, generate UUIDv7 keys in the app instead of using `uuidv7()`. Everything else in the DDL works on 17.
5. **Size:** start on **Micro** (1 GB RAM). 20k entries, embeddings and a year of lookups stay under ~2–3 GB of disk *(estimate)*. Move to Small (~$15/month) if p95 latency suffers; it's a one-click resize.
6. **Backups:** Supabase's daily backups, plus our nightly logical export of published entries to S3, for portability and long retention.
7. **Local development:** the Supabase CLI with `project_id = "smart-dictionary"` and ports **34320–34329**. These sit clear of your LMS stack (54320–54329), the Windows reserved ranges and the dynamic range (49152+). Only the database and Studio run locally; Alembic owns the schema in both places.

**How Supabase bills compute** (from Supabase's docs, read 2026-10-18):
- Pro includes **$10/month in compute credits per organization**. That covers one Micro or Nano project.
- Each project's compute is billed hourly.
- New projects on paid plans can't use Nano, and an existing Nano project in a paid organization **is billed at Micro's price**.
- Your LMS (Nano) therefore already costs the price of Micro while getting half the RAM. Switching it to Micro adds nothing to the bill. It needs a short restart, so do it in a quiet window.

---

## 5. Compute and hosting

**Latency map (estimated round trips):**

```
 BBB (GCP us-central1, Iowa) ──~10–15 ms──▶ dictionary API (AWS us-east-2, Ohio) ──~1–2 ms──▶ Supabase (us-east-2)
                                                ▲  ~10–12 ms
                                                └── LMS (AWS us-east-1)
 vs. dictionary API in Oracle Bogotá: ~70–90 ms from BBB and ~60–80 ms per DB query
```

| Option | For | Against | Verdict |
|---|---|---|---|
| **Dedicated t4g.medium (Graviton, 2 vCPU / 4 GB), us-east-2** | Isolated; cheap; ARM matches our arm64 images; same proven deploy pattern as the LMS (ECR, health-gated, Caddy) | About $30/month all-in *(estimate: instance ~$24.5 + public IPv4 ~$3.6 + 20 GB gp3 ~$1.6)*; a 1-year Savings Plan cuts it ~30% | **✓** |
| LMS EC2 scaled to t3a.large | One host | +$40/month to reach 8 GB; burstable CPU credits drained by TTS and embedding jobs would throttle the LMS; the dictionary's untrusted AI inputs would sit on the same host as the LMS's payment secrets; deploy scripts touch both | ✗ |
| Lambda / API Gateway | Pay per use | spaCy and embedding models mean seconds-long cold starts in class unless you pay for provisioned concurrency | ✗ for v1 |
| ECS Fargate + ALB | Managed containers | ~$35–50/month before compute is even busy | Later, if ever |
| Oracle A1, Bogotá | Free | Latency (above); the free allowance was already halved; idle reclamation | Batch node only |

**The host runs `caddy`, `api` and `worker`.** About 3–3.5 GB is used at peak *(estimate)*: spaCy plus the embedding model in the API, and Kokoro in the worker during off-hours.

**Optional batch node:** the same worker image on the Oracle A1, consuming only the `batch` queue (TTS, embedding backfills, seed imports) and connecting to Supabase. If it disappears, the EC2 worker takes the batch queue at night. Nothing student-facing depends on it.

**Infrastructure as code:** a **separate CDK stack** in this repo (`infra/cdk`), never added to the LMS stack:
- EC2, Elastic IP and security group (443 open; `bbb` routes limited by Caddy to the BBB server's IP);
- an instance role (its S3 prefixes, SSM Parameter Store for secrets);
- the S3 bucket and CloudFront distribution;
- SNS alarms.

**DNS:** e.g. `dictionary.onecultureworld.com` → the Elastic IP.

---

## 6. LMS integration: the contracts

### 6.1 Identity (no LMS code change)

1. BBB `userID` = `person_id`, which the plugin sees as its external ID.
2. nginx on the BBB server runs `auth_request` against BBB's session check, then forwards `X-BBB-Ext-User`, `X-BBB-Meeting` and `X-BBB-Is-Mod`, plus the shared secret (system design §9.4).
3. The dictionary stores it as `(tenant = 'ocw', external_id = person_id)`.

### 6.2 Changes in the LMS repo (small, done in an LMS session)

| # | Change | Why |
|---|---|---|
| L1 | `GET /internal/dictionary/learners/:personId`, guarded by its own `DICTIONARY_SERVICE_TOKEN` (the RoutineTokenGuard pattern). Returns `{personId, nativeLanguage, nativeRegion, interfaceLanguage, cefrLevel, role, isActive}` and nothing else | L1 projection, level fit, and blocking inactive accounts. The dictionary pulls on first sight and caches for 24 h |
| L2 | Add `pluginManifests` to `CreateMeetingParams`, sent only for groups behind a pilot flag | A controlled rollout to 2–3 pilot classes before enabling it everywhere (later: `bbb-web.properties`) |
| L3 *(later)* | Micro-lesson pipeline: call `POST /v1/resolve` (term + context → sense key) and store the `sense_key` on `session_vocabulary_items` | WhatsApp micro-lessons reuse the dictionary's definitions, translations and audio instead of generating their own; one consistent lexicon |
| L4 *(later)* | Send aggregate term counts from classes (`POST /v1/demand`: term → count, **no sentences**) | Real classroom demand drives factory priority |
| L5 *(later)* | The LMS student dashboard shows "My words" by calling the dictionary with the student's **Cognito JWT** | Review outside BBB; it needs the dictionary's JWT auth adapter (§7) |

**Considered and rejected:** reading the LMS database directly, even through a read-only view. It couples schemas and spreads credentials. The LMS owns its data and exposes only what's needed.

### 6.3 Data policy: what we use, and what we don't

| Use now | Use later, with care | Never |
|---|---|---|
| `person_id` (pseudonymous key), role, active status | Aggregate class term counts (L4) | Names, email, phone |
| `native_language` + `native_region` → variety (`es` + any LatAm region → es-419; `es` + `ES` → es-ES display preference; `pt` + `BR` → pt-BR; `ar` → ar) | Links between LMS vocabulary items and senses (L3) | Payments, subscriptions, consents, signup IP and user agent |
| `interface_language` (UI fallback) | The AI tutor's vocabulary states, for a personal review deck (needs a privacy review first) | Transcript text, which is students' personal speech |
| Current CEFR level | — | WhatsApp data, marketing data |

---

## 7. Is the design too coupled for external consumers? No, and three cheap additions make sure of it

- **Content is global.** It's the product, and it carries no LMS dependency.
- **BBB is one identity adapter among several.** The API builds a `Principal(tenant, subject, roles, via)` from one of:
  - `bbb_proxy` (now);
  - `lms_service` (now, server to server);
  - `oidc_jwt` (Cognito for your LMS web and app; any IdP for partners);
  - `api_key` (external apps).
- **The three additions (built in Phase 1):**
  1. a `tenants` table, with `tenant_id` on users, lookups, saved items, review cards, pushes, flags, reports and quotas;
  2. `api_clients` instead of `api_tokens`, with scopes, per-client rate limits and usage metering;
  3. learner IDs that are always unique per tenant: `(tenant_id, external_id)`.
- **Licensing, before selling access:**
  - **AI content:** re-confirm Anthropic's terms for subscription-generated content distributed commercially, or upgrade entries through API keys under the Commercial Terms.
  - **Share-alike data:** forms and IPA derived from UniMorph or Wiktionary carry attribution and share-alike obligations *for those fields*; our original text doesn't.
  - **Audio:** Kokoro is Apache-2.0, so the audio is fine.

---

## 8. BBB 4.0 migration notes for your LMS

From BBB's "what's new in 4.0" page (read today):

- **bbb-web fails to start** if `allowDuplicateExtUserid`, `lockSettingsDisableNote`, `clientLogoutTimerInMinutes`, `enteredUsersTimeout` or `sharedNotesEditor` remain in `bbb-web.properties` or `resources.xml`. `maxUserConcurrentAccesses` replaces `allowDuplicateExtUserid`. That matters because your `userID` is the person ID: two tabs or devices for the same person now follow this setting.
- **Upgrade path:** Ubuntu 24.04; upgrade with `apt full-upgrade` (Etherpad is removed); Java 21.
- **`disabledFeatures`:** `captions` is no longer valid; use `liveTranscription`.
- **Defaults:** audio, camera and screen share now run on LiveKit. The `CUSTOM`, `SMART`, `PRESENTATION_FOCUS` and `VIDEO_FOCUS` layouts are gone.
- **Plugins:** SDK 1.0.0-beta.3; manifests must declare `^1.0.0-beta.1`-style ranges.
- **Not covered by the page, so test after upgrading:**
  - joins using `attendeePW`/`moderatorPW` (if they're refused, switch the LMS to the `role` join parameter);
  - the global webhook and the signed analytics callback;
  - `getMeetingInfo`'s XML responses.
- **Unrelated, but worth knowing before 16–17-year-olds enroll:** the LMS's Gemini API fallback (in place since 2026-10-16) sits under the Gemini API terms, which require users to be 18+.

---

## 9. Cost (monthly, estimates)

| Item | Before | After |
|---|---|---|
| Dictionary compute (t4g.medium + IPv4 + EBS) | — | ~$30 (≈ $21 with a 1-year Savings Plan) |
| Dictionary Supabase project (Micro) | legacy project ~$10 (deleted) | ~$10 (your Supabase bill: Pro $25 + LMS $10 + dictionary $10 − $10 credit = $35, the same as with the legacy project) |
| S3 + CloudFront (audio, exports) | — | ~$0–1 (inside CloudFront's always-free tier) |
| AI gateway | — | ≤ $5 (hard cap) |
| Content factory | Max 5x (already paid) | unchanged |
| **Net new** | | **≈ $30–35/month** |

**The $0 alternative still exists:** everything on the Oracle A1 with self-hosted Postgres. You'd accept ~70–90 ms from BBB, slower DB access from AWS, and the free tier's reliability risk. I don't recommend it for a service students depend on in live classes.

---

## 10. What this changes in the plan

- **System design:** §1–2 (diagram, ADRs 002 and 016–019), §3.4 (tenants, `api_clients`, UUIDv7), §5.1 (auth contexts), §11 (AWS infrastructure) now follow this document.
- **Roadmap:**
  - Phase 0 gains the AWS, Supabase and DNS setup and the deletion of the legacy project.
  - Phase 1 builds the CDK stack and tenancy.
  - LMS tasks L1–L2 join Phase 4 (in an LMS session).
  - The Oracle risks become optional-batch-node notes.
- **Product spec:** NF-9 cost (~$30–35/month infrastructure, plus ≤ $5 AI); §10 assumptions.
