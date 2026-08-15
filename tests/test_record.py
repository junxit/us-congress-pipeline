"""Tests for building a Congress's Congressional Record repository.

The real hazard here is not parsing -- it is *placement*. Every other job in
this project has an unambiguous key: a bill is ``hr-588`` of the 113th and
belongs nowhere else. An issue of the Congressional Record has neither a stable
package nor an unambiguous Congress, and both failures are silent.

Three measured facts drive most of what follows. Of the 346 CREC packages the
119th Congress lists, **11 March 2025 is published as three overlapping
packages** whose granules share 267 identifiers, so merging on the package
double-counts a day and committing each package separately has the second commit
overwrite the first's file numbering. **4 January 2023 is two genuinely
different issues** that share no granule at all, so *not* merging loses half the
day. And **``CREC-2025-01-03-v170`` is dated the day the 119th convened but
declares the 118th**, which adjourned sine die that morning: placing it by date
files it in the wrong shard, and filtering the 119th on its declared Congress
without widening the 118th's discovery window loses it from both. A package
lost from both shards is exactly the absence this project treats as a build that
quietly failed.

The fixtures are trimmed from the real documents those facts come from.
"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import date
from pathlib import Path

import pytest

from uscongress.gitbuild import GitRepo
from uscongress.jobs import record
from uscongress.jobs.record import (
    BOUND,
    DAILY,
    DayPart,
    Granule,
    Issue,
    PackageRef,
    _fetch_cached,
    _write_gaps,
    built_days,
    commit_message,
    congress_of,
    congress_span,
    discover,
    gap_documents,
    granule_markdown,
    granule_text,
    issue_documents,
    merge_issues,
    parse_mods,
    parse_title,
    place,
    read_package,
    slug,
)

# --------------------------------------------------------------------------
# Fixtures, trimmed from real documents
# --------------------------------------------------------------------------


def _rendition(body: str, pre: bool = True) -> bytes:
    """A granule's HTML rendition, shaped as govinfo serves it.

    The real one is a fixed-width dump inside one ``<pre>``, preceded by a
    header block naming the volume, the section and the page.
    """
    inner = (
        "[Congressional Record Volume 172, Number 127 (Tuesday, August 4, 2026)]\n"
        "[Senate]\n"
        "[Page S4415]\n"
        "From the Congressional Record Online through the Government Publishing "
        'Office [<a href="https://www.gpo.gov">www.gpo.gov</a>]\n'
        "\n\n"
        f"{body}\n"
    )
    return (
        "<html>\n<head>\n<title>Congressional Record</title>\n</head>\n<body>"
        + (f"<pre>\n{inner}</pre>" if pre else f"<div>{inner}</div>")
        + "</body>\n</html>\n"
    ).encode()


#: govinfo's soft 404: the ordinary web page, HTTP 200, measured at 44,165
#: bytes. Trimmed here to its distinguishing head.
_SOFT_404 = (
    b'<!DOCTYPE html>\n<html lang="en" dir="ltr" prefix="content: '
    b'http://purl.org/rss/1.0/modules/content/">\n<head><title>govinfo</title>'
    b"</head><body><div>Page not found</div></body></html>"
)


#: One granule of a package MODS, trimmed from ``CREC-2026-08-04``. Note the
#: document has **no XML declaration** -- the real one begins ``<mods xmlns:…``.
_MODS = (
    '<mods xmlns="http://www.loc.gov/mods/v3" '
    'xmlns:xlink="http://www.w3.org/1999/xlink">'
    '<relatedItem type="constituent" ID="id-CREC-2026-08-04-pt1-PgS4415-10">'
    "<titleInfo><title>MEASURE PLACED ON THE CALENDAR--S. 5221</title></titleInfo>"
    '<relatedItem type="otherFormat" xlink:href="'
    "https://www.govinfo.gov/content/pkg/CREC-2026-08-04/html/"
    'CREC-2026-08-04-pt1-PgS4415-10.htm"/>'
    '<relatedItem type="otherFormat" xlink:href="'
    "https://www.govinfo.gov/content/pkg/CREC-2026-08-04/pdf/"
    'CREC-2026-08-04-pt1-PgS4415-10.pdf"/>'
    '<identifier type="congressional record citation">172 Cong. Rec. S4415</identifier>'
    '<part type="article"><extent unit="pages"><start>S4415</start>'
    "<end>S4415</end></extent></part>"
    "<extension>"
    "<accessId>CREC-2026-08-04-pt1-PgS4415-10</accessId>"
    "<granuleClass>SENATE</granuleClass>"
    "<granuleDate>2026-08-04</granuleDate>"
    '<bill congress="119" context="TITLE" number="5221" type="S"></bill>'
    '<bill congress="119" context="OTHER" number="5221" type="S"></bill>'
    '<congMember bioGuideId="T000250" chamber="S" congress="119" party="R" '
    'role="SPEAKING" state="SD">'
    '<name type="parsed">Mr. THUNE</name>'
    '<name type="authority-fnf">John Thune</name>'
    '<name type="authority-lnf">Thune, John</name>'
    "</congMember>"
    "</extension>"
    "</relatedItem>"
    "</mods>"
).encode()


#: The bound edition's package MODS, trimmed from ``CRECB-2018-pt10``. Its
#: constituents carry **no ``<extension>`` at all** -- no access id, no class,
#: no date, no bill, no member -- which is the difference that decides what a
#: bound document can say.
_MODS_BOUND = (
    '<mods xmlns="http://www.loc.gov/mods/v3" '
    'xmlns:xlink="http://www.w3.org/1999/xlink">'
    '<relatedItem type="constituent" ID="id-CRECB-2018-pt10-Pg12725-6">'
    "<titleInfo><title>NOMINATION OF BRETT KAVANAUGH</title>"
    "<partName>Senate</partName></titleInfo>"
    '<relatedItem type="otherFormat" xlink:href="'
    "https://www.govinfo.gov/content/pkg/CRECB-2018-pt10/html/"
    'CRECB-2018-pt10-Pg12725-6.htm"/>'
    '<part type="article"><extent unit="pages"><start>12725</start>'
    "<end>12726</end></extent></part>"
    "</relatedItem>"
    "</mods>"
).encode()


def _granule(**overrides: object) -> Granule:
    """A granule with sensible defaults."""
    base: dict[str, object] = {
        "granule_id": "CREC-2026-08-04-pt1-PgS4415-10",
        "package_id": "CREC-2026-08-04",
        "title": "MEASURE PLACED ON THE CALENDAR--S. 5221",
        "section": "SENATE",
        "when": date(2026, 8, 4),
        "url": "https://example.invalid/x.htm",
        "page": "S4415",
        "citation": "172 Cong. Rec. S4415",
        "measures": ("S. 5221",),
        "speakers": (("Thune, John", "T000250", "R", "SD"),),
    }
    base.update(overrides)
    return Granule(**base)  # type: ignore[arg-type]


def _issue(granules: tuple[Granule, ...], **overrides: object) -> Issue:
    """An issue with sensible defaults."""
    base: dict[str, object] = {
        "edition": DAILY,
        "congress": 119,
        "when": date(2026, 8, 4),
        "sources": (PackageRef("CREC-2026-08-04", "172", "127"),),
        "granules": granules,
    }
    base.update(overrides)
    return Issue(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Placement: which shard a day belongs to
# --------------------------------------------------------------------------


def test_a_congress_is_read_from_the_date_across_the_january_boundary() -> None:
    """A Congress convenes on 3 January, not 1 January.

    Getting this wrong shifts every issue of the first two days of an odd year
    into the wrong shard, and there is no error to notice: the day simply
    appears in a repository whose name says a different Congress.
    """
    assert congress_of(date(2026, 8, 4)) == 119
    assert congress_of(date(2025, 1, 3)) == 119
    assert congress_of(date(2025, 1, 2)) == 118
    assert congress_of(date(2024, 12, 31)) == 118


def test_the_twentieth_amendment_moved_the_boundary_in_1935() -> None:
    """Before the 74th, a Congress convened on 4 March, not 3 January.

    The bound edition reaches back to 1873, so a January 1933 sitting placed by
    the modern rule lands in the 73rd Congress when it belongs to the 72nd --
    an off-by-one that would be invisible in any sample taken after 1935.
    """
    assert congress_of(date(1935, 1, 3)) == 74  # first under the new rule
    assert congress_of(date(1935, 1, 2)) == 73  # last under the old one
    assert congress_of(date(1933, 3, 4)) == 73
    assert congress_of(date(1933, 1, 2)) == 72
    assert congress_of(date(1873, 3, 4)) == 43  # the Record itself begins here


def test_a_congress_span_is_the_window_packages_are_asked_for() -> None:
    """Discovery asks govinfo for a date range, so the range must be right.

    A span one day short at either end silently drops the convening or sine-die
    sitting, which are the two days most likely to matter.
    """
    assert congress_span(119) == (date(2025, 1, 3), date(2027, 1, 2))
    assert congress_span(115) == (date(2017, 1, 3), date(2019, 1, 2))
    assert congress_span(43) == (date(1873, 3, 4), date(1875, 3, 3))


def test_a_daily_package_is_placed_by_its_declared_congress_not_its_date() -> None:
    """``CREC-2025-01-03-v170`` is dated 3 January 2025 and declares the 118th.

    The 118th adjourned sine die that morning and the 119th convened at noon,
    so both sat that day and govinfo publishes two packages for it. Placing by
    date files the outgoing Congress's final sitting in the incoming Congress's
    shard; placing by the declared Congress puts it where it belongs, and both
    shards legitimately hold a ``2025/01-03/``.
    """
    sine_die = {
        "packageId": "CREC-2025-01-03-v170",
        "dateIssued": "2025-01-03",
        "congress": "118",
    }
    convening = {
        "packageId": "CREC-2025-01-03-v171",
        "dateIssued": "2025-01-03",
        "congress": "119",
    }

    assert place(sine_die, DAILY, 118) is True
    assert place(sine_die, DAILY, 119) is False
    assert place(convening, DAILY, 119) is True
    assert place(convening, DAILY, 118) is False


def test_a_package_with_no_declared_congress_falls_back_to_its_date() -> None:
    """The bound listing leaves ``congress`` null on the modern parts.

    Refusing a package for want of a declared Congress would drop it from every
    shard, so the date decides when govinfo does not.
    """
    entry = {"packageId": "CREC-2026-08-04", "dateIssued": "2026-08-04", "congress": None}

    assert place(entry, DAILY, 119) is True
    assert place(entry, DAILY, 118) is False


def test_every_bound_part_is_read_because_one_can_straddle_a_congress() -> None:
    """``GPO-CRECB-1890-pt12-v21`` covers "March 4, 1889 to October 1, 1890".

    That is two Congresses in one package, so a part cannot be accepted or
    refused as a unit; its granules are placed individually by their own dates
    and the part itself is always read.
    """
    straddling = {
        "packageId": "GPO-CRECB-1890-pt12-v21",
        "dateIssued": "1890-10-01",
        "congress": "51",
    }

    assert place(straddling, BOUND, 51) is True
    assert place(straddling, BOUND, 50) is True


class _Listing:
    """A govinfo listing endpoint that pages, as the real one does."""

    def __init__(self, pages: list[list[dict]]) -> None:
        self._pages = pages
        self.paths: list[str] = []
        self.marks: list[str] = []

    async def api_json(self, path: str, **params: object) -> dict:
        self.paths.append(path)
        mark = str(params["offsetMark"])
        self.marks.append(mark)
        index = 0 if mark == "*" else int(mark)
        page = self._pages[index] if index < len(self._pages) else []
        # govinfo keeps serving a nextPage on the last page, pointing back at
        # the mark just consumed.
        nxt = min(index + 1, len(self._pages) - 1)
        return {
            "count": sum(len(p) for p in self._pages),
            "nextPage": f"https://api.govinfo.gov/x?offsetMark={nxt}",
            "packages": page,
            "granules": page,
        }


def test_discovery_pages_to_the_end_and_stops() -> None:
    """govinfo answers the final page with a nextPage pointing back at itself.

    Following it without noticing loops for ever against a live API -- the same
    trap the daily update job absorbs, reached here by a different endpoint.
    """
    listing = _Listing(
        [
            [{"packageId": "CREC-2026-08-04", "dateIssued": "2026-08-04",
              "congress": "119"}],
            [{"packageId": "CREC-2026-08-05", "dateIssued": "2026-08-05",
              "congress": "119"}],
        ]
    )
    found = asyncio.run(discover(listing, 119, DAILY))

    assert [e["packageId"] for e in found] == ["CREC-2026-08-04", "CREC-2026-08-05"]
    assert listing.marks == ["*", "1"]


def test_discovery_asks_the_published_service_not_the_collections_one() -> None:
    """``collections/CREC/{start}`` filters on ``lastModified``, not on issue date.

    Asking it for 2026-08-04 returns the issue of 2026-06-18 — restamped
    upstream that week — and misses every issue published before the watermark.
    Enumerating a Congress that way would return whatever govinfo happened to
    touch recently, which is not the question being asked.
    """
    listing = _Listing([[]])
    asyncio.run(discover(listing, 119, DAILY))

    assert listing.paths[0].startswith("published/")
    assert "collections" not in listing.paths[0]


def test_the_bound_window_widens_forward_only() -> None:
    """A bound part is dated by the *last* day it covers.

    So a part covering the final sitting is filed after the Congress ended and
    must still be found, while a part whose last day already falls before the
    Congress convened cannot hold a day inside it. Widening backwards would read
    a year of irrelevant parts at 500-700 granules and 7 MB of MODS each.
    """
    listing = _Listing([[]])
    asyncio.run(discover(listing, 115, BOUND))
    start, end = listing.paths[0].removeprefix("published/").split("/")

    assert date.fromisoformat(start) == date(2017, 1, 3)  # not widened back
    assert date.fromisoformat(end) > date(2019, 1, 2)  # widened forward


# --------------------------------------------------------------------------
# Merging: one day, several packages
# --------------------------------------------------------------------------


def _part(package_id: str, ids: list[str], when: date = date(2025, 3, 11)) -> DayPart:
    """One package's contribution to a day."""
    return DayPart(
        when=when,
        source=PackageRef(package_id, "171", "45"),
        granules=tuple(
            _granule(granule_id=g, package_id=package_id, when=when, page="S1")
            for g in ids
        ),
    )


