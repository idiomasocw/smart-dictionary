"""Lease the next batch and split it into groups for the writers (ROUTINE.md step 1).

    python3 factory/bin/pull_batch.py --size 40 --groups 4 --label "2026-10-19 run 1"

Writes factory/work/batch.json (the whole lease) and factory/work/groups/group-N.json (the
grounding packs for writer N, in queue order). Exits 2 when nothing is left to lease.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

from _api import WORK, Api, ApiError, utf8_output, write_json


def split(packs: list[dict[str, Any]], groups: int) -> list[list[dict[str, Any]]]:
    """Consecutive slices of nearly equal size, so each group keeps the queue order."""
    groups = max(1, min(groups, len(packs)))
    size, extra = divmod(len(packs), groups)
    slices, start = [], 0
    for index in range(groups):
        end = start + size + (1 if index < extra else 0)
        slices.append(packs[start:end])
        start = end
    return slices


def prepare(work: Path, discard: bool) -> None:
    """Refuse to overwrite a batch that was leased but never submitted, unless asked to."""
    if (work / "batch.json").exists() and not (work / "result.json").exists() and not discard:
        raise SystemExit(
            f"{work / 'batch.json'} was never submitted. Submit it first, or pass --discard "
            "(its entries go back to the queue when the lease expires)."
        )
    for name in ("batch.json", "result.json"):
        (work / name).unlink(missing_ok=True)
    for name in ("groups", "out", "review"):
        shutil.rmtree(work / name, ignore_errors=True)


def pull(api: Api, size: int, groups: int, label: str | None, work: Path) -> int:
    lease = api.call("POST", "/v1/factory/batches", {"size": size, "label": label})
    packs = lease["entries"]
    if not packs:
        print("Nothing to lease: the queue is empty.")
        return 2
    write_json(work / "batch.json", lease)
    for number, group in enumerate(split(packs, groups), start=1):
        write_json(work / "groups" / f"group-{number}.json", group)
        keys = ", ".join(pack["key"] for pack in group)
        print(f"group-{number}: {len(group)} entries: {keys}")
    print(
        f"Leased batch {lease['batch_id']}: {len(packs)} entries, until "
        f"{lease['lease_expires_at']}."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--size", type=int, default=40, help="entries to lease (1-100)")
    parser.add_argument("--groups", type=int, default=4, help="one per writer")
    parser.add_argument("--label", help="a name for this run, kept with the batch")
    parser.add_argument("--work", type=Path, default=WORK)
    parser.add_argument("--discard", action="store_true", help="drop an unsubmitted batch")
    args = parser.parse_args(argv)
    utf8_output()
    prepare(args.work, args.discard)
    try:
        return pull(Api(), args.size, args.groups, args.label, args.work)
    except ApiError as error:
        print(f"Lease failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
