"""The registry of what this pipeline builds, and the plan it is building to.

Two lists, deliberately separate.

:data:`REPOSITORIES` is an index of repositories: what each one holds, what it
is built from, and which phase creates it. ``uscongress index`` renders it to
``REPOSITORIES.md`` with live status pulled from GitHub, so the index cannot
drift from reality -- a missing repository is reported as missing rather than
implied to exist.

:data:`PHASES` is the roadmap. It exists because an index shaped around
repositories cannot hold the whole plan: three of the eight phases produce no
repository at all. Phase 3 stands up the daily loop, phase 4 backfills a family
that already exists, and phase 7 adds derived material inside repositories built
earlier. Listing only the repository-producing phases left the numbering
skipping from 2 to 5 with nothing to say why, and lost the one phase whose
absence actually mattered: the plan ordered the daily loop *before* the corpus
expanded, precisely because bot rot is what killed every predecessor, and
running phases 4, 5 and 6 while 3 was missing is the failure that ordering
existed to prevent.
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


#: A phase that has shipped.
DONE = "done"

#: A phase not started.
PLANNED = "planned"


@dataclass(frozen=True)
class Phase:
    """One phase of the plan.

    Attributes:
        number: Phase number, as the plan numbers them.
        title: Short name for the work.
        detail: What the phase does, and why it sits where it does.
        state: :data:`DONE` or :data:`PLANNED`.
        produces: Repository the phase creates, or an empty string when it
            produces none. Three phases produce nothing: standing up the daily
            loop, backfilling a family that already exists, and adding derived
            material inside repositories built earlier.
    """

    number: int
    title: str
    detail: str
    state: str
    produces: str = ""

    @property
    def is_done(self) -> bool:
        """Whether the phase has shipped."""
        return self.state == DONE


PHASES: list[Phase] = [
    Phase(
        number=0,
        title="Scaffold, and snapshot the Statute Compilations",
        detail=(
            "The ETL itself, and the one genuinely time-sensitive job in the "
            "project: govinfo replaces Statute Compilations in place and keeps no "
            "version archive, so every day without a snapshot is history that "
            "cannot be recovered."
        ),
        state=DONE,
        produces=PIPELINE_REPO,
    ),
    Phase(
        number=1,
        title="The codified US Code",
        detail=(
            "383 distinct OLRC release points, each a commit and a tag, with "
            "per-law attribution from Table III."
        ),
        state=DONE,
        produces="us-congress-code",
    ),
    Phase(
        number=2,
        title="Bills of the current Congress",
        detail="Every measure of the 119th as a branch, one commit per text version.",
        state=DONE,
        produces="us-congress-bills-{congress}",
    ),
    Phase(
        number=3,
        title="The daily loop, and a heartbeat that goes stale on its own",
        detail=(
            "Rebuild whatever govinfo reports as changed, and publish the date it "
            "last ran. Ordered before the corpus expanded, not after: bot rot is "
            "what killed every predecessor, and a stopped job raises no error — it "
            "simply stops, which is why the signal has to be a date going stale "
            "rather than an alert having to fire."
        ),
        state=DONE,
    ),
    Phase(
        number=4,
        title="Backfill the 118th through the 108th",
        detail=(
            "The remaining eleven Congresses, ~160,000 further branches. Produces "
            "no new repository: it fills out the family phase 2 created."
        ),
        state=DONE,
    ),
    Phase(
        number=5,
        title="Statutes at Large",
        detail=(
            "Session laws as enacted, volumes 1–137 (1789–2023). Independent of "
            "everything above and of phase 6."
        ),
        state=PLANNED,
        produces="us-congress-statutes",
    ),
    Phase(
        number=6,
        title="The Congressional Record",
        detail=(
            "Floor proceedings from 1873, sharded by Congress and linked to bill "
            "branches by metadata. Independent of phase 5."
        ),
        state=PLANNED,
        produces="us-congress-record-{congress}",
    ),
    Phase(
        number=7,
        title="Experimental amendment execution",
        detail=(
            "What a bill would do to existing law, under `derived/` and never "
            "authoritative. Measured across seven real bills only ~49% of "
            "amendatory instructions carry a machine-readable US Code reference, "
            "and a large bill would need ~99.99% per-instruction accuracy to come "
            "out wholly correct, so the output is marked derived and unapplied "
            "instructions are stated rather than guessed at."
        ),
        state=PLANNED,
    ),
    Phase(
        number=8,
        title="Roll-call votes",
        detail=(
            "How each member voted, on the commit for the version that was voted "
            "on. Needs a Congress.gov API key, which nothing here reads yet — "
            "everything built so far comes from govinfo. Produces no new "
            "repository: it adds to the measures already built. Note that commit "
            "messages are part of what a commit hashes, so filling them in "
            "rewrites every affected branch, which is why it is its own phase "
            "rather than a change to phase 2."
        ),
        state=PLANNED,
    ),
    Phase(
        number=9,
        title="Hand the daily loop its own credentials",
        detail=(
            "**The schedule is paused until this lands.** Phase 3 built the loop "
            "and proved it against live data, but the token GitHub injects into a "
            "workflow is scoped to the repository running it: enough to commit the "
            "heartbeat here, not enough to push the thirteen data repositories. "
            "That needs a `DATA_REPO_TOKEN` secret carrying Contents: read/write "
            "on the `junxit` repositories, which has to be minted by hand. Then "
            "`gh workflow enable update`. Tracked as a phase rather than a note "
            "because an unattended loop that nobody turned on is the same silent "
            "failure as one that stopped."
        ),
        state=PLANNED,
    ),
]


def phase_of(number: int) -> Phase | None:
    """Look up one phase.

    Args:
        number: Phase number.

    Returns:
        The phase, or None if there is no such phase.
    """
    return next((p for p in PHASES if p.number == number), None)


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
