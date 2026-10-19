"""The routine's batch scripts (factory/bin), run against the real API in-process."""

import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_docs import GO_DOC, go_doc, world
from smart_dictionary.api.app import create_app
from smart_dictionary.db.sync import connect
from smart_dictionary.factory import tokens
from smart_dictionary.factory.queue import queue
from smart_dictionary.paths import REPO_DIR

sys.path.insert(0, str(REPO_DIR / "factory" / "bin"))
client_module = importlib.import_module("_api")
pull_batch = importlib.import_module("pull_batch")
check_script = importlib.import_module("validate")
submit_batch = importlib.import_module("submit_batch")

BASE = "http://dictionary.test"


def through(client: TestClient) -> Any:
    """Send the scripts' HTTP calls straight into the app."""

    def send(method: str, url: str, body: Any, headers: dict[str, str]) -> tuple[int, Any]:
        response = client.request(method, url.removeprefix(BASE), json=body, headers=headers)
        return response.status_code, response.json()

    return send


def write(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def test_groups_keep_the_queue_order_and_near_equal_sizes():
    sizes = [len(group) for group in pull_batch.split(list(range(10)), 4)]
    assert sizes == [3, 3, 2, 2]
    assert pull_batch.split(list(range(10)), 4)[0] == [0, 1, 2]
    assert [len(group) for group in pull_batch.split([1, 2, 3], 4)] == [1, 1, 1]


def test_a_batch_runs_end_to_end_through_the_scripts(
    migrated_database_url, anyio_backend_options, tmp_path, capsys
):
    with connect() as conn:
        world(conn)
        queue(conn, lemmas=["go", "realize"])
        token = tokens.create(conn, "routine", ["factory"])
    with TestClient(create_app(), backend_options=anyio_backend_options) as client:
        api = client_module.Api(base=BASE, token=token, send=through(client))

        assert pull_batch.pull(api, 5, 2, "test run", tmp_path) == 0
        groups = sorted(path.name for path in (tmp_path / "groups").glob("*.json"))
        assert groups == ["group-1.json", "group-2.json"]
        [pack] = json.loads((tmp_path / "groups" / "group-1.json").read_text(encoding="utf-8"))
        assert pack["key"] == "en:go:v"

        # A writer's first draft fails the checks; the fixed one passes.
        out = tmp_path / "out" / "group-1.json"
        draft = go_doc()
        draft["senses"][0]["examples"] = draft["senses"][0]["examples"][:1]
        write(out, [{"key": "en:go:v", "doc": draft}])
        assert check_script.check(api, [out]) == 1
        write(out, [{"key": "en:go:v", "doc": GO_DOC}])
        assert check_script.check(api, [out]) == 0

        write(tmp_path / "review" / "group-1.json", [{"key": "en:go:v", "verdict": "pass"}])
        assert submit_batch.submit(api, "test-model", tmp_path) == 0

    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert [(item["key"], item["outcome"]) for item in result["results"]] == [
        ("en:go:v", "published")
    ]
    # Nobody wrote realize: the batch keeps it until the lease ends.
    assert result["not_written"] == ["en:realize:v"]
    assert result["status"] == "leased"
    printed = capsys.readouterr().out
    assert "ERROR en:go:v" in printed and "OK    en:go:v" in printed
    assert "published 1" in printed and "not written 1" in printed
    with connect() as conn:
        produced = conn.execute("select produced_by from entry_revisions").fetchone()
    assert produced is not None
    assert produced[0]["model"] == "test-model"
    assert produced[0]["prompt"].startswith("instructions-")


def test_pull_refuses_to_overwrite_an_unsubmitted_batch(tmp_path):
    write(tmp_path / "batch.json", {"batch_id": "x", "entries": []})
    write(tmp_path / "out" / "group-1.json", [])
    with pytest.raises(SystemExit):
        pull_batch.prepare(tmp_path, discard=False)
    pull_batch.prepare(tmp_path, discard=True)
    assert not (tmp_path / "batch.json").exists() and not (tmp_path / "out").exists()


def test_api_errors_carry_the_problem_and_retries_ride_out_a_restart(monkeypatch):
    def refused(method: str, url: str, body: Any, headers: dict[str, str]) -> tuple[int, Any]:
        return 401, {"code": "unauthorized", "title": "Missing or invalid factory token"}

    with pytest.raises(client_module.ApiError, match="401 unauthorized: Missing or invalid"):
        client_module.Api(base=BASE, token="t", send=refused).call("POST", "/v1/factory/batches")

    monkeypatch.setattr(client_module.time, "sleep", lambda seconds: None)
    answers = iter([(503, {"title": "restarting"}), (200, {"ok": True})])
    api = client_module.Api(base=BASE, token=None, send=lambda *args: next(answers))
    assert api.call("POST", "/v1/factory/validate", {}, retries=2) == {"ok": True}


def test_the_instructions_version_changes_with_any_instruction(tmp_path):
    for name in submit_batch.INSTRUCTIONS:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_DIR / "factory" / name, target)
    before = submit_batch.prompt_version(tmp_path)
    assert before == submit_batch.prompt_version(tmp_path)
    style = tmp_path / "STYLE.md"
    style.write_text(style.read_text(encoding="utf-8") + "\nOne more rule.\n", encoding="utf-8")
    assert submit_batch.prompt_version(tmp_path) != before
