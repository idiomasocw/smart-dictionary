# Content factory

The Claude Code routine that writes dictionary entries lives here. The rubric, routine instructions and batch scripts arrive later in Phase 2 (see `docs/04-roadmap.md` and `docs/03-system-design.md` §6).

| Path | Contents |
|---|---|
| `STYLE.md` | The editorial style guide: how to write every part of an entry. It details the product's content standard (`docs/02-product-spec.md` §6) and matches the server's checks |
| `schema/entry.v1.json` | JSON Schema of an entry document. **Generated**: never edit it by hand. After changing `backend/src/smart_dictionary/core/entrydoc.py`, run `uv run smart-dictionary schema export` from `backend/`. A test fails if the file is stale |
