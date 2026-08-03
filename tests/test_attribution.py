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


def test_first_release_point() -> None:
    """With no predecessor, everything up to the law number is covered."""
    assert laws_covered(_point(113, "3", 3), None) == ["113-1", "113-2", "113-3"]


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
