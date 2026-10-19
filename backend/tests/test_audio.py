"""Audio (design §10.4): the IPA in Kokoro's alphabet, and the bucket's layout."""

import pytest

from smart_dictionary.audio import layout
from smart_dictionary.audio.phonemes import UnsupportedPhonemes, from_espeak, from_ipa


@pytest.mark.parametrize(
    ("ipa", "variety", "expected"),
    [
        ("haʊs", "en-US", "hˈWs"),  # a stressed word gets its mark
        ("haʊz", "en-GB", "hˈWz"),
        ("dɪˈsaɪd", "en-US", "dɪsˈId"),  # stress moves to the vowel
        ("ˈbɑroʊ", "en-US", "bˈɑɹO"),
        ("ˈbɒrəʊ", "en-GB", "bˈɒɹQ"),
        ("ˈɡoʊ", "en-US", "ɡˈO"),
        ("ˈgəʊ", "en-GB", "ɡˈQ"),  # an ASCII g, as some sources write it
        ("ˈkɪtʃən", "en-US", "kˈɪʧən"),
        ("ˈdʒʌmp", "en-GB", "ʤˈʌmp"),  # misaki's own jump
        ("ˈkʌmftəbl̩", "en-GB", "kˈʌmftəbᵊl"),  # a syllabic l
        ("ˈkɐmftəbl̩", "en-GB", "kˈʌmftəbᵊl"),  # Britfone's strut vowel
        ("ˈfɑðɚ", "en-US", "fˈɑðəɹ"),
        ("ˈfɑːðə", "en-GB", "fˈɑːðə"),
        ("bɝd", "en-US", "bˈɜɹd"),
        ("bɜːd", "en-GB", "bˈɜːd"),
        ("ˈæktʃuəl", "en-US", "ˈækʧuəl"),  # a vowel first: the mark stays
        ("ˌʌndərˈstænd", "en-US", "ˌʌndəɹstˈænd"),
        ("ˈæktʃuəl", "en-GB", "ˈakʧuəl"),
        ("keə", "en-GB", "kˈɛː"),
        ("/ˈhæp.i/", "en-US", "hˈæpi"),  # slashes and syllable dots
    ],
)
def test_dictionary_ipa_becomes_kokoro_phonemes(ipa, variety, expected):
    assert from_ipa(ipa, variety) == expected


def test_symbols_kokoro_lacks_are_refused_not_guessed():
    with pytest.raises(UnsupportedPhonemes, match="ʘ"):
        from_ipa("ʘa", "en-US")
    with pytest.raises(UnsupportedPhonemes):
        from_ipa("", "en-US")


def test_espeak_phonemes_follow_misakis_rules():
    # misaki's own example (EN_PHONES.md).
    assert from_espeak("mˈɜːt^ʃəntʃˌɪp", "en-US") == "mˈɜɹʧəntʃˌɪp"
    assert from_espeak("mˈɜːt^ʃəntʃˌɪp", "en-GB") == "mˈɜːʧəntʃˌɪp"
    sentence = "ðə nˈuː dˈɔː^ɹm wɪl hˈa^ʊz mˈɔː^ɹ ðɐn tˈuː hˈʌndɹɪd stˈuːdənts."
    expected = "ðə nˈu dˈɔɹm wɪl hˈWz mˈɔɹ ðən tˈu hˈʌndɹɪd stˈudənts."
    assert from_espeak(sentence, "en-US") == expected


def test_the_bucket_layout():
    engine, voice = "kokoro-82m@1.0", "af_heart"
    headword = layout.s3_key("en:house:n", "en-US", voice, engine, "hWs")
    assert headword.startswith("v1/en/house/n/pron.en-us.af_heart.")
    assert headword.endswith(".m4a") and len(headword.split(".")[-2]) == 12
    sense = layout.s3_key("en:bow:n~2#2", "en-GB", "bf_emma", engine, "bQ")
    assert sense.startswith("v1/en/bow/n-2/s2-pron.en-gb.bf_emma.")
    example = layout.s3_key("en:run_into:phrv#1.ex2", "en-US", voice, engine, "Text.")
    assert example.startswith("v1/en/run_into/phrv/s1-ex2.en-us.af_heart.")
    # What's said is in the name: new words, a new file; the same words, the same file.
    assert layout.s3_key("en:house:n", "en-US", voice, engine, "hWz") != headword
    assert layout.s3_key("en:house:n", "en-US", voice, engine, "hWs") == headword
    assert layout.safe("résumé") == "resume" and layout.safe("don't") == "don-t"
    with pytest.raises(ValueError):
        layout.s3_key("en:house:n#1.note1", "en-US", voice, engine, "x")
