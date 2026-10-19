# Smart Video Dictionary: Roadmap

> **Status:** draft v0.1 for review · **Date:** 2026-10-18 (Sunday) · **Owner:** Diego E
> **Plans the work in:** `02-product-spec.md` (requirement IDs) and `03-system-design.md` (sections) · **Decisions:** audit §16.1
> **Assumptions:** you build with Claude Code a few hours a day; content production runs alongside engineering once the factory works. **W1 = the week of Monday 2026-10-19.** All durations are estimates, to be re-planned after the pilot (Phase 2).

---

## 1. At a glance

```
            W0   W1   W2   W3   W4   W5   W6   W7   W8   W9  W10  W11  W12  W13  W14  W15  W16
Phase 0     ██                                                          (kickoff, specs, accounts)
Phase 1          ████████                                               (foundation)
Phase 2               █████████████                                     (factory MVP + pilot)
Phase 3                         ██████████████  → continues as Phase 6  (content ramp to 5k)
Phase 4               ███████████████████                               (BBB plugin MVP, parallel)
Phase 5                              ██████████████                     (adaptive AI + learning)
v1 launch                                             ▲ ~W8–W9 (≥ 5k entries)
Phase 6                                               ████████████████████████████  (scale to 20k)
```

**The critical path is content.** The factory must be producing by about W4, because every later milestone depends on published entries. The plugin is built in parallel against fixture data and switches to real entries as they arrive.

---

## 2. Phases

### Phase 0: Kickoff (W0, this week)

| # | Task | Who |
|---|---|---|
| 0.1 | Review and approve `02`–`04` (or send changes) | you |
| 0.2 | Upgrade BBB to 4.0; confirm root access to its nginx | you |
| 0.3 | Create a **private GitHub repo**; authenticate `gh` locally so I can push | you → me |
| 0.4 | Tag the current code as `prototype-v0` (it stays in git history) | me |
| 0.5 | Supabase: create the dictionary project in **us-east-2 (Ohio)** on **Micro** in the academy org, with its Data API turned off (the legacy project is already deleted) | you |
| 0.6 | AWS: confirm the dictionary may use the academy account. I write the CDK stack in Phase 1 (EC2 t4g.medium, Elastic IP, S3 + CloudFront, IAM role, SSM) for you to deploy | you → me |
| 0.7 | DNS: a subdomain (e.g. `dictionary.onecultureworld.com`), pointed at the Elastic IP once the stack exists | you |
| 0.8 | API keys **for the dictionary only**: Anthropic (Haiku 5.5), DeepSeek, Groq | you |
| 0.9 | Put your word lists in `data/seeds/owner/` (TXT, CSV or XLSX) | you |

**Exit:** specs approved, repo on GitHub, BBB 4.0 running, the Supabase project created, keys ready.

### Phase 1: Foundation (W1–W2)

| # | Task | Design § | Req |
|---|---|---|---|
| 1.1 | New monorepo layout (`backend/ factory/ plugin/ data/ infra/ docs/`); remove the prototype folders | audit §4.5 | — |
| 1.2 | Backend scaffold: uv, FastAPI, settings that fail fast, JSON logging, ruff/pyright/pytest | §1.2 | NF-10 |
| 1.3 | Alembic migrations for DDL v1 + the tenancy amendments + `ref_*` tables; seed `languages`, `varieties` and tenant `ocw` | §3.4 | — |
| 1.4 | `core/`: `norm()`, key builder, EntryDoc models → JSON Schema export | §3.1–3.5 | — |
| 1.5 | Dataset importers: CEFR-J, Octanove, NGSL, wordfreq, UniMorph, LemmInflect, CMUdict, Britfone, kaikki subset | §3.4 | CG-1 |
| 1.6 | Read API v0 against fixture entries: `/entries/{key}`, `/suggest`, `/lookup` (G1, G2, G5, G6) | §4, §5 | LK-1, 2, 4 |
| 1.7 | Infra: the CDK stack (EC2 t4g.medium, Elastic IP, security group, IAM role, S3 + CloudFront, SSM, SNS alarms); Compose (api, worker, caddy); Supabase wiring (roles, session pooler, `LISTEN/NOTIFY` check); nightly export to S3; Uptime Kuma | §11, 05 §4–5 | OP-3 |
| 1.8 | CI/CD: lint, tests, OpenAPI → TS client drift check; arm64 images to ECR; health-gated deploy | §11.4 | NF-10 |
| 1.9 | `docs/contracts/lms-integration.md`: the L1–L2 contract that the LMS session implements | 05 §6.2 | LK-9 |

