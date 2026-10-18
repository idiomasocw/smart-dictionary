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

## 4. Planned, not yet agreed

| ID | What | Direction |
|---|---|---|
| L3 | `POST /v1/resolve`: term + context → sense key, so micro-lesson vocabulary links to dictionary entries | LMS → dictionary (`lms` token) |
| L4 | `POST /v1/demand`: aggregate term counts from classes (no sentences) | LMS → dictionary |
| L5 | "My words" in the LMS dashboard, authorized with the student's Cognito JWT | LMS frontend → dictionary |

Each becomes its own section here before anyone implements it.

---

## 5. Test checklist (LMS side)

- [ ] Guard: a missing token, a wrong token, and the other routine tokens are all rejected with 401.
- [ ] A known student returns exactly the eight fields, with the `cefrLevel` of the active enrollment.
- [ ] A teacher returns `role: "teacher"` and `cefrLevel: null`.
- [ ] A suspended student returns `isActive: false`.
- [ ] An unknown UUID returns 404; a malformed UUID returns 400 or 404, never 500.
- [ ] Logs contain no names, emails or phones.
- [ ] L2: a flagged group's `create` call includes `pluginManifests`; an unflagged group's call doesn't.
