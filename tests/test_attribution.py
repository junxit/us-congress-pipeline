"""Tests for per-law attribution.

A release point closes over several public laws, so working out *which* laws a
commit adds is what makes Table III trailers meaningful. The out-of-sequence
cases are the ones that matter: a law excluded by one release point and picked
up by a later one must be attributed exactly once, to the right commit.
"""

from __future__ import annotations

from datetime import date

from uscongress.jobs.table3 import Classification, trailers
from uscongress.jobs.uscode import ReleasePoint, laws_covered


def _point(congress: int, spec: str, number: int, excludes: tuple[int, ...] = ()) -> ReleasePoint:
    """Build a release point for testing."""
    return ReleasePoint(
        congress=congress,
        law_spec=spec,
        law_number=number,
        excludes=excludes,
        published=date(2025, 1, 1),
        titles=(5,),
        order=0,
        is_current=False,
    )


def test_consecutive_laws() -> None:
    """A plain advance covers everything in between."""
    assert laws_covered(_point(119, "10", 10), _point(119, "7", 7)) == [
        "119-8",
        "119-9",
        "119-10",
    ]


def test_excluded_law_is_withheld() -> None:
    """A ``not`` suffix means that law is not in this snapshot yet."""
    covered = laws_covered(_point(119, "12not11", 12, (11,)), _point(119, "10", 10))
    assert covered == ["119-12"]
    assert "119-11" not in covered


def test_previously_excluded_law_is_picked_up_later() -> None:
    """The withheld law is attributed to the release point that includes it.

    Miss this and the law is never attributed to any commit.
    """
    covered = laws_covered(
        _point(119, "14", 14), _point(119, "12not11", 12, (11,))
    )
    assert covered == ["119-11", "119-13", "119-14"]


def test_new_congress_starts_from_one() -> None:
    """Crossing into a new Congress restarts numbering."""
    assert laws_covered(_point(119, "3", 3), _point(118, "400", 400)) == [
        "119-1",
        "119-2",
        "119-3",
    ]


def test_baseline_release_point_claims_no_laws() -> None:
    """The first release point is a baseline, not the effect of any law.

    Its tree is the whole US Code as accumulated since 1926. Attributing it to
    the handful of laws in that Congress would badly misstate the commit.
    """
    assert laws_covered(_point(113, "21", 21), None) == []


def test_trailers_report_missing_attribution_explicitly() -> None:
    """A law absent from Table III must say so, not be silently dropped.

    Table III lags the Code by about a year. Omitting an unattributed law would
    read as "this law changed nothing", which is worse than admitting the gap.
    """
    index = {"119-4": [Classification("1(a)", "7", "1636i")]}
    lines = trailers(index, ["119-4", "119-99"])
    assert lines[0] == "Classified-By-PL-119-4: 7 USC 1636i"
    assert lines[1] == "Classified-By-PL-119-99: not yet in Table III"


def test_trailers_deduplicate_citations() -> None:
    """Several act sections touching one USC section yield one citation."""
    index = {
        "118-2": [
            Classification("1(a)", "50", "3161 nt"),
            Classification("1(b)", "50", "3161 nt"),
            Classification("2", "12", "1454"),
        ]
    }
    (line,) = trailers(index, ["118-2"])
    assert line == "Classified-By-PL-118-2: 12 USC 1454, 50 USC 3161 nt"


def test_commit_message_records_exclusions_and_attribution() -> None:
    """An out-of-sequence release point must say what it omits, and why."""
    from uscongress.jobs.uscode import commit_message

    point = _point(119, "102not101", 102, (101,))
    message = commit_message(
        point,
        section_count=58_327,
        law_ids=["119-102"],
        attribution=["Classified-By-PL-119-102: 5 USC 101"],
    )
    assert "excluding 119-101" in message
    assert "Public laws:   119-102" in message
    assert "Classified-By-PL-119-102: 5 USC 101" in message
    assert "codified them out of sequence" in message
    # The reader must not mistake a release point for a single law's effect.
    assert "not the effect of a single law" in message


def test_commit_message_baseline_has_no_law_list() -> None:
    """The baseline snapshot lists no laws, since it claims none."""
    from uscongress.jobs.uscode import commit_message

    message = commit_message(_point(113, "21", 21), 56_900, law_ids=[], attribution=None)
    assert "Public laws:" not in message
    assert "Classified-By" not in message


def test_undeclared_truncated_title_is_carried_forward() -> None:
    """A title that vanishes without OLRC declaring it changed is defective.

    usc46.xml drops from 912 sections to 576 across release points 113-44 and
    113-45, then returns to 912 at 113-46, while parsing cleanly throughout.
    Committing it verbatim records 336 repeals and then reverses them.
    """
    from uscongress.jobs.uscode import repair_truncated_titles

    previous = {f"title-46/sec-{i}.md": f"body {i}" for i in range(100)}
    previous["title-05/sec-1.md"] = "unrelated"
    # Title 46 collapses; the release point declares only title 5.
    files = {f"title-46/sec-{i}.md": f"body {i}" for i in range(30)}
    files["title-05/sec-1.md"] = "changed"

    fixed, repaired = repair_truncated_titles(files, previous, declared=(5,))
    assert len(repaired) == 1 and "title-46" in repaired[0]
    assert len([k for k in fixed if k.startswith("title-46/")]) == 100
    # The genuinely-changed title must not be reverted.
    assert fixed["title-05/sec-1.md"] == "changed"


def test_declared_title_loss_is_believed() -> None:
    """If OLRC says the title changed, a large drop is real law, not damage."""
    from uscongress.jobs.uscode import repair_truncated_titles

    previous = {f"title-46/sec-{i}.md": "x" for i in range(100)}
    files = {f"title-46/sec-{i}.md": "x" for i in range(30)}
    fixed, repaired = repair_truncated_titles(files, previous, declared=(46,))
    assert repaired == []
    assert len(fixed) == 30


def test_small_losses_do_not_trip_the_guard() -> None:
    """Ordinary repeals must pass through untouched."""
    from uscongress.jobs.uscode import repair_truncated_titles

    previous = {f"title-46/sec-{i}.md": "x" for i in range(100)}
    files = {f"title-46/sec-{i}.md": "x" for i in range(95)}
    fixed, repaired = repair_truncated_titles(files, previous, declared=())
    assert repaired == []
    assert len(fixed) == 95
