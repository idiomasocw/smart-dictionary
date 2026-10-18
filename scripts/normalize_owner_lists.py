"""Normalize the owner's word lists into one seed file (system design §6.1, step 1).

Reads every `*.txt` list in `dictionary-entries/` (one item per line) and writes
`data/seeds/owner/owner-seeds.csv`, one row per candidate entry. The raw lists are
never edited: rerun this script after changing them.

What it does, in order:

1. **Characters.** Unicode NFC; curly apostrophes and quotes become `'`, dashes become
   `-` (the same tables `core.text.norm` uses); whitespace is collapsed.
2. **Citation form.** The infinitive `to` of phrasal-verbs.txt goes, and so does a
   leading `be` (kept as a variant) unless one word would be left ("be into"). Sentence
   idioms lose their capital ("Hang on to your hat!" -> "hang on to your hat!"), proper
   names keep theirs.
3. **Parentheses.** An optional part becomes a variant of one entry, never a second
   entry: "(sitting) on top of the world" is `on top of the world` with the variant
   `sitting on top of the world`. A complement such as "(with someone)" says how the
   phrase is used, so it goes to `patterns` instead. Irregular cases are in OVERRIDES.
4. **Placeholders.** Object placeholders are spelled out (someone, something,
   one's). A trailing one is dropped from the lemma and kept as a pattern and a
   variant ("have it in for someone" -> `have it in for`), as in `run into`.
5. **Kind.** From the list it came from, then by shape: a verb followed only by
   particles is a phrasal verb, anything else with words of its own is an idiom.
6. **Merge.** Rows with the same normalized lemma merge, collecting sources and
   variants. A capitalized form never merges with a lowercase one (March, march),
   except the pairs in CASE_MERGES that are one word (internet, OK).
7. **Inflected forms.** A word Wiktionary records only as an inflection of another word
   is flagged with its lemma. Many such plurals and participles are headwords in their own
   right (savings, terms, located), so a person decides; clear cases fold through
   OVERRIDES (clams -> clam).
8. **US spelling is the headword.** Where Wiktionary records a word as the UK (or
   Commonwealth) spelling of another, the US spelling becomes the lemma and the UK one a
   variant written `colour [UK]`. That covers multiword items too (call centre ->
   call center). Regular endings (-our, -re, -ise, -ence...) add the pairs Wiktionary
   gives full entries on both sides (defence, defense), but only when both spellings are
   listed. EXTRA_PAIRS and PHRASE_PAIRS add the regional pairs Wiktionary marks only as
   alternatives (plough, plow; on second thoughts, on second thought). UK spellings that
   only match some meanings of the US word (cheque, check) stay separate entries: see
   SEPARATE_ENTRIES.
9. **Variants that were also listed.** A row that another row lists as a variant is the
   same entry, so it folds in (drop a line -> drop someone a line).

Anything that needs a person's decision is listed in the `flags` column.

Steps 7 and 8 read the kaikki.org extract (`smart-dictionary data fetch kaikki-en`) once
and cache what they need in data/raw/inflections.tsv and data/raw/spelling-pairs.tsv;
without the extract or the caches, they're skipped.

Usage (from backend/, so `smart_dictionary` and LemmInflect are importable):
    uv run python ../scripts/normalize_owner_lists.py
"""

import csv
import gzip
import itertools
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

from lemminflect import getAllInflections  # noqa: E402

from smart_dictionary.core.text import APOSTROPHES, DASHES, norm  # noqa: E402

SOURCE_DIR = ROOT / "dictionary-entries"
OUTPUT = ROOT / "data" / "seeds" / "owner" / "owner-seeds.csv"
KAIKKI = ROOT / "data" / "raw" / "kaikki-en" / "kaikki.org-dictionary-English.jsonl.gz"
PAIRS_CACHE = ROOT / "data" / "raw" / "spelling-pairs.tsv"
INFLECTIONS_CACHE = ROOT / "data" / "raw" / "inflections.tsv"

# Lists the owner compiled by kind: their kind wins over a guess from another list.
KIND_BY_FILE = {"idioms.txt": "idiom", "phrasal-verbs.txt": "phrasal_verb"}
# Display-form precedence when the same entry comes from several lists.
PRECEDENCE = ["idioms.txt", "phrasal-verbs.txt", "advanced-everyday-vocab.txt"]

