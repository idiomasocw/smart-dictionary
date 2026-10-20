"""Word requests (design §6.7): a real word nobody seeded waits for the owner, who approves or
rejects it in the console. And what students see (design §3.3): only approved entries, and a
Beta entry stays shown while the routine rewrites it."""

from typing import Any

from alembic import command
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from factory_docs import GO_DOC, go_doc
from smart_dictionary.ai.gateway import Gateway, get_gateway
from smart_dictionary.api.app import create_app
from smart_dictionary.cli import alembic_config
from smart_dictionary.content.word_requests import headwords
from smart_dictionary.core import tunables
from smart_dictionary.db.sync import connect
from smart_dictionary.factory import lease, tokens
from smart_dictionary.factory.submit import Submission, submit
from test_provisional import KETTLE, STUDENT, Model, kettle_world, lookup, row, seed

OTHER_STUDENT = {"x-bbb-ext-user": "person-2"}
PERSON = "3f2c9a0e-5b7d-4c1a-9e2f-8d6b1a4c7e90"


def api(options: dict, gateway: Gateway | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_gateway] = lambda: gateway or Gateway([], 5.0)
    return TestClient(app, backend_options=options)


def ask(client: TestClient, q: str, headers: dict[str, str] = STUDENT, **body: Any) -> Any:
    return client.post("/v1/requests", json={"q": q, **body}, headers=headers)


def admin(name: str = "lms") -> dict[str, str]:
    with connect() as conn:
        token = tokens.create(conn, name, ["admin"])
    return {"Authorization": f"Bearer {token}", "X-Admin-Actor": PERSON}


def pie_in_wiktionary() -> None:
    with connect() as conn:
        conn.execute(
            "insert into ref_freq (dataset_id, language, word, zipf) values ('t-freq', 'en',"
            " 'pie', 4.2)"
        )
        conn.execute(
            "insert into ref_wiktionary (dataset_id, word, word_norm, pos, pos_raw, kind, glosses)"
            " values ('t-wiki', 'pie', 'pie', 'noun', 'noun', 'word', '{A baked dish.}')"
        )


def test_a_student_requests_a_real_word_once_within_a_daily_limit(
    migrated_database_url, anyio_backend_options
):
    kettle_world(seeded=False)
    pie_in_wiktionary()
    with connect() as conn:
        conn.execute("insert into ref_freq (dataset_id, language, word, zipf)"
                     " values ('t-freq', 'en', 'recieve', 2.75)")  # fmt: skip
    with api(anyio_backend_options) as client:
        made = ask(client, "Kettles!")
        assert made.status_code == 202, made.text
        assert made.json() == {"status": "requested", "headword": "kettle", "entry_key": None}
        assert lookup(client, "kettles")["provisional"]["status"] == "requested"
        other = client.get("/v1/lookup", params={"q": "kettle"}, headers=OTHER_STUDENT).json()
        assert other["provisional"]["status"] == "requestable"
        # Asking again costs nothing and counts once.
        assert ask(client, "kettle").status_code == 202
        assert ask(client, "kettle", OTHER_STUDENT).status_code == 202
        assert row("select students from word_requests where lemma_norm = 'kettle'") == (2,)

        # The owner allows one request a day: person-1 has used it.
        with connect() as conn:
            conn.execute("insert into app_settings (name, value, updated_by)"
                         " values ('word_requests_per_day', '1', 'lms')")  # fmt: skip
        tunables.forget()
        # A refusal is an answer, not an error (the plugin's console stays clean).
        refused = ask(client, "pie")
        assert (refused.status_code, refused.json()["status"]) == (200, "quota_exceeded")
        assert row("select count(*) from word_requests where lemma_norm = 'pie'") == (0,)

        # Typos and nonsense can't be requested, and nobody anonymous can request at all.
        for typo in ("recieve", "zzqxv"):
            wrong = ask(client, typo)
            assert (wrong.status_code, wrong.json()["status"]) == (200, "not_requestable")
        anonymous = ask(client, "pie", headers={})
        assert (anonymous.status_code, anonymous.json()["status"]) == (200, "no_student")
    votes = row("select array_agg(query order by query) from word_request_votes")
    assert votes == (["kettle", "kettles"],)  # the searches, never a name


