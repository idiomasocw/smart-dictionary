import pytest

from smart_dictionary.core.keys import (
    entry_key,
    example_key,
    note_key,
    parse_key,
    sense_key,
    slug,
)
from smart_dictionary.core.text import norm, spacing_variants


@pytest.mark.parametrize(
    ("raw", "language", "expected"),
    [
        ("  Don’t  ", "en", "don't"),
        ("Café", "en", "cafe"),
        ("ÁRBOL", "es", "arbol"),
        ("well–known", "en", "well-known"),
        ("«hola»", "es", "hola"),
        ("¿Qué?", "es", "que"),
        ("me   di\tcuenta", "es", "me di cuenta"),
        ("Ｆｕｌｌｗｉｄｔｈ", "en", "fullwidth"),
        ("أَحْمَد", "ar", "احمد"),
        ("مدرسة", "ar", "مدرسه"),
        ("على", "ar", "علي"),
        ("كـتـاب", "ar", "كتاب"),
    ],
)
def test_norm(raw, language, expected):
    assert norm(raw, language) == expected


def test_spacing_variants_cover_hyphen_and_space():
    assert spacing_variants("Well-Known") == {"well-known", "well known"}
    assert spacing_variants("actually") == {"actually"}


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("en", "actually", "adverb"), "en:actually:adv"),
        (("en", "run into", "verb", "phrasal_verb"), "en:run_into:phrv"),
        (("en", "break the ice", "phrase", "idiom"), "en:break_the_ice:idiom"),
        (("en", "by the way", "phrase", "phrase"), "en:by_the_way:phr"),
        (("en", "bow", "noun", "word", 2), "en:bow:n~2"),
        (("en", "o’clock", "adverb"), "en:o'clock:adv"),
        (("en", "well-known", "adjective"), "en:well-known:adj"),
        (("en", "résumé", "noun"), "en:résumé:n"),
    ],
)
def test_entry_key(args, expected):
    assert entry_key(*args) == expected


def test_child_keys_round_trip():
    entry = entry_key("en", "actually", "adverb")
    sense = sense_key(entry, 1)
    example = example_key(sense, 2)
    note = note_key(sense, 1)
    assert (sense, example, note) == (
        "en:actually:adv#1",
        "en:actually:adv#1.ex2",
        "en:actually:adv#1.note1",
    )
    parsed = parse_key(example)
    assert parsed.level == "example"
    assert parsed.entry_key == entry
    assert parsed.sense_key == sense
    assert parse_key(note).level == "note"
    assert parse_key("en:bow:n~2#3").homograph == 2


@pytest.mark.parametrize(
    "bad",
    [
        "en:actually",
        "en:actually:xyz",
        "EN:actually:adv",
        "en:bow:n~1",
        "en:actually:adv#0",
        "en:actually:adv#1.ex0",
        "en:actually:adv.ex1",
        "en:act ually:adv",
    ],
)
def test_parse_key_rejects_malformed_keys(bad):
    with pytest.raises(ValueError):
        parse_key(bad)


def test_children_need_the_right_parent():
    with pytest.raises(ValueError):
        example_key("en:actually:adv", 1)
    with pytest.raises(ValueError):
        sense_key("en:actually:adv#1", 2)
    with pytest.raises(ValueError):
        slug("?!")
