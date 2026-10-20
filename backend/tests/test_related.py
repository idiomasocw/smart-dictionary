"""Related words (content.related) and approvals at once (word_requests.auto_approve): a card
links only to entries a student can open, a link to a missing word becomes a suggestion, and
a listed word with a clear part of speech is approved without waiting for the owner."""

from typing import Any, LiteralString

import pytest
from psycopg.types.json import Jsonb

from factory_docs import go_doc, world
from smart_dictionary.ai.gateway import Gateway
from smart_dictionary.cli import main
from smart_dictionary.content.publish import publish
from smart_dictionary.core import tunables
from smart_dictionary.core.entrydoc import EntryDoc
from smart_dictionary.core.text import norm
from smart_dictionary.db.sync import connect
from smart_dictionary.factory import lease
from smart_dictionary.factory.queue import queue
from smart_dictionary.factory.submit import Submission, submit
from test_provisional import KETTLE, STUDENT, Model, kettle_world, lookup, row
from test_word_requests import admin, api, ask, pie_in_wiktionary


def rows(sql: LiteralString, *params: Any) -> list[tuple[Any, ...]]:
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def entry(
    conn: Any, key: str, lemma: str, pos: str, kind: str = "word", *, homograph: int = 1,
    status: str = "published", written: bool = True,
) -> None:  # fmt: skip
    """An approved entry, written (shown to students) unless `written` is false."""
    conn.execute(
        "insert into entries (key, language, lemma, lemma_norm, pos, kind, homograph, status,"
        " sources, doc, published_at, approved_at, approved_by) values (%s, 'en', %s, %s, %s,"
        " %s, %s, %s, array['owner:test.txt'], %s, case when %s then now() end, now(), 'seeds')",
        (key, lemma, norm(lemma), pos, kind, homograph, status, Jsonb({}) if written else None,
         written),
    )  # fmt: skip


def wiktionary(conn: Any, word: str, pos: str, kind: str = "word") -> None:
    conn.execute(
        "insert into ref_wiktionary (dataset_id, word, word_norm, pos, pos_raw, kind, glosses)"
        " values ('t-wiki', %s, %s, %s, %s, %s, '{A test sense.}')",
        (word, norm(word), pos, pos, kind),
    )


def listed(conn: Any, word: str, dataset: str = "ngsl", pos: str | None = None) -> None:
    conn.execute(
        "insert into ref_datasets (id, name, version, license, attribution, source_url)"
        " values (%s, %s, '1', 'test', 'test', 'file:///test') on conflict do nothing",
        (dataset, dataset),
    )
    conn.execute(
        "insert into ref_lists (dataset_id, lemma, lemma_norm, pos) values (%s, %s, %s, %s)",
        (dataset, word, norm(word), pos),
    )


def test_a_card_links_only_to_entries_a_student_can_open(
    migrated_database_url, anyio_backend_options
):
    with connect() as conn:
        world(conn)
        doc = go_doc()
        doc["related"] = [
            {"type": "see_also", "key": "en:by_the_way:phr"},  # seeded as an idiom
            {"type": "synonym", "key": "en:tv:n"},  # retired; its homograph is shown
            {"type": "family", "key": "en:ghost:n"},  # queued: nobody has written it
            {"type": "antonym", "key": "en:wait:adj"},  # the word, as another part of speech
            {"type": "see_also", "key": "en:the_last_straw:idiom"},  # listed without "the"
            {"type": "see_also", "key": "en:nothing_here:n"},  # no entry at all
            {"type": "see_also", "key": "en:go:v"},  # the entry itself
        ]
        publish(conn, EntryDoc.model_validate(doc), produced_by={"source": "test"})
        entry(conn, "en:by_the_way:idiom", "by the way", "phrase", "idiom")
        entry(conn, "en:tv:n", "tv", "noun", status="retired")
        entry(conn, "en:tv:n~2", "TV", "noun", homograph=2)
        entry(conn, "en:ghost:n", "ghost", "noun", status="queued", written=False)
        entry(conn, "en:wait:v", "wait", "verb")
        entry(conn, "en:last_straw:idiom", "last straw", "phrase", "idiom")
    with api(anyio_backend_options) as client:
        card = client.get("/v1/entries/en:go:v", headers=STUDENT)
        assert card.status_code == 200, card.text
        assert [(link["type"], link["key"]) for link in card.json()["related"]] == [
            ("see_also", "en:by_the_way:idiom"),
            ("synonym", "en:tv:n~2"),
            ("see_also", "en:last_straw:idiom"),
        ]
        # Once the routine writes "ghost", the card links it: the old ETag doesn't match.
        with connect() as conn:
            conn.execute("update entries set status = 'published', doc = '{}',"
                         " published_at = now() where key = 'en:ghost:n'")  # fmt: skip
        again = client.get("/v1/entries/en:go:v",
                           headers=STUDENT | {"If-None-Match": card.headers["etag"]})  # fmt: skip
        assert again.status_code == 200
        assert ("family", "en:ghost:n") in [(r["type"], r["key"]) for r in again.json()["related"]]


