# Contract: LMS ↔ Smart Dictionary (v1)

> **Status:** draft for the LMS session · **Date:** 2026-10-18 · **Owner:** Diego E
> **Parties:** the LMS backend (`academic-platform-ocw`, NestJS on AWS) and the Smart Dictionary API (this repo).
> **Why it exists:** the two systems never share code or database tables (`05-integration-and-hosting.md` §6). Everything one needs from the other is listed here, and changing it means changing this document first.

---

## 1. Identity: what the dictionary relies on

The LMS passes **`persons.person_id`** as BigBlueButton's `userID` on every join. That covers group sessions, flex bookings, scheduled lessons and teachers alike (verified at LMS commit `0e31082a`).

On BBB, the plugin and the identity proxy see this value as the user's external ID, and the dictionary stores it as `(tenant 'ocw', external_id = person_id)`.

**The LMS's obligation:** keep passing the person ID as `userID` on every join path, including new ones. If that ever changes, the dictionary can't recognize students, so this document has to change first.

---

## 2. L1: learner profile endpoint (LMS implements, dictionary calls)

```
GET /internal/dictionary/learners/{personId}
Authorization: Bearer <DICTIONARY_SERVICE_TOKEN>
```

- **Caller:** the dictionary API, server to server over HTTPS, at most about once per student per 24 hours. Calls burst at class start, never beyond class size.
- **Auth:** its own secret, `DICTIONARY_SERVICE_TOKEN`, compared in constant time. This is the same pattern as the LMS's routine tokens, and it must not be shared with any other token. Rotation: the LMS may accept a second value (`DICTIONARY_SERVICE_TOKEN_NEXT`) during a changeover.
- **Path parameter:** `personId`, the UUID the LMS passes to BBB as `userID`.

### Responses

**200**: exactly these fields, and nothing else:

```json
{
  "personId": "3f2c9a0e-5b7d-4c1a-9e2f-8d6b1a4c7e90",
  "nativeLanguage": "es",
  "nativeRegion": "CO",
  "interfaceLanguage": "es",
  "cefrLevel": "B1",
  "role": "student",
  "isActive": true,
  "isMinor": null
}
```

| Field | Type | Source in the LMS | Notes |
|---|---|---|---|
| `personId` | UUID | `persons.person_id` | Echoes the path |
| `nativeLanguage` | string \| null | `persons.native_language` | The catalog code as stored (e.g. `es`, `pt`, `ar`) |
| `nativeRegion` | string \| null | `persons.native_region` | ISO 3166-1 alpha-2 when known |
| `interfaceLanguage` | string \| null | `persons.interface_language` | — |
| `cefrLevel` | `A1`…`C2` \| null | Level of the course of the student's most recent **active** group enrollment | null when there's none, or the level has no CEFR equivalent |
| `role` | `student` \| `teacher` \| `staff` | The person's roles | `staff` covers admins and other non-teaching roles |
| `isActive` | boolean | Students: `student_profiles.status = 'active'`; others: `persons.is_active` | The dictionary refuses personal features to inactive accounts |
| `isMinor` | boolean \| null | Not recorded yet | Stays null until the LMS records age (needed before enrolling 16–17-year-olds) |

**404** `{"error": "not_found"}`: no such person.
**401** `{"error": "unauthorized"}`: missing or wrong token.

### Rules

- **Never** return names, email, phone, payment, subscription or consent data, or anything not in the table. A new field means a new version of this document.
- **Performance:** p95 < 200 ms. The dictionary times out after 3 s.
- **When the call fails,** the dictionary uses its cached profile, or falls back to the BBB UI locale with no level. A student never loses a lookup because the LMS is slow.
- **Logging:** log the person ID and status code only.
- **Versioning:** additive fields only within v1; breaking changes move to `/internal/dictionary/v2/…`.

### How the dictionary uses the profile (for reference)

