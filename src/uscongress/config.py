"""Configuration: filesystem layout and credentials.

Paths are all rooted at ``data/``, which is gitignored in full. Nothing in this
module creates directories as a side effect of import; call :func:`ensure_dirs`
explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
REPOS_DIR = DATA_DIR / "repos"
COMPS_DIR = DATA_DIR / "comps"

#: Content-addressed blob store shared by every snapshot, so an unchanged
#: package costs nothing on the second run.
COMPS_OBJECTS_DIR = COMPS_DIR / "objects"

#: One manifest per snapshot: ``{package_id: sha256}``.
COMPS_SNAPSHOTS_DIR = COMPS_DIR / "snapshots"

GOVINFO_BULKDATA = "https://www.govinfo.gov/bulkdata"
GOVINFO_API = "https://api.govinfo.gov"

#: govinfo permits 36,000 requests/hour (10/s). We stay just under it; the
#: limit is per-key, and tripping it costs more time than it saves.
GOVINFO_RATE_PER_SEC = 9.0


def _load_dotenv(path: Path) -> None:
    """Load ``KEY=value`` pairs from a .env file into the environment.

    Existing environment variables always win, so an exported shell variable is
    never clobbered by a stale file. Silently does nothing if the file is absent.

    Args:
        path: Path to the .env file.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def govinfo_api_key() -> str:
    """Return the govinfo API key.

    Checks the environment first, then ``.env`` at the repo root.

    Returns:
        The API key.

    Raises:
        RuntimeError: If no key is configured.
    """
    _load_dotenv(REPO_ROOT / ".env")
    key = os.environ.get("GOVINFO_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "GOVINFO_API_KEY is not set. Export it or copy .env.example to .env. "
            "Free key: https://www.govinfo.gov/api-signup"
        )
    return key


def ensure_dirs() -> None:
    """Create the data directories that jobs write into."""
    for directory in (RAW_DIR, REPOS_DIR, COMPS_OBJECTS_DIR, COMPS_SNAPSHOTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