def test_links_to_missing_words_become_suggestions_when_an_entry_publishes(
    migrated_database_url, anyio_backend_options
):
    with connect() as conn:
        world(conn)  # "run into" is a seed nobody queued yet
        wiktionary(conn, "deal with", "verb", "phrasal_verb")  # in no list
        wiktionary(conn, "leave", "verb")
        listed(conn, "leave")
        conn.execute("insert into ref_freq (dataset_id, language, word, zipf)"
                     " values ('t-freq', 'en', 'leave', 5.0)")  # fmt: skip
        wiktionary(conn, "light", "noun")  # listed, but which part of speech?
        wiktionary(conn, "light", "adjective")
        listed(conn, "light")
        wiktionary(conn, "kill", "verb")  # blocked: never requested
        listed(conn, "kill")
        entry(conn, "en:wait:v", "wait", "verb")
        queue(conn, lemmas=["go"])
        batch = lease.lease(conn, 1)
        assert batch.id is not None
        doc = go_doc()
        doc["related"] = [
            {"type": "see_also", "key": "en:deal_with:phrv"},
            {"type": "antonym", "key": "en:leave:v"},
            {"type": "confusable", "key": "en:light:v"},
            {"type": "see_also", "key": "en:run_into:phrv"},
            {"type": "see_also", "key": "en:kill:v"},
            {"type": "synonym", "key": "en:recieve:v"},  # a typo
            {"type": "synonym", "key": "en:wait:adj"},  # "wait" has an entry
        ]
        _, results = submit(conn, batch.id, [Submission("en:go:v", doc, "pass")],
                            produced_by={}, threshold=0.7)  # fmt: skip
        assert [result.outcome for result in results] == ["published"]

    assert rows(
        "select lemma, origin, status, pos, kind::text, decided_by, entry_key from word_requests"
        " order by lemma"
    ) == [
        ("deal with", "related", "pending", "verb", "phrasal_verb", None, None),
        ("leave", "related", "approved", "verb", "word", "auto:lists", "en:leave:v"),
        ("light", "related", "pending", "noun", "word", None, None),
        ("run into", "related", "approved", "verb", "phrasal_verb", "auto:lists",
         "en:run_into:phrv"),
    ]  # fmt: skip
    # Nobody waits for them: their natural place in the queue, and no Beta now.
    assert row("select status::text, priority, approved_by from entries where key = 'en:leave:v'"
               ) == ("queued", 500, "auto:lists")  # fmt: skip
    assert row("select count(*) from misses") == (0,)
    # The seed nobody had queued is the new entry's.
    assert row(
        "select e.key from seed_candidates c join entries e on e.id = c.entry_id"
        " where c.lemma_norm = 'run into'"
    ) == ("en:run_into:phrv",)
    assert row("select note from word_requests where lemma = 'leave'") == (
        "auto: on ngsl; verb/word from the link, as Wiktionary has it",
    )

    with api(anyio_backend_options) as client:
        auth = admin()
        listing = client.get("/v1/admin/requests", params={"origin": "related"}, headers=auth)
        by_lemma = {item["lemma"]: item for item in listing.json()["items"]}
        assert set(by_lemma) == {"deal with", "light"}  # pending only
        assert by_lemma["deal with"]["linked_from"] == ["en:go:v see_also"]
        assert by_lemma["light"]["options"] == [{"pos": "noun", "kind": "word"},
                                                {"pos": "adjective", "kind": "word"}]  # fmt: skip
        stats = client.get("/v1/admin/stats", headers=auth).json()["requests"]
        assert (stats["pending_related"], stats["auto_approved"]) == (2, 2)


def test_a_listed_word_a_student_requests_is_approved_and_written_at_once(
    migrated_database_url, anyio_backend_options
):
    kettle_world(seeded=False)
    with connect() as conn:
        listed(conn, "kettle")
    model = Model(KETTLE)
    with api(anyio_backend_options, Gateway([model], 5.0)) as client:
        made = ask(client, "kettles")
        assert (made.status_code, made.json()["status"]) == (202, "requested")
        # Approved once the answer had gone, and AI wrote the Beta: the next search finds it.
        assert model.calls == 1
        [item] = lookup(client, "kettles")["groups"][0]["items"]
        assert (item["entry_key"], item["provisional"]) == ("en:kettle:n", True)
    assert row("select status, decided_by, note from word_requests") == (
        "approved", "auto:lists", "auto: on ngsl; noun/word from Wiktionary, its only one",
    )  # fmt: skip
    assert row("select priority, approved_by from entries where key = 'en:kettle:n'") == (
        100_000, "auto:lists",
    )  # fmt: skip


