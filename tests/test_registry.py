"""Tests for the registry of repositories and the roadmap beside it.

The roadmap exists because an index shaped around repositories cannot hold the
whole plan: three of the eight phases produce no repository. Listing only the
repository-producing ones left the numbering jumping from 2 to 5 with nothing to
explain it, and quietly dropped the phase whose absence mattered most -- the
daily loop the plan deliberately ordered *before* the corpus expanded, because
bot rot is what killed every predecessor.

So these check the two ways the roadmap can go wrong: losing a phase, and
drifting from the repositories it claims to produce.
"""

from __future__ import annotations

from uscongress.jobs import artifacts
from uscongress.jobs.index import _roadmap
from uscongress.registry import PHASES, REPOSITORIES, Phase, Repository, phase_of


def test_every_phase_of_the_plan_is_present() -> None:
    """The plan has ten phases, 0 through 9, and none may go missing.

    Phase 3 went missing once already: it produces no repository, so a registry
    keyed on repositories had nowhere to put it, and the daily loop was skipped
    while phases 4, 5 and 6 were considered -- the exact reordering the plan's
    numbering existed to prevent.
    """
    assert [p.number for p in PHASES] == list(range(10))


def test_the_phases_that_produce_no_repository_are_the_expected_five() -> None:
    """Half the plan adds to repositories that already exist.

    Standing up the loop, backfilling a family, derived material, votes, and the
    credentials the loop needs to run unattended. If a sixth appears, either the
    plan changed or a `produces` was dropped by accident; both are worth
    stopping on.
    """
    assert [p.number for p in PHASES if not p.produces] == [3, 4, 7, 8, 9]


def test_every_produced_repository_is_in_the_index() -> None:
    """The roadmap and the repository index must not drift apart.

    A phase claiming to produce a repository the index has never heard of would
    render a row pointing at nothing.
    """
    known = {r.name for r in REPOSITORIES}
    assert {p.produces for p in PHASES if p.produces} <= known


def test_every_repository_names_a_real_phase() -> None:
    """The other direction: no repository may claim a phase that does not exist."""
    for repo in REPOSITORIES:
        assert phase_of(repo.phase) is not None, repo.name


def test_a_repository_agrees_with_the_phase_that_makes_it() -> None:
    """Where a phase produces a repository, they must point at each other."""
    for phase in PHASES:
        if not phase.produces:
            continue
        repo = next(r for r in REPOSITORIES if r.name == phase.produces)
        assert repo.phase == phase.number


def test_the_daily_loop_has_shipped() -> None:
    """The phase this file's existence is downstream of.

    Pinned deliberately: the corpus is complete, public and static, the 119th
    Congress is still legislating, and a roadmap that quietly reverted phase 3
    to planned would mean nothing was watching again.
    """
    loop = phase_of(3)
    assert loop is not None
    assert loop.is_done


def test_the_roadmap_renders_every_phase() -> None:
    """It is the first thing in REPOSITORIES.md, so a dropped row is visible."""
    text = "\n".join(_roadmap())

    for phase in PHASES:
        assert f"| {phase.number} |" in text
        assert phase.title in text
    assert "**shipped**" in text
    assert "planned" in text


def test_the_roadmap_explains_the_gap_in_the_numbering() -> None:
    """A reader should not have to wonder why the table below skips numbers.

    The list is derived, not written out, so adding a phase that produces no
    repository cannot leave the explanation naming the wrong ones.
    """
    text = "\n".join(_roadmap())
    assert "Phases 3, 4, 7, 8 and 9 produce no repository" in text


def test_repository_state_comes_from_the_roadmap_not_the_disk() -> None:
    """Every generated repository carries the cross-reference table.

    Deriving "has this phase shipped" from what happens to be on this machine
    reports a shipped phase as planned on any machine that has not built it,
    and that answer is then published into all fourteen repositories at once.

    Both cases are constructed rather than picked out of ``REPOSITORIES``. The
    planned one used to be whichever repository happened not to be built yet,
    which meant the test broke every time a phase shipped -- first when the
    Statutes at Large landed, then again when the Congressional Record did, at
    which point no unshipped repository was left to point at. What is being
    tested is the mapping from phase state to phrase, not the state of the
    roadmap on the day it runs.
    """
    shipped = Repository(
        name="us-congress-code", summary="s", source="x", phase=1
    )
    planned = Repository(
        name="us-congress-record-{congress}", summary="s", source="x", phase=6
    )
    done = Phase(1, "t", "d", "done")
    todo = Phase(6, "t", "d", "planned")

    def status(repo, phase):
        monkey = {1: done, 6: todo}[repo.phase]
        original = artifacts.phase_of
        artifacts.phase_of = lambda n: monkey  # noqa: ARG005
        try:
            return artifacts._status_of(repo, built=set())  # noqa: SLF001
        finally:
            artifacts.phase_of = original

    assert status(shipped, done) == "built"
    assert status(planned, todo) == "planned"


def test_a_shard_family_reports_what_is_actually_there() -> None:
    """A count of built shards is more useful than "shipped" once they exist."""
    bills = next(r for r in REPOSITORIES if "{congress}" in r.name)
    built = {"us-congress-bills-118", "us-congress-bills-119"}

    assert artifacts._status_of(bills, built) == "2 of these built"  # noqa: SLF001


def test_a_phase_knows_whether_it_is_done() -> None:
    """State is a string in the data and a predicate in the code."""
    assert Phase(9, "t", "d", "done").is_done
    assert not Phase(9, "t", "d", "planned").is_done