**Exit:** the skeleton serves fixture entries over HTTPS from the dictionary EC2; a nightly export lands in S3; CI is green.

### Phase 2: Content factory MVP and pilot (W2–W4)

| # | Task | Design § | Req |
|---|---|---|---|
| 2.1 | Seed builder, plus a **curation run** → ranked CSV for your skim → queue it | §6.1 | CG-1 |
| 2.2 | Grounding packs; factory endpoints (lease with `SKIP LOCKED`, submit); lease expiry job | §6.2, §3.3 | CG-2 |
| 2.3 | Validators V1–V11; quality score; publish transaction + projections | §6.4 | CG-3 |
| 2.4 | `ROUTINE.md`, `STYLE.md`, `rubric.md`, `pull/validate/submit` scripts; the local skill twin | §6.3 | CG-2 |
| 2.5 | Configure the routine: Custom network, the token as a network secret, no connectors (`factory/README.md`) | §6.3 | — |
| 2.6 | **Pilot A:** 50 entries → **your golden-set session (~1 h)** → calibrate τ and the rubric; pick Sonnet vs Opus | §6.6 | CG-6 |
| 2.7 | Embedding bake-off (EmbeddingGemma 2 / bge-m3 / harrier) → fix the vector dimension | §10.3 | LK-7, 8 |
| 2.8 | Kokoro TTS job (IPA → audio → S3) | §10.4 | EC-1, 3 |
| 2.9 | **Pilot B:** 500 entries; measure usage per run → set batch size and daily cadence | §6.3 | — |

**Exit:** 500 entries published automatically; golden-set agreement ≥ 90%; real usage per run measured; the throughput table (§3) updated with real numbers.

### Phase 3: Content ramp to 5k (W4 → ~W7, then it continues as Phase 6)

- Daily manual runs, repairs first, then by tier.
- Nightly TTS and embedding backfill.
- Audit slots in every 10th batch; a weekly glance at `/v1/admin/stats`: pass rate, quarantines, reports.
- Whenever the validators change, re-validate every published entry and send failures to repair [CG-7].

**Milestone M3:** ≥ 5,000 published entries covering CEFR-J A1–B2, top-tier phrasal verbs and idioms, and your lists.

### Phase 4: BBB plugin MVP (W2–W6, in parallel)

| # | Task | Design § | Req |
|---|---|---|---|
| 4.1 | **Spike on BBB 4.0**: hello-world, the `auth_request` round trip, chat DOM selection, a data-channel push | §9.5 | — |
| 4.2 | Plugin scaffold with `sdk-adapter/`, the generated API client, i18n EN/ES | §9.1–9.3 | NF-6 |
| 4.3 | Floating panel: search, autocomplete, direction toggle, entry card, US/UK audio | §8, §4 | LK-1…7, EC-* |
| 4.4 | Chat selection → context lookup (API: G3, §4.4, spaCy + embeddings) | §4.4 | LK-8 |
| 4.5 | Teacher tools: push, class words; *Flag* for moderators and *Hide* for verified teachers (LMS role), with reasons, caps and undo | §8, §6.6 | TF-1…3 |
| 4.6 | Accessibility pass; payload and latency check | — | NF-1, 4, 5 |
| 4.7 | **In an LMS session:** L1 learner-profile endpoint + `DICTIONARY_SERVICE_TOKEN`; L2 pilot `pluginManifests` flag | 05 §6.2 | LK-9 |
| 4.8 | BBB server: the nginx identity proxy, after the 4.0 upgrade | §9.4 | NF-8 |

**Exit:** the plugin is used in 2–3 real pilot classes, and their feedback is recorded.

### Phase 5: Adaptive AI and learning (W5–W8)