PARTICLES = {"about", "across", "after", "against", "ahead", "along", "apart", "around",
             "aside", "at", "away", "back", "behind", "by", "down", "for", "forward", "from", "in",
             "into", "like", "of", "off", "on", "onto", "out", "over", "past", "round", "through",
             "to", "together", "under", "up", "upon", "with", "without"}  # fmt: skip
PLACEHOLDERS = (
    r"someone|something|somewhere|someone's|one's|oneself|doing something|to do something|doing"
)
ARGUMENT = re.compile(rf"\b({PLACEHOLDERS})\b")
TRAILING = re.compile(r"\s+(someone or something|someone|something|somewhere)$")
# Proper names that start an idiom keep their capital.
PROPER_START = ("Box ", "Darby ", "Davy ", "Hobson's", "Jekyll ", "Job's", "Greek ", "Tom,")

# Loanwords that learner dictionaries spell with accents; norm() strips accents for
# matching, so the plain spelling still finds them.
ACCENTS = {"cliche": "cliché", "deja vu": "déjà vu", "fiance": "fiancé", "fiancee": "fiancée",
           "passe": "passé", "protege": "protégé", "risque": "risqué", "souffle": "soufflé",
           "creme brulee": "crème brûlée", "creme caramel": "crème caramel",
           "maitre d'": "maître d'", "pied-a-terre": "pied-à-terre",
           "piece de resistance": "pièce de résistance", "menage a trois": "ménage à trois",
           "fin de siecle": "fin de siècle", "cafe au lait": "café au lait",
           "blase": "blasé", "naivete": "naïveté", "entree": "entrée"}  # fmt: skip

# Lines the general rules get wrong: lemma, variants, patterns and (optionally) kind.
OVERRIDES: dict[str, tuple[str, list[str], list[str], str | None]] = {
    "fit someone in(to something)": ("fit someone in", ["fit someone into something"], [], None),
    "get away (from it all)": ("get away from it all", [], [], None),
    "get by (on a shoe-string)": ("get by on a shoestring", ["get by on a shoe-string"], [], None),
    "hark(en) back to something": (
        "hark back to",
        ["harken back to", "hark back to something", "harken back to something"],
        ["hark back to something"],
        "phrasal_verb",
    ),
    "have (high) hopes of something": (
        "have high hopes",
        ["have high hopes of something", "have hopes of something"],
        ["have high hopes of something"],
        None,
    ),
    "let someone off (the hook)": ("let someone off the hook", [], [], None),
    "make a point of (doing) something": (
        "make a point of",
        ["make a point of doing something", "make a point of something"],
        ["make a point of doing something"],
        None,
    ),
    "mark my word(s)": ("mark my words", ["mark my word"], [], None),
    "pig(gy)-in-the-middle": ("piggy in the middle", ["pig in the middle"], [], None),
    "pile in(to something)": ("pile in", ["pile into"], ["pile into something"], "phrasal_verb"),
    "pull the rug out from under someone('s feet)": (
        "pull the rug out from under someone's feet",
        ["pull the rug out from under someone"],
        [],
        None,
    ),
    "in thing (to do)": ("the in thing", ["in thing", "the in thing to do"], [], None),
    "pitch in (and help)": ("pitch in", ["pitch in and help"], [], "phrasal_verb"),
    "down to someone": ("down to someone", [], [], "idiom"),
    "keep away (from)": (
        "keep away",
        ["keep away from"],
        ["keep away from someone or something"],
        "phrasal_verb",
    ),
    # Inflected forms, sample sentences and incomplete forms, mapped to their citation form.
    "ad-libbed": ("ad-lib", [], [], "word"),
    "down to him": ("down to someone", [], [], "idiom"),
    "down to you": ("down to someone", [], [], "idiom"),
    "got another think coming": (
        "have another think coming",
        ["have got another think coming", "got another think coming"],
        [],
        "idiom",
    ),
    "put something behind you": (
        "put something behind one",
        ["put something behind you"],
        [],
        "idiom",
    ),
    "string a sentence together": (
        "string together",
        ["string something together"],
        ["string something together"],
        "phrasal_verb",
    ),
    "get off one's chest": (
        "get something off one's chest",
        ["get off one's chest"],
        [],
        "idiom",
    ),
    "put one's foot in": ("put one's foot in it", [], [], "idiom"),
    "brakes": ("brake", [], [], "word"),
    "clams": ("clam", [], [], "word"),
    "elves": ("elf", [], [], "word"),
    "headlights": ("headlight", [], [], "word"),
}

