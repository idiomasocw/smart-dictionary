"""The seed builder: parts of speech, the open lists, spelling and spacing variants, homographs."""

from pathlib import Path
from typing import Any

import pytest

from smart_dictionary.importers.db import connect
from smart_dictionary.seeds import builder, owner, store
from smart_dictionary.seeds.builder import Candidate, ListRow, Reference, WikiRow
from smart_dictionary.seeds.owner import OwnerRow, Variant

HEADER = "lemma,pos,kind,level,tags,variants,patterns,sources,original,flags\n"


def owner_row(
    lemma: str, *, kind: str | None = "word", variants: tuple[Variant, ...] = (), flags=()
) -> OwnerRow:
    return OwnerRow(
        lemma=lemma,
        pos=None,
        kind=kind,
        levels=[],
        tags=[],
        variants=list(variants),
        patterns=[],
        sources=["list.txt"],
        originals=[lemma],
        flags=list(flags),
    )


def wiki(word: str, pos: str, senses: int = 3, **extra: Any) -> WikiRow:
    extra["variant_of"] = tuple(extra.get("variant_of", ()))
    extra["tags"] = frozenset(extra.get("tags", ()))
    return WikiRow(word, pos, senses=senses, **extra)


REF = Reference(
    cefr={
        "house": [ListRow("cefrj", "house", "noun", "A1")],
        "may": [ListRow("cefrj", "may", "verb", "A1")],
        "these": [ListRow("cefrj", "these", "determiner", "A1")],
        "were": [ListRow("cefrj", "were", "verb", "A1")],
        "is": [ListRow("cefrj", "is", "verb", "A1")],
        "centre": [ListRow("cefrj", "centre", "noun", "A2")],
        "sports centre": [ListRow("cefrj", "sports centre", "noun", "A2")],
    },
    lists={"house": [ListRow("ngsl", "house")], "invoice": [ListRow("bsl", "invoice")]},
    wiki={
        "house": [
            wiki("house", "noun", 8),
            wiki("house", "verb", 5),
            wiki("house", "adjective", 1),
        ],
        "may": [wiki("May", "noun", 3), wiki("may", "verb", 8)],
        "catalog": [wiki("catalog", "noun", 0, variant_of=["catalogue"], tags=["US"])],
        "catalogue": [wiki("catalogue", "noun", 6)],
        "backward": [wiki("backward", "adjective", 5), wiki("backward", "adverb", 4)],
        "backwards": [wiki("backwards", "adverb", 3)],
        "centre": [wiki("centre", "noun", 0, variant_of=["center"], tags=["UK"])],
        "center": [wiki("center", "noun", 8)],
        "best seller": [wiki("best seller", "noun", 0, variant_of=["bestseller"])],
        "bestseller": [wiki("bestseller", "noun", 3)],
        "break the ice": [wiki("break the ice", "verb", 2, kind="idiom")],
        "invoice": [wiki("invoice", "noun", 3), wiki("invoice", "verb", 2)],
        "pole": [wiki("Pole", "noun", 2), wiki("pole", "noun", 6)],
        "be": [wiki("be", "verb", 8)],
        "is": [wiki("is", "verb", 1)],  # a nonstandard sense keeps a row of its own
    },
    zipf={"house": 5.71, "these": 6.5, "bestseller": 3.0, "invoice": 3.5, "be": 7.5},
    forms={"zebra": {"noun"}},
)


def build(*rows: OwnerRow) -> dict[tuple[str, str | None], Candidate]:
    return {(c.lemma, c.pos): c for c in builder.build(rows, REF)}


def test_parts_of_speech():
    # The CEFR lists first, then Wiktionary's strong ones: house the adjective is too thin.
    assert builder.parts_of_speech("house", REF) == ["noun", "verb"]
    assert builder.parts_of_speech("May", REF) == ["noun"]  # case picks the month...
    assert builder.parts_of_speech("may", REF) == ["verb"]  # ...or the modal
    assert builder.parts_of_speech("catalog", REF) == ["noun"]  # counts catalogue's senses
    assert builder.parts_of_speech("zebra", REF) == ["noun"]  # UniMorph, without Wiktionary
    assert builder.parts_of_speech("zzyzx", REF) == []


def test_owner_rows_get_a_candidate_per_part_of_speech():
    found = build(
        owner_row("house"),
        owner_row("backward", variants=(Variant("backwards", ("UK",)),)),
        owner_row("break the ice", kind=None),
        owner_row("zzyzx", flags=["spacing variant of zz yzx (merge if same POS)"]),
    )
    house = found[("house", "noun")]
    assert (house.tier, house.levels, house.zipf) == (0, {"A1"}, 5.71)
    assert house.sources == ["owner:list.txt", "cefrj", "ngsl"]
    assert found[("house", "verb")].sources == ["owner:list.txt", "ngsl"]
    # A regional spelling goes only where it applies: backwards is an adverb.
    assert found[("backward", "adverb")].variants == [Variant("backwards", ("UK",))]
    assert found[("backward", "adjective")].variants == []
    assert found[("break the ice", "verb")].kind == "idiom"  # its function, from Wiktionary
    # The normalizer's spacing note is recomputed per part of speech; none applies here.
    assert found[("zzyzx", None)].flags == ["no part of speech found"]


