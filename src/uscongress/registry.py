"""The registry of repositories this pipeline produces.

This module is the single source of truth for what exists, what each repository
holds, and which phase creates it. ``uscongress index`` renders it to
``REPOSITORIES.md`` with live status pulled from GitHub, so the index cannot
drift from reality: if a repository is missing, the index says so rather than
implying it exists.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

OWNER = "junxit"
PIPELINE_REPO = "us-congress-pipeline"

#: Every repository name is prefixed with this.
PREFIX = "us-congress-"


@dataclass(frozen=True)
class Repository:
    """A repository the pipeline owns or generates.

    Attributes:
        name: Repository name, always prefixed ``us-congress-``.
        summary: One-line description of what it holds.
        source: Upstream data source it is built from.
        phase: Plan phase that creates it.
        shards: Description of sharding, if the repo is one of a family.
        is_pipeline: True for this repository itself.
    """

    name: str
    summary: str
    source: str
    phase: int
    shards: str = ""
    is_pipeline: bool = False

    @property
    def url(self) -> str:
        """HTTPS URL of the repository on GitHub."""
        return f"https://github.com/{OWNER}/{self.name}"


REPOSITORIES: list[Repository] = [
    Repository(
        name=PIPELINE_REPO,
        summary="The ETL itself. Generates every repository below.",
        source="—",
        phase=0,
        is_pipeline=True,
    ),
    Repository(
        name="us-congress-code",
        summary=(
            "The codified US Code. One commit per OLRC release point, tagged, "
            "with per-law attribution from Table III."
        ),
        source="uscode.house.gov release points (USLM 1.0 XML)",
        phase=1,
    ),
    Repository(
        name="us-congress-bills-{congress}",
        summary="One branch per measure; one commit per bill text version.",
        source="govinfo BILLS + BILLSTATUS",
        phase=2,
        shards="one repo per Congress, 108 through 119",
    ),
    Repository(
        name="us-congress-statutes",
        summary="Statutes at Large — session laws as enacted, volumes 1–137.",
        source="govinfo STATUTE (USLM 2.0 XML)",
        phase=5,
    ),
    Repository(
        name="us-congress-record-{congress}",
        summary=(
            "Congressional Record floor proceedings, 1873 to present, "
            "linked to bills by metadata."
        ),
        source="govinfo CREC + CRECB",
        phase=6,
        shards="one repo per Congress",
    ),
]


@dataclass
class RepoStatus:
    """Live GitHub state for a repository.

    Attributes:
        exists: Whether the repository exists.
        private: Whether it is private.
        pushed_at: ISO timestamp of the last push, if known.
        error: Why the lookup failed, if it did.
    """

    exists: bool = False
    private: bool = True
    pushed_at: str = ""
    error: str = field(default="")


def fetch_status(name: str) -> RepoStatus:
    """Look up a repository's current state on GitHub via the ``gh`` CLI.

    Args:
        name: Repository name without the owner.

    Returns:
        The live status. A repository that does not exist yet is reported as
        ``exists=False`` rather than raising.
    """
    if "{" in name:  # sharded family; no single repo to query
        return RepoStatus(exists=False)
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{OWNER}/{name}", "--jq",
             "{private:.private,pushed_at:.pushed_at}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return RepoStatus(error=f"{type(exc).__name__}: {exc}")

    if result.returncode != 0:
        return RepoStatus(exists=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return RepoStatus(error=str(exc))
    return RepoStatus(
        exists=True,
        private=bool(payload.get("private", True)),
        pushed_at=str(payload.get("pushed_at") or ""),
    )
