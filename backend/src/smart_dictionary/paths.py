"""Repository locations used by the CLI, the importers and the tests."""

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
DATA_DIR = REPO_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
MANIFEST_PATH = DATA_DIR / "datasets.toml"
SCHEMA_PATH = REPO_DIR / "factory" / "schema" / "entry.v1.json"
FIXTURES_DIR = BACKEND_DIR / "fixtures"
OWNER_SEEDS = DATA_DIR / "seeds" / "owner" / "owner-seeds.csv"
SEED_EXPORT = RAW_DIR / "seed-candidates.csv"
