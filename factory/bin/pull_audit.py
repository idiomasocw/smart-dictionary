"""Lease the entries due for an audit and split them into groups (AUDIT.md step 1).

    python3 factory/bin/pull_audit.py --size 100 --groups 4 --label "2026-10-21 audit"

Writes factory/work/audit/batch.json (the lease, with the instructions' version) and
factory/work/audit/groups/group-N.json (the items for auditor N, in the order the server gave
them). Exits 2 when nothing is due.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

from _api import FACTORY_DIR, WORK, Api, ApiError, utf8_output, write_json
from pull_batch import split

AUDIT_WORK = WORK / "audit"
# Every entry records which instructions audited it; a change makes every entry due again.
INSTRUCTIONS = ["AUDIT.md", "STYLE.md", "rubric.md"]


def audit_version(factory_dir: Path = FACTORY_DIR) -> str:
    digest = hashlib.sha256()
    for name in INSTRUCTIONS:
        digest.update((factory_dir / name).read_bytes())
    return "audit-" + digest.hexdigest()[:12]


def prepare(work: Path, discard: bool) -> None:
    """Refuse to overwrite a lease that was never submitted, unless asked to."""
    if (work / "batch.json").exists() and not (work / "result.json").exists() and not discard:
        raise SystemExit(
            f"{work / 'batch.json'} was never submitted. Submit it first, or pass --discard "
            "(its entries are due again when the lease expires)."
        )
    for name in ("batch.json", "result.json"):
        (work / name).unlink(missing_ok=True)
    for name in ("groups", "out"):
        shutil.rmtree(work / name, ignore_errors=True)


def pull(api: Api, size: int, groups: int, label: str | None, work: Path) -> int:
    version = audit_version()
    lease = api.call(
        "POST", "/v1/audit/batches", {"size": size, "label": label, "audit_version": version}
    )
    items = lease["items"]
    if not items:
        print("Nothing is due for an audit.")
        return 2
    write_json(work / "batch.json", {**lease, "audit_version": version})
    for number, group in enumerate(split(items, groups), start=1):
        write_json(work / "groups" / f"group-{number}.json", group)
        rules = sum(len(item["rule_findings"]) for item in group)
        print(f"group-{number}: {len(group)} entries, {rules} rule findings to answer")
    print(f"Leased audit {lease['batch_id']}: {len(items)} entries, until {lease['expires_at']}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--size", type=int, default=100, help="entries to lease (1-200)")
    parser.add_argument("--groups", type=int, default=4, help="one per auditor")
    parser.add_argument("--label", help="a name for this run, kept with the lease")
    parser.add_argument("--work", type=Path, default=AUDIT_WORK)
    parser.add_argument("--discard", action="store_true", help="drop an unsubmitted lease")
    args = parser.parse_args(argv)
    utf8_output()
    prepare(args.work, args.discard)
    try:
        return pull(Api(token_env="AUDIT_TOKEN"), args.size, args.groups, args.label, args.work)
    except ApiError as error:
        print(f"Lease failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
