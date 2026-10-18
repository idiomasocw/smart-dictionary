"""The card a student sees, projected from a stored entry document (design §4.5).

One student language only [LK-9]; retired senses and teacher-hidden content are
removed [TF-3]; notes are limited to general ones and the student's language.
"""

from typing import Any

from pydantic import BaseModel

VARIETY_LANGUAGE = {"es-419": "es", "pt-BR": "pt", "ar": "ar"}
# Rough rank bands from wordfreq Zipf values [EC-1].
FREQUENCY_BANDS = ((5.0, "1k"), (4.5, "3k"), (4.2, "5k"), (3.8, "10k"), (3.3, "20k"))


class CardEquivalent(BaseModel):
    term: str
    match: str
    regions: list[str] = []
    note: str | None = None


class CardL1(BaseModel):
    variety: str
    gloss: str
    equivalents: list[CardEquivalent]


class CardExample(BaseModel):
    key: str
    text: str
    cefr: str | None = None
    translation: str | None = None


class CardNote(BaseModel):
    key: str
    kind: str
    text: str
    see: list[str] = []


class CardSense(BaseModel):
    key: str
    cefr: str
    definition: str
    short_gloss: str
    registers: list[str] = []
    domains: list[str] = []
    regions: list[str] = []
    patterns: list[str] = []
    grammar: dict[str, str] | None = None
    l1: CardL1 | None = None
    examples: list[CardExample] = []
    collocations: list[str] = []
    notes: list[CardNote] = []


class CardPronunciation(BaseModel):
    variety: str
    ipa: str


class CardRelated(BaseModel):
    type: str
    key: str
    note: str | None = None


class EntryCard(BaseModel):
    key: str
    lemma: str
    pos: str
    kind: str
    cefr: str
    frequency_band: str | None
    provisional: bool
    variety: str
    pronunciations: list[CardPronunciation]
    variants: list[str]
    senses: list[CardSense]
    related: list[CardRelated]


def frequency_band(zipf: float | None) -> str | None:
    if zipf is None:
        return None
    return next((band for threshold, band in FREQUENCY_BANDS if zipf >= threshold), "rare")


def equivalent_flag_key(sense_key: str, variety: str, term: str) -> str:
    """How a hidden equivalent is addressed in content_flags.target_key."""
    return f"{sense_key}|{variety}|{term}"


def project_card(
    doc: dict[str, Any], *, variety: str, hidden: set[str], zipf: float | None
) -> EntryCard:
    language = VARIETY_LANGUAGE[variety]
    senses: list[CardSense] = []
    for sense in doc["senses"]:
        key = sense["key"]
        if sense.get("retired") or key in hidden:
            continue
        layer = (sense.get("l1") or {}).get(variety)
        l1 = None
        if layer:
            l1 = CardL1(
                variety=variety,
                gloss=layer["gloss"],
                equivalents=[
                    CardEquivalent(
                        term=e["term"],
                        match=e["match"],
                        regions=e.get("regions") or [],
                        note=e.get("note"),
                    )
                    for e in layer["equivalents"]
                    if equivalent_flag_key(key, variety, e["term"]) not in hidden
                ],
            )
        senses.append(
            CardSense(
                key=key,
                cefr=sense["cefr"],
                definition=sense["definition"],
                short_gloss=sense["short_gloss"],
                registers=sense.get("registers") or [],
                domains=sense.get("domains") or [],
                regions=sense.get("regions") or [],
                patterns=sense.get("patterns") or [],
                grammar={k: v for k, v in (sense.get("grammar") or {}).items() if v} or None,
                l1=l1,
                examples=[
                    CardExample(
                        key=example["key"],
                        text=example["text"],
                        cefr=example.get("cefr"),
                        translation=(example.get("translations") or {}).get(variety),
                    )
                    for example in sense["examples"]
                    if example["key"] not in hidden
                ],
                collocations=sense.get("collocations") or [],
                notes=[
                    CardNote(
                        key=note["key"],
                        kind=note["kind"],
                        text=note["text"],
                        see=note.get("see") or [],
                    )
                    for note in sense.get("notes") or []
                    if note["key"] not in hidden and note.get("l1") in (None, language)
                ],
            )
        )
    return EntryCard(
        key=doc["key"],
        lemma=doc["lemma"],
        pos=doc["pos"],
        kind=doc["kind"],
        cefr=doc["cefr"],
        frequency_band=frequency_band(zipf),
        provisional=bool(doc.get("provisional")),
        variety=variety,
        pronunciations=[
            CardPronunciation(variety=p["variety"], ipa=p["ipa"])
            for p in doc.get("pronunciations") or []
        ],
        variants=[variant["form"] for variant in doc.get("variants") or []],
        senses=senses,
        related=[CardRelated(**related) for related in doc.get("related") or []],
    )


def preview(
    doc: dict[str, Any], variety: str, sense_key: str | None = None
) -> tuple[str | None, str | None, str | None]:
    """(sense key, short gloss, first L1 equivalent) for a one-line search result."""
    active = [s for s in doc["senses"] if not s.get("retired")]
    sense = next((s for s in active if s["key"] == sense_key), active[0] if active else None)
    if sense is None:
        return None, None, None
    layer = (sense.get("l1") or {}).get(variety) or {}
    equivalents = layer.get("equivalents") or []
    return sense["key"], sense["short_gloss"], equivalents[0]["term"] if equivalents else None
