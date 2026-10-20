# Content factory

A Claude Code routine writes the dictionary's entries here, a batch at a time:
1. It leases words from the API.
2. Writer sub-agents draft the entries.
3. Reviewer sub-agents judge them.
4. The server checks every entry again and decides what's published.

The design is in `docs/03-system-design.md` §6.

Each run clones this repository, so the routine finds these files in its copy. Only `ROUTINE.md` goes into the routine's form; the rest it reads, or runs, while it works.

| Path | Who uses it, and how |
|---|---|
| `ROUTINE.md` | **You paste it, whole, into the routine's Instructions box.** It's the complete run: steps, commands, the writer's and the reviewer's briefs. It lives here so it's versioned; when it changes, paste it again |
| `STYLE.md` | The writers read it in full during the run: how to write every part of an entry. It details the product's content standard (`docs/02-product-spec.md` §6) and matches the server's checks. Like your lesson routines' writer guide, it's too long and too central to paste |
| `rubric.md` | The reviewers read it during the run: how to judge an entry |
| `schema/entry.v1.json` | The writers read it: the exact JSON shape of an entry. **Generated**: never edit it by hand. After changing `backend/src/smart_dictionary/core/entrydoc.py`, run `uv run smart-dictionary schema export` from `backend/`. A test fails if the file is stale |
| `bin/` | The scripts the run executes to call the API, below. They're the equivalent of the curl commands in your lesson routines |
| `work/` | One run's files. Git ignores it |
| `README.md` | For you, not the routine: what's here and how to set it up |

`.claude/skills/factory-batch/SKILL.md` runs the same flow in a Claude Code session on your PC.

## The batch scripts

The scripts need Python 3.9 or later and nothing installed, so the routine's environment needs no setup script. They do no checking of their own: `validate.py` asks the server, so there's only one set of rules. Run them from the repo root.

| Script | What it does | Writes |
|---|---|---|
| `pull_batch.py --size 40 --groups 4 --label "<label>"` | Leases the next batch and splits it into one group per writer, in queue order. Exits 2 when the queue is empty. It won't overwrite a batch that was never submitted unless you pass `--discard` | `work/batch.json`, `work/groups/group-N.json` |
| `validate.py work/out/group-N.json` | Runs the server's checks on drafts without saving anything. Exits 1 when any entry has an error | nothing |
| `submit_batch.py --model <model ID>` | Submits every written entry with its review, and records which instructions produced it: `prompt_version` is a hash of ROUTINE, STYLE, the rubric and the schema | `work/result.json` |

They read two environment variables:
- **`FACTORY_API`** is the server: production (`https://dictionary.onecultureworld.com`) when it's unset.
- **`FACTORY_TOKEN`** is sent as `Authorization: Bearer <token>`, when it's set.

A run's files:

```
work/
  batch.json            the lease: batch ID, expiry and every grounding pack
  groups/group-N.json   writer N's packs
  out/group-N.json      writer N's entries: [{key, doc}]
  review/group-N.json   the verdicts on group N: [{key, verdict, notes, needs_repair}]
  result.json           the server's outcome for each entry
```

## Factory tokens

A token lets its holder lease words and submit entries, nothing else. The database keeps only its hash, so a token is shown once, when it's created.

**For production**, run this from `backend/` in Git Bash:

```bash
read -rsp "Owner DATABASE_URL (session pooler): " DATABASE_URL && echo && export DATABASE_URL
uv run smart-dictionary factory token --name routine | tr -d '\r\n' | clip
unset DATABASE_URL
```

- **The first line** points the command at production without showing the URL or saving it in your shell history, as in `infra/README.md`.
- **The second line** creates the token. `tr` drops the line break after it, and `clip` puts it on the clipboard instead of the screen, ready to paste into the routine's settings.
- **The third line** forgets the URL, so later commands go back to the local database.

To cut a token off, for example if it leaks, run `uv run smart-dictionary factory revoke --name routine` the same way, then create a new one.

**For the local database**, run `uv run smart-dictionary factory token --name local` with no URL set.

## Setting up the cloud routine

A routine is a saved Claude Code task, at claude.ai/code/routines. Each run starts a fresh cloud session, which:
1. clones this repository from GitHub's default branch;
2. follows `ROUTINE.md`;
3. ends.

The routine's settings live on claude.ai, not in the repo, so this section is the record of what they should be. Routines are a research preview, so the screens may change; what each setting is for won't.

Do these steps once, in order.

### Step 1. Deploy the factory

The factory's API calls and its database change (revision 0007) aren't in production until you deploy them. A run follows the files on GitHub's `main`, so push them too.

Push your commits, then deploy as `infra/README.md` describes. The deploy applies the migration:

```bash
gh workflow run deploy.yml --repo idiomasocw/smart-dictionary
```

