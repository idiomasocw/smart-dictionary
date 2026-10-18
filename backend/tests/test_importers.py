import gzip
import json
from pathlib import Path
from typing import Any, LiteralString

import psycopg
import pytest

from smart_dictionary.importers import kaikki, sources
from smart_dictionary.importers.db import connect
from smart_dictionary.importers.manifest import Dataset, load_manifest
from smart_dictionary.importers.phonetics import arpabet_to_ipa, britfone_to_ipa
from smart_dictionary.importers.run import IMPORTER_ORDER, import_dataset, in_import_order


@pytest.mark.parametrize(
    ("arpabet", "ipa"),
    [
        ("AH0 B AE1 N D AH0 N", "əˈbændən"),
        ("S T R IY1 T", "ˈstrit"),
        ("IH0 K S P L EY1 N", "ɪkˈspleɪn"),
        ("K AH0 M P Y UW1 T ER0", "kəmˈpjutɚ"),
        ("EH2 K S P L AH0 N EY1 SH AH0 N", "ˌɛkspləˈneɪʃən"),
    ],
)
def test_arpabet_to_ipa_puts_stress_at_the_syllable_onset(arpabet, ipa):
    assert arpabet_to_ipa(arpabet) == ipa


def test_britfone_to_ipa():
    assert britfone_to_ipa("ə b ˈæ n d ə n") == "əˈbændən"
    assert britfone_to_ipa("ɡ ˈəʊ") == "ˈgəʊ"


def test_cefr_list_rows_split_variants_and_map_pos(tmp_path):
    csv_file = tmp_path / "list.csv"
    csv_file.write_text(
        "headword,pos,CEFR,CoreInventory 1,CoreInventory 2,Threshold\n"
        "a.m./A.M./am/AM,adverb,A1,,,\n"
        '"according to",preposition,A2,x,,\n'
        "abandon,verb,B1,,,\n"
        "to,infinitive-to,A1,,,\n",
        encoding="utf-8",
    )
    rows = list(sources.cefr_list_rows(csv_file, "t"))
    lemmas = [(row[1], row[3], row[5]) for row in rows]
    assert lemmas == [
        ("a.m.", "adverb", "A1"),
        ("am", "adverb", "A1"),
        ("according to", "preposition", "A2"),
        ("abandon", "verb", "B1"),
        ("to", None, "A1"),
    ]
    assert rows[2][7].obj == {"CoreInventory 1": "x"}
    assert rows[0][7].obj["headword"] == "a.m./A.M./am/AM"


def test_line_formats(tmp_path):
    unimorph = tmp_path / "eng"
    unimorph.write_text("go\twent\tV;PST\ngo\tgoes\tV;PRS;3;SG\nbroken line\n", encoding="utf-8")
    assert [r[4] for r in sources.unimorph_rows(unimorph, "t", "en")] == ["went", "goes"]

    cmu = tmp_path / "cmudict.dict"
    cmu.write_text("street S T R IY1 T\nread(2) R EH1 D # past tense\n", encoding="utf-8")
    assert [(r[1], r[4]) for r in sources.cmudict_rows(cmu, "t")] == [
        ("street", "ˈstrit"),
        ("read", "ˈrɛd"),
    ]

    brit = tmp_path / "britfone.csv"
    brit.write_text("ABANDON, ə b ˈæ n d ə n\nREAD(1), r ˈiː d\n", encoding="utf-8")
    assert [(r[1], r[4]) for r in sources.britfone_rows(brit, "t")] == [
        ("abandon", "əˈbændən"),
        ("read", "ˈriːd"),
    ]


