"""Server-side checks for submitted entries (design §6.4) [CG-3].

The server is the authority: the routine runs these checks while it writes, through
POST /v1/factory/validate, and the submit step runs them again. Each issue carries the
design's check ID (V1-V11, or "length" for the style guide's limits) and a severity.
Errors block publishing; warnings lower the quality score.

Three checks use stand-ins until their tools exist:
- V5 lets each word vote by frequency instead of running a language detector: a word is
  Spanish evidence when wordfreq finds it at least as common in Spanish as in English.
  Lexicon membership alone can't tell the languages apart, because the Spanish web lists
  everyday English words (to, the, beach).
- V8 compares the words of two definitions, rather than sense embeddings.
- V3 matches inflected forms and multiword patterns without spaCy.
"""

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from typing import Any, Literal

import psycopg
from lemminflect import getAllInflections, getAllLemmas
from pydantic import ValidationError

from smart_dictionary.content.projections import english_forms
from smart_dictionary.core.entrydoc import EntryDoc, LemmaToken, Pattern, SlotToken
from smart_dictionary.core.text import norm

Severity = Literal["error", "warn"]

DEFINING_LISTS = ["ngsl", "ngsl-sup", "ngsl-spoken"]
MIN_DEFINING_SHARE = 0.85
MIN_SPANISH_SHARE = 0.8
MIN_SPANISH_WORDS = 3
# V5: a word votes English only when it's over ten times as common in English (Zipf is a
# log10 scale). Closer than that it's shared, like hotel, local, plan or real, and doesn't
# vote. Neither do a few everyday Spanish words even more common in English: ¿has visto?,
# Diego come, once, date prisa, que use.
ENGLISH_MARGIN = 1.0
SHARED_WORDS = frozenset({"come", "date", "has", "once", "use"})
MAX_UNKNOWN_EQUIVALENT_WORDS = 2
MAX_IPA_DISTANCE = 0.35
MIN_DUPLICATE_OVERLAP = 0.8
NGRAM = 8
SLOT_GAP = 4  # words an open slot (sb, sth, one's) can take

WORD = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*")
QUOTED = re.compile(r"«[^»]*»")
IPA_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzæɑɐɒəɚɛɜɝɪɨʊʉɔʌøœɤɯβçðɖɟɡɣɦʝɫɬɭʎɱŋɲɳɹɾɽʀʁʂʃʈθʋʍχʐʒʔʕːˑˈˌʰʲʷ̥̩̯̃͡ "
)
# Examples are for an audience of 16 and up (EC-7). A short floor: the reviewer and the
# server's spot checks look for the rest.
BLOCKED = frozenset(
    {
        "sex", "sexy", "sexual", "porn", "porno", "pornography", "naked", "nude", "orgasm",
        "rape", "raped", "rapist", "murder", "murdered", "murderer", "kill", "killed",
        "killing", "suicide", "torture", "terrorist", "terrorism", "bomb", "cocaine",
        "heroin", "meth", "drunk", "nazi", "genocide",
    }
)  # fmt: skip
OFFENSIVE_MARKERS = re.compile(r"\b(offensive|insult|insulting|rude|vulgar|taboo|slur)\b")


@dataclass(frozen=True)
class Issue:
    check: str
    severity: Severity
    where: str
    message: str


@dataclass(frozen=True)
class Entry:
    """The stored entry a document is written for: its fixed facts, and its current
    document when there is one (a repair or a provisional entry)."""

    key: str
    lemma: str
    pos: str
    kind: str
    doc: dict[str, Any] | None = None


