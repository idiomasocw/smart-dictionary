# Development fixtures

`entries/` holds a dozen hand-written entry documents for local development and the API tests. Each one exercises a lookup path:

| Path | Fixture |
|---|---|
| Irregular forms | *go* (went, gone) |
| Ambiguous form | *leaves* → *leaf* and *leave* |
| Phrasal verbs | *run into* |
| Idioms and phrases | *break the ice*, *by the way* |
| Spanish reverse lookup | *darse cuenta* → *realize*, *notice* |
| Typos | *beautiful* |
| Spacing variants | *well-known* |
| False friends | *actual*, *actually* |

They're illustrative test data, not factory output, and they're never loaded into production.

```bash
uv run smart-dictionary fixtures load     # publish them into the database in DATABASE_URL
```
