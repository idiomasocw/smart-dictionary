# Open datasets and attribution

The content factory is grounded in these open datasets. They supply facts: headword lists and levels, frequencies, inflected forms, pronunciations and translation candidates. They're used to choose and check entries. The dictionary's definitions, examples and notes are original text written by the factory, never copied from these sources.

`datasets.toml` is the manifest: source URL (pinned to a commit where possible), version, license and importer. Downloads land in `data/raw/`, which git ignores. Each import records the dataset's license, attribution and the SHA-256 of the imported bytes in the `ref_datasets` table.

```bash
cd backend
uv sync --group data
uv run smart-dictionary data list
uv run smart-dictionary data import --all
```

## Datasets

| ID | Dataset | License | Used for |
|---|---|---|---|
| `cefrj` | CEFR-J Vocabulary Profile 1.5 (Yukio Tono, TUFS) | Free for research and commercial use, with citation | Headword seeds and CEFR priors, A1–B2 |
| `octanove-c1c2` | Octanove Vocabulary Profile C1/C2 1.0 | CC BY-SA 4.0 | Headword seeds and CEFR priors, C1–C2 |
| `wordfreq-en`, `wordfreq-es` | wordfreq (Robyn Speer) | Apache-2.0 code, CC BY-SA 4.0 data | Frequency ranking (EN); Spanish lexicon check |
| `unimorph-eng`, `unimorph-spa` | UniMorph | CC BY-SA 3.0 | Inflected forms; Spanish lexicon check |
| `cmudict` | CMU Pronouncing Dictionary | BSD-2-Clause-style | Reference US pronunciations (IPA check) |
| `britfone` | Britfone 3.0.1 (Jose Llarena) | MIT | Reference UK pronunciations (IPA check) |
| `kaikki-en` | Wiktionary via kaikki.org / wiktextract | CC BY-SA 4.0 and GFDL | Sense hints, translation candidates, phrasal-verb and idiom inventory |

## Share-alike data

- **What we keep internal:** Wiktionary glosses (`ref_wiktionary.glosses`) are CC BY-SA text. They stay internal: they give the factory short sense hints and feed the originality check, and the API never serves them.
- **What we may serve:** facts derived from share-alike datasets, such as inflected forms, pronunciations and translation candidates. If those are ever served in bulk to third parties, they must carry attribution and the same license. The dictionary's original text isn't affected.
- **The attribution page:** the public one, required by CC BY-SA and the CEFR-J citation terms, is generated from `ref_datasets`.