def test_ngsl_family_formats(tmp_path):
    stats = tmp_path / "stats.csv"
    stats.write_text(
        "Lemma,SFI Rank,SFI,Adjusted Frequency per Million (U)\n"
        "the,1,87.85,60910\nbe,2,86.86,48575\n",
        encoding="utf-8",
    )
    rows = list(sources.ranked_list_rows(stats, "t", lemma_column="Lemma", rank_column="SFI Rank"))
    assert [(row[1], row[6]) for row in rows] == [("the", 1), ("be", 2)]
    assert rows[0][7].obj == {"SFI": "87.85", "Adjusted Frequency per Million (U)": "60910"}
    with pytest.raises(ValueError, match="Rank"):
        next(sources.ranked_list_rows(stats, "t", lemma_column="Lemma", rank_column="Rank"))

    # BSL carries empty spreadsheet columns, and TSL and NAWL are cp1252 rather than UTF-8.
    business = tmp_path / "business.csv"
    business.write_bytes("Word,BSL Rank,Band,U,,,581.3\nrésumé,2,1,468.5,,,\n".encode("cp1252"))
    rows = list(
        sources.ranked_list_rows(
            business, "t", lemma_column="Word", rank_column="BSL Rank", encoding="cp1252"
        )
    )
    assert [(row[1], row[2], row[6]) for row in rows] == [("résumé", "resume", 2)]
    assert rows[0][7].obj == {"Band": "1", "U": "468.5"}

    # Word families: a byte-order mark, ## comment lines and Windows line endings.
    family = tmp_path / "family.csv"
    family.write_text(
        "## For teaching, not research\r\nMonday,Mondays\r\nzone,zones,zoned,zoning\r\nalone\r\n",
        encoding="utf-8-sig",
        newline="",
    )
    rows = list(sources.word_family_rows(family, "t"))
    assert [(row[1], row[2]) for row in rows] == [
        ("Monday", "monday"),
        ("zone", "zone"),
        ("alone", "alone"),
    ]
    assert rows[1][7].obj == {"forms": ["zones", "zoned", "zoning"]}
    assert rows[2][7] is None


def test_list_imports_fill_ref_lists(migrated_database_url, tmp_path: Path):
    ngsl = tmp_path / "ngsl.csv"
    ngsl.write_text("Lemma,SFI Rank,SFI\nthe,1,87.85\nbe,2,86.86\n", encoding="utf-8")
    sup = tmp_path / "sup.csv"
    sup.write_text("Monday,Mondays\n", encoding="utf-8")
    ranked = _dataset("t-ngsl", "ranked_list", lemma_column="Lemma", rank_column="SFI Rank")
    assert import_dataset(ranked, path=ngsl) == 2
    assert import_dataset(_dataset("t-sup", "word_family"), path=sup) == 1
    with connect() as conn:
        rows = conn.execute(
            "select dataset_id, lemma_norm, rank, extra from ref_lists order by dataset_id, rank"
        ).fetchall()
    assert rows == [
        ("t-ngsl", "the", 1, {"SFI": "87.85"}),
        ("t-ngsl", "be", 2, {"SFI": "86.86"}),
        ("t-sup", "monday", None, {"forms": ["Mondays"]}),
    ]


def _entry(word: str, pos: str, senses: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"word": word, "pos": pos, "lang_code": "en", "senses": senses, **extra}


DICTIONARY = _entry(
    "dictionary",
    "noun",
    [{"glosses": ["A reference work listing words."]}],
    sounds=[
        {"tags": ["Received-Pronunciation"], "ipa": "/ˈdɪk.ʃə.nə.ɹi/"},
        {"tags": ["US"], "ipa": "/ˈdɪk.ʃəˌnɛɹ.i/"},
        {"tags": ["US"], "enpr": "dĭk'shə-nĕr-ē"},
    ],
    translations=[
        {"lang_code": "es", "word": "diccionario"},
        {"lang_code": "pt", "word": "dicionário"},
        {"lang_code": "ar", "word": "قاموس"},
        {"lang_code": "fr", "word": "dictionnaire"},
    ],
    forms=[
        {"form": "dictionaries", "tags": ["plural"]},
        {"form": "dixnary", "tags": ["alternative", "obsolete"]},
    ],
)
RUN_INTO = _entry(
    "run into",
    "verb",
    [{"glosses": ["To meet by chance."], "categories": [{"name": "English phrasal verbs"}]}],
)
GIVE_UP = _entry(
    "give up",
    "verb",
    [
        {
            "glosses": ["To stop trying."],
            "categories": [{"name": 'English phrasal verbs formed with "up"'}],
        }
    ],
)
BREAK_THE_ICE = _entry(
    "break the ice",
    "verb",
    [{"glosses": ["To start a conversation."], "categories": [{"name": "English idioms"}]}],
)
SKIPPED = [
    _entry("pies", "noun", [{"glosses": ["plural of pie"], "tags": ["form-of"]}]),
    _entry("zyzzyva", "noun", [{"glosses": ["A weevil."]}]),
    _entry("Paris", "name", [{"glosses": ["The capital of France."]}]),
    _entry("eftsoons", "adv", [{"glosses": ["Soon after."], "tags": ["obsolete"]}]),
    {**_entry("dictionnaire", "noun", [{"glosses": ["dictionary"]}]), "lang_code": "fr"},
]


def keep_listed(*spellings: str) -> kaikki.KeepWord:
    """A keep filter whose lists hold exactly these headwords."""

    def keep(word: str, word_norm: str, candidate: kaikki.Candidate) -> bool:
        return (word if candidate == "name" else word_norm) in spellings

    return keep


