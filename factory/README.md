# Content factory

The Claude Code routine that writes dictionary entries lives here. The style guide, rubric, routine instructions and batch scripts arrive in Phase 2 (see `docs/04-roadmap.md` and `docs/03-system-design.md` §6).

| Path | Contents |
|---|---|
| `schema/entry.v1.json` | JSON Schema of an entry document. **Generated**: never edit it by hand. After changing `backend/src/smart_dictionary/core/entrydoc.py`, run `uv run smart-dictionary schema export` from `backend/`. A test fails if the file is stale |