# Regional markers in Wiktionary's sense tags and in glosses such as "Commonwealth
# standard spelling of color" or "US standard spelling of catalogue".
US_MARKS = re.compile(r"\b(US|American)\b")
UK_MARKS = re.compile(
    r"\b(UK|British|Commonwealth|Ireland|Irish|Australia|Canada|New Zealand|South Africa)\b"
)
SPELLING_OF = re.compile(r"\bspelling of ([A-Za-z][A-Za-z' -]*?)\s*[.;,(]?\s*$")
US_TAGS = {"US"}
UK_TAGS = {"UK", "Commonwealth", "Ireland", "Australia", "New-Zealand", "South-Africa"}
# A sense with any of these is a dialect, register or shortening, not a spelling standard
# (afraid -> 'fraid, apartment -> APT).
NOT_A_SPELLING = {"dialectal", "slang", "colloquial", "informal", "humorous", "abbreviation",
                  "initialism", "acronym", "clipping", "contraction", "misspelling",
                  "pronunciation-spelling", "eye-dialect", "nonstandard", "obsolete", "archaic",
                  "rare", "form-of", "Southern-US", "Scotland", "Northern-England"}  # fmt: skip
# UK spellings that only match some meanings of the US word, so a variant on the whole
# entry would be wrong: they stay separate entries, cross-referenced by the factory.
SEPARATE_ENTRIES = {"cheque", "tyre", "kerb", "storey", "draught", "metre", "programme",
                    "disc", "practise", "licence", "racquet"}  # fmt: skip
# Pairs Wiktionary gets backwards or that are different words for this purpose.
NOT_SPELLING_PAIRS = {"donut", "drier", "popularize", "swathe", "travelogue"}
# Regular US/UK endings, applied only when both spellings are already candidates. They
# catch pairs Wiktionary gives full entries on both sides (defence, defense).
SPELLING_PATTERNS = [(r"our\b", "or"), (r"([bgt])re(s?)\b", r"\1er\2"),
                     (r"is(e|ed|es|ing|ation|ations|er|ers)\b", r"iz\1"),
                     (r"ys(e|ed|es|ing)\b", r"yz\1"), (r"ae", "e"), (r"oe", "e"),
                     (r"ence\b", "ense"), (r"ll(ed|ing|er|ers)\b", r"l\1")]  # fmt: skip
NOT_PATTERN_PAIRS = {"aloe", "canoe", "filling", "four", "hoe", "our", "poet", "prise",
                     "roe", "shoe", "woe", "licence", "metre", "timbre"}  # fmt: skip
# Regional pairs Wiktionary marks only as alternatives, not as spelling standards. Word
# pairs also apply inside multiword items (plough on -> plow on); phrase pairs apply only
# to the whole lemma.
EXTRA_PAIRS = {"plough": "plow", "tsar": "czar", "towards": "toward",
               "afterwards": "afterward", "backwards": "backward", "forwards": "forward",
               "upwards": "upward", "downwards": "downward",
               "fibreglass": "fiberglass", "maths": "math"}  # fmt: skip
# Wiktionary sense tags that make a form-of sense an inflection, not a derived word
# (agent nouns, diminutives).
INFLECTION_TAGS = {"plural", "past", "participle", "present", "comparative", "superlative",
                   "third-person", "gerund", "singular"}  # fmt: skip
PHRASE_PAIRS = {"any more": "anymore", "blank cheque": "blank check",
                "fully fledged": "full-fledged", "half-time": "halftime",
                "per cent": "percent", "rowing boat": "rowboat", "skimmed milk": "skim milk",
                "self-raising flour": "self-rising flour",
                "on second thoughts": "on second thought",
                "grist to the mill": "grist for the mill",
                "go off at a tangent": "go off on a tangent",
                "laugh up one's sleeve": "laugh in one's sleeve"}  # fmt: skip
# Capitalized and lowercase rows that are one word, and the casing the entry keeps. Other
# pairs are different words (March and march, Pole and pole).
CASE_MERGES = {"internet": "internet", "ok": "OK", "golden age": "golden age",
               "supreme court": "Supreme Court", "secretary of state": "Secretary of State",
               "mafia": "Mafia"}  # fmt: skip