@dataclass(frozen=True)
class Context:
    """The reference data the checks need, loaded once for a set of documents."""

    vocab: frozenset[str]
    spanish: frozenset[str]
    zipf: dict[tuple[str, str], float]  # (language, word) -> Zipf, for words in Spanish texts
    ipa: dict[str, dict[str, list[str]]]
    glosses: dict[str, frozenset[tuple[str, ...]]]

    @classmethod
    def load(
        cls, conn: psycopg.Connection, entries: Iterable[Entry], docs: Iterable[dict[str, Any]]
    ) -> "Context":
        words = sorted({norm(entry.lemma) for entry in entries})
        tokens = sorted({token for doc in docs for _, text in _spanish_texts(doc) for token in
                         _spanish_tokens(text)} | {token for doc in docs for term in
                         _equivalent_terms(doc) for token in _spanish_tokens(term)})  # fmt: skip
        vocab = conn.execute(
            "select lemma_norm from ref_lists where dataset_id = any(%s)", (DEFINING_LISTS,)
        ).fetchall()
        spanish = conn.execute(
            "select word from ref_freq where language = 'es' and word = any(%(t)s)"
            " union select form_norm from ref_forms"
            " where language = 'es' and form_norm = any(%(t)s)",
            {"t": tokens},
        ).fetchall()
        zipf = conn.execute(
            "select language, word, max(zipf) from ref_freq"
            " where language in ('en', 'es') and word = any(%s) group by 1, 2",
            (tokens,),
        ).fetchall()
        ipa: dict[str, dict[str, list[str]]] = {}
        for word, variety, value in conn.execute(
            "select word_norm, variety, ipa from ref_ipa where word_norm = any(%s) order by id",
            (words,),
        ):
            ipa.setdefault(word, {}).setdefault(variety, []).append(value)
        glosses: dict[str, set[tuple[str, ...]]] = {}
        for word, texts in conn.execute(
            "select word_norm, glosses from ref_wiktionary where word_norm = any(%s)", (words,)
        ):
            grams = glosses.setdefault(word, set())
            for text in texts:
                grams.update(_ngrams(_english_tokens(text)))
        return cls(
            vocab=frozenset(row[0] for row in vocab),
            spanish=frozenset(row[0] for row in spanish),
            zipf={(language, word): float(value) for language, word, value in zipf},
            ipa=ipa,
            glosses={word: frozenset(grams) for word, grams in glosses.items()},
        )


def validate(entry: Entry, raw: dict[str, Any], context: Context) -> list[Issue]:
    """Every issue in the document, errors and warnings."""
    try:
        doc = EntryDoc.model_validate(raw)
    except ValidationError as error:
        return [
            Issue("V1", "error", ".".join(map(str, problem["loc"])) or "entry", problem["msg"])
            for problem in error.errors()
        ]
    checks = (
        _fixed_facts(entry, doc),
        _kept_keys(entry, doc),
        _counts_and_lengths(doc),
        _headword_in_examples(doc),
        _definitions(doc, context),
        _spanish(doc, context),
        _pronunciation(doc, context),
        _duplicates(doc),
        _copied(doc, context),
        _safety(doc),
    )
    return [issue for check in checks for issue in check]


# Quality score: each warning costs its weight (0.1 by default), and the in-run reviewer's
# verdict adds its penalty. An entry publishes at or above the threshold (settings).
WARNING_WEIGHTS = {"V4": 0.1, "V6": 0.1, "V7": 0.05, "V8": 0.15}
REVIEW_PENALTY = {"pass": 0.0, "uncertain": 0.15, "fail": 0.5}


def score(issues: Iterable[Issue], verdict: str | None) -> float:
    penalty = sum(WARNING_WEIGHTS.get(i.check, 0.1) for i in issues if i.severity == "warn")
    penalty += REVIEW_PENALTY.get(verdict or "pass", 0.0)
    return round(max(0.0, 1.0 - penalty), 3)


# V2 ---------------------------------------------------------------------------------------


def _fixed_facts(entry: Entry, doc: EntryDoc) -> Iterator[Issue]:
    for name in ("key", "lemma", "pos", "kind"):
        expected, found = getattr(entry, name), getattr(doc, name)
        if found != expected:
            yield Issue("V2", "error", name, f"must stay {expected!r}, as leased (got {found!r})")


def _kept_keys(entry: Entry, doc: EntryDoc) -> Iterator[Issue]:
    """Published keys never disappear; a sense is retired instead (ADR-005)."""
    if not entry.doc:
        return
    missing = _keys(entry.doc) - _keys(doc.model_dump(mode="json", by_alias=True))
    for key in sorted(missing):
        yield Issue("V2", "error", key, "a published key is missing; retire it instead")