def test_overlapping_packages_of_one_day_merge_on_the_granule() -> None:
    """11 March 2025 is published as three packages sharing 267 granules.

    ``CREC-2025-03-11`` lists 288, ``-i45`` lists 273 and ``-i46`` lists 21;
    their union is the day's real 308. Merging on the package would count the
    shared ones three times, and committing each package separately would have
    the second commit renumber the first's files inside the same directory.
    """
    parts = [
        _part("CREC-2025-03-11", ["g1", "g2", "g3"]),
        _part("CREC-2025-03-11-i45", ["g2", "g3", "g4"]),
        _part("CREC-2025-03-11-i46", ["g5"]),
    ]
    issues = merge_issues(DAILY, 119, parts)

    assert len(issues) == 1
    assert [g.granule_id for g in issues[0].granules] == ["g1", "g2", "g3", "g4", "g5"]
    assert len(issues[0].sources) == 3


def test_two_distinct_issues_of_one_day_are_both_kept() -> None:
    """``CREC-2023-01-04`` and ``-i2`` share no granule at all.

    They are issues 3 and 2 of 4 January 2023 — two separate publications of one
    calendar day. Deduplicating on the package rather than the granule would
    throw one of them away, losing 11 of the day's 19 documents.
    """
    parts = [
        _part("CREC-2023-01-04", ["a1", "a2"], when=date(2023, 1, 4)),
        _part("CREC-2023-01-04-i2", ["b1", "b2", "b3"], when=date(2023, 1, 4)),
    ]
    issues = merge_issues(DAILY, 118, parts)

    assert len(issues) == 1
    assert len(issues[0].granules) == 5


