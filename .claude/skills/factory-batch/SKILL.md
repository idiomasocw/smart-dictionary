---
name: factory-batch
description: Run one Smart Dictionary content-factory batch in this session, the way the cloud routine does it. It leases words from the dictionary API, writes their entries following factory/STYLE.md, checks them with the server, has a separate reviewer judge them against factory/rubric.md, and submits them. The owner starts it with /factory-batch on their PC; the cloud routine follows factory/ROUTINE.md directly.
disable-model-invocation: true
---

# Factory batch, in this session

This is the local twin of the cloud routine. Follow `factory/ROUTINE.md`, with the differences below.

## Server and token

The scripts read `FACTORY_API` and `FACTORY_TOKEN` from the environment. Ask the owner which server to use, then check both are set before step 1. Here the owner is present, so ask when something is unclear: the routine's "don't stop to ask questions" rule is for unattended runs.

**Local server:**
1. Start the API from `backend/` with `uv run smart-dictionary api`.
2. Set `FACTORY_API=http://127.0.0.1:8000`.
3. The owner creates a token with `uv run smart-dictionary factory token --name local`, against the local database.

**Production:**
1. Leave `FACTORY_API` unset; production is the default.
2. The owner creates the token against production, as `factory/README.md` describes.

Never print the token or write it to a file. The owner sets it in the shell, for example with `read -rsp "Factory token: " FACTORY_TOKEN && export FACTORY_TOKEN`.

## Words

The words must be queued in the same database first:

```bash
uv run smart-dictionary seeds queue --lemma <word> [--lemma <word> ...]
```

Use `--dry-run` to preview. If nothing is queued, `pull_batch.py` says so and stops.

## Running the batch

- **Start small:** `--size 5 --groups 1` is a good first run. Scale up once entries publish.
- **Keep the reviewer separate:** a reviewer sub-agent in a fresh context judges the writer's entries, even with one group. The writer never reviews its own work.
- **Run the scripts with `python`** from the repo root, where ROUTINE.md says `python3`. On Windows, `python3` is often the Microsoft Store's placeholder, which only says Python wasn't found.
- **Commit nothing:** `factory/work/` is ignored by git.
