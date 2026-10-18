"""Pronunciation notation for the reference dictionaries, rendered as learner-style IPA.

Stress marks go at the start of the stressed syllable, found by taking the longest
legal English onset before the vowel: abandon /əˈbændən/, explain /ɪkˈspleɪn/.
Plain "g" and "r" are used, as in learner dictionaries.
"""

Phone = tuple[str, int | None]  # (IPA symbol, stress 0/1/2 for vowels; None for consonants)

ARPA_VOWELS = {
    "AA": "ɑ", "AE": "æ", "AH": "ʌ", "AO": "ɔ", "AW": "aʊ", "AY": "aɪ", "EH": "ɛ", "ER": "ɝ",
    "EY": "eɪ", "IH": "ɪ", "IY": "i", "OW": "oʊ", "OY": "ɔɪ", "UH": "ʊ", "UW": "u",
}  # fmt: skip
ARPA_REDUCED = {"AH": "ə", "ER": "ɚ"}  # the unstressed (0) forms
ARPA_CONSONANTS = {
    "B": "b", "CH": "tʃ", "D": "d", "DH": "ð", "F": "f", "G": "g", "HH": "h", "JH": "dʒ",
    "K": "k", "L": "l", "M": "m", "N": "n", "NG": "ŋ", "P": "p", "R": "r", "S": "s", "SH": "ʃ",
    "T": "t", "TH": "θ", "V": "v", "W": "w", "Y": "j", "Z": "z", "ZH": "ʒ",
}  # fmt: skip
STRESS_MARKS = {1: "ˈ", 2: "ˌ"}
IPA_VOWEL_LETTERS = set("aeiouæɑɒɔəɚɝɛɜɪʊʌɐ")

_ONSET_CLUSTERS = {
    ("p", "l"), ("p", "r"), ("b", "l"), ("b", "r"), ("t", "r"), ("d", "r"), ("k", "l"),
    ("k", "r"), ("g", "l"), ("g", "r"), ("f", "l"), ("f", "r"), ("θ", "r"), ("ʃ", "r"),
    ("s", "p"), ("s", "t"), ("s", "k"), ("s", "m"), ("s", "n"), ("s", "l"), ("s", "w"),
    ("s", "f"), ("t", "w"), ("d", "w"), ("k", "w"), ("g", "w"), ("θ", "w"), ("p", "j"),
    ("b", "j"), ("t", "j"), ("d", "j"), ("k", "j"), ("g", "j"), ("f", "j"), ("v", "j"),
    ("m", "j"), ("n", "j"), ("h", "j"), ("l", "j"), ("s", "p", "l"), ("s", "p", "r"),
    ("s", "t", "r"), ("s", "k", "r"), ("s", "k", "w"), ("s", "p", "j"), ("s", "k", "j"),
}  # fmt: skip


def legal_onset(consonants: tuple[str, ...]) -> bool:
    if len(consonants) <= 1:
        return consonants != ("ŋ",)
    return consonants in _ONSET_CLUSTERS


def render(phones: list[Phone]) -> str:
    marks: dict[int, str] = {}
    for index, (_, stress) in enumerate(phones):
        if stress not in STRESS_MARKS:
            continue
        start = index
        while start > 0 and phones[start - 1][1] is None:
            start -= 1
        if start > 0:  # word-initial clusters are legal by definition
            run = tuple(symbol for symbol, _ in phones[start:index])
            take = next(n for n in range(len(run), -1, -1) if legal_onset(run[len(run) - n :]))
            start = index - take
        marks[start] = STRESS_MARKS[stress]
    return "".join(marks.get(i, "") + symbol for i, (symbol, _) in enumerate(phones))


def arpabet_to_ipa(arpabet: str) -> str:
    phones: list[Phone] = []
    for token in arpabet.split():
        base, digit = (token[:-1], int(token[-1])) if token[-1].isdigit() else (token, None)
        if base in ARPA_VOWELS:
            stress = digit or 0
            reduced = stress == 0 and base in ARPA_REDUCED
            phones.append((ARPA_REDUCED[base] if reduced else ARPA_VOWELS[base], stress))
        elif base in ARPA_CONSONANTS:
            phones.append((ARPA_CONSONANTS[base], None))
        else:
            raise ValueError(f"unknown ARPAbet symbol {token!r}")
    return render(phones)


def britfone_to_ipa(spaced: str) -> str:
    """Britfone writes one phone per token, with the stress mark glued to the vowel."""
    phones: list[Phone] = []
    for raw in spaced.split():
        token, stress = raw, None
        if token[0] in "ˈˌ":
            stress, token = (1 if token[0] == "ˈ" else 2), token[1:]
        token = token.replace("ɡ", "g")
        is_vowel = any(ch in IPA_VOWEL_LETTERS for ch in token)
        phones.append((token, (stress or 0) if is_vowel else None))
    return render(phones)
