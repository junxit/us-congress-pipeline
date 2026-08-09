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
            "Congressional Record floor proceedings as text, 1994 to present, "
            "sharded by Congress and linked to bills by metadata."
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
            "Session laws as enacted, 135 volumes and 101,975 laws, one commit "
            "per volume. Volumes 7 and 8 get none: they hold only Indian and "
            "foreign treaties, which are ratification rather than passage and "
            "presentment. Independent of everything above and of phase 6."
        ),
        state=DONE,
        produces="us-congress-statutes",
    ),
    Phase(
        number=6,
        title="The Congressional Record",
        detail=(
            "Floor proceedings, 17 shards from the 103rd to the 119th: 9,382 "
            "issue days and 1,330,322 documents, one commit per issue day, with "
            "the daily and bound editions on separate branches. **The "
            "machine-readable Record begins in 1994, not 1873**: of 2,420 "
            "bound-edition packages, the 2,083 covering 1873–1998 are scanned "
            "page images whose granules offer a PDF and no `txtLink` at all, so "
            "that century is unbuildable rather than merely unbuilt — measured "
            "against `GPO-CRECB-1947-pt1` and `GPO-CRECB-1970-pt2`. The bound "
            "edition also stops at 2018, which is why the 116th onward carry a "
            "`daily` branch and no `bound`."
        ),
        state=DONE,
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
            "on. **Not from the Congress.gov API**, which the plan assumed for "
            "years and which cannot serve this corpus: its roll-call endpoint "
            "covers the 118th and 119th Congresses and the House alone, against "
            "twelve Congresses and both chambers. The votes come from the "
            "chambers — `clerk.house.gov` and `senate.gov` — which BILLSTATUS "
            "already links and neither of which is keyed, so this shipped "
            "without adding a credential. The House publishes bioguide IDs, the "
            "same ones sponsors carry; the Senate publishes only its own LIS "
            "IDs, and that asymmetry is stated rather than crosswalked. "
            "Produces no new repository: it adds to the measures already built. "
            "Commit messages are part of what a commit hashes, so filling them "
            "in rewrote every affected branch — but only 7,510 of 172,082 "
            "measures carry a recorded vote, so a full rebuild of all twelve "
            "shards moved 5,969 refs and left 160,248 branches byte-identical. "
            "19,471 roll calls were fetched and **none was missing**; what is "
            "recorded as a gap instead is 1,949 votes taken after the last text "
            "version their measure ever published, which therefore sit on no "
            "commit — 128 of them in the 108th, where every voted measure with "
            "a branch has only its introduced text."
        ),
        state=DONE,
    ),
    Phase(
        number=9,
        title="Hand the daily loop its own credentials",
        detail=(
            "The schedule is live. The token GitHub injects into a workflow run "
            "is scoped to the repository running it — enough to commit the "
            "heartbeat, not enough to push the 30 data repositories — so the loop "
            "carries a `DATA_REPO_TOKEN` of its own: a fine-grained token with "
            "Contents: read/write on the `us-congress-*` repositories and nothing "
            "else, minted by hand because no API can create one. Proved by a real "
            "run rather than a green tick: 544 measures checked, 82 branches "
            "rebuilt and published, the watermark advanced and the heartbeat "
            "written. Tracked as a phase rather than a note because an unattended "
            "loop nobody turned on is the same silent failure as one that stopped."
        ),
        state=DONE,
    ),
    Phase(
        number=10,
        title="A members crosswalk, so Senate votes are joinable",
        detail=(
            "Phase 8 left the two chambers keyed differently, because the "
            "sources are: the House Clerk publishes bioguide IDs — the same ones "
            "sponsors and cosponsors carry — while the Senate publishes only its "
            "own LIS member IDs. Nothing was inferred at the time, which was "
            "right, but it leaves the first question anyone doing analysis asks "
            "— how one member voted across both chambers — answerable only by a "
            "join the reader has to build themselves. **Measured before being "
            "planned**: 246 distinct LIS IDs appear across all 4,932 Senate roll "
            "calls in the corpus and all 246 resolve to a bioguide ID in "
            "`unitedstates/congress-legislators` (CC0), with surname, state and "
            "party agreeing independently for 244 — the two exceptions being a "
            "diacritic and a name change, the same people either way. The table "
            "is **vendored and pinned rather than fetched**: it is edited "
            "continuously upstream, and a live read would re-render every "
            "affected vote file the day someone corrects a spelling, breaking "
            "the unchanged-input-unchanged-bytes rule the daily loop rests on. "
            "246 rows is also small enough to read in review, which no feed is. "
            "The added identifier is marked as a crosswalk rather than passed "
            "off as something the Senate published, and a row whose name, state "
            "and party do not agree across both sources is not used and is said "
            "so: a vote attributed to the wrong senator is worse than a vote "
            "with no identifier at all. Produces no new repository, and rewrites "
            "only branches carrying a Senate vote — a subset of the 5,969 phase "
            "8 moved."
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
