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

#: Run state for the daily job. Outside ``data/`` and tracked in git on purpose:
#: a scheduled runner is a fresh machine every time, so a watermark it cannot
#: carry between runs is not a watermark.
STATE_DIR = REPO_ROOT / "state"

#: Content-addressed blob store shared by every snapshot, so an unchanged
#: package costs nothing on the second run.
COMPS_OBJECTS_DIR = COMPS_DIR / "objects"

#: One manifest per snapshot: ``{package_id: sha256}``.
COMPS_SNAPSHOTS_DIR = COMPS_DIR / "snapshots"

GOVINFO_BULKDATA = "https://www.govinfo.gov/bulkdata"
GOVINFO_API = "https://api.govinfo.gov"

#: One pace for both hosts, and they are not governed the same way.
#:
#: ``api.govinfo.gov`` is rate limited: 36,000 requests/hour, enforced per API
#: key by api.data.gov's API Umbrella, which says so in its own headers --
#: ``x-ratelimit-limit: 36000`` beside ``x-ratelimit-remaining``. Exceeding it
#: answers 429, and a higher allowance is requested from api.data.gov.
#:
#: ``www.govinfo.gov`` is not. The bulkdata tree, ``/content/pkg/`` renditions
#: and ``/metadata/pkg/`` MODS take no key, return no rate-limit headers, and
#: ``robots.txt`` states no Crawl-delay.
#:
#: That distinction was worth measuring because it is where the time goes. A
#: Congressional Record shard is ~100,000 rendition fetches and ~600 MODS
#: fetches against a few dozen keyed listing calls, so with the crawl running
#: flat out at ~37,000 requests/hour the key showed ~120 consumed in that hour:
#: over 99% of the traffic never touches the limited API. Pacing at 9/s was
#: therefore throttling unkeyed static files as though they spent API quota.
#:
#: 20/s is above the keyed limit, which is safe only because the keyed calls are
#: a few dozen per shard and cannot sustain it -- do not raise this to speed up
#: something that *is* API-bound without pacing the two hosts separately. It is
#: also a deliberate long way below what the unkeyed host will serve, measured
#: at ~66/s: an absence of a published limit is not permission to flood a public
#: service.
GOVINFO_RATE_PER_SEC = 20.0


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


def github_token() -> str:
    """Return the GitHub credential used to push the generated repositories.

    Checks the environment first, then ``.env`` at the repo root, which is the
    same order :func:`govinfo_api_key` uses. Reading ``os.environ`` directly --
    which is what the CLI used to do -- worked only because a ``GovInfoClient``
    happened to be constructed first and loaded ``.env`` as a side effect. That
    ordering was incidental, and it did not hold for a command that pushes
    without fetching anything.

    Returns:
        The token, or an empty string when none is configured. Empty is not an
        error here: a missing credential has to be reported by the caller that
        knows whether publishing was actually asked for, so that the heartbeat
        records the failure rather than the process exiting before it is
        written. See :func:`uscongress.jobs.update.run`.
    """
    _load_dotenv(REPO_ROOT / ".env")
    return os.environ.get("GITHUB_TOKEN", "").strip()


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
    """Create the directories that jobs write into."""
    for directory in (
        RAW_DIR,
        REPOS_DIR,
        COMPS_OBJECTS_DIR,
        COMPS_SNAPSHOTS_DIR,
        STATE_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def built_shards(template: str) -> list[str]:
    """Return the shard repositories of a family that exist on disk, in order.

    Asked of the filesystem rather than of a hardcoded range, because the range
    is what goes stale: ``republish`` defaulted to ``range(108, 120)``, so the
    120th Congress convening would have made it skip ``us-congress-bills-120``
    while still exiting zero -- the silent kind of failure this project is
    shaped around preventing.

    Args:
        template: Repository name containing ``{congress}``, e.g.
            ``us-congress-bills-{congress}``.

    Returns:
        Directory names, sorted by their trailing number rather than as text so
        the 109th does not sort after the 110th. Empty if none are cloned.
    """
    found = [
        p.name
        for p in REPOS_DIR.glob(template.replace("{congress}", "*"))
        # A preserved pre-fix copy is not a repository anyone consumes.
        if (p / ".git").is_dir() and not p.name.endswith(".pre-fix")
    ]

    def key(name: str) -> tuple[int, str]:
        tail = name.rsplit("-", 1)[-1]
        return (int(tail), name) if tail.isdigit() else (0, name)

    return sorted(found, key=key)
