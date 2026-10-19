"""Submit the batch's entries with the reviewers' verdicts (ROUTINE.md step 5).

    python3 factory/bin/submit_batch.py --model claude-sonnet-5-5

Reads factory/work/batch.json, work/out/group-*.json (the writers' documents) and
work/review/group-*.json (the reviewers' verdicts). The server checks every entry again and
decides: published, sent back for repair, or quarantined. Writes work/result.json and prints
the outcome of each entry.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from _api import FACTORY_DIR, WORK, Api, ApiError, read_json, utf8_output, write_json

# Every entry records which instructions produced it.
INSTRUCTIONS = ["ROUTINE.md", "STYLE.md", "rubric.md", "schema/entry.v1.json"]


def prompt_version(factory_dir: Path = FACTORY_DIR) -> str:
    digest = hashlib.sha256()
    for name in INSTRUCTIONS:
        digest.update((factory_dir / name).read_bytes())
    return "instructions-" + digest.hexdigest()[:12]


def assemble(work: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """The batch, the entries to submit (each with its review), and leased keys nobody wrote."""
    batch = read_json(work / "batch.json")
    reviews: dict[str, dict[str, Any]] = {}
    for path in sorted((work / "review").glob("group-*.json")):
        for review in read_json(path):
            reviews[review["key"]] = review
    entries: list[dict[str, Any]] = []
    for path in sorted((work / "out").glob("group-*.json")):
        for item in read_json(path):
            entry: dict[str, Any] = {"key": item["key"], "doc": item["doc"]}
            review = reviews.get(item["key"])
            if review:
                entry["review"] = {
                    "verdict": review["verdict"],
                    "notes": list(review.get("notes") or [])[:20],
                }
                entry["needs_repair"] = bool(review.get("needs_repair"))
            entries.append(entry)
    written = {entry["key"] for entry in entries}
    unwritten = [pack["key"] for pack in batch["entries"] if pack["key"] not in written]
    return batch, entries, unwritten


def summary(results: list[dict[str, Any]], unwritten: list[str]) -> list[str]:
    counts = Counter(result["outcome"] for result in results)
    lines = [
        f"published {counts['published']}, needs repair {counts['needs_repair']}, "
        f"quarantined {counts['quarantined']}, not leased {counts['not_leased']}, "
        f"not written {len(unwritten)}"
    ]
    for result in results:
        score = "-" if result["score"] is None else f"{result['score']:.2f}"
        lines.append(f"{result['outcome']:<12} {result['key']} (score {score})")
        if result["outcome"] != "published":
            for issue in result["errors"] + result["warnings"]:
                lines.append(f"      {issue['severity']:<5} {issue['check']:<6} {issue['message']}")
    for key in unwritten:
        lines.append(f"{'not written':<12} {key} (back in the queue when the lease expires)")
    return lines


def submit(api: Api, model: str | None, work: Path) -> int:
    batch, entries, unwritten = assemble(work)
    if not entries:
        print("Nothing was written; the batch's entries return to the queue when it expires.")
        return 1
    payload = {"model": model, "prompt_version": prompt_version(), "entries": entries}
    path = f"/v1/factory/batches/{batch['batch_id']}/entries"
    answer = api.call("POST", path, payload, retries=2)
    write_json(work / "result.json", {**answer, "not_written": unwritten})
    print(f"Batch {batch['batch_id']} is now {answer['status']}.")
    print("\n".join(summary(answer["results"], unwritten)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", help="the model that wrote the entries")
    parser.add_argument("--work", type=Path, default=WORK)
    args = parser.parse_args(argv)
    utf8_output()
    try:
        return submit(Api(), args.model, args.work)
    except ApiError as error:
        print(f"Submit failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