def test_the_canonical_package_wins_a_granule_listed_by_several() -> None:
    """A rendition is served from the package path that listed it.

    Where several list the same granule the shortest identifier is used, because
    the granule identifiers themselves are prefixed with it —
    ``CREC-2025-03-11-i45`` lists ``CREC-2025-03-11-pt1-PgE201-2``. Picking an
    arbitrary one would fetch from a path that may not serve it.
    """
    parts = [
        _part("CREC-2025-03-11-i45", ["shared"]),
        _part("CREC-2025-03-11", ["shared"]),
    ]
    issues = merge_issues(DAILY, 119, parts)

    assert issues[0].granules[0].package_id == "CREC-2025-03-11"
    assert issues[0].sources[0].package_id == "CREC-2025-03-11"


def test_bound_days_outside_the_congress_are_dropped_at_the_merge() -> None:
    """A bound part straddling a boundary contributes to two shards.

    ``CRECB-2018-pt10`` spans 23 to 25 July 2018, but a part covering a January
    of an odd year spans two Congresses. Each day is placed by its own date, so
    the part is read once and its days land in whichever shard is being built.
    """
    parts = [
        _part("CRECB-2019-pt1", ["a"], when=date(2019, 1, 2)),  # 115th
        _part("CRECB-2019-pt1", ["b"], when=date(2019, 1, 3)),  # 116th
    ]

    assert [i.when for i in merge_issues(BOUND, 115, parts)] == [date(2019, 1, 2)]
    assert [i.when for i in merge_issues(BOUND, 116, parts)] == [date(2019, 1, 3)]


class _Package:
    """govinfo serving one package's granule listing and its MODS."""

    def __init__(self, granules: list[dict], mods: bytes = _MODS) -> None:
        self._granules = granules
        self._mods = mods

    async def api_json(self, path: str, **params: object) -> dict:
        return {"granules": self._granules, "nextPage": ""}

    async def get_bytes(self, url: str) -> bytes:
        return self._mods


def test_a_daily_package_is_one_day_even_when_a_granule_is_dated_earlier(
    tmp_path: Path, monkeypatch
) -> None:
    """``CREC-1994-01-25`` carries granules dated 1993-11-23.

    That is the ordinary way Extensions of Remarks are held over, not an error.
    Splitting the issue by granule date would scatter one day's proceedings
    across two directories and two commits, and put a 1993 commit in a shard
    whose first sitting is in 1994.
    """
    monkeypatch.setattr("uscongress.jobs.record.config.RAW_DIR", tmp_path)
    upstream = _Package(
        [
            {"granuleId": "CREC-1994-01-25-pt1-PgE1", "granuleClass": "EXTENSIONS",
             "title": "held over", "dateIssued": "1993-11-23"},
            {"granuleId": "CREC-1994-01-25-pt1-PgS1", "granuleClass": "SENATE",
             "title": "today", "dateIssued": "1994-01-25"},
        ]
    )
    entry = {"packageId": "CREC-1994-01-25", "dateIssued": "1994-01-25",
             "title": "Congressional Record Volume 140, Issue 1, (January 25, 1994)"}
    parts = asyncio.run(read_package(upstream, 103, DAILY, entry))

    assert [p.when for p in parts] == [date(1994, 1, 25)]
    assert {g.when for g in parts[0].granules} == {date(1993, 11, 23), date(1994, 1, 25)}


