"""The seed builder (design §6.1): the owner's seeds plus the open lists, as ranked candidates.

Each owner row gets one candidate per learner-relevant part of speech, and an unclassified
item gets the kind Wiktionary records for it. The CEFR lists and the NGSL family are then
credited to the candidates they match, and the headwords they have that the owner's lists
lack join as lower tiers. Spacing variants that Wiktionary records as alternative forms
fold together, and homographs that differ only in case or accents are numbered. No LLM is
involved: the curation run reviews whatever the flags point at.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

import psycopg
from lemminflect import getLemma

from smart_dictionary.core import headwords
from smart_dictionary.core.text import norm
from smart_dictionary.seeds.owner import OwnerRow, Variant

CEFR_DATASETS = ("cefrj", "octanove-c1c2")
# Tiers (design §6.1 step 6): 0 is the owner's lists, 1-3 the CEFR levels, 4 the other
# open lists.
LEVEL_TIERS = {"A1": 1, "A2": 1, "B1": 2, "B2": 2, "C1": 3, "C2": 3}
OPEN_LIST_TIER = 4
# A Wiktionary part of speech gets a candidate of its own with at least this many senses and
# this share of the main part of speech's (house: noun and verb, but not adjective).
MIN_SENSES = 3
MIN_SHARE = 0.4
UNIMORPH_POS = {"N": "noun", "V": "verb", "ADJ": "adjective"}
LEMMINFLECT_POS = {"noun": "NOUN", "verb": "VERB", "adjective": "ADJ"}
UK_TAGS = frozenset({"UK", "Commonwealth", "Ireland", "Australia", "New-Zealand"})
# CEFR-J lists the forms of be, have and do as headwords of their own.
AUXILIARY_FORMS = {"am": "be", "is": "be", "are": "be", "was": "be", "were": "be", "been": "be",
                   "being": "be", "has": "have", "had": "have", "having": "have", "does": "do",
                   "did": "do", "done": "do", "doing": "do"}  # fmt: skip
# Wiktionary's region tags as entry regions.
REGIONS = {"US": "US", "UK": "UK", "Commonwealth": "UK", "Australia": "AU", "Canada": "CA",
           "Ireland": "IE", "New-Zealand": "NZ"}  # fmt: skip
_SPACING = re.compile(r"[ \-]")


@dataclass(frozen=True)
class ListRow:
    dataset: str
    lemma: str
    pos: str | None = None
    level: str | None = None


@dataclass(frozen=True)
class WikiRow:
    word: str
    pos: str
    kind: str = "word"
    senses: int = 0
    variant_of: tuple[str, ...] = ()
    tags: frozenset[str] = frozenset()


@dataclass
class Reference:
    """What the builder looks up, from the ref_* tables (or built by hand in tests)."""

    cefr: dict[str, list[ListRow]] = field(default_factory=dict)  # by lemma_norm
    lists: dict[str, list[ListRow]] = field(default_factory=dict)  # the other lists
    wiki: dict[str, list[WikiRow]] = field(default_factory=dict)  # by word_norm
    zipf: dict[str, float] = field(default_factory=dict)
    forms: dict[str, set[str]] = field(default_factory=dict)  # UniMorph parts of speech


@dataclass
class Candidate:
    lemma: str
    pos: str | None
    kind: str | None
    tier: int
    sources: list[str] = field(default_factory=list)
    levels: set[str] = field(default_factory=set)
    variants: list[Variant] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    originals: list[str] = field(default_factory=list)
    zipf: float | None = None
    homograph: int = 1
    lemma_norm: str = field(init=False, default="")

    def __post_init__(self) -> None:
        self.lemma_norm = norm(self.lemma)


def load_reference(conn: psycopg.Connection) -> Reference:
    ref = Reference()
    for dataset, lemma, lemma_norm, pos, level in conn.execute(
        "select dataset_id, lemma, lemma_norm, pos, level from ref_lists"
    ):
        target = ref.cefr if dataset in CEFR_DATASETS else ref.lists
        target.setdefault(lemma_norm, []).append(ListRow(dataset, lemma, pos, level))
    for word, word_norm, pos, kind, senses, variant_of, tags in conn.execute(
        "select word, word_norm, pos, kind, cardinality(glosses), variant_of, tags"
        " from ref_wiktionary where pos is not null"
    ):
        row = WikiRow(word, pos, kind, senses, tuple(variant_of), frozenset(tags))
        ref.wiki.setdefault(word_norm, []).append(row)
    ref.zipf = {
        word: float(zipf)
        for word, zipf in conn.execute(
            "select word, max(zipf) from ref_freq where language = 'en' group by word"
        )
    }
    for lemma_norm, head in conn.execute(
        "select distinct lemma_norm, split_part(features, ';', 1) from ref_forms"
        " where language = 'en'"
    ):
        if head in UNIMORPH_POS:
            ref.forms.setdefault(lemma_norm, set()).add(UNIMORPH_POS[head])
    return ref


def build(rows: Iterable[OwnerRow], ref: Reference) -> list[Candidate]:
    """Candidates for every owner row and open-list headword, deduplicated and numbered."""
    candidates = [candidate for row in rows for candidate in _owner_candidates(row, ref)]
    candidates += _open_list_candidates(candidates, ref)
    candidates = _fold_spacing_variants(candidates, ref)
    for candidate in candidates:
        if " " not in candidate.lemma:
            candidate.zipf = ref.zipf.get(candidate.lemma_norm)
    _number_homographs(candidates)
    return candidates


def parts_of_speech(lemma: str, ref: Reference) -> list[str]:
    """Learner-relevant parts of speech: the CEFR lists' first, then Wiktionary's main ones,
    then UniMorph's for words the Wiktionary subset lacks."""
    key = norm(lemma)
    cefr = [row.pos for row in ref.cefr.get(key, []) if row.pos and _same_case(row.lemma, lemma)]
    found = list(dict.fromkeys(cefr))
    weights = _wiki_weights(lemma, ref)
    if weights:
        top = max(weights.values())
        for pos, weight in sorted(weights.items(), key=lambda item: -item[1]):
            strong = weight >= MIN_SENSES and weight >= MIN_SHARE * top
            if pos not in found and (weight == top or strong):
                found.append(pos)
    return found or sorted(ref.forms.get(key, ()))


