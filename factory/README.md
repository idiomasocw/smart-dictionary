# Content factory

The Claude Code routine that writes dictionary entries lives here. The rubric, routine instructions and batch scripts arrive later in Phase 2 (see `docs/04-roadmap.md` and `docs/03-system-design.md` §6).

**The server's API.** Every call sends `Authorization: Bearer <token>`, created with `smart-dictionary factory token --name <label>`.

| Call | What it does |
|---|---|
| `POST /v1/factory/batches` `{size, label}` | Leases the next batch for 3 hours and returns one grounding pack per entry |
| `POST /v1/factory/validate` `{entries: [{key, doc}]}` | Runs the server's checks (V1–V11 and the length limits) without saving anything |
| `POST /v1/factory/batches/{id}/entries` `{model, prompt_version, entries: [{key, doc, review, needs_repair}]}` | Submits documents. The server checks them again, then publishes, repairs or quarantines each one |
| `GET /v1/factory/batches/{id}` | The batch and the status of each of its entries |

| Path | Contents |
|---|---|
| `STYLE.md` | The editorial style guide: how to write every part of an entry. It details the product's content standard (`docs/02-product-spec.md` §6) and matches the server's checks |
| `schema/entry.v1.json` | JSON Schema of an entry document. **Generated**: never edit it by hand. After changing `backend/src/smart_dictionary/core/entrydoc.py`, run `uv run smart-dictionary schema export` from `backend/`. A test fails if the file is stale |