def test_open_lists_credit_candidates_and_add_what_the_owner_lacks():
    found = build(owner_row("house"), owner_row("center", variants=(Variant("centre", ("UK",)),)))
    center = found[("center", "noun")]
    assert (center.tier, center.levels, center.sources) == (0, {"A2"}, ["owner:list.txt", "cefrj"])
    assert found[("these", "determiner")].tier == 1
    assert found[("may", "verb")].tier == 1
    be = found[("be", "verb")]  # CEFR-J's "were" and "is" belong under their lemma
    assert (be.levels, be.sources) == ({"A1"}, ["cefrj"])
    assert ("is", "verb") not in found
    # The owner's centre -> center respells the open lists' compounds too.
    assert found[("sports center", "noun")].tier == 1
    assert ("sports centre", "noun") not in found
    invoice = found[("invoice", "noun")]
    assert (invoice.tier, invoice.sources) == (4, ["bsl"])
    assert ("invoice", "verb") not in found


def test_spacing_variants_fold_and_homographs_get_numbers():
    found = build(
        owner_row("bestseller"),
        owner_row("best seller"),
        owner_row("bow tie"),
        owner_row("bowtie"),
        owner_row("pole"),
        owner_row("Pole"),
    )
    assert ("best seller", "noun") not in found
    assert found[("bestseller", "noun")].variants == [Variant("best seller")]
    # Wiktionary says nothing about these two, so a person decides.
    note = "spacing variant of bowtie, same part of speech: merge?"
    assert found[("bow tie", None)].flags[-1] == note
    assert (found[("pole", "noun")].homograph, found[("Pole", "noun")].homograph) == (1, 2)


def test_read_owner_seeds(tmp_path: Path):
    path = tmp_path / "seeds.csv"
    path.write_text(
        HEADER
        + "color,,word,,,colour [UK],,list-1.txt | oxford.txt,color | colour,\n"
        + "break the ice,phrase,idiom,,,,,idioms.txt,break the ice,\n",
        encoding="utf-8",
    )
    color, ice = owner.read_owner_seeds(path)
    assert color.variants == [Variant("colour", ("UK",))]
    assert color.sources == ["list-1.txt", "oxford.txt"]
    assert (ice.pos, ice.kind) == ("phrase", "idiom")
    path.write_text(HEADER + "x,,word-ish,,,,,a.txt,x,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="kind"):
        owner.read_owner_seeds(path)
    path.write_text(HEADER + "x,,word,,,y [GB],,a.txt,x,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="region"):
        owner.read_owner_seeds(path)


def test_import_stores_candidates_and_keeps_decisions(migrated_database_url, tmp_path: Path):
    with connect() as conn:
        conn.execute(
            "insert into ref_datasets (id, name, version, license, attribution, source_url)"
            " values ('cefrj', 'x', 'x', 'x', 'x', 'x'), ('kaikki-en', 'x', 'x', 'x', 'x', 'x')"
        )
        conn.execute(
            "insert into ref_lists (dataset_id, lemma, lemma_norm, pos, level)"
            " values ('cefrj', 'house', 'house', 'noun', 'A1')"
        )
        conn.execute(
            "insert into ref_wiktionary (dataset_id, word, word_norm, pos, pos_raw, kind, glosses)"
            " values ('kaikki-en', 'house', 'house', 'verb', 'verb', 'word', '{a,b,c}'),"
            " ('kaikki-en', 'Pole', 'pole', 'noun', 'name', 'word', '{a}'),"
            " ('kaikki-en', 'pole', 'pole', 'noun', 'noun', 'word', '{a,b}')"
        )
    path = tmp_path / "seeds.csv"
    path.write_text(
        HEADER
        + "color,,word,,,colour [UK],,a.txt,color,\n"
        + "house,,word,,,,,a.txt,house,\n"
        + "pole,,word,,,,,a.txt,pole,\n"
        + "Pole,,word,,,,,a.txt,Pole,\n",
        encoding="utf-8",
    )

    def run() -> tuple[int, int]:
        with connect() as conn:
            candidates = builder.build(owner.read_owner_seeds(path), builder.load_reference(conn))
            return store.store(conn, candidates)

    assert run() == (5, 0)
    with connect() as conn:
        conn.execute("update seed_candidates set decision = 'accept' where lemma = 'house'")
    assert run() == (3, 2)  # a second import replaces the undecided rows and keeps the rest
    with connect() as conn:
        rows = conn.execute(
            "select lemma, pos, homograph, variants, tier, decision from seed_candidates"
        ).fetchall()
        exported = tmp_path / "out.csv"
        assert store.export(conn, exported) == 5
    assert ("house", "noun", 1, [], 0, "accept") in rows
    assert ("house", "verb", 1, [], 0, "accept") in rows
    assert ("pole", "noun", 1, [], 0, None) in rows and ("Pole", "noun", 2, [], 0, None) in rows
    assert ("color", None, 1, [{"form": "colour", "regions": ["UK"]}], 0, None) in rows
    assert "colour [UK]" in exported.read_text(encoding="utf-8")