def function_of(lemma: str, kind: str, ref: Reference) -> str:
    """A multiword item's part of speech: a phrasal verb is a verb; an idiom or a phrase takes
    the one word class Wiktionary gives it (as long as: conjunction), else "phrase", for the
    curation run to decide (policy of 2026-10-24)."""
    if kind == "phrasal_verb":
        return "verb"
    found = {row.pos for row in ref.wiki.get(norm(lemma), [])} - {"phrase"}
    return next(iter(found)) if len(found) == 1 else "phrase"


def guess_kind(lemma: str, ref: Reference) -> str | None:
    """An unclassified item's kind, from Wiktionary; None when nothing says."""
    kinds = {row.kind for row in ref.wiki.get(norm(lemma), []) if _same_case(row.word, lemma)}
    for kind in ("idiom", "phrasal_verb", "phrase"):
        if kind in kinds:
            return kind
    return "word" if kinds or " " not in lemma else None


def summary(candidates: list[Candidate], ref: Reference) -> str:
    tiers = Counter(candidate.tier for candidate in candidates)
    kinds = Counter(candidate.kind or "unknown" for candidate in candidates)
    no_pos = sum(1 for candidate in candidates if candidate.pos is None)
    flagged = sum(1 for candidate in candidates if candidate.flags)
    homographs = sum(1 for candidate in candidates if candidate.homograph > 1)
    ungrounded = sum(1 for candidate in candidates if candidate.lemma_norm not in ref.wiki)
    return "\n".join(
        [
            f"{len(candidates)} candidates by tier: {dict(sorted(tiers.items()))}",
            f"by kind: {dict(sorted(kinds.items()))}",
            f"no part of speech: {no_pos}; homographs numbered: {homographs}; "
            f"flagged for review: {flagged}",
            f"without a Wiktionary entry: {ungrounded} (`data import kaikki-en` adds the ones"
            " Wiktionary has, then `seeds import` again assigns their parts of speech)",
        ]
    )


def _owner_candidates(row: OwnerRow, ref: Reference) -> list[Candidate]:
    kind = row.kind or guess_kind(row.lemma, ref)
    if row.pos:
        parts = [row.pos]
    elif kind and kind != "word":
        parts = [function_of(row.lemma, kind, ref)]
    else:
        parts = parts_of_speech(row.lemma, ref)
    # Spacing pairs are settled per part of speech below, so the normalizer's notes go.
    flags = [flag for flag in row.flags if not flag.startswith("spacing variant of")]
    if kind is None:
        flags.append("kind unknown")
    if not parts:
        flags.append("no part of speech found")
    return [_mended(Candidate(
        lemma=row.lemma,
        pos=pos,
        kind=kind,
        tier=0,
        sources=[f"owner:{source}" for source in row.sources],
        levels=set(row.levels),
        variants=_variants_for(row.variants, pos, ref, several=len(parts) > 1),
        patterns=list(row.patterns),
        flags=list(flags),
        originals=list(row.originals),
    )) for pos in parts or [None]]  # fmt: skip


