"""Tests for OLRC release-point parsing.

Every release-point fixture below is real text copied from OLRC's pages:
``priorreleasepoints.htm`` through September 2026, ``priorreleasepoints.html``
since. These cover the cases that would otherwise corrupt the history silently.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date

import pytest

from uscongress.jobs.uscode import (
    CURRENT_URL,
    PRIOR_URL,
    ReleasePoint,
    discover,
    parse_current,
    parse_prior,
)


def _entry(law_spec: str, text: str, congress: str = "119") -> str:
    """Build one release-point list item as the page writes it."""
    return (
        f'<li class="releasepoint"><a class="releasepoint" '
        f'href="releasepoints/us/pl/{congress}/{law_spec}/usc-rp@{congress}-{law_spec}.htm">'
        f"{text}</a></li>"
    )


def test_current_release_point_is_commented_out() -> None:
    """The newest entry is HTML-commented; parsing must not pick it up here.

    It is added back from download.shtml. Treating the comment as live would
    double-count it; ignoring it entirely would lose the newest snapshot.
    """
    html = (
        "<!--" + _entry("102", "Public Law 119-102 (07/12/2026), affecting titles 7.") + "-->"
        + _entry("100", "Public Law 119-100 (06/26/2026), affecting title 47.")
    )
    entries = parse_prior(html)
    assert len(entries) == 1
    assert entries[0]["law_spec"] == "100"


def test_not_suffix_yields_exclusions() -> None:
    """``102not101`` means "through 119-102 but excluding 119-101"."""
    html = _entry(
        "102not101",
        "Public Law 119-102 (07/12/2026) , except 119-101, affecting titles 5, 16.",
    )
    (entry,) = parse_prior(html)
    assert entry["law_number"] == 102
    assert entry["excludes"] == (101,)
    assert entry["titles"] == (5, 16)


def test_multiple_exclusions() -> None:
    """Three-way exclusions occur, e.g. ``296not287not291not295``."""
    html = _entry(
        "296not287not291not295",
        "Public Law 113-296 (12/19/2014), except 113-287, 113-291, 113-295, "
        "affecting titles 1, 2, 4.",
        congress="113",
    )
    (entry,) = parse_prior(html)
    assert entry["excludes"] == (287, 291, 295)


def test_undated_update_entry_is_kept() -> None:
    """``115-40u1`` has no bracketed date; it must still parse.

    Dropping it would lose an editorial reclassification from the history.
    """
    html = _entry(
        "40u1",
        "Public Law 115-40, with additional updates for Title 7, ch. 17 and Title 43, "
        "ch. 28 editorial reclassifications (effective July 1, 2017), affecting "
        "titles 5, 7, 10.",
        congress="115",
    )
    (entry,) = parse_prior(html)
    assert entry["law_number"] == 40
    assert entry["published"] is None
    assert entry["titles"] == (5, 7, 10)


def test_single_digit_date_and_abbreviated_label() -> None:
    """``116-155`` writes "Pub. L." with a single-digit date, ``8/8/2020``."""
    html = _entry(
        "155",
        "Pub. L. 116-155 (8/8/2020), affecting titles 5, 8, 22.",
        congress="116",
    )
    (entry,) = parse_prior(html)
    assert entry["published"] == date(2020, 8, 8)


def test_combined_exclusion_and_update_suffix() -> None:
    """``145not128u1`` carries both an exclusion and an update suffix."""
    html = _entry(
        "145not128u1",
        "Public Law 113-145 (08/20/2014), except 113-128, affecting title 38.",
        congress="113",
    )
    (entry,) = parse_prior(html)
    assert entry["law_number"] == 145
    assert entry["excludes"] == (128,)


def test_parse_current_finds_release_point() -> None:
    """The current release point is discovered from the download page."""
    html = '<a href="releasepoints/us/pl/119/102/xml_uscAll@119-102.zip">XML</a>'
    assert parse_current(html) == (119, "102")


def test_release_point_urls() -> None:
    """Tag and download URLs follow OLRC's ``@congress-lawspec`` convention."""
    point = ReleasePoint(
        congress=119,
        law_spec="102not101",
        law_number=102,
        excludes=(101,),
        published=date(2026, 7, 12),
        titles=(5, 16),
        order=385,
        is_current=False,
    )
    assert point.tag == "pl-119-102not101"
    assert point.xml_url.endswith("/119/102not101/xml_uscAll@119-102not101.zip")
    assert point.title_xml_url("26").endswith("xml_usc26@119-102not101.zip")


def test_update_release_points_try_both_archive_names() -> None:
    """Archive naming is almost consistent, but not quite.

    16 of the 17 update release points name the archive with their ``u``
    suffix; ``114-219u1`` alone names it without. Both are tried rather than
    special-casing one release point.

    The fallback keeps the *directory* fixed and varies only the filename: the
    archive in the plain ``/219/`` directory is a different, smaller file
    (90,810,781 vs 91,038,779 bytes), so falling back to it would silently
    substitute the wrong snapshot.
    """
    point = ReleasePoint(
        congress=114,
        law_spec="219u1",
        law_number=219,
        excludes=(),
        published=date(2016, 7, 29),
        titles=(5,),
        order=78,
        is_current=False,
    )
    candidates = point.xml_url_candidates
    assert len(candidates) == 2
    assert candidates[0].endswith("/219u1/xml_uscAll@114-219u1.zip")
    assert candidates[1].endswith("/219u1/xml_uscAll@114-219.zip")
    # Both must live in the u1 directory.
    assert all("/114/219u1/" in url for url in candidates)