def test_a_bound_part_splits_into_the_days_it_covers(
    tmp_path: Path, monkeypatch
) -> None:
    """``CRECB-2018-pt10`` is 534 granules spanning 23 to 25 July 2018.

    A bound part is a volume part, not an issue, so it has no single date to
    commit under. Treating it as one would produce a commit holding three days'
    proceedings under whichever date govinfo happened to stamp on the part.
    """
    monkeypatch.setattr("uscongress.jobs.record.config.RAW_DIR", tmp_path)
    upstream = _Package(
        [
            {"granuleId": "CRECB-2018-pt10-Pg12725-6", "granuleClass": "SENATE",
             "title": "NOMINATION OF BRETT KAVANAUGH", "dateIssued": "2018-07-23"},
            {"granuleId": "CRECB-2018-pt10-Pg12800", "granuleClass": "HOUSE",
             "title": "A LATER DAY", "dateIssued": "2018-07-25"},
        ],
        mods=_MODS_BOUND,
    )
    entry = {"packageId": "CRECB-2018-pt10", "dateIssued": "2018-07-25",
             "title": "Congressional Record (Bound Edition), Volume 164 (2018), Part 10"}
    parts = asyncio.run(read_package(upstream, 115, BOUND, entry))

    assert [p.when for p in parts] == [date(2018, 7, 23), date(2018, 7, 25)]


# --------------------------------------------------------------------------
# The documents themselves
# --------------------------------------------------------------------------


def test_the_rendition_header_is_dropped_and_nothing_else_is() -> None:
    """Every field in that header is reproduced in the document's front matter.

    Volume, issue, section and page all appear there, so keeping the block would
    print each of them twice on 1.1 million documents. Only the *leading* run is
    dropped: a bracketed line further down is body text.
    """
    text = granule_text(_rendition("  The Senate met at noon.\n\n  [Applause.]"))

    assert "Congressional Record Volume 172" not in text
    assert "From the Congressional Record Online" not in text
    assert text.startswith("  The Senate met at noon.")
    assert "[Applause.]" in text


def test_a_page_break_marker_is_body_text_and_survives() -> None:
    """``[[Page S1641]]`` marks where the printed page breaks mid-document.

    It is a citation landmark, not header decoration. Trimming the header by
    eating every leading bracketed line removed it from 7 of 884 sampled
    granules — the ones where a page happened to break at the top — so the trim
    anchors on the credit line, which appears exactly once in all 884.
    """
    text = granule_text(_rendition("[[Page S1641]]\n\n  The Senate met at noon."))

    assert text.startswith("[[Page S1641]]")


def test_a_rendition_with_no_header_keeps_all_its_text() -> None:
    """Guessing at where an unrecognized header ended would cut a speech short.

    "From the" opens plenty of ordinary sentences on the floor, so the search is
    bounded to the first 8 lines and finds nothing rather than trimming on a
    coincidence.
    """
    plain = (
        b"<html><body><pre>\n"
        b"  Mr. THUNE. From the beginning, Mr. President.\n"
        b"</pre></body></html>"
    )

    assert granule_text(plain) == "  Mr. THUNE. From the beginning, Mr. President."


def test_a_bullet_marks_material_that_was_never_spoken() -> None:
    """``<bullet>`` is not decoration.

    The Record uses it to mark statements inserted into the printed proceedings
    that were never made on the floor. Stripping it with the other tags would
    silently assert that 42 of the 884 granules sampled were spoken aloud.
    """
    text = granule_text(_rendition("<bullet> Mr. BARRASSO. Mr. President, I would"))

    assert text.startswith("● Mr. BARRASSO.")


def test_the_gpo_credit_link_and_doc_marker_are_removed() -> None:
    """Three tag forms occur inside the ``<pre>``, and only three.

    Counted across 884 granules of three real packages: 1,786 ``<a>``, 1,060
    ``<DOC>`` and 130 ``<bullet>``. Leaving the first two in would put raw HTML
    into the middle of a fenced block.
    """
    text = granule_text(_rendition("<DOC>\n\n  The Senate met at noon."))

    assert "<DOC>" not in text
    assert "<a href" not in text
    assert "</a>" not in text


def test_a_soft_404_is_rejected_rather_than_rendered() -> None:
    """govinfo answers a missing granule with its web page and HTTP 200.

    44,165 bytes of it, measured. There is no ``<pre>`` in it, which is the only
    thing that separates it from a real rendition — both are HTML, so the
    ``<?xml`` test that guards the bills job cannot be used here.
    """
    with pytest.raises(ValueError, match="soft 404"):
        granule_text(_SOFT_404)


def test_a_soft_404_is_never_cached(tmp_path: Path) -> None:
    """Cached unchecked, it writes a web page under the granule's name.

    The document is then dropped at render time on every later run, for a reason
    nothing records — and the cache makes the mistake permanent.
    """

    class _Client:
        async def get_bytes(self, url: str) -> bytes:
            return _SOFT_404

    target = tmp_path / "CREC-2026-08-04-pt1-PgS4415-10.htm"
    with pytest.raises(ValueError, match="not a rendition"):
        asyncio.run(_fetch_cached(_Client(), "https://example.invalid/x", target))
    assert not target.exists()


def test_mods_is_accepted_although_it_has_no_xml_declaration(tmp_path: Path) -> None:
    """A govinfo MODS document begins ``<mods xmlns:…``, not ``<?xml``.

    The bills job guards its cache by requiring a leading ``<?xml``, and reusing
    that test here would reject every valid MODS document there is — losing page
    numbers, citations, bill references and speakers on every granule, while the
    build carried on looking successful.
    """

    class _Client:
        async def get_bytes(self, url: str) -> bytes:
            return _MODS

    target = tmp_path / "CREC-2026-08-04.xml"
    payload = asyncio.run(
        _fetch_cached(_Client(), "https://example.invalid/x", target, kind="xml")
    )

    assert payload.startswith(b"<mods")
    assert target.exists()


def test_cached_documents_are_reused(tmp_path: Path) -> None:
    """A resumed crawl of 1.1 million renditions must not refetch what it has."""

    class _Client:
        async def get_bytes(self, url: str) -> bytes:  # pragma: no cover
            raise AssertionError("should not fetch when cached")

    target = tmp_path / "g.htm"
    target.write_bytes(_rendition("cached"))
    assert asyncio.run(_fetch_cached(_Client(), "https://example.invalid/x", target))