keep_dictionary_only = keep_listed("dictionary")


def test_kaikki_extract_keeps_known_words_and_every_multiword_kind():
    item = kaikki.extract(DICTIONARY, keep_dictionary_only)
    assert item is not None
    assert item["translations"] == {"es": ["diccionario"], "pt": ["dicionário"], "ar": ["قاموس"]}
    assert item["ipa"] == {"en-GB": ["ˈdɪk.ʃə.nə.ɹi"], "en-US": ["ˈdɪk.ʃəˌnɛɹ.i"]}
    assert item["forms"] == ["dictionaries"]
    assert item["glosses"] == ["A reference work listing words."]

    run_into = kaikki.extract(RUN_INTO, keep_dictionary_only)
    give_up = kaikki.extract(GIVE_UP, keep_dictionary_only)
    ice = kaikki.extract(BREAK_THE_ICE, keep_dictionary_only)
    assert run_into is not None and run_into["kind"] == "phrasal_verb"
    assert give_up is not None and give_up["kind"] == "phrasal_verb"
    assert ice is not None and ice["kind"] == "idiom"
    assert [kaikki.extract(e, keep_dictionary_only) for e in SKIPPED] == [None] * len(SKIPPED)


APRIL = _entry("April", "name", [{"glosses": ["The fourth month."]}])
AGO = _entry("ago", "postp", [{"glosses": ["Before now."]}])
CATALOG = _entry(
    "catalog",
    "noun",
    [
        {
            "glosses": ["US standard spelling of catalogue."],
            "tags": ["US", "alt-of"],
            "alt_of": [{"word": "catalogue"}],
        }
    ],
    forms=[{"form": "catalogs", "tags": ["plural"]}],
)
GDP = _entry(
    "GDP",
    "noun",
    [
        {
            "glosses": ["Initialism of gross domestic product."],
            "tags": ["abbreviation", "alt-of", "initialism"],
            "alt_of": [{"word": "gross domestic product"}],
        }
    ],
)
FREE_AS_THREE = _entry(
    "free",
    "num",
    [
        {
            "glosses": ["Pronunciation spelling of three."],
            "tags": ["alt-of", "pronunciation-spelling"],
            "alt_of": [{"word": "three"}],
        }
    ],
)
FORTH = _entry("forth", "adv", [{"glosses": ["Forward."], "tags": ["archaic"]}])


def _alternative(word: str, pos: str, target: str, *tags: str, **sense: Any) -> dict[str, Any]:
    alternative = {
        "glosses": [f"Alternative form of {target}."],
        "tags": ["alt-of", "alternative", *tags],
        "alt_of": [{"word": target}],
        **sense,
    }
    return _entry(word, pos, [alternative])


def test_kaikki_extract_listed_names_postpositions_variants_and_archaic_words():
    keep = keep_listed(
        "April", "ago", "catalog", "forth", "free", "gdp", "okay", "ok", "with", "is", "us"
    )
    april = kaikki.extract(APRIL, keep)
    assert april is not None and (april["pos"], april["pos_raw"]) == ("noun", "name")
    assert kaikki.extract(APRIL, keep_listed("april")) is None  # names need the listed spelling
    ago = kaikki.extract(AGO, keep)
    assert ago is not None and ago["pos"] == "adverb"

    catalog = kaikki.extract(CATALOG, keep)
    assert catalog is not None
    assert catalog["variant_of"] == ["catalogue"] and catalog["glosses"] == []
    assert catalog["forms"] == ["catalogs"] and catalog["tags"] == ["US"]
    # A plain alternative form counts only when its target is kept too ("withe" isn't), and
    # register or dialect tags rule a sense out.
    okay = kaikki.extract(_alternative("okay", "adj", "OK"), keep)
    assert okay is not None and okay["variant_of"] == ["ok"]
    assert kaikki.extract(_alternative("with", "noun", "withe"), keep) is None
    assert kaikki.extract(_alternative("is", "pron", "us", "Geordie"), keep) is None
    # A target that describes rather than spells is a parsing artifact.
    description = "miss when used as a term of address"
    assert kaikki.extract(_alternative("miss", "noun", description), keep_listed("miss")) is None
    # An idiom's variant is an idiom too, and its target may run longer than one word.
    idioms = [{"name": "English idioms"}]
    bush = _alternative("beat about the bush", "verb", "beat around the bush", categories=idioms)
    idiom = kaikki.extract(bush, keep_listed("beat around the bush"))
    assert idiom is not None and idiom["kind"] == "idiom"
    assert idiom["variant_of"] == ["beat around the bush"]
    # Abbreviations and pronunciation spellings point elsewhere too, but aren't variants.
    assert kaikki.extract(GDP, keep) is None
    assert kaikki.extract(FREE_AS_THREE, keep) is None

    # Wiktionary marks every sense of "forth" archaic; being on a list keeps it anyway.
    assert kaikki.extract(FORTH, keep) is not None
    assert kaikki.extract(FORTH, keep_dictionary_only) is None