def test_a_word_that_exists_needs_no_request(migrated_database_url, anyio_backend_options):
    kettle_world()  # seeded: AI writes it on the next search
    with api(anyio_backend_options) as client:
        exists = ask(client, "kettles")
        assert exists.status_code == 200
        assert exists.json() == {"status": "exists", "headword": None, "entry_key": "en:kettle:n"}
    assert row("select count(*) from word_requests") == (0,)


def test_approving_a_request_queues_the_word_and_ai_writes_it(
    migrated_database_url, anyio_backend_options
):
    kettle_world(seeded=False)
    model = Model(KETTLE)
    with api(anyio_backend_options, Gateway([model], 5.0)) as client:
        assert ask(client, "kettles").status_code == 202
        auth = admin()
        queue = client.get("/v1/admin/requests", headers=auth).json()
        [request] = queue["items"]
        assert (request["lemma"], request["origin"]) == ("kettle", "student")
        assert request["students"] == 1
        assert request["queries"] == ["kettles"] and request["zipf"] == 3.4
        assert request["options"] == [{"pos": "noun", "kind": "word"}]
        assert model.calls == 0  # nothing written before the owner decides

        decision = {"lemma": "kettle", "pos": "noun", "kind": "word", "note": "useful"}
        approved = client.post(f"/v1/admin/requests/{request['id']}/approve", json=decision,
                               headers=auth)  # fmt: skip
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"
        assert approved.json()["entry_key"] == "en:kettle:n"
        assert approved.json()["decided_by"] == f"lms:{PERSON}"
        # AI wrote its Beta right after the decision: the student finds it.
        assert model.calls == 1
        [item] = lookup(client, "kettles")["groups"][0]["items"]
        assert (item["entry_key"], item["provisional"]) == ("en:kettle:n", True)

        again = client.post(f"/v1/admin/requests/{request['id']}/approve", json=decision,
                            headers=auth)  # fmt: skip
        assert again.status_code == 409
    assert row("select priority, approved_by, sources from entries where key = 'en:kettle:n'") == (
        100_000, f"lms:{PERSON}", ["request"],
    )  # fmt: skip
    assert row("select action, actor, target from admin_audit") == (
        "request.approve", PERSON, "kettle",
    )  # fmt: skip


def test_a_rejected_word_is_closed_for_good(migrated_database_url, anyio_backend_options):
    kettle_world(seeded=False)
    pie_in_wiktionary()
    with api(anyio_backend_options) as client:
        assert ask(client, "pie").status_code == 202
        auth = admin()
        [request] = client.get("/v1/admin/requests", headers=auth).json()["items"]
        rejected = client.post(f"/v1/admin/requests/{request['id']}/reject",
                               json={"note": "not for learners"}, headers=auth)  # fmt: skip
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"
        assert lookup(client, "pie")["provisional"]["status"] == "invalid"
        other = client.get("/v1/lookup", params={"q": "pie"}, headers=OTHER_STUDENT).json()
        assert other["provisional"]["status"] == "invalid"
        declined = ask(client, "pie", OTHER_STUDENT)
        assert (declined.status_code, declined.json()["status"]) == (200, "not_requestable")
        decision = {"lemma": "pie", "pos": "noun", "kind": "word"}
        late = client.post(f"/v1/admin/requests/{request['id']}/approve", json=decision,
                           headers=auth)  # fmt: skip
        assert late.status_code == 409
        pending = client.get("/v1/admin/requests", headers=auth).json()
        assert pending["total"] == 0  # the queue shows what waits for you
    assert row("select count(*) from entries") == (0,)