def _mended(candidate: Candidate) -> Candidate:
    """The headword rules (core/headwords.py): *drop someone* becomes *drop*, with the
    pattern "drop sb", and a plain verb now that one word is left."""
    lemma, pattern = headwords.mended(candidate.lemma, candidate.pos)
    if lemma == candidate.lemma:
        return candidate
    candidate.flags.append(f"headword mended from {candidate.lemma!r}")
    candidate.lemma, candidate.lemma_norm = lemma, norm(lemma)
    if candidate.kind:
        candidate.kind = headwords.mended_kind(lemma, candidate.kind)
    if pattern and pattern not in candidate.patterns:
        candidate.patterns.append(pattern)
    return candidate


def _variants_for(
    variants: list[Variant], pos: str | None, ref: Reference, *, several: bool
) -> list[Variant]:
    """The variants that belong with this part of speech's candidate.

    With one candidate, that's all of them. With several, a regional spelling goes only to the
    parts of speech Wiktionary gives it: backwards is an adverb, so backward the adjective
    doesn't get it."""
    if not several or pos is None:
        return list(variants)
    kept = []
    for variant in variants:
        known = {row.pos for row in ref.wiki.get(norm(variant.form), [])}
        if not variant.regions or not known or pos in known:
            kept.append(variant)
    return kept


def _open_list_candidates(candidates: list[Candidate], ref: Reference) -> list[Candidate]:
    """Credit the open lists to the candidates they match; return the headwords they add."""
    index: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        index[candidate.lemma_norm].append(candidate)
        for variant in candidate.variants:
            index[norm(variant.form)].append(candidate)
    # The UK -> US pairs the owner's seed file settled (centre -> center) respell the open
    # lists first, so "sports centre" lands under "sports center".
    respell = {
        norm(variant.form): candidate.lemma
        for candidate in candidates
        for variant in candidate.variants
        if "UK" in variant.regions
    }
    added: list[Candidate] = []

    def matching(lemma: str, pos: str | None) -> list[Candidate]:
        found = index.get(norm(lemma), [])
        found = [c for c in found if _same_case(c.lemma, lemma)] or found
        return [c for c in found if pos is None or c.pos == pos]

    def add(lemma: str, pos: str | None, kind: str, tier: int) -> Candidate:
        candidate = Candidate(lemma=lemma, pos=pos, kind=kind, tier=tier)
        if pos is None:
            candidate.flags.append("no part of speech found")
        added.append(candidate)
        index[candidate.lemma_norm].append(candidate)
        return candidate

    for rows in ref.cefr.values():
        for row in rows:
            lemma = _citation(row.lemma, row.pos, ref, respell)
            tier = LEVEL_TIERS.get(row.level or "", OPEN_LIST_TIER)
            kind = guess_kind(lemma, ref) or "word"
            if kind == "word":
                pos = row.pos
            elif kind == "phrasal_verb":
                pos = "verb"
            else:
                pos = row.pos or function_of(lemma, kind, ref)
            for target in matching(lemma, pos) or [add(lemma, pos, kind, tier)]:
                if row.level:
                    target.levels.add(row.level)
                if target.tier:
                    target.tier = min(target.tier, tier)
                _append(target.sources, row.dataset)
    for rows in ref.lists.values():
        for row in rows:
            lemma = _citation(row.lemma, None, ref, respell)
            targets = matching(lemma, None)
            if not targets:
                kind = guess_kind(lemma, ref) or "word"
                parts = [function_of(lemma, kind, ref)] if kind != "word" else parts_of_speech(
                    lemma, ref)  # fmt: skip
                targets = [add(lemma, pos, kind, OPEN_LIST_TIER) for pos in parts or [None]]
            for target in targets:
                _append(target.sources, row.dataset)
    return added


def _citation(lemma: str, pos: str | None, ref: Reference, respell: dict[str, str]) -> str:
    """The headword an open-list item belongs under: its US spelling, then its lemma."""
    return _lemma_of(_us_spelling(lemma, ref, respell), pos, ref)


def _us_spelling(text: str, ref: Reference, respell: dict[str, str]) -> str:
    """Respell UK spellings the US way: the owner's pairs first, then the words Wiktionary
    records only as UK spellings (centre -> center)."""
    if norm(text) in respell:
        return respell[norm(text)]

    def us(token: str) -> str:
        owner = respell.get(norm(token))
        if owner is not None and " " not in owner:
            return owner
        rows = ref.wiki.get(norm(token), [])
        uk_only = rows and all(
            not row.senses and row.variant_of and row.tags & UK_TAGS and "US" not in row.tags
            for row in rows
        )
        targets = {target for row in rows for target in row.variant_of}
        if not uk_only or len(targets) != 1:
            return token
        (target,) = targets
        return next((row.word for row in ref.wiki.get(target, []) if row.senses), target)

    return " ".join(us(token) for token in text.split(" "))