def test_manifest_lists_known_importers_in_dependency_order():
    manifest = load_manifest()
    assert {dataset.importer for dataset in manifest.values()} <= set(IMPORTER_ORDER)
    ordered = [dataset.importer for dataset in in_import_order(manifest.values())]
    assert ordered == sorted(ordered, key=IMPORTER_ORDER.index)
    assert ordered[-1] == "kaikki"
    for dataset in manifest.values():
        if dataset.importer == "ranked_list":
            assert {"lemma_column", "rank_column"} <= dataset.options.keys(), dataset.id


def _dataset(dataset_id: str, importer: str, **options: Any) -> Dataset:
    return Dataset(
        id=dataset_id,
        name=dataset_id,
        version="test",
        license="test",
        attribution="test",
        url=f"file:///{dataset_id}",
        importer=importer,
        options=options,
    )


def test_imports_load_the_reference_tables(migrated_database_url, tmp_path: Path):
    cefr = tmp_path / "cefr.csv"
    # "dictionary" ranks below wordfreq's top 5,000, so listing it is what keeps its entry.
    cefr.write_text(
        "headword,pos,CEFR\nabandon,verb,B1\nbus stop,noun,A2\ndictionary,noun,A2\n"
        "catalog,noun,B1\n",
        encoding="utf-8",
    )
    unimorph = tmp_path / "eng"
    unimorph.write_text("go\twent\tV;PST\n", encoding="utf-8")
    cmu = tmp_path / "cmudict.dict"
    cmu.write_text("street S T R IY1 T\n", encoding="utf-8")
    brit = tmp_path / "britfone.csv"
    brit.write_text("STREET, s t r ˈiː t\n", encoding="utf-8")
    wiktionary = tmp_path / "kaikki.jsonl.gz"
    with gzip.open(wiktionary, "wt", encoding="utf-8") as handle:
        for entry in (DICTIONARY, RUN_INTO, BREAK_THE_ICE, CATALOG, *SKIPPED):
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    counts = {
        "t-cefr": import_dataset(_dataset("t-cefr", "cefr_list"), path=cefr),
        "t-freq": import_dataset(_dataset("t-freq", "wordfreq", language="en", top_n=5000)),
        "t-forms": import_dataset(_dataset("t-forms", "unimorph", language="en"), path=unimorph),
        "t-cmu": import_dataset(_dataset("t-cmu", "cmudict"), path=cmu),
        "t-brit": import_dataset(_dataset("t-brit", "britfone"), path=brit),
        "t-wiki": import_dataset(
            _dataset("t-wiki", "kaikki", min_zipf=3.0, min_multiword_zipf=2.5), path=wiktionary
        ),
    }
    assert counts["t-cefr"] == 4 and counts["t-forms"] == 1 and counts["t-wiki"] == 4
    assert counts["t-freq"] > 4000

    # A second import replaces the rows instead of duplicating them.
    assert import_dataset(_dataset("t-cefr", "cefr_list"), path=cefr) == 4

    with connect() as conn:
        assert _scalar(conn, "select count(*) from ref_lists") == 4
        assert _scalar(conn, "select row_count from ref_datasets where id = 't-wiki'") == 4
        kinds = dict(conn.execute("select word, kind from ref_wiktionary").fetchall())
        assert kinds == {
            "dictionary": "word",
            "run into": "phrasal_verb",
            "break the ice": "idiom",
            "catalog": "word",
        }
        variants = conn.execute(
            "select word, variant_of from ref_wiktionary where variant_of <> '{}'"
        ).fetchall()
        assert variants == [("catalog", ["catalogue"])]
        ipa = conn.execute("select variety, ipa from ref_ipa order by variety").fetchall()
        assert ipa == [("en-GB", "ˈstriːt"), ("en-US", "ˈstrit")]


def _scalar(conn: psycopg.Connection, query: LiteralString) -> Any:
    row = conn.execute(query).fetchone()
    assert row is not None
    return row[0]