def test_a_fence_is_longer_than_any_backtick_run_in_the_body() -> None:
    """The Record's own quoting convention produces triple backticks.

    Three of the 884 granules sampled contain them, all from amendatory text of
    the form ``by striking ```national service''' and inserting``. A fixed
    three-backtick fence closes the block early and spills the rest of the
    document onto the page as broken Markdown.
    """
    body = "       (A) by striking ```national service''' and inserting"
    rendered = granule_markdown(_granule(), _issue((_granule(),)), body)

    assert "````\n" in rendered
    assert rendered.count("````") == 2


def test_the_text_is_fenced_rather_than_reflowed() -> None:
    """The Record is set in fixed-width columns and the alignment carries meaning.

    Roll-call tallies, tables of appropriations and the indentation of quoted
    amendatory text are all positional, so unwrapping into prose destroys
    information no later pass could recover.
    """
    body = "  Mr. THUNE. Mr. President,\n       A bill (S. 5221) to prohibit"
    rendered = granule_markdown(_granule(), _issue((_granule(),)), body)

    assert "```\n  Mr. THUNE. Mr. President,\n       A bill" in rendered


def test_a_document_carries_its_citation_speakers_and_measures() -> None:
    """This is the whole cross-reference to the bills repositories.

    Without it a granule is an unattributed wall of text: the reader cannot tell
    who spoke, which measure was before the chamber, or how to cite the page.
    """
    rendered = granule_markdown(_granule(), _issue((_granule(),)), "text")

    assert "granule: CREC-2026-08-04-pt1-PgS4415-10" in rendered
    assert "citation: 172 Cong. Rec. S4415" in rendered
    assert "**Speaking:** Thune, John (T000250) [R-SD]" in rendered
    assert "**Measures:** S. 5221" in rendered


def test_a_held_over_granule_says_which_day_it_was_submitted_for() -> None:
    """A granule dated before its issue is normal, and looks like a filing error.

    ``CREC-1994-01-25`` carries granules dated 1993-11-23. A reader who saw only
    the directory would take the earlier speech for a mistake, so the document
    states the distinction rather than leaving it to be inferred.
    """
    held = _granule(when=date(2026, 7, 30))
    rendered = granule_markdown(held, _issue((held,)), "text")

    assert "Submitted for 2026-07-30" in rendered
    assert "issue of 2026-08-04" in rendered


def test_bill_references_are_plain_text_not_repository_links() -> None:
    """A link to a repository nobody has created is a 404, multiplied.

    ``us-congress-bills`` exists for the 108th to the 119th only, and the Record
    reaches back to the 103rd. Emitting a GitHub link per measure would put the
    same dead link into tens of thousands of documents in the older shards —
    exactly the failure ``uscongress check-links`` was written to catch, at a
    scale it cannot report usefully.
    """
    rendered = granule_markdown(_granule(), _issue((_granule(),)), "text")

    assert "github.com" not in rendered
    assert "S. 5221" in rendered


# --------------------------------------------------------------------------
# MODS
# --------------------------------------------------------------------------


def test_mods_gives_a_granule_its_page_citation_bills_and_speakers() -> None:
    """One request per package replaces one per granule.

    A 288-granule day is 3.4 MB of MODS against 288 separate summary calls,
    which across ~1.1 million granules is the difference between a 34-hour crawl
    and a 67-hour one.
    """
    found = parse_mods(_MODS)
    entry = found["CREC-2026-08-04-pt1-PgS4415-10"]

    assert entry["page"] == "S4415"
    assert entry["citation"] == "172 Cong. Rec. S4415"
    assert entry["speakers"] == (("Thune, John", "T000250", "R", "SD"),)


def test_a_measure_referenced_twice_is_listed_once() -> None:
    """S. 5221 appears twice on page S4415, once as TITLE and once as OTHER.

    Those are two indexing contexts for one reference, so printing both would
    have the document claim the Senate considered the same bill twice.
    """
    assert parse_mods(_MODS)["CREC-2026-08-04-pt1-PgS4415-10"]["measures"] == ("S. 5221",)


def test_the_bound_edition_has_no_granule_extension_at_all() -> None:
    """``CRECB-2018-pt10``'s constituents carry page numbers and nothing else.

    No access id, no granule class, no date, no bill, no member. So a bound
    document can be given its page but not its speakers, and printing an empty
    Speaking line would assert that nobody spoke rather than that govinfo does
    not say.
    """
    found = parse_mods(_MODS_BOUND)
    entry = found["CRECB-2018-pt10-Pg12725-6"]

    assert entry["page"] == "12725"
    assert entry["speakers"] == ()
    assert entry["measures"] == ()

    rendered = granule_markdown(
        _granule(
            granule_id="CRECB-2018-pt10-Pg12725-6", page="12725", citation="",
            measures=(), speakers=(),
        ),
        _issue((_granule(),), edition=BOUND),
        "text",
    )
    assert "**Speaking:**" not in rendered
    assert "**Measures:**" not in rendered


def test_a_granule_with_no_rendition_is_never_requested(
    tmp_path: Path, monkeypatch
) -> None:
    """The 2,083 pre-1999 bound parts are scanned page images.

    Their MODS lists a PDF and no ``.htm``, and asking for the text answers
    HTTP 400 — which the client retries five times. Reading the absence out of
    MODS turns roughly 6,000 doomed requests per pre-1999 Congress into none,
    and makes "there is no text here" something this job knows rather than
    rediscovers one 400 at a time.
    """
    monkeypatch.setattr("uscongress.jobs.record.config.RAW_DIR", tmp_path)
    pdf_only = (
        '<mods xmlns="http://www.loc.gov/mods/v3" '
        'xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<relatedItem type="constituent" ID="id-GPO-CRECB-1970-pt2-1-2">'
        "<titleInfo><title>Senate: January 28, 1970</title></titleInfo>"
        '<relatedItem type="otherFormat" xlink:href="'
        'https://www.govinfo.gov/content/pkg/GPO-CRECB-1970-pt2/pdf/'
        'GPO-CRECB-1970-pt2-1-2.pdf"/>'
        "</relatedItem></mods>"
    ).encode()

    assert parse_mods(pdf_only)["GPO-CRECB-1970-pt2-1-2"]["rendition"] is False
    assert parse_mods(_MODS)["CREC-2026-08-04-pt1-PgS4415-10"]["rendition"] is True
    assert parse_mods(_MODS_BOUND)["CRECB-2018-pt10-Pg12725-6"]["rendition"] is True

    upstream = _Package(
        [{"granuleId": "GPO-CRECB-1970-pt2-1-2", "granuleClass": "CONTENT",
          "title": "Senate: January 28, 1970", "dateIssued": "1970-02-06"}],
        mods=pdf_only,
    )
    entry = {"packageId": "GPO-CRECB-1970-pt2", "dateIssued": "1970-02-06",
             "title": "Volume 116, Part 2 (January 28, 1970 to February 6, 1970)"}
    parts = asyncio.run(read_package(upstream, 91, BOUND, entry))

    assert parts[0].granules[0].url == ""


