"""Text normalization for matching (system design §3.2).

`norm()` is the single definition of "the same string" for lookups: every
`*_norm` column is computed with it, and every query passes through it before
it reaches SQL. Display text is never normalized.
"""

import re
import unicodedata

# Right/left single quotes, modifier apostrophe, acute, grave and prime become "'".
APOSTROPHES = str.maketrans(dict.fromkeys("\u2019\u2018\u02bc\u00b4\u0060\u2032", "'"))
# Hyphen, non-breaking hyphen, figure dash, en dash, em dash and minus become "-".
DASHES = str.maketrans(dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2212", "-"))

# ASCII quotes and brackets, guillemets, curly double quotes, inverted ! and ?, ellipsis,
# and the Arabic comma and question mark.
_EDGE_PUNCTUATION = "\u0022'()[]{}.,;:!?\u00ab\u00bb\u201c\u201d\u00a1\u00bf\u2026\u060c\u061f"
_WHITESPACE = re.compile(r"\s+")

# Arabic: harakat, Quranic marks and tatweel go; alef, yaa and taa marbuta variants fold.
_ARABIC_MARKS = re.compile("[\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")
_ARABIC_FOLD = str.maketrans(
    {
        "\u0623": "\u0627",  # alef with hamza above -> alef
        "\u0625": "\u0627",  # alef with hamza below -> alef
        "\u0622": "\u0627",  # alef with madda -> alef
        "\u0649": "\u064a",  # alef maqsura -> yaa
        "\u0629": "\u0647",  # taa marbuta -> haa
    }
)


def norm(text: str, language: str = "en") -> str:
    """Casefolded, accent-free, punctuation-trimmed form used for matching."""
    value = unicodedata.normalize("NFKC", text).translate(APOSTROPHES).translate(DASHES)
    if language == "ar":
        value = _ARABIC_MARKS.sub("", value).translate(_ARABIC_FOLD)
    else:
        value = strip_diacritics(value.casefold())
    value = _WHITESPACE.sub(" ", value).strip()
    return value.strip(_EDGE_PUNCTUATION).strip()


def strip_diacritics(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    kept = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return unicodedata.normalize("NFC", kept)


def spacing_variants(text: str, language: str = "en") -> set[str]:
    """Normalized forms that should all find the same entry: well-known, well known."""
    value = norm(text, language)
    variants = {value}
    if "-" in value:
        variants.add(_WHITESPACE.sub(" ", value.replace("-", " ")).strip())
    return variants
