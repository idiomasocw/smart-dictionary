"""Submit the auditors' answers (AUDIT.md step 4).

    python3 factory/bin/submit_audit.py --model claude-opus-5-5

Reads factory/work/audit/batch.json and work/audit/out/group-*.json, where each auditor saved
one item per entry: {key, rules: [...], findings: [...]}. The server records the findings for
the owner, applies high-confidence content fixes at once when the owner allows it, and marks
each entry audited. Writes work/audit/result.json and prints the outcome of each entry.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from _api import Api, ApiError, read_json, utf8_output, write_json
from pull_audit import AUDIT_WORK


def assemble(work: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """The lease, the answers to submit, and leased keys nobody answered."""
    batch = read_json(work / "batch.json")
    answers: list[dict[str, Any]] = []
    for path in sorted((work / "out").glob("group-*.json")):
        for item in read_json(path):
            answers.append({
                "key": item["key"],
                "rules": list(item.get("rules") or []),
                "findings": list(item.get("findings") or []),
            })  # fmt: skip
    answered = {answer["key"] for answer in answers}
    unanswered = [item["key"] for item in batch["items"] if item["key"] not in answered]
    return batch, answers, unanswered


def summary(results: list[dict[str, Any]], unanswered: list[str]) -> list[str]:
    audited = [result for result in results if result["outcome"] == "audited"]
    lines = [
        f"audited {len(audited)}, findings {sum(r['findings'] for r in audited)}, "
        f"applied at once {sum(r['applied'] for r in audited)}, "
        f"not leased {len(results) - len(audited)}, not answered {len(unanswered)}"
    ]
    for result in results:
        if result["findings"] or result["errors"] or result["outcome"] != "audited":
            lines.append(
                f"{result['outcome']:<10} {result['key']} ({result['findings']} findings,"
                f" {result['applied']} applied)"
            )
        for error in result["errors"]:
            lines.append(f"      not recorded: {error}")
    for key in unanswered:
        lines.append(f"{'not answered':<10} {key} (due again in the next run)")
    return lines


def submit(api: Api, model: str | None, work: Path) -> int:
    batch, answers, unanswered = assemble(work)
    if not answers:
        print("Nothing was answered; the entries are due again when the lease expires.")
        return 1
    payload = {"model": model, "audit_version": batch["audit_version"], "entries": answers}
    answer = api.call("POST", f"/v1/audit/batches/{batch['batch_id']}/results", payload, retries=2)
    write_json(work / "result.json", {**answer, "not_answered": unanswered})
    print(f"Audit {batch['batch_id']} is submitted.")
    print("\n".join(summary(answer["results"], unanswered)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", help="the model that audited the entries")
    parser.add_argument("--work", type=Path, default=AUDIT_WORK)
    args = parser.parse_args(argv)
    utf8_output()
    try:
        return submit(Api(token_env="AUDIT_TOKEN"), args.model, args.work)
    except ApiError as error:
        print(f"Submit failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