def test_ai_entries_from_before_reviews_wait_hidden_for_the_owner(
    scratch_database_url, anyio_backend_options
):
    config = alembic_config()
    command.upgrade(config, "0011")
    with connect() as conn:
        # Before reviews: AI wrote "go" for a student, with no seed behind it, and the owner
        # seeded "kettle" (linked from its seed candidate).
        ai_entry = conn.execute(
            "insert into entries (key, language, lemma, lemma_norm, pos, kind, status, doc,"
            " sources, published_at) values ('en:go:v', 'en', 'go', 'go', 'verb', 'word',"
            " 'provisional', %s, array['provisional'], now()) returning id",
            (Jsonb(GO_DOC),),
        ).fetchone()
        assert ai_entry is not None
        conn.execute("insert into entry_forms (entry_id, form, form_norm, source)"
                     " values (%s, 'go', 'go', 'doc')", (ai_entry[0],))  # fmt: skip
        seeded = conn.execute(
            "insert into entries (key, language, lemma, lemma_norm, pos, kind, status, sources)"
            " values ('en:kettle:n', 'en', 'kettle', 'kettle', 'noun', 'word', 'queued',"
            " array['owner:list.txt']) returning id"
        ).fetchone()
        assert seeded is not None
        conn.execute(
            "insert into seed_candidates (lemma, lemma_norm, pos, kind, sources, entry_id,"
            " originals) values ('kettle', 'kettle', 'noun', 'word', array['owner:list.txt'],"
            " %s, array['kettle'])",
            (seeded[0],),
        )
        conn.execute("insert into misses (tenant_id, language, query_norm, status, entry_key)"
                     " values ('ocw', 'en', 'went', 'provisional', 'en:go:v')")  # fmt: skip
    command.upgrade(config, "head")

    assert row("select key from entries where approved_at is null") == ("en:go:v",)
    assert row("select approved_by from entries where key = 'en:kettle:n'") == ("seeds",)
    assert row("select lemma, origin, status, entry_key from word_requests") == (
        "go", "ai_entry", "pending", "en:go:v",
    )  # fmt: skip
    assert row("select status, headword from misses") == ("requestable", "go")

    with api(anyio_backend_options) as client:
        # Hidden from students, and the routine won't expand it, until the owner decides.
        assert lookup(client, "go")["groups"] == []
        assert client.get("/v1/entries/en:go:v", headers=STUDENT).status_code == 404
        with connect() as conn:
            assert lease.lease(conn, 10).entry_ids == [seeded[0]]
        auth = admin()
        [request] = client.get("/v1/admin/requests", headers=auth).json()["items"]
        assert request["definition"] == GO_DOC["senses"][0]["definition"]
        decision = {"lemma": "go", "pos": "verb", "kind": "word"}
        approved = client.post(f"/v1/admin/requests/{request['id']}/approve", json=decision,
                               headers=auth)  # fmt: skip
        assert approved.status_code == 200, approved.text
        # Shown again at once, as it was.
        [item] = lookup(client, "go")["groups"][0]["items"]
        assert (item["entry_key"], item["provisional"]) == ("en:go:v", False)
        assert client.get("/v1/entries/en:go:v", headers=STUDENT).status_code == 200
    assert row("select status, entry_key from misses where query_norm = 'went'") == (
        "provisional", "en:go:v",
    )  # fmt: skip
    command.downgrade(config, "0011")


def test_a_beta_entry_stays_shown_while_the_routine_rewrites_it(
    migrated_database_url, anyio_backend_options
):
    kettle_world()
    bad = go_doc()
    bad["senses"][0]["examples"] = bad["senses"][0]["examples"][:1]  # V11 fails
    with api(anyio_backend_options, Gateway([Model(KETTLE)], 5.0)) as client:
        lookup(client, "kettles")  # AI writes the Beta
        with connect() as conn:
            batch = lease.lease(conn, 10)
        assert batch.id is not None
        assert row("select status::text from entries where key = 'en:kettle:n'") == ("generating",)
        # Being rewritten: still found, still a Beta, and its card opens.
        [item] = lookup(client, "kettles")["groups"][0]["items"]
        assert (item["entry_key"], item["provisional"]) == ("en:kettle:n", True)
        assert client.get("/v1/entries/en:kettle:n", headers=STUDENT).status_code == 200

        # The rewrite failed its checks and went back for repair: the Beta still shows.
        broken = {**bad, "key": "en:kettle:n", "lemma": "kettle", "pos": "noun"}
        with connect() as conn:
            submit(conn, batch.id, [Submission("en:kettle:n", broken)], produced_by={},
                   threshold=0.7)  # fmt: skip
        assert row("select status::text from entries where key = 'en:kettle:n'") == (
            "needs_repair",
        )  # fmt: skip
        assert lookup(client, "kettles")["groups"][0]["items"][0]["entry_key"] == "en:kettle:n"
        card = client.get("/v1/entries/en:kettle:n", headers=STUDENT)
        assert card.status_code == 200 and card.json()["provisional"] is True


