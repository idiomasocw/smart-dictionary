"""The dataset manifest (data/datasets.toml): where each dataset comes from and how to load it."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smart_dictionary.paths import MANIFEST_PATH, RAW_DIR

_FIELDS = {"name", "version", "license", "attribution", "url", "importer", "sha256"}


@dataclass(frozen=True)
class Dataset:
    id: str
    name: str
    version: str
    license: str
    attribution: str
    url: str
    importer: str
    sha256: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        """Where the downloaded file lives."""
        return RAW_DIR / self.id / self.url.rstrip("/").rsplit("/", 1)[-1]


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Dataset]:
    specs = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        dataset_id: Dataset(
            id=dataset_id,
            name=spec["name"],
            version=spec["version"],
            license=spec["license"],
            attribution=spec["attribution"],
            url=spec["url"],
            importer=spec["importer"],
            sha256=spec.get("sha256"),
            options={key: value for key, value in spec.items() if key not in _FIELDS},
        )
        for dataset_id, spec in specs.items()
    }