def test_with_the_setting_off_or_an_unclear_part_of_speech_a_request_waits(
    migrated_database_url, anyio_backend_options, capsys: pytest.CaptureFixture[str]
):
    kettle_world(seeded=False)
    pie_in_wiktionary()
    with connect() as conn:
        listed(conn, "kettle")
        listed(conn, "pie")
        wiktionary(conn, "pie", "verb")  # pie the noun, or to pie someone?
        conn.execute("insert into app_settings (name, value, updated_by)"
                     " values ('auto_approve_listed', 'false', 'lms')")  # fmt: skip
    tunables.forget()
    model = Model(KETTLE)
    with api(anyio_backend_options, Gateway([model], 5.0)) as client:
        assert ask(client, "kettle").status_code == 202
        with connect() as conn:
            conn.execute("delete from app_settings")
        tunables.forget()
        assert ask(client, "pie").status_code == 202
        assert model.calls == 0
    assert rows("select lemma, status from word_requests order by lemma") == [
        ("kettle", "pending"), ("pie", "pending"),
    ]  # fmt: skip

    # Later CEFR-J gives pie its part of speech; the sync applies the rule to what waits.
    with connect() as conn:
        listed(conn, "pie", "cefrj", "noun")
    main(["requests", "sync", "--dry-run"])
    assert "approved at once: kettle -> en:kettle:n" in capsys.readouterr().out
    assert row("select count(*) from word_requests where status = 'pending'") == (2,)
    main(["requests", "sync"])
    out = capsys.readouterr().out
    assert "approved at once: pie -> en:pie:n" in out and "2 approved at once" in out
    assert row("select note from word_requests where lemma = 'pie'") == (
        "auto: on cefrj, ngsl; noun/word from the lists and Wiktionary",
    )
    # A student waits for each, so their next search has AI write it.
    assert rows("select query_norm, status from misses order by 1") == [
        ("kettle", "pending"), ("pie", "pending"),
    ]  # fmt: skip


def test_the_sync_suggests_what_published_entries_link_to(
    migrated_database_url, capsys: pytest.CaptureFixture[str]
):
    with connect() as conn:
        world(conn)
        wiktionary(conn, "leave", "verb")
        listed(conn, "leave")
        doc = go_doc()
        doc["related"] = [{"type": "antonym", "key": "en:leave:v"}]
        publish(conn, EntryDoc.model_validate(doc), produced_by={"source": "fixtures"})
    main(["requests", "sync"])
    out = capsys.readouterr().out
    assert "1 published entries have related links" in out
    assert "approved at once: leave -> en:leave:v" in out
    assert row("select origin, status from word_requests") == ("related", "approved")
    main(["requests", "sync"])  # again: nothing new
    assert "0 new suggestions" in capsys.readouterr().out


def test_one_expression_takes_one_entry(migrated_database_url, anyio_backend_options):
    with connect() as conn:
        world(conn)
        entry(conn, "en:the_last_straw:idiom", "the last straw", "phrase", "idiom")
        for word in ("last", "straw"):
            conn.execute("insert into ref_freq (dataset_id, language, word, zipf)"
                         " values ('t-freq', 'en', %s, 4.5)", (word,))  # fmt: skip
        # A request from before lookups found the expression without its article.
        conn.execute(
            "insert into word_requests (lemma, lemma_norm, pos, kind, origin, students)"
            " values ('last straw', 'last straw', 'phrase', 'idiom', 'student', 1)"
        )
    with api(anyio_backend_options) as client:
        auth = admin()
        [request] = client.get("/v1/admin/requests", headers=auth).json()["items"]
        assert request["twins"] == ["en:the_last_straw:idiom"]
        decision = {"lemma": "last straw", "pos": "phrase", "kind": "idiom"}
        refused = client.post(f"/v1/admin/requests/{request['id']}/approve", json=decision,
                              headers=auth)  # fmt: skip
        assert refused.status_code == 409
        assert refused.json()["detail"].startswith("en:the_last_straw:idiom is the same")
        word = {"lemma": "last straw", "pos": "noun", "kind": "word"}
        added = client.post("/v1/admin/entries", json=word, headers=auth)
        assert added.status_code == 409
        # Two parts of speech of one plain headword are two entries on purpose.
        with connect() as conn:
            entry(conn, "en:a_few:det", "a few", "determiner")
        pronoun = {"lemma": "a few", "pos": "pronoun", "kind": "word"}
        assert client.post("/v1/admin/entries", json=pronoun, headers=auth).status_code == 201
        idiom = {"lemma": "a few", "pos": "phrase", "kind": "idiom"}
        assert client.post("/v1/admin/entries", json=idiom, headers=auth).status_code == 409
        # A search without the article finds it.
        found = lookup(client, "last straw")["provisional"]
        assert (found["status"], found["entry_key"]) == ("ready", "en:the_last_straw:idiom")
    assert rows("select key from entries order by key") == [
        ("en:a_few:det",), ("en:a_few:pron",), ("en:the_last_straw:idiom",),
    ]  # fmt: skip


def test_the_owner_turns_the_approval_at_once_off(migrated_database_url, anyio_backend_options):
    with api(anyio_backend_options) as client:
        auth = admin()
        changed = client.put("/v1/admin/settings/auto_approve_listed", json={"value": False},
                             headers=auth)  # fmt: skip
        assert changed.status_code == 200, changed.text
        body = changed.json()
        assert (body["kind"], body["value"], body["default"], body["minimum"]) == (
            "boolean", False, True, None,
        )  # fmt: skip
        with connect() as conn:
            assert tunables.overrides(conn)["auto_approve_listed"] is False