def test_the_owner_adds_retires_and_requeues_entries(migrated_database_url, anyio_backend_options):
    kettle_world(seeded=False)
    with connect() as conn:  # by the way, as a phrase; and a quarantined A1 verb
        seed(conn, "en:by_the_way:phr", "by the way", "phrase", kind="phrase")
        seed(conn, "en:kill:v", "kill", "verb", status="quarantined")
        conn.execute("update entries set attempts = 2 where key = 'en:kill:v'")
    with api(anyio_backend_options) as client:
        auth = admin()
        assert ask(client, "kettles").status_code == 202  # a student asked first
        word = {"lemma": "kettle", "pos": "noun", "kind": "word"}
        added = client.post("/v1/admin/entries", json=word, headers=auth)
        assert added.status_code == 201, added.text
        body = added.json()
        assert (body["key"], body["status"], body["approved"], body["priority"]) == (
            "en:kettle:n", "queued", True, 100_000,
        )  # fmt: skip
        # The student's request is answered by it.
        assert row("select status, entry_key from word_requests") == ("approved", "en:kettle:n")
        assert client.post("/v1/admin/entries", json=word, headers=auth).status_code == 409
        idiom = {"lemma": "by the way", "pos": "phrase", "kind": "idiom"}
        twin = client.post("/v1/admin/entries", json=idiom, headers=auth)
        assert (twin.status_code, twin.json()["detail"]) == (
            409, "en:by_the_way:phr is the same expression",
        )  # fmt: skip

        retired = client.post("/v1/admin/entries/en:kettle:n/retire", json={"note": "dup"},
                              headers=auth)  # fmt: skip
        assert retired.status_code == 200 and retired.json()["status"] == "retired"
        assert lookup(client, "kettles")["provisional"]["status"] == "invalid"  # not requestable
        assert client.post("/v1/admin/entries/en:kettle:n/retire", json={},
                           headers=auth).status_code == 409  # fmt: skip

        requeued = client.post("/v1/admin/entries/en:kill:v/requeue", json={}, headers=auth)
        assert requeued.status_code == 200, requeued.text
        assert (requeued.json()["status"], requeued.json()["attempts"]) == ("queued", 0)
        assert client.post("/v1/admin/entries/en:kill:v/requeue", json={},
                           headers=auth).status_code == 409  # fmt: skip
        assert client.post("/v1/admin/entries/en:none:n/retire", json={},
                           headers=auth).status_code == 404  # fmt: skip
    actions = row("select array_agg(action order by id) from admin_audit")
    assert actions == (["entry.add", "entry.retire", "entry.requeue"],)


def test_each_refusal_answers_200_and_writes_nothing(migrated_database_url, anyio_backend_options):
    kettle_world(seeded=False)
    pie_in_wiktionary()
    with connect() as conn:  # the owner declined "pie" earlier
        conn.execute("insert into word_requests (lemma, lemma_norm, origin, status)"
                     " values ('pie', 'pie', 'student', 'rejected')")  # fmt: skip
    cases = [
        ("no language path", {"q": "kettle", "dir": "l1", "variety": "pt-BR"}, STUDENT,
         "not_requestable"),
        ("look it up first", {"q": "tetera", "dir": "l1"}, STUDENT, "not_requestable"),
        ("declined", {"q": "pie"}, STUDENT, "not_requestable"),
        ("no student", {"q": "kettle"}, {}, "no_student"),
    ]  # fmt: skip
    with api(anyio_backend_options) as client:
        for name, body, headers, status in cases:
            answer = client.post("/v1/requests", json=body, headers=headers)
            assert answer.status_code == 200, (name, answer.text)
            assert answer.json() == {"status": status, "headword": None, "entry_key": None}, name
        # Today's limit: none left.
        with connect() as conn:
            conn.execute("insert into app_settings (name, value, updated_by)"
                         " values ('word_requests_per_day', '0', 'lms')")  # fmt: skip
        tunables.forget()
        limited = client.post("/v1/requests", json={"q": "kettle"}, headers=STUDENT)
        assert (limited.status_code, limited.json()["status"]) == (200, "quota_exceeded")
    assert row("select count(*) from word_requests where lemma_norm <> 'pie'") == (0,)
    assert row("select count(*) from word_request_votes") == (0,)


