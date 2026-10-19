"""Check written entries with the server's own checks, saving nothing (ROUTINE.md step 2).

    python3 factory/bin/validate.py factory/work/out/group-1.json [more files]

Each file holds a JSON list of {"key": ..., "doc": ...}. Prints every entry's errors and
warnings, and exits 1 if any entry has an error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from _api import Api, ApiError, read_json, utf8_output


def report(results: list[dict[str, Any]]) -> list[str]:
    lines = []
    for result in results:
        errors, warnings = result["errors"], result["warnings"]
        score = "-" if result["score"] is None else f"{result['score']:.2f}"
        if not errors and not warnings:
            lines.append(f"OK    {result['key']} (score {score})")
            continue
        state = "ERROR" if errors else "WARN "
        lines.append(
            f"{state} {result['key']} (score {score}): {len(errors)} errors, "
            f"{len(warnings)} warnings"
        )
        for issue in errors + warnings:
            lines.append(
                f"      {issue['severity']:<5} {issue['check']:<6} {issue['where']}: "
                f"{issue['message']}"
            )
    return lines


def check(api: Api, files: list[Path]) -> int:
    entries: list[dict[str, Any]] = []
    for path in files:
        written = read_json(path)
        if not isinstance(written, list):
            raise SystemExit(f"{path}: expected a JSON list of {{key, doc}} objects")
        entries.extend({"key": item["key"], "doc": item["doc"]} for item in written)
    if not entries:
        print("No entries to check.")
        return 0
    results: list[dict[str, Any]] = []
    for start in range(0, len(entries), 100):
        chunk = entries[start : start + 100]
        answer = api.call("POST", "/v1/factory/validate", {"entries": chunk}, retries=2)
        results.extend(answer["results"])
    print("\n".join(report(results)))
    failing = sum(1 for result in results if result["errors"])
    print(f"{len(results) - failing} of {len(results)} entries have no errors.")
    return 1 if failing else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args(argv)
    utf8_output()
    try:
        return check(Api(), args.files)
    except ApiError as error:
        print(f"Check failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