def test_a_granule_missing_from_mods_is_still_fetched(
    tmp_path: Path, monkeypatch
) -> None:
    """Absent metadata is not evidence that there is no text.

    A MODS document that failed to parse yields an empty enrichment for every
    granule in the package. Reading that as "no rendition" would silently drop a
    whole day of real proceedings, so only an explicit absence inside a MODS
    entry that *was* read counts.
    """
    monkeypatch.setattr("uscongress.jobs.record.config.RAW_DIR", tmp_path)
    upstream = _Package(
        [{"granuleId": "CREC-2026-08-04-pt1-PgS4415-99", "granuleClass": "SENATE",
          "title": "not in the mods", "dateIssued": "2026-08-04"}],
        mods=_MODS,
    )
    entry = {"packageId": "CREC-2026-08-04", "dateIssued": "2026-08-04", "title": ""}
    parts = asyncio.run(read_package(upstream, 119, DAILY, entry))

    assert parts[0].granules[0].url.endswith(
        "/CREC-2026-08-04/html/CREC-2026-08-04-pt1-PgS4415-99.htm"
    )


def test_unreadable_mods_loses_metadata_but_not_the_text() -> None:
    """The granule listing already carries identity, class, date and title.

    So a package whose MODS is damaged still builds; what is lost is page
    numbers and cross-references, not the proceedings. Failing the day instead
    would trade a complete record with thin metadata for no record at all.
    """
    assert parse_mods(b"<mods><relatedItem") == {}


def test_a_package_title_carries_the_volume_and_issue_number() -> None:
    """The ``published`` listing has no volume or issue field.

    Its entries carry only congress, dateIssued, docClass, lastModified,
    packageId, packageLink and title — so reading them from the title is what
    keeps a day self-describing without a second request per package.
    """
    assert parse_title(
        "Congressional Record Volume 171, Issue 45, (March 11, 2025)"
    ) == ("171", "45")
    assert parse_title(
        "Congressional Record (Bound Edition), Volume 164 (2018), Part 10"
    ) == ("164", "10")
    assert parse_title("Volume 116, Part 2 (January 28, 1970 to February 6, 1970)") == (
        "116",
        "2",
    )


# --------------------------------------------------------------------------
# Layout and ordering
# --------------------------------------------------------------------------


def test_documents_are_numbered_in_printed_order() -> None:
    """Page prefix, then page number, then the ordinal on the identifier.

    Lexicographic order puts page S4415-10 before S4415-2, which reverses the
    floor. A reader following ``git show`` down a day would see the vote before
    the debate that produced it.
    """
    granules = tuple(
        _granule(granule_id=f"CREC-2026-08-04-pt1-PgS4415-{n}", page="S4415", title=f"t{n}")
        for n in (10, 2, 9)
    )
    files, _ = issue_documents(_issue(granules), {g.granule_id: "x" for g in granules})

    assert sorted(p for p in files if "senate" in p) == [
        "2026/08-04/senate/001-t2.md",
        "2026/08-04/senate/002-t9.md",
        "2026/08-04/senate/003-t10.md",
    ]


def test_the_bound_edition_orders_on_page_number_alone() -> None:
    """Its pagination is one continuous run with no chamber prefix.

    The daily edition numbers the Senate S1..., the House H1... and Extensions
    E1..., so the prefix orders the sections; the bound edition prints them in
    one sequence, so ranking a missing prefix anywhere but first would scramble
    every bound day.
    """
    granules = tuple(
        _granule(granule_id=f"CRECB-2018-pt10-Pg{p}", page=str(p), title=f"p{p}",
                 section="SENATE")
        for p in (12726, 12725)
    )
    files, _ = issue_documents(
        _issue(granules, edition=BOUND), {g.granule_id: "x" for g in granules}
    )

    assert "2026/08-04/senate/001-p12725.md" in files
    assert "2026/08-04/senate/002-p12726.md" in files


def test_a_granule_with_no_text_leaves_a_hole_in_the_numbering() -> None:
    """Ordinals are assigned over every *listed* granule, not every fetched one.

    Closing the gap would renumber every file after the missing one, so the next
    run — after govinfo repaired it, or after a transient failure cleared —
    would rewrite the whole day for a change nobody made.
    """
    granules = tuple(
        _granule(granule_id=f"g{n}", page="S1", title=f"t{n}") for n in (1, 2, 3)
    )
    files, written = issue_documents(_issue(granules), {"g1": "x", "g3": "z"})

    assert sorted(written) == [
        "2026/08-04/senate/001-t1.md",
        "2026/08-04/senate/003-t3.md",
    ]


def test_each_section_gets_its_own_directory() -> None:
    """The Record prints four separately paginated sections each day.

    The House and the Senate sit at the same time, so one flat sequence would
    interleave two chambers' proceedings into a single unreadable run.
    """
    granules = (
        _granule(granule_id="s", section="SENATE", page="S1", title="s"),
        _granule(granule_id="h", section="HOUSE", page="H1", title="h"),
        _granule(granule_id="e", section="EXTENSIONS", page="E1", title="e"),
        _granule(granule_id="d", section="DAILYDIGEST", page="D1", title="d"),
    )
    files, _ = issue_documents(_issue(granules), {g.granule_id: "x" for g in granules})

    assert sorted(files) == [
        "2026/08-04/README.md",
        "2026/08-04/daily-digest/001-d.md",
        "2026/08-04/extensions/001-e.md",
        "2026/08-04/house/001-h.md",
        "2026/08-04/senate/001-s.md",
    ]


def test_an_unknown_section_is_kept_rather_than_dropped() -> None:
    """A granule dropped for having an unrecognized class vanishes silently.

    Nothing outside the five known classes appeared in the packages sampled,
    which is the reason to handle it rather than a reason not to: the failure
    would be a missing speech with nothing to say it ever existed.
    """
    odd = _granule(granule_id="x", section="SOMETHING-NEW", page="S1", title="odd")
    files, _ = issue_documents(_issue((odd,)), {"x": "body"})

    assert "2026/08-04/other/001-odd.md" in files