| `nativeLanguage` | `nativeRegion` | Variety shown to the student |
|---|---|---|
| `es` | `ES` | Spanish content with Spain's variants shown first |
| `es` | anything else, or null | `es-419` |
| `pt` | any | `pt-BR`, once that layer exists |
| `ar` | any | `ar`, once that layer exists |
| other / null | — | The BBB UI locale; otherwise English only |

A student can override the variety in the plugin, and the dictionary keeps that choice even when it re-syncs the profile.

---

## 3. L2: pilot flag for loading the plugin (LMS implements)

During the pilot, the plugin loads only in chosen groups' meetings:

- Add an optional `pluginManifests` field to `CreateMeetingParams`, and send it on BBB `create` **only** for groups with the dictionary enabled:
  ```
  pluginManifests=[{"url":"<DICTIONARY_PLUGIN_MANIFEST_URL>"}]
  ```
- Keep the URL in the LMS config (`DICTIONARY_PLUGIN_MANIFEST_URL`). It will look like `https://dictionary.onecultureworld.com/plugin/manifest.json`, and the exact value ships with the plugin.
- How groups are flagged is the LMS's choice. Suggested: a `groups.dictionary_enabled boolean not null default false` column that admins can toggle.
- **Once the plugin is generally available,** register the manifest for every meeting in `bbb-web.properties` on the BBB server, and keep L2 only as a kill switch.

---

## 4. L6: the owner's console (dictionary implements, LMS calls)

The LMS gives the owner a console for the dictionary: every entry with its definition, by letter and status; the pipeline's state, including what needs repair and why; missed searches; the **word requests** the owner approves or rejects; and the few settings the owner can change. The dictionary serves the data at `/v1/admin/*`, and the LMS backend calls it and renders the pages.

### Auth and safety

- **Token:** `DICTIONARY_ADMIN_TOKEN`, sent as `Authorization: Bearer <token>`. It starts with `sda_`. The owner creates it with `smart-dictionary admin token --name lms`, and the LMS keeps it with its other server secrets.
- **It isn't any other credential.** It's not the BBB proxy secret, the dictionary's factory token, or `DICTIONARY_SERVICE_TOKEN` (§2, the other direction). Each of those opens something else, and none of them opens this.
- **Server to server only.** The token never reaches a browser, and the browser never calls the dictionary.
  - The LMS exposes its own routes, guarded by its owner or super-admin check, and calls the dictionary from its backend. The dictionary sends no CORS headers, so a browser couldn't call it anyway.
  - The LMS calls only the fixed paths below, with parameters it has validated. It never forwards a path or query string the browser composed, which would turn it into an open door to the admin API.
- **Writes say who.** `PUT` and `DELETE` send `X-Admin-Actor: <personId>` of the signed-in owner, which the dictionary records in its audit. It's an ID only: the header accepts letters, digits, `.`, `_`, `:` and `-` (64 at most), so names and emails are refused.
- **The address can be pinned.** The owner may store the LMS server's public egress IP in the dictionary's `ADMIN_ALLOWED_IPS`. Calls from any other address then get a plain 404.
- **Base URL:** `DICTIONARY_API_URL`, `https://dictionary.onecultureworld.com`.
- **Timeouts:** 10 s per call. Retry reads at most once, and never retry a write on its own.
- **Caching:** none. Responses carry `Cache-Control: no-store`, and the LMS shouldn't cache them either.

### Endpoints

All `GET` unless marked. Pages are numbered from 1, `per_page` is at most 200 (default 50), and every list response has `page`, `per_page`, `total`, `pages` and `items`.