| # | Task | Design § | Req |
|---|---|---|---|
| 5.1 | AI gateway: Haiku → DeepSeek → Groq chain, circuit breakers, budget cap, cache, quotas | §7 | OP-2 |
| 5.2 | Missing words → provisional entries (gate, advisory lock, publish) | §6.5 | LK-10, CG-4 |
| 5.3 | Use-it coach | §7.1 | LF-3 |
| 5.4 | Tailored examples, with weekly promotion into entries | §7.1, §7.5 | LF-4 |
| 5.5 | Explain the difference; the routine pre-generates frequent confusable pairs | §7.1 | LF-5 |
| 5.6 | Saved words, review deck (FSRS), My words tab, history | §8 | LF-1, 2, 6 |
| 5.7 | Reports: ⚑ with the reason list (spec §5.7), caps and dedupe; the triage job and the 3-reporter rule; repair packs with evidence; *My reports* and fix notifications; `/v1/admin/quality` | §6.6, §8 | CG-5, 8, 9 |
| 5.8 | Behavior signals (`/v1/events` plus server-side ones) → suspicion-score job → automatic re-checks; batch-cluster re-checks | §6.6 | CG-10 |

**Exit:** every v1 AI feature is live under the $5 cap; the fallback chain has been tested by deliberately failing each provider; and a test report has gone all the way from ⚑ to a republished fix and the reporter's notification.

### v1 launch (≈ W8–W9)

Launch when **all** of these hold:
- ≥ 5k published entries;
- miss rate < 10% in the pilot classes;
- NF-1 latency targets met in the load test;
- a restore test passed;
- the privacy notes and a one-page teacher guide are ready;
- no open P1 bugs.

### Phase 6: Scale to 20k (W8 → ~W16)

- Continue the runs: tiers T3 and T4 (C1/C2, long tail), multiword expressions, and misses-driven priorities.
- Run promotion and audit jobs.

**Milestone M6: 20,000 published entries.** The real date comes from the throughput measured in Phase 2.

### After v1 (order to confirm)

| Release | Contents |
|---|---|
| v1.x | "Words on this slide", end-of-class recap, teacher micro-review (TF-5), better stats |
| v2 | Admin dashboard; **Portuguese layer** (a pt-BR layer pass over published entries) |
| v3 | **Clips**, after the rights decision (audit §8) |
| v4 | Arabic layer |
| v5 | Spanish headwords (bidirectional) |

---

## 3. Content production plan (estimate; replaced by Pilot B data)

At 40 entries per run:

| Runs per day | Entries per day | Days to 5k | Days to 20k |
|---|---|---|---|
| 4 | 160 | ~31 | ~125 |
| 8 | 320 | ~16 | ~63 |
| 12 | 480 | ~10 | ~42 |

**The real limit is your Max 5x usage** (5-hour windows plus weekly caps), not the number of runs. Pilot B measures how much one run uses. If the result is below your target, the levers are, in order:
1. leaner runs (fewer validation loops, compact packs);
2. Sonnet instead of Opus for bulk;
3. larger groups per sub-agent;
4. the Batch API escape hatch (≈ $235 for all 20k with Sonnet 5.5, my estimate).

---

## 4. Risks by phase

| Phase | Risk | Mitigation |
|---|---|---|
| 1 | Some datasets are hard to normalize (POS tag sets, formats) | Import only the columns we need; record the source version |
| 2 | Golden-set agreement < 90% | Iterate STYLE and the rubric; switch the bulk model to Opus; tighten validators |
| 2–3 | Usage per run is too high for the targets | The levers in §3 |
| 4 | SDK 1.0 beta differs from what the research found | The spike comes first; the `sdk-adapter/` isolates changes |
| 4 | The `auth_request` pattern doesn't work on 4.0 | Fallback: a portal-signed join token (system design §9.4) |
| 5 | A provider outage or a price change | The fallback chain; prices live in config; the cap |
| 5 | Report spam, misused hides, or noisy behavior signals | Caps and dedupe; hides only by verified teachers, logged and undoable; reports never change content; suspicion weights start conservative and are tuned on pilot data |
| 1 | Supabase's pooler and `LISTEN/NOTIFY` (Procrastinate) | Session mode or a direct IPv6 connection; verified in task 1.7 |
| All | Cost creep on AWS or Supabase | AWS Budgets alert; start on t4g.medium and Supabase Micro; resize only when alarms say so |

---

## 5. What happens right after you approve

In this session I'll start **Phase 0.4 and Phase 1**:
- tag the prototype;
- restructure the repo;
- scaffold the backend;
- write the DDL v1 migrations (tenancy included), `norm()`, the key builder and the EntryDoc models;
- write the CDK stack and the LMS contract doc.

Nothing gets pushed until your private GitHub repo exists (task 0.3).