def _keys(raw: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for sense in raw.get("senses") or []:
        keys.add(sense["key"])
        parts = (sense.get("examples") or []) + (sense.get("notes") or [])
        keys.update(part["key"] for part in parts)
    return keys


# V11 and the style guide's limits ---------------------------------------------------------


def _counts_and_lengths(doc: EntryDoc) -> Iterator[Issue]:
    active = [sense for sense in doc.senses if not sense.retired]
    if not 1 <= len(active) <= 6:
        yield Issue("V11", "error", doc.key, f"{len(active)} active senses; allowed 1-6")
    for sense in active:
        where = sense.key
        if not 2 <= len(sense.examples) <= 3:
            yield Issue("V11", "error", where, f"{len(sense.examples)} examples; allowed 2-3")
        if len(sense.collocations) > 6:
            yield Issue("V11", "error", where, f"{len(sense.collocations)} collocations; max 6")
        if len(sense.notes) > 3:
            yield Issue("V11", "error", where, f"{len(sense.notes)} notes; max 3")
        if len(_words(sense.definition)) > 20:
            yield Issue("V4", "error", where, "definition over 20 words")
        if len(_words(sense.short_gloss)) > 5:
            yield Issue("length", "error", where, "short gloss over 5 words")
        layer = sense.l1.es_419
        if layer is not None:
            if len(layer.gloss) > 160:
                yield Issue("length", "error", where, "Spanish gloss over 160 characters")
            if not 1 <= len(layer.equivalents) <= 5:
                yield Issue("V11", "error", where, f"{len(layer.equivalents)} equivalents; 1-5")
            for equivalent in layer.equivalents:
                if equivalent.note and len(equivalent.note) > 120:
                    yield Issue("length", "error", where, f"note on {equivalent.term!r} over 120")
        for example in sense.examples:
            count = len(_words(example.text))
            if not 6 <= count <= 16:
                yield Issue("length", "error", example.key, f"{count} words; allowed 6-16")
        for note in sense.notes:
            if len(note.text) > 300:
                yield Issue("length", "error", note.key, "note over 300 characters")
    for related in doc.related:
        if related.note and len(related.note) > 120:
            yield Issue("length", "error", related.key, "related note over 120 characters")


# V3 ---------------------------------------------------------------------------------------


def _headword_in_examples(doc: EntryDoc) -> Iterator[Issue]:
    forms = set(english_forms(doc))
    for sense in doc.senses:
        if sense.retired:
            continue
        for example in sense.examples:
            tokens = _english_tokens(example.text)
            # A possessive shows its word too: the company's profits.
            found = any(
                _matches(words, doc.pattern)
                if doc.pattern
                else any(gram in forms for gram in _grams(words))
                for words in (tokens, _without_possessives(tokens))
            )
            if not found:
                yield Issue("V3", "error", example.key, "no form of the headword in the example")


def _matches(tokens: list[str], pattern: Pattern) -> bool:
    """The pattern's words in order: a lemma in any form, text as written, and an open slot
    taking up to SLOT_GAP words. A separable phrasal verb's particle may come up to
    `max_gap` words after the verb (turn the TV off)."""
    steps: list[tuple[frozenset[str], int]] = []
    gap = 0
    for token in pattern.tokens:
        if isinstance(token, SlotToken):
            gap = SLOT_GAP
            continue
        if isinstance(token, LemmaToken):
            words = _inflected(token.lemma)
        else:
            words = frozenset({norm(token.text)})
        steps.append((words, gap))
        gap = 0
    if pattern.separable and len(steps) > 1:
        steps[1] = (steps[1][0], max(steps[1][1], pattern.max_gap))
    return bool(steps) and any(_match_from(tokens, i, steps) for i in range(len(tokens)))


def _match_from(tokens: list[str], start: int, steps: list[tuple[frozenset[str], int]]) -> bool:
    if tokens[start] not in steps[0][0]:
        return False
    if len(steps) == 1:
        return True
    gap = steps[1][1]
    end = min(len(tokens), start + 2 + gap)
    return any(_match_from(tokens, i, steps[1:]) for i in range(start + 1, end))


@lru_cache(maxsize=4096)
def _inflected(lemma: str) -> frozenset[str]:
    forms = {norm(lemma)}
    for values in getAllInflections(lemma).values():
        forms.update(norm(value) for value in values)
    return frozenset(forms)


# V4 ---------------------------------------------------------------------------------------


def _definitions(doc: EntryDoc, context: Context) -> Iterator[Issue]:
    forms = set(english_forms(doc))
    for sense in doc.senses:
        if sense.retired:
            continue
        tokens = _english_tokens(sense.definition)
        bare = _without_possessives(tokens)  # someone's is as basic as someone
        if any(gram in forms for words in (tokens, bare) for gram in _grams(words)):
            yield Issue("V4", "error", sense.key, "the definition uses the headword")
        if bare and context.vocab:
            share = sum(_defining(token, context.vocab) for token in bare) / len(bare)
            if share < MIN_DEFINING_SHARE:
                yield Issue(
                    "V4", "warn", sense.key, f"{share:.0%} of the words are basic; aim for 85%"
                )


def _defining(token: str, vocab: frozenset[str]) -> bool:
    if token in vocab:
        return True
    return any(lemma in vocab for lemmas in getAllLemmas(token).values() for lemma in lemmas)


# V5 and V6 --------------------------------------------------------------------------------


def _spanish(doc: EntryDoc, context: Context) -> Iterator[Issue]:
    raw = doc.model_dump(mode="json", by_alias=True)
    for where, text in _spanish_texts(raw):
        share = _spanish_share(_spanish_tokens(text), context)
        if share is not None and share < MIN_SPANISH_SHARE:
            yield Issue("V5", "error", where, f"doesn't read as Spanish ({share:.0%} Spanish)")
    unknown = [
        token
        for term in _equivalent_terms(raw)
        for token in _spanish_tokens(term)
        if token not in context.spanish
    ]
    if unknown:
        severity: Severity = "error" if len(unknown) > MAX_UNKNOWN_EQUIVALENT_WORDS else "warn"
        words = ", ".join(sorted(set(unknown)))
        yield Issue("V6", severity, doc.key, f"equivalents use words the lexicon lacks: {words}")


def _spanish_share(tokens: list[str], context: Context) -> float | None:
    """The share of voting words that vote Spanish. A word votes Spanish when it's at least
    as frequent in Spanish as in English, and English when its English Zipf is more than
    ENGLISH_MARGIN higher. Shared words, and words neither language knows (names, typos),
    don't vote; None when too few do."""
    votes = []
    for token in tokens:
        spanish = context.zipf.get(("es", token), 0.0)
        english = context.zipf.get(("en", token), 0.0)
        if token in SHARED_WORDS or not (spanish or english):
            continue
        if spanish >= english:
            votes.append(True)
        elif english - spanish > ENGLISH_MARGIN:
            votes.append(False)
    if len(votes) < MIN_SPANISH_WORDS:
        return None
    return sum(votes) / len(votes)


def _spanish_texts(raw: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """Every Spanish text in a document, read defensively: it may not be valid yet."""
    for sense in _list(raw.get("senses")):
        key = str(sense.get("key", "sense"))
        layer = _dict(_dict(sense.get("l1")).get("es-419"))
        if isinstance(layer.get("gloss"), str):
            yield f"{key} es-419 gloss", layer["gloss"]
        for equivalent in _list(layer.get("equivalents")):
            if isinstance(equivalent.get("note"), str):
                yield f"{key} es-419 note on {equivalent.get('term')!r}", equivalent["note"]
        for example in _list(sense.get("examples")):
            text = _dict(example.get("translations")).get("es-419")
            if isinstance(text, str):
                yield f"{example.get('key', key)} es-419", text
        for note in _list(sense.get("notes")):
            if note.get("l1") == "es" and isinstance(note.get("text"), str):
                yield str(note.get("key", key)), note["text"]


def _equivalent_terms(raw: dict[str, Any]) -> Iterator[str]:
    for sense in _list(raw.get("senses")):
        layer = _dict(_dict(sense.get("l1")).get("es-419"))
        for equivalent in _list(layer.get("equivalents")):
            if isinstance(equivalent.get("term"), str):
                yield equivalent["term"]


def _spanish_tokens(text: str) -> list[str]:
    """Normalized Spanish words, leaving out quoted English («go home») and capitalized names
    after the first word."""
    words = _words(QUOTED.sub(" ", text))
    return [norm(word, "es") for i, word in enumerate(words) if i == 0 or not word[0].isupper()]


# V7 ---------------------------------------------------------------------------------------


def _pronunciation(doc: EntryDoc, context: Context) -> Iterator[Issue]:
    references = context.ipa.get(norm(doc.lemma), {})
    for pronunciation in doc.pronunciations:
        where = f"{pronunciation.variety} IPA"
        odd = sorted({char for char in pronunciation.ipa if char not in IPA_CHARS})
        if odd:
            yield Issue("V7", "warn", where, f"characters outside IPA: {''.join(odd)}")
        candidates = references.get(pronunciation.variety)
        if candidates:
            distance = min(_distance(pronunciation.ipa, ref) for ref in candidates)
            if distance > MAX_IPA_DISTANCE:
                yield Issue("V7", "warn", where, f"far from the dictionary ({distance:.2f})")


def _distance(a: str, b: str) -> float:
    """Normalized edit distance, ignoring stress, syllable dots and r/ɹ, g/ɡ spellings."""
    a, b = _plain_ipa(a), _plain_ipa(b)
    if not a or not b:
        return 1.0
    row = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        previous, row[0] = row[0], i
        for j, char_b in enumerate(b, start=1):
            previous, row[j] = (
                row[j],
                min(row[j] + 1, row[j - 1] + 1, previous + (char_a != char_b)),
            )
    return row[-1] / max(len(a), len(b))


def _plain_ipa(value: str) -> str:
    return value.translate(str.maketrans({"ˈ": None, "ˌ": None, ".": None, " ": None,
                                          "ɹ": "r", "ɡ": "g"}))  # fmt: skip


# V8 ---------------------------------------------------------------------------------------


def _duplicates(doc: EntryDoc) -> Iterator[Issue]:
    active = [sense for sense in doc.senses if not sense.retired]
    for first, second in combinations(active, 2):
        a, b = set(_english_tokens(first.definition)), set(_english_tokens(second.definition))
        if a and b and len(a & b) / len(a | b) >= MIN_DUPLICATE_OVERLAP:
            yield Issue("V8", "warn", second.key, f"may mean the same as {first.key}")


# V9 ---------------------------------------------------------------------------------------


def _copied(doc: EntryDoc, context: Context) -> Iterator[Issue]:
    grams = context.glosses.get(norm(doc.lemma))
    if not grams:
        return
    for where, text in _english_texts(doc):
        if any(gram in grams for gram in _ngrams(_english_tokens(text))):
            yield Issue("V9", "error", where, f"{NGRAM} words in a row match Wiktionary")


def _english_texts(doc: EntryDoc) -> Iterator[tuple[str, str]]:
    for sense in doc.senses:
        yield f"{sense.key} definition", sense.definition
        for example in sense.examples:
            yield example.key, example.text
        for note in sense.notes:
            if note.l1 is None:
                yield note.key, note.text


# V10 --------------------------------------------------------------------------------------


def _safety(doc: EntryDoc) -> Iterator[Issue]:
    for sense in doc.senses:
        if sense.retired:
            continue
        offensive = OFFENSIVE_MARKERS.search(sense.definition.lower())
        if offensive and "offensive" not in sense.registers:
            yield Issue("V10", "error", sense.key, "an offensive sense needs the offensive label")
        for example in sense.examples:
            blocked = ", ".join(sorted(BLOCKED & set(_english_tokens(example.text))))
            if blocked:
                yield Issue("V10", "error", example.key, f"unsuitable for 16+: {blocked}")


# Text helpers -----------------------------------------------------------------------------


def _words(text: str) -> list[str]:
    return WORD.findall(text)


def _english_tokens(text: str) -> list[str]:
    return [norm(word) for word in _words(text)]


def _without_possessives(tokens: list[str]) -> list[str]:
    """someone's -> someone. Kept apart from the tokens, because a multiword headword can hold
    a possessive itself (at arm's length)."""
    return [token.removesuffix("'s") for token in tokens]


def _grams(tokens: list[str], longest: int = 3) -> Iterator[str]:
    for size in range(1, longest + 1):
        for i in range(len(tokens) - size + 1):
            yield " ".join(tokens[i : i + size])


def _ngrams(tokens: list[str]) -> Iterator[tuple[str, ...]]:
    for i in range(len(tokens) - NGRAM + 1):
        yield tuple(tokens[i : i + NGRAM])


def _list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