| Call | Parameters | Returns |
|---|---|---|
| `/v1/admin/stats` | — | Entries by status with a `meaning` for each, `total`, `published_last_24h`, `waiting`, `days_to_clear`, `unapproved` (AI entries hidden until their request is decided); `requests` (`pending`, `pending_ai_entries`, `pending_related`, `approved`, `auto_approved` (of the approved, those approved at once), `rejected`); missed searches by status and `stuck`; AI use (`month_spend_usd`, `monthly_budget_usd`, `searches_per_day`, `searches_last_24h`, `students_at_cap`, `provider_order`, `providers_with_keys`, `active_providers`, `calls_last_24h` by provider and outcome); audio `uploaded` and `waiting`; the last 10 factory runs |
| `/v1/admin/entries` | `letter` (`a`–`z`, or `other` for digits and symbols), `status`, `q` (the start of the headword, any case or accents), `sort` (`lemma` or `updated`, the newest change first), `page`, `per_page` | Entries: `key`, `lemma`, `pos`, `kind`, `homograph`, `cefr`, `status`, `approved` (false: hidden, waiting for a request's decision), `quality`, `attempts`, `definition` (the first sense's, null until written), `senses`, `problems` (why the latest submission was sent back, for `needs_repair` and `quarantined`), `updated_at`, `published_at` |
| `/v1/admin/entries/letters` | `status` | 27 rows, `a`–`z` then `other`, each with its `count`, zeros included |
| `/v1/admin/entries/{key}` | — | One entry's row, plus `priority`, `freq_zipf`, `sources`, `created_at`, `lease_expires_at`, the full `doc` (null until written), and its last 20 `revisions`, each with `outcome`, `score`, `problems`, `warnings`, `produced_by` and the full `qa` |
| `POST /v1/admin/entries` | Body `{"lemma", "pos", "kind", "note"?}`, header `X-Admin-Actor` | **201**: the new entry, as `/entries/{key}` returns it: queued, approved, first in the factory's queue. A pending request for the word is approved with it. **409** when the key exists, or another entry has the same multiword expression (another kind, or with or without its article); `detail` names it |
| `POST /v1/admin/entries/{key}/retire` | Body `{"note"?}`, header `X-Admin-Actor` | The entry, `retired`: out of the dictionary for good, its key never reused. For duplicates and unwanted words |
| `POST /v1/admin/entries/{key}/requeue` | Body `{"note"?}`, header `X-Admin-Actor` | The entry, back in the queue with its attempts reset. Only for `quarantined` entries, once their cause is fixed |
| `/v1/admin/misses` | `status`, `stuck` (`true`: AI gave up and no seed covers the word), `q`, `page`, `per_page` | Missed searches, the latest first: `tenant`, `language`, `query`, `hits`, `status`, `entry_key`, `attempts`, `stuck`, `last_error`, `first_seen`, `last_seen`, `last_attempt_at` |
| `/v1/admin/requests` | `status` (`pending` by default, `approved`, `rejected`), `origin` (`student`, `ai_entry`, `related`), `q`, `page`, `per_page` | Word requests, pending first and most asked first: `id`, `lemma`, `pos` and `kind` (the suggestion, or what was approved), `origin`, `status`, `entry_key`, `students` (how many asked), `searches`, `queries` (up to 5 searches that led here), `zipf`, `lists` (open lists naming it, with their level), `seed_decisions`, `options` (Wiktionary's `{pos, kind}`, likeliest first), `definition` (for `ai_entry`: the hidden Beta's), `twins` (the dictionary's other entries for this headword: for a multiword one, under any kind or with or without its article), `linked_from` (for `related`: up to 10 entries that link to it, as `"key type"`), `first_asked_at`, `last_asked_at`, `decided_at`, `decided_by` (`auto:lists` when approved at once), `note` |
| `POST /v1/admin/requests/{id}/approve` | Body `{"lemma", "pos", "kind", "note"?}`, header `X-Admin-Actor` | The request, `approved`, with its `entry_key`. The word is queued first in the factory's queue, and AI writes its Beta at once. **409** when it was decided already, its key was retired, or an approved twin exists (`detail` names it: retire one, or reject the request) |
| `POST /v1/admin/requests/{id}/reject` | Body `{"note"?}`, header `X-Admin-Actor` | The request, `rejected`. Closed for good; an `ai_entry` is retired |
| `/v1/admin/settings` | — | `settings` (below), `known_providers`, `providers_with_keys` and `applies_within_seconds` |
| `PUT /v1/admin/settings/{name}` | Body `{"value": …}`, header `X-Admin-Actor` | The setting as it is now |
| `DELETE /v1/admin/settings/{name}` | Header `X-Admin-Actor` | The setting, back on its default |
| `/v1/admin/audit` | `page`, `per_page` | Changes, newest first: `created_at`, `client`, `actor`, `action` (`setting.set`, `setting.reset`, `request.approve`, `request.reject`, `entry.add`, `entry.retire` or `entry.requeue`), `target`, `before`, `after` |

Entry statuses: `published`, `provisional`, `generating`, `validating`, `queued`, `needs_repair`, `quarantined`, `candidate`, `retired`. Missed-search statuses: `pending`, `provisional`, `published`, `queued`, `requestable`, `invalid`. Request statuses: `pending`, `approved`, `rejected`; origins: `student` (students asked), `ai_entry` (a Beta AI wrote before reviews existed, hidden until decided) and `related` (an entry links to a word the dictionary lacks; nobody asked, so `students` is 0 until someone does).

**Word requests** (dictionary design §6.7). AI writes entries only for words the owner chose. A real word a student searched for that nobody seeded becomes a request, and only the owner's approval adds it:
- **Approve** with `lemma` as requested (casing may change), `pos` from `noun`, `verb`, `adjective`, `adverb`, `pronoun`, `determiner`, `preposition`, `conjunction`, `interjection`, `numeral`, `phrase`, and `kind` from `word`, `phrasal_verb`, `idiom`, `phrase`. Offer `options` as the choices, the first selected. A `phrasal_verb` takes `verb`, and an `idiom` or `phrase` takes `phrase`.
- **Reject** closes the word: students can't request it again.
- **Each is final:** deciding a request twice answers 409.
- **Approved at once:** with `auto_approve_listed` on (the default), a student's request or a related word is approved without waiting when a curated list or a seed names it, the dictionary has no entry for it, and its part of speech is clear. Its `decided_by` is `auto:lists` and its `note` says why (`auto: on ngsl; noun/word from Wiktionary, its only one`). Show these under approved, and let the owner retire one in Entries. No `admin_audit` row: no token made the decision.
- **Related words** an approval adds wait in their natural place in the factory's queue (level, then frequency), and AI writes no Beta for them until a student searches. A student's request, approved by the owner or at once, goes first and gets its Beta at once.
- **One multiword expression, one entry:** `twins` shows what a decision would collide with. One headword of one kind under two parts of speech is fine (*a few*, determiner and pronoun).

**Settings.** Each one has `name`, `kind` (`providers`, `integer`, `number` or `boolean`), `description`, `minimum`, `maximum` (null for `providers` and `boolean`), `value` (what applies now), `default` (the dictionary's environment), `overridden`, `updated_at` and `updated_by`.

| `name` | `value` | Bounds |
|---|---|---|
| `ai_provider_order` | List of names from `known_providers`, first tried first | Each once; `[]` turns request-time AI off. A provider missing from `providers_with_keys` is skipped |
| `ai_searches_per_day` | Whole number: missed searches each student can send to AI in 24 hours | 0–200 |
| `ai_monthly_budget_usd` | The month's cap on paid AI, in USD | 0–50 |
| `word_requests_per_day` | Whole number: words each student can request in 24 hours | 0–50 |
| `auto_approve_listed` | `true` or `false`: approve a requested or related word at once when a curated list names it and its part of speech is clear | A switch; `true` by default |
| `factory_publish_threshold` | The score a factory entry needs to publish | 0.5–1 |

A change applies within `applies_within_seconds` (15), with no deploy. `DELETE` goes back to `default`.

### Examples

`GET /v1/admin/entries?letter=g&per_page=2`:

```json
{
  "page": 1, "per_page": 2, "total": 1, "pages": 1,
  "items": [{
    "key": "en:go:v", "lemma": "go", "pos": "verb", "kind": "word", "homograph": 1,
    "cefr": "A1", "status": "needs_repair", "quality": 1.0, "attempts": 1,
    "definition": null, "senses": 0,
    "problems": ["V11 en:go:v#1: 1 examples; allowed 2-3"],
    "updated_at": "2026-10-20T11:46:50.791712Z", "published_at": null
  }]
}
```

`PUT /v1/admin/settings/ai_searches_per_day` with `{"value": 40}`:

```json
{
  "name": "ai_searches_per_day", "kind": "integer",
  "description": "Missed searches each student can send to AI in 24 hours.",
  "minimum": 0.0, "maximum": 200.0, "value": 40, "default": 30, "overridden": true,
  "updated_at": "2026-10-20T11:46:51.138771Z", "updated_by": "lms"
}
```

### Errors

RFC 9457 `application/problem+json`, with a stable `code`:

| Status | `code` | When | What the console shows |
|---|---|---|---|
| 401 | `unauthorized` | Missing, wrong or revoked token | "The dictionary rejected the console's token": an operator problem, not the user's |
| 404 | `not_found` | No such entry, request or setting; also any call from an address outside `ADMIN_ALLOWED_IPS` | "Not found" |
| 409 | `conflict` | A request decided already, or its key retired; an entry that exists already, or exists under another kind (`detail` names it); retiring one the routine is writing now; requeuing one that isn't quarantined | `detail`, then reload |
| 422 | `invalid_value` | A setting out of bounds, or an approval whose headword or part of speech can't make an entry; `detail` says why, e.g. `expected a value from 0 to 50` | `detail`, next to the field |
| 422 | `invalid_request` | A bad parameter, body or `X-Admin-Actor` | A bug in the LMS: log it |

---

## 5. Planned, not yet agreed

| ID | What | Direction |
|---|---|---|
| L3 | `POST /v1/resolve`: term + context → sense key, so micro-lesson vocabulary links to dictionary entries | LMS → dictionary (`lms` token) |
| L4 | `POST /v1/demand`: aggregate term counts from classes (no sentences) | LMS → dictionary |
| L5 | "My words" in the LMS dashboard, authorized with the student's Cognito JWT | LMS frontend → dictionary |

Each becomes its own section here before anyone implements it.

---

## 6. Test checklist (LMS side)

- [ ] Guard: a missing token, a wrong token, and the other routine tokens are all rejected with 401.
- [ ] A known student returns exactly the eight fields, with the `cefrLevel` of the active enrollment.
- [ ] A teacher returns `role: "teacher"` and `cefrLevel: null`.
- [ ] A suspended student returns `isActive: false`.
- [ ] An unknown UUID returns 404; a malformed UUID returns 400 or 404, never 500.
- [ ] Logs contain no names, emails or phones.
- [ ] L2: a flagged group's `create` call includes `pluginManifests`; an unflagged group's call doesn't.
- [ ] L6: the console's routes refuse everyone but the owner (or super-admin) with the LMS's own 401/403, before any call to the dictionary.
- [ ] L6: `DICTIONARY_ADMIN_TOKEN` appears in no browser response, page source, bundle or log.
- [ ] L6: the LMS calls only the listed paths, and validates `letter`, `status`, `q`, `page` and `per_page` before passing them on.
- [ ] L6: every `PUT`, `DELETE` and `POST` sends `X-Admin-Actor` with the signed-in person's ID.
- [ ] L6: approving and rejecting a request each ask for confirmation in the LMS's own modal, and show the 409 `detail` when it was decided elsewhere.
- [ ] L6: Add, Retire and Requeue each confirm in the LMS's own modal; Retire says it can't be undone.
- [ ] L6: a dictionary 401, 404, 422 or timeout shows a message and never breaks the rest of the LMS.