def test_a_title_that_slugs_to_nothing_still_gets_a_distinct_file() -> None:
    """Otherwise every such granule in a day collides on one filename.

    fast-import takes the last write, so the collision is silent: the day would
    simply hold fewer documents than its own index lists.
    """
    assert slug("---", "CREC-2026-08-04-pt1-PgS1") == "crec-2026-08-04-pt1-pgs1"
    assert slug("", "") == "untitled"


def test_the_day_index_names_every_package_it_was_built_from() -> None:
    """A day assembled from three overlapping packages is a fact about the day.

    A reader checking this repository against govinfo needs to know which
    packages it was assembled from, or the granule count will not reconcile with
    any single one of them.
    """
    granules = (_granule(granule_id="g", page="S1", title="t"),)
    issue = _issue(
        granules,
        sources=(
            PackageRef("CREC-2025-03-11", "171", "45"),
            PackageRef("CREC-2025-03-11-i46", "171", "46"),
        ),
    )
    files, _ = issue_documents(issue, {"g": "x"})
    index = files["2026/08-04/README.md"]

    assert "CREC-2025-03-11" in index
    assert "CREC-2025-03-11-i46" in index
    assert "Volume 171, issue 46" in index


def test_the_commit_message_states_a_partially_built_day() -> None:
    """A day with holes must say so where its own history is read.

    Recording it only in ``GAPS.md`` leaves the commit claiming a complete
    issue, and the commit is what anyone reading ``git log`` sees first.
    """
    message = commit_message(_issue((_granule(),)), written=61, missing=1)

    assert message.startswith("Congressional Record — 2026-08-04")
    assert "61 documents" in message
    assert "Granules-Without-Text: 1" in message
    assert "Measures-Referenced: 1" in message


def test_duplicate_issue_labels_are_not_repeated_on_the_commit() -> None:
    """``CREC-2025-03-11`` and ``-i45`` are both titled Volume 171, Issue 45.

    Printing the label once per package makes the commit read as though the day
    held two issues when it held one published twice.
    """
    issue = _issue(
        (_granule(),),
        sources=(
            PackageRef("CREC-2025-03-11", "171", "45"),
            PackageRef("CREC-2025-03-11-i45", "171", "45"),
            PackageRef("CREC-2025-03-11-i46", "171", "46"),
        ),
    )
    message = commit_message(issue, written=1, missing=0)

    assert "Issue:    Volume 171, issue 45; Volume 171, issue 46" in message


# --------------------------------------------------------------------------
# The repository
# --------------------------------------------------------------------------


def _build(repo: GitRepo, branch: str, days: list[date]) -> None:
    """Commit one accumulating issue day per date."""
    for when in days:
        granule = _granule(granule_id=f"g-{when}", page="S1", title="t")
        issue = _issue((granule,), when=when, edition=branch)
        files, written = issue_documents(issue, {granule.granule_id: "body"})
        with repo.fast_import() as stream:
            stream.commit(
                branch, files, commit_message(issue, len(written), 0), when,
                whole_tree=False,
            )


def test_the_history_accumulates_rather_than_replacing_the_tree(tmp_path: Path) -> None:
    """The Record is a serial publication: an issue succeeds, it does not revise.

    So each commit adds a day and leaves the rest in place, and the diff of a
    commit is exactly what was published that day. Replacing the whole tree
    instead — which is what fast-import does by default — would leave the branch
    holding only the most recent issue and make every diff a full churn.
    """
    repo = GitRepo(tmp_path / "us-congress-record-119")
    repo.init()
    _build(repo, DAILY, [date(2026, 8, 3), date(2026, 8, 4)])

    files = repo.list_files(DAILY)
    assert "2026/08-03/README.md" in files
    assert "2026/08-04/README.md" in files

    changed = subprocess.run(
        ["git", "-C", str(repo.path), "show", "--name-only", "--format=", DAILY],
        capture_output=True, text=True,
    ).stdout.split()
    assert all(p.startswith("2026/08-04/") for p in changed)


def test_built_days_are_read_from_the_tree_for_resumption(tmp_path: Path) -> None:
    """Resumption must not cost a rebuild.

    A finished shard holds ~63,000 documents, so the question "which days are
    already here" is answered with one ``ls-tree`` rather than by reading commit
    messages or by ``read_tree``, which costs one ``git show`` per file.
    """
    repo = GitRepo(tmp_path / "us-congress-record-119")
    repo.init()
    _build(repo, DAILY, [date(2026, 8, 3), date(2026, 8, 4)])

    assert built_days(repo, DAILY) == {date(2026, 8, 3), date(2026, 8, 4)}
    assert built_days(repo, BOUND) == set()


def test_the_two_editions_are_separate_branches(tmp_path: Path) -> None:
    """CREC and CRECB are two publications of the same proceedings.

    The bound edition appears years later with corrections folded in, so
    interleaving them on one branch would put 2018 in the log twice. Apart, the
    same day in each is diffable — which is the only way to see what changed
    between what was said and what was printed permanently.
    """
    repo = GitRepo(tmp_path / "us-congress-record-115")
    repo.init()
    _build(repo, DAILY, [date(2018, 7, 23)])
    _build(repo, BOUND, [date(2018, 7, 23)])
    refs = repo.ref_map()

    # `main` carries only the artifacts and the gap record, so it holds no
    # commit until one of those is written -- the editions never touch it.
    assert set(refs) == {DAILY, BOUND}
    assert refs[DAILY] != refs[BOUND]
    assert built_days(repo, DAILY) == built_days(repo, BOUND) == {date(2018, 7, 23)}


def _report(**daily: object) -> dict[str, dict[str, object]]:
    """A finished run's record for one Congress."""
    base: dict[str, object] = {
        "packages": 422, "days_present": 4, "documents_present": 400,
        "first": "2017-01-03", "last": "2017-01-06",
        "missing": [], "unreadable": [], "days_empty": 0,
    }
    base.update(daily)
    return {
        DAILY: base,
        BOUND: {"packages": 0, "days_present": 0, "documents_present": 0,
                "missing": [], "unreadable": [], "days_empty": 0},
    }


