"""Where each audio file lives in the bucket (docs/03 §10.4).

    v1/en/house/n/pron.en-us.af_heart.3f9a2c1d0b7e.m4a      the headword
    v1/en/bow/n-2/s2-pron.en-gb.bf_emma.9c0d4e7a1b2f.m4a    a pronunciation for sense 2
    v1/en/house/n/s1-ex2.en-us.af_heart.71aa03bc94d2.m4a    sense 1, example 2

One folder per entry, from its key, and the hash of what's said in each file's name.
Anything that changes the sound changes the name, so a file is never overwritten and every
copy can be cached for a year. Paths use only a-z 0-9 - _ ., so none needs URL encoding.
"""

import hashlib
import re
import unicodedata

from smart_dictionary.core.keys import parse_key

VERSION = "v1"
SUFFIX = ".m4a"
CONTENT_TYPE = "audio/mp4"
CACHE_CONTROL = "public, max-age=31536000, immutable"
_UNSAFE = re.compile(r"[^a-z0-9_-]+")


def s3_key(owner_key: str, variety: str, voice: str, engine: str, spoken: str) -> str:
    """The file for one thing said: a headword pronunciation (owner: the entry or a sense)
    or an example (owner: the example), in one variety and voice."""
    parsed = parse_key(owner_key)
    code = parsed.code + (f"-{parsed.homograph}" if parsed.homograph > 1 else "")
    folder = f"{VERSION}/{parsed.language}/{safe(parsed.slug)}/{code}"
    if parsed.level == "entry":
        part = "pron"
    elif parsed.level == "sense":
        part = f"s{parsed.sense}-pron"
    elif parsed.level == "example":
        part = f"s{parsed.sense}-ex{parsed.part_number}"
    else:
        raise ValueError(f"{owner_key!r}: only entries, senses and examples have audio")
    digest = content_hash(engine, voice, variety, spoken)
    return f"{folder}/{part}.{variety.lower()}.{voice}.{digest}{SUFFIX}"


def content_hash(engine: str, voice: str, variety: str, spoken: str) -> str:
    return hashlib.sha256(f"{engine}|{voice}|{variety}|{spoken}".encode()).hexdigest()[:12]


def safe(segment: str) -> str:
    """A key's slug as a path segment: accents dropped, anything else outside a-z 0-9 _ -
    turned into -. Two slugs that fold alike (résumé, resume) share a folder, never a file."""
    folded = unicodedata.normalize("NFKD", segment).encode("ascii", "ignore").decode()
    return _UNSAFE.sub("-", folded.casefold()).strip("-") or "x"