def test_ordinary_release_points_have_a_single_candidate() -> None:
    """Only update release points need a fallback."""
    point = ReleasePoint(
        congress=119,
        law_spec="102not101",
        law_number=102,
        excludes=(101,),
        published=date(2026, 7, 12),
        titles=(5,),
        order=385,
        is_current=False,
    )
    assert len(point.xml_url_candidates) == 1


def _listed(law_spec: str, text: str, congress: str = "119") -> str:
    """Build one release-point list item as ``priorreleasepoints.html`` writes it."""
    return (
        f'<li class="releasepoint"><a class="releasepoint" '
        f'href="download/{congress}-{law_spec}.html">{text}</a></li>'
    )


#: The two newest entries on ``priorreleasepoints.html`` on 2026-10-24.
_NEW_LISTING = _listed(
    "121", "Public Law 119-121 (October 9, 2026), affecting title 15."
) + _listed(
    "120", "Public Law 119-120 (October 7, 2026), affecting titles 31, 42."
)

#: The current release point's archive link as ``download.html`` writes it.
_NEW_LINK = (
    '<a href="download/releasepoints/us/pl/119/124/xml_uscAll@119-124.zip">[XML]</a>'
)

#: The current release point as ``download.html`` dates and links it.
_NEW_DOWNLOAD = (
    "All files are current through Public Law 119-124 (October 15, 2026). "
    + _NEW_LINK
)

#: What ``download/download.shtml`` serves at HTTP 200 since October 2026.
_MAINTENANCE = (
    "<title>Under Maintenance</title><p>Site is currently under maintenance</p>"
)


class _Site:
    """Serve fixed pages by URL, standing in for uscode.house.gov."""

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages

    async def get_bytes(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        """Return the page at ``url``."""
        return self._pages[url].encode()


def test_new_listing_links_a_page_per_entry() -> None:
    """Since 2026-10-22 an entry links a page of its own and spells out its date.

    The old pattern finds none of the 22 entries on the new page, and an
    unparsed date would leave the reverse-chronological check nothing to check.
    """
    html = _listed(
        "102not101",
        "Public Law 119-102not101 (July 12, 2026), except 119-101, "
        "affecting titles 5, 16.",
    )
    (entry,) = parse_prior(html)
    assert entry["law_spec"] == "102not101"
    assert entry["excludes"] == (101,)
    assert entry["published"] == date(2026, 7, 12)
    assert entry["titles"] == (5, 16)


def test_discover_reads_the_new_pages() -> None:
    """The current release point, absent from the new listing, is added as newest."""
    site = _Site({PRIOR_URL: _NEW_LISTING, CURRENT_URL: _NEW_DOWNLOAD})
    points = asyncio.run(discover(site))
    assert [p.tag for p in points] == ["pl-119-120", "pl-119-121", "pl-119-124"]
    assert [p.published for p in points] == [
        date(2026, 10, 7),
        date(2026, 10, 9),
        date(2026, 10, 15),
    ]
    assert [p.is_current for p in points] == [False, False, True]


def test_the_current_release_point_is_dated_from_the_download_page() -> None:
    """Undated, a release point built while current is committed as never dated.

    ``pl-119-102`` was built while current, so its commit reads "date not
    published" and 2026-08-04, the day it was built, where OLRC gives July 12.
    The download page states the date itself; a page that does not leaves the
    release point undated rather than guessing.
    """
    dated = _Site({PRIOR_URL: _NEW_LISTING, CURRENT_URL: _NEW_DOWNLOAD})
    *_, current = asyncio.run(discover(dated))
    assert current.published == date(2026, 10, 15)

    undated = _Site({PRIOR_URL: _NEW_LISTING, CURRENT_URL: _NEW_LINK})
    *_, current = asyncio.run(discover(undated))
    assert current.tag == "pl-119-124"
    assert current.published is None


@pytest.mark.parametrize("unreadable", [PRIOR_URL, CURRENT_URL])
def test_a_page_with_no_release_point_is_an_error(unreadable: str) -> None:
    """Reading nothing is not "nothing new".

    From 2026-10-01 both old addresses answered HTTP 200 with no release point
    in them, so discovery returned nothing, nothing was missing, and the daily
    check reported the repository "current at 0" on every run for three weeks.
    """
    pages = {PRIOR_URL: _NEW_LISTING, CURRENT_URL: _NEW_DOWNLOAD}
    pages[unreadable] = _MAINTENANCE
    with pytest.raises(ValueError, match=re.escape(unreadable)):
        asyncio.run(discover(_Site(pages)))