def test_a_congress_before_1994_says_its_gap_is_permanent() -> None:
    """The machine-readable Record begins in 1994, not 1873.

    CRECB holds 2,420 parts back to 1873 and only the 337 from 1999 have an HTML
    rendition; the other 2,083 are scanned page images whose ``/htm`` answers
    HTTP 400. An empty repository that did not explain itself would read as a
    build that quietly failed, which is the one thing this project treats as
    worse than a gap.
    """
    text = gap_documents(80, {DAILY: {"packages": 0}, BOUND: {"packages": 30}})["GAPS.md"]

    assert "There is no machine-readable text for this Congress" in text
    assert "HTTP 400" in text
    assert "2,083" in text


def test_an_edition_that_was_not_asked_for_is_not_reported_as_absent() -> None:
    """"Not looked at" and "looked at and not there" are different claims.

    A run restricted to ``--edition daily`` says nothing about whether the bound
    edition exists, and a gap record that reported it as missing upstream would
    be asserting a fact the run never checked — the same class of error as an
    unexplained absence, pointed the other way.
    """
    partial = gap_documents(115, {DAILY: _report()[DAILY]})["GAPS.md"]
    full = gap_documents(115, _report())["GAPS.md"]

    assert "Not examined by the run that wrote this" in partial
    assert "bound edition does not exist for this Congress yet" not in partial
    assert "Not examined by the run that wrote this" not in full


def test_a_recent_congress_explains_the_missing_bound_branch() -> None:
    """The newest bound volume govinfo carries is 2018.

    So a shard from the 116th on has one branch, and a reader comparing it with
    the 115th would otherwise conclude that half the build failed rather than
    that GPO has not published the volume yet.
    """
    text = gap_documents(119, _report())["GAPS.md"]

    assert "The bound edition does not exist for this Congress yet" in text
    assert "2018" in text


def test_the_coverage_table_reports_the_branch_not_the_run() -> None:
    """A count of what this run wrote would rewrite ``main`` on every invocation.

    The figures are read back out of the branches, so a re-run that finds
    everything already built renders the identical document and commits nothing
    — which is what makes the job safe to leave running.
    """
    text = gap_documents(115, _report())["GAPS.md"]

    assert "| Daily edition (CREC) | `daily` | 4 | 400 | 2017-01-03 | 2017-01-06 |" in text
    assert "read back out of the branch itself" in text


def test_missing_granules_are_listed_until_there_are_too_many() -> None:
    """Past a couple of hundred the list stops being readable and becomes a grep.

    Same threshold and same reasoning as the bills job's gap table: a table of
    thousands of rows is past the point where a reader can use it and past the
    point where forges render it reliably.
    """
    small = gap_documents(115, _report(missing=[f"g{i}" for i in range(5)]))
    large = gap_documents(115, _report(missing=[f"g{i}" for i in range(900)]))

    assert sorted(small) == ["GAPS.md"]
    assert sorted(large) == ["GAPS.md", "GAPS.tsv"]
    assert large["GAPS.tsv"].count("\n") == 901  # header + 900 rows


def test_the_companion_is_linked_only_when_it_is_written() -> None:
    """A link and the file it points at must never drift apart.

    ``check-links`` reads every root-level Markdown file on ``main``, so a link
    to a ``GAPS.tsv`` that was not written fails the whole project's link check.
    """
    small = gap_documents(115, _report(missing=["g1"]))
    large = gap_documents(115, _report(missing=[f"g{i}" for i in range(900)]))

    assert ("GAPS.tsv" in small["GAPS.md"]) is ("GAPS.tsv" in small)
    assert ("GAPS.tsv" in large["GAPS.md"]) is ("GAPS.tsv" in large)


def test_the_bills_repository_is_linked_only_when_it_exists() -> None:
    """``us-congress-bills`` exists for the 108th to the 119th; the Record is older.

    Emitting the link unconditionally puts a 404 on the front page of every
    shard below the 108th — the precise failure that made ``check-links``
    necessary, which found the same dead link repeated across thirteen
    repositories.
    """
    linked = gap_documents(115, _report(), bills_repo="us-congress-bills-115")["GAPS.md"]
    unlinked = gap_documents(104, _report(), bills_repo="")["GAPS.md"]

    assert "https://github.com/junxit/us-congress-bills-115" in linked
    assert "github.com" not in unlinked
    assert "as plain text rather than as links" in unlinked


def test_writing_gaps_preserves_the_readme_and_license(tmp_path: Path) -> None:
    """fast-import sets the whole tree, so main must be read before writing.

    Writing only the gap record would delete the artifacts that
    ``uscongress artifacts`` puts on this branch — silently, on the next build.
    """
    repo = GitRepo(tmp_path / "us-congress-record-115")
    repo.init()
    with repo.fast_import() as stream:
        stream.commit("main", {"README.md": "readme\n", "LICENSE": "license\n"}, "Artifacts")

    _write_gaps(repo, 115, _report())

    assert sorted(repo.read_tree("main")) == ["GAPS.md", "LICENSE", "README.md"]


def test_writing_gaps_twice_makes_only_one_commit(tmp_path: Path) -> None:
    """The gap record is a pure function of the repository, so it must settle.

    A second run that found nothing new has nothing to say, and a ``main`` that
    gained a commit every time the job ran would make the branch's history
    useless as a record of when coverage actually changed.
    """
    repo = GitRepo(tmp_path / "us-congress-record-115")
    repo.init()
    _write_gaps(repo, 115, _report())
    first = repo.ref_map()["main"]
    _write_gaps(repo, 115, _report())

    assert repo.ref_map()["main"] == first


def test_a_sibling_shard_is_not_judged_by_what_is_on_this_machine(
    monkeypatch, tmp_path: Path
) -> None:
    """Reading disk alone published a falsehood to a public repository.

    The scheduled Record job runs on a fresh machine and fetches only the shard
    it is building, so `us-congress-bills-119` was not there and GAPS.md went
    out saying the sibling "does not exist" -- of a repository carrying 18,000
    branches. It also flip-flopped: a run from a machine that had the bills
    shard wrote the true version back, so `main` would have churned between the
    two for ever.
    """
    monkeypatch.setattr(record.config, "REPOS_DIR", tmp_path / "nothing-here")

    assert record._sibling_published("us-congress-bills-119")  # noqa: SLF001


def test_an_unknown_repository_family_is_not_linked(
    monkeypatch, tmp_path: Path
) -> None:
    """Absent evidence, do not link: a 404 is repeated across every document."""
    monkeypatch.setattr(record.config, "REPOS_DIR", tmp_path / "nothing-here")

    assert not record._sibling_published("us-congress-nonesuch-119")  # noqa: SLF001