def test_the_openapi_shows_both_answers(migrated_database_url):
    spec = create_app().openapi()
    responses = spec["paths"]["/v1/requests"]["post"]["responses"]
    for code in ("200", "202"):
        schema = responses[code]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/RequestOut"}
    status = spec["components"]["schemas"]["RequestOut"]["properties"]["status"]
    assert status["enum"] == ["requested", "exists", "quota_exceeded", "not_requestable",
                              "no_student"]  # fmt: skip


def test_a_long_idiom_can_be_requested(migrated_database_url, anyio_backend_options):
    kettle_world(seeded=False)
    with connect() as conn:
        for word in ("hit", "the", "nail", "on", "head"):
            conn.execute("insert into ref_freq (dataset_id, language, word, zipf)"
                         " values ('t-freq', 'en', %s, 4.5)", (word,))  # fmt: skip
        conn.execute(
            "insert into ref_wiktionary (dataset_id, word, word_norm, pos, pos_raw, kind, glosses)"
            " values ('t-wiki', 'hit the nail on the head', 'hit the nail on the head', 'verb',"
            " 'verb', 'idiom', '{To be exactly right.}')"
        )
    with api(anyio_backend_options) as client:
        found = lookup(client, "Hit the nail on the head!")["provisional"]
        assert (found["status"], found["headword"]) == ("requestable", "hit the nail on the head")
        made = ask(client, "Hit the nail on the head")
        assert made.status_code == 202, made.text
        assert made.json()["headword"] == "hit the nail on the head"
        # Longer than any seeded expression: not a headword.
        assert lookup(client, " ".join(["nail"] * 11))["provisional"]["status"] == "invalid"
    assert row("select pos, kind::text, origin from word_requests") == (
        "phrase", "idiom", "student",
    )  # fmt: skip


def test_a_search_finds_an_expression_with_or_without_its_article():
    assert headwords("last straw") == ["last straw", "the last straw", "a last straw"]
    assert headwords("open secret") == ["open secret", "the open secret", "an open secret"]
    assert headwords("the last straw") == ["the last straw", "last straw"]
    assert headwords("a few") == ["a few"]  # one word would be left
    assert headwords("geese") == ["geese", "goose"]


def test_seeds_left_without_their_entry_find_it(scratch_database_url):
    config = alembic_config()
    command.upgrade(config, "0012")
    with connect() as conn:
        # "raise" was seeded, but a Beta AI wrote for a search existed first: `seeds queue`
        # skipped the key, so 0012 took the Beta for a word nobody chose.
        beta = conn.execute(
            "insert into entries (key, language, lemma, lemma_norm, pos, kind, status, sources,"
            " published_at) values ('en:raise:v', 'en', 'raise', 'raise', 'verb', 'word',"
            " 'provisional', array['provisional'], now()) returning id"
        ).fetchone()
        assert beta is not None
        conn.execute(
            "insert into seed_candidates (lemma, lemma_norm, pos, kind, sources, originals)"
            " values ('raise', 'raise', 'verb', 'word', array['owner:list.txt'], array['raise'])"
        )
        conn.execute(
            "insert into word_requests (lemma, lemma_norm, pos, kind, origin, entry_key)"
            " values ('raise', 'raise', 'verb', 'word', 'ai_entry', 'en:raise:v')"
        )
    command.upgrade(config, "head")
    assert row("select approved_by from entries") == ("seeds",)
    assert row("select entry_id = %s, decision from seed_candidates", beta[0]) == (True, "accept")
    assert row("select status, decided_by from word_requests") == ("approved", "seeds")
    command.downgrade(config, "0012")