@dataclass
class Row:
    lemma: str
    kind: str
    variants: set[str] = field(default_factory=set)
    patterns: set[str] = field(default_factory=set)
    sources: list[str] = field(default_factory=list)
    originals: list[str] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    regional: dict[str, str] = field(default_factory=dict)  # variant -> region, e.g. UK


def clean(text: str) -> str:
    value = unicodedata.normalize("NFC", text).translate(APOSTROPHES).translate(DASHES)
    return re.sub(r"\s+", " ", value).strip()


def is_verb(word: str) -> bool:
    """A verb LemmInflect knows (dictionary only, no out-of-vocabulary guesses)."""
    return bool(getAllInflections(word.lower(), upos="VERB"))


def expand_alternatives(text: str) -> list[str]:
    """'someone or something' placeholders yield one form per alternative."""
    if "someone or something" not in text:
        return [text]
    return [text.replace("someone or something", alt) for alt in ("someone", "something")]


def split_parentheses(line: str) -> tuple[str, str, list[str], list[str]]:
    """Core form, form with inner placeholders, optional-part variants and patterns."""
    parts = re.split(r"(\([^)]*\))", line)
    optional = [i for i, part in enumerate(parts) if part.startswith("(")]
    patterns: list[str] = []
    variants: list[str] = []
    arguments = {i for i in optional if ARGUMENT.search(parts[i])}
    at_end = {i for i in arguments if not "".join(parts[i + 1 :]).strip()}

    def render(included: set[int]) -> str:
        out = []
        for i, part in enumerate(parts):
            if part.startswith("("):
                if i in included:
                    out.append(part[1:-1])
            else:
                out.append(part)
        return re.sub(r"\s+", " ", "".join(out)).strip()

    for i in at_end:  # a complement describes usage: "(with someone)"
        patterns.append(render(set(optional) - (at_end - {i})))
    choosable = [i for i in optional if i not in at_end]
    core = render(set())
    with_objects = render(arguments - at_end)  # "drop (someone) a line"
    for size in range(1, len(choosable) + 1):
        for combo in itertools.combinations(choosable, size):
            variants.append(render(set(combo)))
    return core, with_objects, variants, patterns


def classify(lemma: str, file_kind: str | None) -> str:
    words = lemma.split()
    phrasal_shape = (
        len(words) >= 2
        and is_verb(words[0])
        and all(w in PARTICLES or w in ("someone", "something", "oneself") for w in words[1:])
    )
    if len(words) == 1:
        return "word"  # "to bug" in a phrasal-verb list is just the verb
    if file_kind:
        if file_kind == "phrasal_verb" and not phrasal_shape:
            return "idiom"
        if file_kind == "idiom" and phrasal_shape:
            return "phrasal_verb"
        return file_kind
    if phrasal_shape:
        return "phrasal_verb"
    return ""  # compound word, idiom or fixed phrase: the curation run decides


def process(line: str, name: str) -> Row | None:
    original = line
    text = clean(line)
    if not text:
        return None
    flags: set[str] = set()
    if name == "phrasal-verbs.txt":
        text = re.sub(r"^to ", "", text)
    owner_list = name in ("idioms.txt", "phrasal-verbs.txt", "advanced-everyday-vocab.txt")
    if owner_list and text[:1].isupper() and not text.startswith(PROPER_START):
        text = text[0].lower() + text[1:]

    kind_override = None
    if text in OVERRIDES:
        lemma, variants, patterns, kind_override = OVERRIDES[text]
        variants, patterns = list(variants), list(patterns)
    else:
        lemma, with_objects, variants, patterns = split_parentheses(text)
        # An idiom keeps an inner object ("drop someone a line"); a phrasal verb drops
        # it ("fill someone in" -> fill in), as its pattern carries the slot.
        inner_kind = classify(with_objects, KIND_BY_FILE.get(name))
        if with_objects != lemma and inner_kind != "phrasal_verb":
            variants.append(lemma)
            lemma = with_objects

    if lemma.startswith("be ") and len(lemma.split()) > 2:  # "be into" stays
        variants.append(lemma)
        lemma = lemma[3:]
        flags.add("dropped leading 'be'")
    file_kind = KIND_BY_FILE.get(name)
    kind = kind_override or classify(lemma, file_kind)
    if file_kind == "phrasal_verb" and kind == "idiom":
        flags.add("listed as phrasal verb, shaped like an idiom")

    match = TRAILING.search(lemma)
    overridden = text in OVERRIDES
    if match and not overridden and kind in ("phrasal_verb", "idiom") and len(lemma.split()) > 2:
        variants.append(lemma)
        patterns.append(lemma)
        lemma = lemma[: match.start()]
    if ", and " in lemma:
        variants.append(lemma.replace(",", ""))
    if lemma in ACCENTS:
        lemma = ACCENTS[lemma]

    row = Row(lemma=lemma, kind=kind, sources=[name], originals=[original.strip()], flags=flags)
    for variant in variants:
        row.variants.update(expand_alternatives(variant))
    row.patterns.update(patterns)
    return row