def _lemma_of(word: str, pos: str | None, ref: Reference) -> str:
    """An inflected list item's lemma (were -> be), unless Wiktionary gives the form an entry
    of its own for that part of speech (left, found)."""
    if pos == "verb" and word.lower() in AUXILIARY_FORMS:
        return AUXILIARY_FORMS[word.lower()]
    upos = LEMMINFLECT_POS.get(pos or "")
    if upos is None or " " in word:
        return word
    if any(row.pos == pos and row.senses for row in ref.wiki.get(norm(word), [])):
        return word
    lemmas = getLemma(word.lower(), upos=upos, lemmatize_oov=False)
    return lemmas[0] if lemmas and lemmas[0] != word.lower() else word


def _fold_spacing_variants(candidates: list[Candidate], ref: Reference) -> list[Candidate]:
    """Fold spacing and hyphen variants with the same part of speech when Wiktionary records
    one as the other's alternative form (best seller -> bestseller); flag the pairs it
    doesn't settle."""
    groups: dict[tuple[str, str | None, str | None], list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        key = (_SPACING.sub("", candidate.lemma_norm), candidate.pos, candidate.kind)
        groups[key].append(candidate)
    folded: set[int] = set()
    for group in groups.values():
        if len({candidate.lemma_norm for candidate in group}) < 2:
            continue  # one spelling: homographs that differ only in case
        for candidate in group:
            target = next(
                (
                    other
                    for other in group
                    if other is not candidate
                    and id(other) not in folded
                    and _alternative_of(candidate, other, ref)
                ),
                None,
            )
            if target is not None:
                _absorb(target, candidate, ref)
                folded.add(id(candidate))
        left = [candidate for candidate in group if id(candidate) not in folded]
        for candidate in left:
            others = sorted({o.lemma for o in left if o.lemma_norm != candidate.lemma_norm})
            if others:
                candidate.flags.append(
                    f"spacing variant of {' / '.join(others)}, same part of speech: merge?"
                )
    return [candidate for candidate in candidates if id(candidate) not in folded]


def _alternative_of(candidate: Candidate, other: Candidate, ref: Reference) -> bool:
    """Whether Wiktionary records the candidate only as an alternative form of the other."""
    rows = [
        row
        for row in ref.wiki.get(candidate.lemma_norm, [])
        if _same_case(row.word, candidate.lemma)
        and (candidate.pos is None or row.pos == candidate.pos)
    ]
    return bool(rows) and all(not row.senses and other.lemma_norm in row.variant_of for row in rows)


def _absorb(target: Candidate, other: Candidate, ref: Reference) -> None:
    tags = {tag for row in ref.wiki.get(other.lemma_norm, []) for tag in row.tags}
    regions = tuple(sorted({REGIONS[tag] for tag in tags if tag in REGIONS}))
    for variant in [Variant(other.lemma, regions), *other.variants]:
        _append(target.variants, variant)
    for name in ("sources", "patterns", "flags", "originals"):
        for value in getattr(other, name):
            _append(getattr(target, name), value)
    target.levels |= other.levels
    target.tier = min(target.tier, other.tier)


def _number_homographs(candidates: list[Candidate]) -> None:
    """Number the candidates that share a normalized lemma, part of speech and kind
    (pole and Pole, resume and résumé), lowercase first, as entry keys do (en:pole:n~2)."""
    groups: dict[tuple[str, str | None, str | None], list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        groups[(candidate.lemma_norm, candidate.pos, candidate.kind)].append(candidate)
    for group in groups.values():
        group.sort(
            key=lambda candidate: (candidate.lemma != candidate.lemma.lower(), candidate.lemma)
        )
        for number, candidate in enumerate(group, start=1):
            candidate.homograph = number


def _wiki_weights(lemma: str, ref: Reference) -> dict[str, int]:
    """Senses per part of speech in the Wiktionary entries spelled like the lemma.

    A spelling variant's entry (catalog: US spelling of catalogue) has no senses of its own,
    so it counts its main spelling's senses for the same part of speech."""
    weights: dict[str, int] = {}
    for row in ref.wiki.get(norm(lemma), []):
        if not _same_case(row.word, lemma):
            continue
        senses = row.senses
        if not senses and row.variant_of:
            main = [
                r.senses for t in row.variant_of for r in ref.wiki.get(t, []) if r.pos == row.pos
            ]
            senses = max(main, default=1)
        weights[row.pos] = weights.get(row.pos, 0) + senses
    return weights


def _same_case(a: str, b: str) -> bool:
    """Whether two spellings of one normalized form are the same word as far as case goes:
    March (the month) isn't march (the verb)."""
    return a[:1].isupper() == b[:1].isupper()


def _append[T](values: list[T], value: T) -> None:
    if value not in values:
        values.append(value)
