"""Download dataset files into data/raw/<id>/, checking the pinned SHA-256 when there is one."""

import hashlib
import logging
import urllib.request
from pathlib import Path

from smart_dictionary.importers.manifest import Dataset

log = logging.getLogger(__name__)

# Importers that read an installed package instead of a downloaded file.
NO_DOWNLOAD = {"wordfreq"}
_CHUNK = 1 << 20
_REPORT_EVERY = 64 * _CHUNK


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(dataset: Dataset, *, force: bool = False) -> Path | None:
    """Return the local file, downloading it first if needed; None for package datasets."""
    if dataset.importer in NO_DOWNLOAD:
        return None
    path = dataset.path
    if path.exists() and not force and _matches(path, dataset.sha256):
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    request = urllib.request.Request(dataset.url, headers={"User-Agent": "smart-dictionary/0.1"})
    log.info("downloading %s from %s", dataset.id, dataset.url)
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
        received = 0
        while chunk := response.read(_CHUNK):
            out.write(chunk)
            received += len(chunk)
            if received % _REPORT_EVERY < _CHUNK:
                log.info("%s: %d MB", dataset.id, received >> 20)
    if not _matches(partial, dataset.sha256):
        partial.unlink()
        raise ValueError(f"{dataset.id}: downloaded file does not match the pinned sha256")
    partial.replace(path)
    return path


def _matches(path: Path, expected: str | None) -> bool:
    return expected is None or sha256_of(path) == expected