def merge_key(row: Row) -> tuple[str, bool]:
    return norm(row.lemma), row.lemma[:1].isupper()


def rank(source: str) -> int:
    return PRECEDENCE.index(source) if source in PRECEDENCE else len(PRECEDENCE)


def absorb(kept: Row, row: Row) -> None:
    """Fold a second row for the same entry into the first."""
    kept.variants |= row.variants
    kept.patterns |= row.patterns
    kept.flags |= row.flags
    kept.regional.update(row.regional)
    for source in row.sources:
        if source not in kept.sources:
            kept.sources.append(source)
    kept.originals.extend(row.originals)
    if row.kind and kept.kind and row.kind != kept.kind:
        if "word" in (row.kind, kept.kind):
            kept.kind = "word"
        elif any(source in KIND_BY_FILE for source in row.sources):
            kept.kind = row.kind
        elif kept.sources[0] not in KIND_BY_FILE:
            kept.flags.add(f"kind conflict: {kept.kind} vs {row.kind}")
    kept.kind = kept.kind or row.kind


def spelling_pairs(words: set[str]) -> dict[str, str]:
    """UK spelling -> US spelling, for the words in `words`, as Wiktionary records them."""
    if PAIRS_CACHE.exists():
        lines = PAIRS_CACHE.read_text(encoding="utf-8").splitlines()
        pairs = dict(line.split("\t") for line in lines)
        return {uk: us for uk, us in pairs.items() if uk in words}
    if not KAIKKI.exists():
        print("no kaikki extract and no cached pairs: US/UK folding skipped")
        return {}
    pairs = {}
    with gzip.open(KAIKKI, "rt", encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            word = entry.get("word") or ""
            if entry.get("lang_code") != "en" or not word.isalpha():
                continue
            # Only an entry that is nothing but a pointer to one other spelling counts:
            # "colour: Commonwealth standard spelling of color".
            found = [_spelling_pointer(sense, word) for sense in entry.get("senses") or []]
            if not found or None in found or len(set(found)) != 1:
                continue
            side, target = found[0]  # type: ignore[misc]
            if SequenceMatcher(None, word.lower(), target.lower()).ratio() < 0.75:
                continue
            uk, us = (word, target) if side == "UK" else (target, word)
            pairs.setdefault(uk, us)
    # A word can't be on both sides of a pair.
    pairs = {uk: us for uk, us in pairs.items() if pairs.get(us) != uk}
    PAIRS_CACHE.write_text(
        "".join(f"{uk}\t{us}\n" for uk, us in sorted(pairs.items())), encoding="utf-8"
    )
    return {uk: us for uk, us in pairs.items() if uk in words}


def _spelling_pointer(sense: dict, word: str) -> tuple[str, str] | None:
    """('UK' or 'US', the other spelling) when a sense is a regional spelling pointer."""
    tags = set(sense.get("tags") or [])
    if tags & NOT_A_SPELLING:
        return None
    gloss = (sense.get("glosses") or [""])[0]
    if sense.get("alt_of"):
        target = (sense["alt_of"][0].get("word") or "").strip()
    elif match := SPELLING_OF.search(gloss):
        target = match.group(1).strip()
    else:
        return None
    if not target.isalpha() or target.lower() == word.lower():
        return None
    head = gloss.split("spelling of")[0] if "spelling of" in gloss else ""
    uk = bool(tags & UK_TAGS or UK_MARKS.search(head))
    # A US pointer must name a standard, not a minority form ("US standard spelling of
    # catalogue", but not dialog or kidnaping, which Wiktionary marks plain US).
    us = bool(tags & US_TAGS or US_MARKS.search(head)) and (
        "standard" in tags or "standard" in head
    )
    if uk == us:  # unmarked, or marked for both
        return None
    return ("UK" if uk else "US"), target


def fold_spellings(rows: dict[tuple[str, bool], Row]) -> int:
    """Make the US spelling the lemma and the UK spelling a variant tagged UK."""
    tokens = {t for r in rows.values() for t in re.findall(r"[A-Za-z]+", r.lemma)}
    pairs = {
        uk: us
        for uk, us in spelling_pairs(tokens).items()
        if uk not in SEPARATE_ENTRIES and uk not in NOT_SPELLING_PAIRS
    }
    pairs |= {uk: us for uk, us in EXTRA_PAIRS.items() if uk in tokens}

    def to_us(text: str) -> str:
        if not pairs:
            return text
        return re.sub(r"[A-Za-z]+", lambda m: pairs.get(m.group(0), m.group(0)), text)

    lemmas = {r.lemma for r in rows.values()}
    renames: dict[str, str] = {}
    for lemma in lemmas:
        us = PHRASE_PAIRS.get(lemma) or to_us(lemma)
        if us == lemma and lemma not in NOT_PATTERN_PAIRS:
            for pattern, replacement in SPELLING_PATTERNS:
                candidate = re.sub(pattern, replacement, lemma)
                if candidate != lemma and candidate in lemmas:
                    us = candidate
                    break
        if us != lemma:
            renames[lemma] = us

    folded = 0
    for key, row in list(rows.items()):
        if rows.get(key) is not row or row.lemma not in renames:
            continue
        uk_lemma = row.lemma
        del rows[key]
        row.lemma = renames[uk_lemma]
        row.regional[uk_lemma] = "UK"
        row.variants = {renames.get(v, to_us(v)) for v in row.variants} - {uk_lemma}
        target = merge_key(row)
        if target in rows:
            absorb(rows[target], row)
        else:
            rows[target] = row
        folded += 1
    return folded


def merge_case_pairs(rows: dict[tuple[str, bool], Row]) -> int:
    """Merge the capitalized and lowercase rows that are one word (CASE_MERGES)."""
    merged = 0
    for key_norm, preferred in CASE_MERGES.items():
        upper, lower = rows.get((key_norm, True)), rows.get((key_norm, False))
        if upper is None or lower is None:
            continue
        keep, other = (upper, lower) if preferred[:1].isupper() else (lower, upper)
        del rows[merge_key(other)]
        absorb(keep, other)
        keep.lemma = preferred
        merged += 1
    return merged


def inflection_lemmas(words: set[str]) -> dict[str, set[str]]:
    """Word -> its lemmas, for the words Wiktionary records only as inflected forms."""
    if INFLECTIONS_CACHE.exists():
        lines = INFLECTIONS_CACHE.read_text(encoding="utf-8").splitlines()
        cached = (line.split("\t") for line in lines)
        return {word: set(lemmas.split("|")) for word, lemmas in cached if word in words}
    if not KAIKKI.exists():
        print("no kaikki extract and no cached inflections: inflection folding skipped")
        return {}
    lemmas: dict[str, set[str]] = defaultdict(set)
    has_senses: set[str] = set()
    with gzip.open(KAIKKI, "rt", encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            word = entry.get("word") or ""
            if entry.get("lang_code") != "en" or not (word.isalpha() and word.islower()):
                continue
            for sense in entry.get("senses") or []:
                tags = set(sense.get("tags") or [])
                if not sense.get("glosses"):
                    continue
                pointers = sense.get("form_of") or []
                target = (pointers[0].get("word") or "").strip() if pointers else ""
                if "form-of" in tags and tags & INFLECTION_TAGS and target and target != word:
                    lemmas[word].add(target)
                else:  # a meaning of its own, a spelling variant or a derived word
                    has_senses.add(word)
    pure = {word: found for word, found in lemmas.items() if word not in has_senses}
    INFLECTIONS_CACHE.write_text(
        "".join(f"{word}\t{'|'.join(sorted(found))}\n" for word, found in sorted(pure.items())),
        encoding="utf-8",
    )
    return {word: found for word, found in pure.items() if word in words}


def flag_inflections(rows: dict[tuple[str, bool], Row]) -> int:
    """Flag the words Wiktionary records only as inflections, naming their lemmas.

    They aren't folded automatically: Wiktionary files many lexicalized plurals and
    participle adjectives (savings, terms, located) only as forms of another word.
    """
    words = {row.lemma for row in rows.values() if row.lemma.isalpha() and row.lemma.islower()}
    lemmas = inflection_lemmas(words)
    flagged = 0
    for row in rows.values():
        if found := lemmas.get(row.lemma):
            lemma = " or ".join(sorted(found))
            row.flags.add(f"inflected form of {lemma} (merge unless it has a meaning of its own)")
            flagged += 1
    return flagged


def merge_listed_variants(rows: dict[tuple[str, bool], Row]) -> int:
    """Fold a row into the row that lists it as a variant: they are one entry."""
    merged = 0
    for key, row in list(rows.items()):
        if rows.get(key) is not row:
            continue
        for variant in sorted(row.variants):
            other = rows.get((norm(variant), variant[:1].isupper()))
            if other is None or other is row:
                continue
            if other.kind and row.kind and other.kind != row.kind:
                continue  # a noun and a phrasal verb are different entries
            del rows[merge_key(other)]
            absorb(row, other)
            row.variants.add(other.lemma)
            merged += 1
    return merged


def main() -> None:
    rows: dict[tuple[str, bool], Row] = {}
    for path in sorted(SOURCE_DIR.glob("*.txt")):
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            row = process(line, path.name)
            if row is None:
                continue
            key = merge_key(row)
            if key not in rows:
                rows[key] = row
                continue
            kept = rows[key]
            if path.name in kept.sources and row.lemma == kept.lemma:
                kept.flags.add(f"duplicate line in {path.name}")
            better = rank(path.name) < min(rank(s) for s in kept.sources)
            accented = row.lemma != unicodedata.normalize("NFKD", row.lemma)
            if better or (accented and norm(kept.lemma) == norm(row.lemma)):
                kept.variants.add(kept.lemma)
                kept.lemma = row.lemma
            else:
                kept.variants.add(row.lemma)
            absorb(kept, row)

    cased = merge_case_pairs(rows)
    folded = fold_spellings(rows)
    listed = merge_listed_variants(rows)
    inflected = flag_inflections(rows)

    lookup = {norm(r.lemma) for r in rows.values()}
    squashed: dict[str, list[Row]] = defaultdict(list)
    for r in rows.values():
        squashed[re.sub(r"[ \-]", "", norm(r.lemma))].append(r)
    for r in rows.values():
        # A phrasal verb and its one-word noun are different entries (break down, breakdown).
        others = [
            o.lemma
            for o in squashed[re.sub(r"[ \-]", "", norm(r.lemma))]
            if o is not r
            and "phrasal_verb" not in (r.kind, o.kind)
            and o.lemma.lower() != r.lemma.lower()  # March and march are different words
        ]
        if others:
            r.flags.add("spacing variant of " + " / ".join(sorted(others)) + " (merge if same POS)")
        r.variants = {v for v in r.variants if norm(v) != norm(r.lemma) and v not in r.regional}
        for v in list(r.variants):
            if norm(v) in lookup and norm(v) != norm(r.lemma):
                r.flags.add(f"variant '{v}' is also an entry of its own")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["lemma", "pos", "kind", "level", "tags", "variants", "patterns",
                         "sources", "original", "flags"])  # fmt: skip
        for r in sorted(rows.values(), key=lambda r: (norm(r.lemma), r.lemma)):
            pos = {"phrasal_verb": "verb", "idiom": "phrase"}.get(r.kind, "")
            regional = [f"{v} [{region}]" for v, region in sorted(r.regional.items())]
            writer.writerow([
                r.lemma, pos, r.kind, "", "",
                " | ".join([*sorted(r.variants), *regional]),
                " | ".join(sorted(r.patterns)),
                " | ".join(r.sources), " | ".join(dict.fromkeys(r.originals)),
                " | ".join(sorted(r.flags)),
            ])  # fmt: skip
    kinds = defaultdict(int)
    for r in rows.values():
        kinds[r.kind or "unclassified"] += 1
    flagged = sum(1 for r in rows.values() if r.flags)
    print(f"{len(rows)} candidates -> {OUTPUT.relative_to(ROOT)}")
    print("by kind:", dict(sorted(kinds.items())))
    print(f"flagged for review: {flagged}")
    print(f"UK spellings folded under a US headword: {folded}")
    print(f"case pairs merged: {cased}; inflected forms flagged for review: {inflected}")
    print(f"rows folded into the row that lists them as a variant: {listed}")


if __name__ == "__main__":
    main()
