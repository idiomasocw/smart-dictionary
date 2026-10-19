"""Phonemes in Kokoro's alphabet: misaki's English set (github.com/hexgrad/misaki, EN_PHONES.md).

Kokoro was trained on misaki's phonemes, so the closer its input is to them, the better it
speaks. Two sources reach it:
- **Headwords** come from the entry's IPA. Converting it, instead of letting a text front
  end guess, makes the audio say exactly what the dictionary says (ADR-013): the verb
  *house* is /haʊz/ because the entry says so.
- **Example sentences** have no IPA. espeak-ng phonemizes them, and misaki's own conversion
  rules (Apache-2.0) turn espeak's symbols into misaki's.

Anything outside the variety's 45 symbols raises UnsupportedPhonemes, so a pronunciation the
converter doesn't understand is reported instead of being mispronounced.
"""

import re
import unicodedata

SHARED = "AIWYbdfhijklmnpstuvwzðŋɑɔəɛɜɡɪɹʃʊʌʒʤʧˈˌθᵊ"
VOCAB = {"en-US": frozenset(SHARED + "Oæɾᵻ"), "en-GB": frozenset(SHARED + "Qaɒː")}
# Kokoro reads these in sentences too.
PUNCTUATION = frozenset(' ;:,.!?—…"()“”')
SYLLABIC = re.compile("(\\S)̩")


class UnsupportedPhonemes(ValueError):
    """The text holds symbols Kokoro's alphabet lacks for this variety."""


# Our IPA (CMUdict, Britfone and the style guide's conventions) to misaki, longest first.
_IPA_COMMON = [
    ("eɪ", "A"), ("aɪ", "I"), ("aʊ", "W"), ("ɔɪ", "Y"), ("dʒ", "ʤ"), ("tʃ", "ʧ"),
    ("r", "ɹ"), ("ɻ", "ɹ"), ("g", "ɡ"), ("ɐ", "ʌ"), ("ɫ", "l"), ("ɬ", "l"), ("ʔ", "t"),
    ("x", "k"), ("ɨ", "ɪ"), ("ʉ", "u"), ("ɘ", "ə"), ("e", "ɛ"), ("ˑ", ""), ("ʰ", ""),
]  # fmt: skip
_IPA_US = [
    ("ɝː", "ɜɹ"), ("ɝ", "ɜɹ"), ("ɜː", "ɜɹ"), ("ɚ", "əɹ"), ("oʊ", "O"), ("əʊ", "O"),
    ("ɪə", "iə"), ("ɒ", "ɑ"), ("o", "O"), ("ː", ""),
]  # fmt: skip
_IPA_GB = [
    ("ɝː", "ɜː"), ("ɝ", "ɜː"), ("ɚ", "ə"), ("əʊ", "Q"), ("oʊ", "Q"), ("eə", "ɛː"),
    ("ɛə", "ɛː"), ("æ", "a"), ("ɾ", "t"), ("ᵻ", "ɪ"), ("o", "ɔ"),
]  # fmt: skip

# misaki's FROM_ESPEAKS, sorted longest first as misaki does.
_ESPEAK = sorted(
    {
        "̃": "", "a^ɪ": "I", "a^ʊ": "W", "d^ʒ": "ʤ", "e": "A", "e^ɪ": "A", "r": "ɹ",
        "t^ʃ": "ʧ", "x": "k", "ç": "k", "ɐ": "ə", "ɔ^ɪ": "Y", "ə^l": "ᵊl", "ɚ": "əɹ",
        "ɬ": "l", "ʔ": "t", "ʔn": "tᵊn", "ʔˌn̩": "tᵊn", "ʲ": "", "ʲO": "jO", "ʲQ": "jQ",
    }.items(),
    key=lambda pair: -len(pair[0]),
)  # fmt: skip


def from_ipa(ipa: str, variety: str) -> str:
    """A dictionary IPA transcription as Kokoro phonemes for the variety (en-US or en-GB)."""
    value = unicodedata.normalize("NFC", ipa).strip().strip("/[]")
    for mark in (".", "‿", "͡", "͜"):
        value = value.replace(mark, "")
    value = SYLLABIC.sub(r"ᵊ\1", value).replace("̩", "")
    # The variety's own rules first: they read pairs such as oʊ before the shared rules
    # split them into single letters.
    for old, new in (_IPA_US if variety == "en-US" else _IPA_GB) + _IPA_COMMON:
        value = value.replace(old, new)
    if variety == "en-US":
        value = value.replace("a", "æ")  # what's left after aɪ and aʊ became I and W
    return check(_stress_on_vowels(value), variety)


# Dictionary IPA marks stress where the syllable starts (ˈkʌmfərtəbəl); misaki, and so
# Kokoro, mark it right before the vowel (kˈʌmfəɹtəbəl, as misaki's jump => ʤˈʌmp). A mark
# left in front of a consonant made Kokoro add a stray vowel ("e-comfortable").
VOWELS = "AIOQWYaiuæɑɒɔəɛɜɪʊʌᵻᵊ"
_BEFORE_ONSET = re.compile(f"([ˈˌ])([^{VOWELS}ˈˌ ]+)")
_FIRST_VOWEL = re.compile(f"([{VOWELS}])")


def _stress_on_vowels(phonemes: str) -> str:
    moved = _BEFORE_ONSET.sub(r"\2\1", phonemes)
    if "ˈ" not in moved:
        # One-syllable words come without a mark (STYLE §15), but misaki marks every
        # stressed word (house => hˈWs), so Kokoro expects one.
        moved = _FIRST_VOWEL.sub(r"ˈ\1", moved, count=1)
    return moved


def from_espeak(phonemes: str, variety: str) -> str:
    """espeak-ng's phonemes (tie character ^) in misaki's alphabet, as misaki converts them."""
    value = phonemes
    for old, new in _ESPEAK:
        value = value.replace(old, new)
    value = SYLLABIC.sub(r"ᵊ\1", value).replace("̩", "")
    if variety == "en-GB":
        value = value.replace("e^ə", "ɛː").replace("iə", "ɪə").replace("ə^ʊ", "Q")
    else:
        value = value.replace("o^ʊ", "O").replace("ɜːɹ", "ɜɹ").replace("ɜː", "ɜɹ")
        value = value.replace("ɪə", "iə").replace("ː", "")
    return check(value.replace("^", ""), variety, sentence=True)


def check(phonemes: str, variety: str, *, sentence: bool = False) -> str:
    allowed = VOCAB[variety] | (PUNCTUATION if sentence else frozenset())
    unknown = sorted({symbol for symbol in phonemes if symbol not in allowed})
    if unknown or not phonemes.strip():
        raise UnsupportedPhonemes(
            f"{phonemes!r} has symbols Kokoro's {variety} alphabet lacks: {' '.join(unknown)}"
        )
    return phonemes