When the deploy is done, check that production has the factory:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://dictionary.onecultureworld.com/v1/factory/batches
```

- **`401` is right:** the call exists and asks for a token.
- **`404`** means the deploy hasn't happened yet.

### Step 2. Queue the words

A run leases only queued words, and production's queue starts empty. From `backend/`, point the shell at production with the `read -rsp` line from "Factory tokens", then:

```bash
uv run smart-dictionary seeds queue --lemma <word> --lemma <word> --dry-run
uv run smart-dictionary seeds queue --lemma <word> --lemma <word>
unset DATABASE_URL
```

- **`--dry-run`** shows what would be queued, and changes nothing.
- **A word can have several entries,** one per part of speech: `--lemma house` queues the noun and the verb.
- **Without `--lemma`,** `--limit 50` queues the 50 candidates with the highest priority.

### Step 3. Create the token

Follow "Factory tokens" above, with `--name routine`. Keep the token on the clipboard for step 4.

### Step 4. Create the environment

An environment is the machine a cloud session runs on: what it can reach on the network, and which secrets it holds. At claude.ai/code, open the environment selector and add an environment. Fill in its form:

| Field | Value | Why |
|---|---|---|
| Name | `smart-dictionary-factory` | A separate environment, so these rules don't affect your other cloud sessions |
| Network access | **Custom**. Under Allowed domains, one line: `dictionary.onecultureworld.com`. Leave "Also include default list of common package managers" unchecked | The run needs only the API; GitHub has its own path. Blocking every other host means text planted in a grounding pack can't make the session send data to a server of someone else's choosing |
| Environment variables | Leave empty | The scripts already default to production, and a token here could be read by anyone who uses the environment |
| Setup script | Leave empty | The scripts use only Python's standard library, so there's nothing to install |
| Network secrets → **Add secret** | Secret type **Bearer**. Name `factory token`. Allowed websites `dictionary.onecultureworld.com`. Custom headers: keep the default row, Name `Authorization` and Prefix `Bearer`, and paste the token (Ctrl+V) as its Value. Click **Add** | Anthropic's proxy adds the header after each request leaves the session's machine, so the token never enters it: Claude can't print or leak a token it never sees. Once saved, nobody can view the value, you included |

Click **Save changes**.

**If your form has no Network secrets section,** your plan doesn't offer them. Put the token in Environment variables instead, as the line `FACTORY_TOKEN=<paste the token>`. The scripts send it themselves, and ROUTINE.md already covers both cases.

### Step 5. Create the routine

At claude.ai/code/routines, click **New routine** and fill in its form:

| Field | Value | Why |
|---|---|---|
| Name | `Smart Dictionary content factory` | |
| Instructions | Paste the whole of `factory/ROUTINE.md`, from the first line to the last. Then change its `BATCH:` line to `BATCH: 5 words in 1 group.` for the first run | It's the complete run: steps, commands and the writers' and reviewers' briefs. The trial size keeps the first run short and cheap |
| Model | In the selector of the Instructions box: **Opus 5.5** for the first runs | Every run uses it. `submit_batch.py` records the model with each entry, so Pilot A can compare Opus and Sonnet on the same kind of words |
| Repository | `idiomasocw/smart-dictionary` | Each run clones it, so the run finds STYLE.md, the rubric, the schema and the scripts |
| Environment | `smart-dictionary-factory` | Step 4's network rules and token |
| Connectors | Remove every one | A new routine includes all your connectors, and a run can use their tools, writes included, without asking |
| Trigger | None: you'll press **Run now** | A schedule comes after the pilot, once a batch's usage is measured. The minimum interval is an hour |

Save the routine.

### Step 6. The first run

On the routine's page, click **Run now**, then open the run to follow it.

**Success looks like this:**
1. The lease prints one group of 5 words.
2. A writer and then a reviewer sub-agent run.
3. `submit_batch.py` prints a summary such as `published 5, needs repair 0, …`.
4. The report ends the run.

**What the errors mean:**
- **`401 unauthorized`:** the token didn't reach the API. Check that the secret's allowed website is exactly `dictionary.onecultureworld.com`.
- **`403` with `host_not_allowed`:** the network allowlist doesn't have the host.
- **`CERTIFICATE_VERIFY_FAILED`:** Python doesn't trust the proxy's certificate. Send me the message.
- **"queue empty":** step 2 queued nothing in production.

Once a trial works, edit the routine and set the `BATCH:` line back to `BATCH: 40 words in 4 groups.` (the routine's menu → Edit → Instructions).

**When `ROUTINE.md` changes in the repo,** paste it into the routine again: the form keeps its own copy. STYLE.md, the rubric, the schema and the scripts need nothing, because every run reads them from its fresh clone.

**Repairs and your notes.** A repair pack carries the draft its notes judged (`repair.draft`), and the writer starts from it. Notes that start with `owner:` are yours, from the console's "Send to repair": the writer follows them first, and the reviewer never fails an entry for following them.

### Reading a run

- **The run list's green status** only means the session started and ended without an infrastructure problem; a failed batch is green too. Open the run to read its report: the summary line, the time taken, and anything to look at.
- **A run leaves no branch.** Everything it writes goes to `factory/work/`, which git ignores. A `claude/` branch from a run means it changed a file it shouldn't have, so look at it before deleting it.
- **If a run dies midway,** its words return to the queue 3 hours after they were leased.
- **The overall progress:** `uv run smart-dictionary factory status` from `backend/`, pointed at production as in "Factory tokens". It shows the entries by status, how many were published in the last 24 hours, and the recent runs with their outcomes.

## The server's API

Every call sends `Authorization: Bearer <token>`.

| Call | What it does |
|---|---|
| `POST /v1/factory/batches` `{size, label}` | Leases the next batch for 3 hours and returns one grounding pack per entry |
| `POST /v1/factory/validate` `{entries: [{key, doc}]}` | Runs the server's checks (V1–V11 and the length limits) without saving anything |
| `POST /v1/factory/batches/{id}/entries` `{model, prompt_version, entries: [{key, doc, review, needs_repair}]}` | Submits documents. The server checks them again, then publishes, repairs or quarantines each one |
| `POST /v1/audit/batches` `{size, label, audit_version}` | The audit's lease: the entries due next, for 3 hours, with their facts and the rules' findings. Audit token only |
| `POST /v1/audit/batches/{id}/results` `{model, audit_version, entries: [{key, rules, findings}]}` | Records the auditor's answers and marks each entry audited. Audit token only |
| `GET /v1/factory/batches/{id}` | The batch and the status of each of its entries |

## The audit routine

A second routine checks what the first one wrote, and the words waiting to be written (design §6.6). It's how the dictionary trusts Opus but verifies: it never edits an entry. It records findings, which you apply or reject in the LMS console's Audit page. With `audit_auto_repair` on (the default), a high-confidence fix to an entry's content goes straight back to the factory, which rewrites the entry with every check.

| Path | Who uses it, and how |
|---|---|
| `AUDIT.md` | **You paste it, whole, into the audit routine's Instructions box.** The run's steps and the auditor's and checker's briefs |
| `bin/pull_audit.py --size 100 --groups 4 --label "<label>"` | Leases the entries due next, with their facts and the server's own findings, into `work/audit/batch.json` and `work/audit/groups/group-N.json`. Exits 2 when nothing is due |
| `bin/submit_audit.py --model <model ID>` | Submits the auditors' answers from `work/audit/out/group-N.json`, and prints what was recorded, applied at once, or refused and why |

The scripts send `AUDIT_TOKEN` when it's set, as the factory's send `FACTORY_TOKEN`. Every entry records the version of the instructions that audited it (a hash of AUDIT.md, STYLE.md and the rubric), so when you change one of them, every entry is due again.

**Why a separate routine, and not a step in the factory's runs.** A reviewer in the same run judges work written minutes earlier, from the same instructions. The audit reads entries days or months later, with the lists' and Wiktionary's evidence and the server's own checks in hand, and it can go back over everything already published. Its token can't lease or publish entries, and the factory's can't audit.

### Set it up

Do these once, after the deploy that brings revision 0014.

1. **Create its token.** From `backend/`, in Git Bash, as for the factory's:

   ```bash
   read -rsp "Owner DATABASE_URL (session pooler): " DATABASE_URL && echo && export DATABASE_URL
   uv run smart-dictionary audit token --name audit | tr -d '\r\n' | clip
   unset DATABASE_URL
   ```

   The token starts with `sdf_` and can only audit. To cut it off, run `uv run smart-dictionary audit revoke --name audit` the same way.
2. **Create its environment** at claude.ai/code, like step 4 above, with the name `smart-dictionary-audit`, the same Custom network rule (only `dictionary.onecultureworld.com`), and a Bearer network secret named `audit token` holding this token. A separate environment keeps each routine's token to itself.
3. **Create the routine** at claude.ai/code/routines:

   | Field | Value | Why |
   |---|---|---|
   | Name | `Smart Dictionary audit` | |
   | Instructions | The whole of `factory/AUDIT.md`. For the first run, change its `BATCH:` line to `BATCH: 10 entries in 1 group.` | A short, cheap first run |
   | Model | **Opus 5.5** | The audit is judgment: the model you trust most |
   | Repository | `idiomasocw/smart-dictionary` | Each run reads STYLE.md, the rubric and the scripts from its clone |
   | Environment | `smart-dictionary-audit` | Its own network rule and token |
   | Connectors | Remove every one | As for the factory |
   | Trigger | Daily, at a quiet hour, once a first run works | Findings wait for you each morning |

4. **Run it once** with **Run now**. Success: the lease prints one group of 10, an auditor and a checker run, and `submit_audit.py` prints a line such as `audited 10, findings 3, applied at once 1, …`. Then set the `BATCH:` line back to `BATCH: 100 entries in 4 groups.`

**When `AUDIT.md` changes in the repo,** paste it into the routine again. The run reads every other file from its clone.

### Reading its work

- **The run's report:** the summary line, anything the server refused, and patterns worth a look, such as the same problem across many entries (a rule in STYLE.md, more than single entries).
- **The console's Audit page:** open findings, the most confident first, and what was applied at once. For each type of finding it shows how often you applied and rejected them over 90 days: when you nearly always apply one type, it's safe to trust it with more.
- **A finding you reject** closes for good: the server's checks never raise it again, and the auditor sees it among the entry's past decisions.
