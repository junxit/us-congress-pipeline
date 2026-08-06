"""Tests for building a Congress's bills repository from BILLSTATUS.

The fixtures mirror H.R. 588 of the 113th Congress, whose seven text versions
exercise every ordering trap at once: it is amended in both chambers, its
enrolled version carries no date, and its BILLSTATUS lists a Public Law entry
that belongs to a different collection.
"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import date

import pytest

from uscongress.gitbuild import GitRepo
from uscongress.jobs.bills import (
    Measure,
    _fetch_cached,
    _write_gaps,
    branch_of,
    TextVersion,
    commit_message,
    metadata_markdown,
    parse_status,
)

_URL = "https://www.govinfo.gov/content/pkg/BILLS-113hr588{code}/xml/BILLS-113hr588{code}.xml"


def _status(versions: str, extra: str = "") -> bytes:
    """Wrap version items in a minimal BILLSTATUS document."""
    return (
        "<billStatus><bill>"
        "<number>588</number><type>HR</type><congress>113</congress>"
        "<introducedDate>2013-02-06</introducedDate>"
        "<title>Vietnam Veterans Donor Acknowledgment Act of 2013</title>"
        "<sponsors><item><fullName>Rep. Young, Don [R-AK-At Large]</fullName>"
        "<bioguideId>Y000033</bioguideId></item></sponsors>"
        f"{extra}"
        f"<textVersions>{versions}</textVersions>"
        "</bill></billStatus>"
    ).encode()


def _item(label: str, when: str, code: str) -> str:
    """One textVersions entry."""
    url = _URL.format(code=code)
    return (
        f"<item><type>{label}</type><date>{when}</date>"
        f"<formats><item><url>{url}</url></item></formats></item>"
    )


def test_versions_are_ordered_by_date() -> None:
    """The bill documents cannot order themselves.

    ``action-date`` is missing from engrossed, enrolled and received versions,
    and a reported version repeats the introduction date, so ordering has to
    come from BILLSTATUS.
    """
    status = _status(
        _item("Engrossed Amendment Senate", "2013-06-03T04:00:00Z", "eas")
        + _item("Introduced in House", "2013-02-06T05:00:00Z", "ih")
        + _item("Reported in House", "2013-04-09T04:00:00Z", "rh")
    )
    assert [v.code for v in parse_status(status).versions] == ["ih", "rh", "eas"]


def test_public_law_entries_are_excluded() -> None:
    """A Public Law entry points at PLAW, which is enacted law, not a bill."""
    status = _status(
        _item("Introduced in House", "2013-02-06", "ih")
        + "<item><type>Public Law</type><date>2013-07-19</date><formats><item>"
        "<url>https://www.govinfo.gov/content/pkg/PLAW-113publ21/xml/PLAW-113publ21.xml</url>"
        "</item></formats></item>"
    )
    versions = parse_status(status).versions
    assert [v.code for v in versions] == ["ih"]


def test_undated_versions_sort_last() -> None:
    """An undated entry is usually the enrolled bill, the end of the process.

    Sorting it first would invert the whole branch.
    """
    status = _status(
        "<item><type>Enrolled Bill</type><date></date><formats><item>"
        f"<url>{_URL.format(code='enr')}</url></item></formats></item>"
        + _item("Introduced in House", "2013-02-06", "ih")
    )
    versions = parse_status(status).versions
    assert [v.code for v in versions] == ["ih", "enr"]
    assert versions[-1].when is None


def test_version_code_comes_from_the_filename() -> None:
    """The code distinguishes versions that share a date."""
    status = _status(_item("Engrossed Amendment Senate", "2013-06-03", "eas"))
    assert parse_status(status).versions[0].code == "eas"


def test_branch_name_and_citation() -> None:
    """A branch should be predictable from a citation."""
    measure = parse_status(_status(_item("Introduced in House", "2013-02-06", "ih")))
    assert measure.branch == "hr-588"
    assert measure.citation == "H.R. 588"


def _measure(**overrides: object) -> Measure:
    """A measure with sensible defaults for metadata tests."""
    base = {
        "congress": "113",
        "kind": "hr",
        "number": "588",
        "title": "Vietnam Veterans Donor Acknowledgment Act of 2013",
        "introduced": date(2013, 2, 6),
        "sponsor": "Rep. Young, Don [R-AK-At Large]",
        "sponsor_id": "Y000033",
        "cosponsors": (
            ("Rep. Early [D-XX-1]", "E000001", date(2013, 2, 6)),
            ("Rep. Late [D-XX-2]", "L000002", date(2013, 6, 20)),
        ),
        "committees": ("House Natural Resources",),
        "actions": (
            (date(2013, 2, 6), "Introduced in House."),
            (date(2013, 7, 18), "Became Public Law No: 113-21."),
        ),
        "law": "Public Law 113-21",
        "versions": (),
    }
    base.update(overrides)
    return Measure(**base)  # type: ignore[arg-type]


def test_metadata_does_not_leak_the_future() -> None:
    """BILLSTATUS is one present-day snapshot for the whole measure.

    Written unfiltered onto every commit, the introduced text would already
    report that the bill became law -- the same trap as Table III's present-day
    classification in the US Code repository.
    """
    introduced = TextVersion("Introduced in House", date(2013, 2, 6), "u", "ih")
    text = metadata_markdown(_measure(), introduced)

    assert "Became Public Law" not in text
    assert "E000001" in text  # cosponsor signed on the day of introduction
    assert "L000002" not in text  # signed months later


def test_metadata_accumulates_by_the_final_version() -> None:
    """By the enrolled bill the full record should be present."""
    enrolled = TextVersion("Enrolled Bill", date(2013, 7, 19), "u", "enr")
    text = metadata_markdown(_measure(), enrolled)

    assert "Became Public Law No: 113-21." in text
    assert "L000002" in text


def test_commit_message_reports_version_and_source() -> None:
    """Each commit should be self-describing."""
    version = TextVersion(
        "Engrossed Amendment Senate", date(2013, 6, 3), "https://example.invalid/x", "eas"
    )
    message = commit_message(_measure(), version)

    assert message.startswith("H.R. 588 Engrossed Amendment Senate")
    assert "Version:  Engrossed Amendment Senate (eas)" in message
    assert "Date:     2013-06-03" in message
    assert "Source: https://example.invalid/x" in message
    assert "Sponsored-By: Y000033" in message


def test_commit_message_is_explicit_about_a_missing_date() -> None:
    """An undated version is committed under a carried-forward date.

    Saying so keeps the commit honest about what upstream actually recorded.
    """
    version = TextVersion("Enrolled Bill", None, "https://example.invalid/x", "enr")
    assert "not recorded upstream" in commit_message(_measure(), version)


def test_a_document_without_a_bill_is_rejected() -> None:
    """A truncated or unrelated document must not build an empty branch."""
    with pytest.raises(ValueError, match="not a BILLSTATUS document"):
        parse_status(b"<billStatus></billStatus>")


def test_variant_billstatus_schema_is_accepted() -> None:
    """govinfo emits two BILLSTATUS spellings.

    Most measures use ``<number>`` and ``<type>``; a minority -- H.R. 4200 of
    the 113th among them -- use ``<billNumber>`` and ``<billType>``. Recognising
    only the first drops them silently, so the branch never appears and nothing
    says why.
    """
    status = (
        "<billStatus><bill>"
        "<billNumber>4200</billNumber><billType>HR</billType>"
        "<congress>113</congress><title>A measure</title>"
        "<textVersions><item><type>Introduced in House</type>"
        "<date>2014-03-11</date><formats><item>"
        "<url>https://www.govinfo.gov/content/pkg/BILLS-113hr4200ih/xml/BILLS-113hr4200ih.xml</url>"
        "</item></formats></item></textVersions>"
        "</bill></billStatus>"
    ).encode()
    measure = parse_status(status)

    assert measure.branch == "hr-4200"
    assert measure.citation == "H.R. 4200"
    assert [v.code for v in measure.versions] == ["ih"]


def test_soft_404_is_rejected_rather_than_cached(tmp_path) -> None:
    """govinfo answers a missing document with HTML and HTTP 200, not a 404.

    Cached unchecked, that writes 44 KB of web page under an ``.xml`` name; the
    version is then dropped at render time for a reason nothing records.
    """

    class _Client:
        async def get_bytes(self, url: str) -> bytes:
            return b'<!DOCTYPE html>\n<html lang="en"><body>Not found</body></html>'

    target = tmp_path / "BILLS-113hr1ih.xml"
    with pytest.raises(ValueError, match="not XML"):
        asyncio.run(_fetch_cached(_Client(), "https://example.invalid/x", target))
    assert not target.exists()


def test_cached_xml_is_reused(tmp_path) -> None:
    """A second run must not refetch what is already on disk."""

    class _Client:
        async def get_bytes(self, url: str) -> bytes:  # pragma: no cover
            raise AssertionError("should not fetch when cached")

    target = tmp_path / "BILLS-113hr1ih.xml"
    target.write_bytes(b"<?xml version='1.0'?><bill/>")
    assert asyncio.run(_fetch_cached(_Client(), "https://example.invalid/x", target))


def test_branch_derived_from_filename_without_parsing() -> None:
    """Resumption reads the branch from the filename.

    Parsing every document to learn its branch would turn a resume of one
    missing measure into a rebuild of the whole Congress.
    """
    assert branch_of("BILLSTATUS-113hr588.xml") == "hr-588"
    assert branch_of("BILLSTATUS-118sconres13.xml") == "sconres-13"
    assert branch_of("nonsense.xml") == ""


def test_gaps_are_recorded_rather_than_left_unexplained(tmp_path) -> None:
    """A measure with no text gets no branch, so its absence must be stated.

    In the 108th Congress that is 8,755 of 10,667 measures. An unexplained
    absence at that scale reads as a build that quietly failed, which is the
    same reasoning behind GAPS.md in the US Code repository.
    """
    repo = GitRepo(tmp_path / "repo")
    repo.init()
    _write_gaps(
        repo,
        "108",
        [("hres-1", "H.Res. 1", "Electing officers of the House.")],
    )

    assert repo.branches() == {"main"}
    text = subprocess.run(
        ["git", "-C", str(repo.path), "show", "main:GAPS.md"],
        capture_output=True,
        text=True,
    ).stdout
    assert "108th Congress" in text
    assert "`H.Res. 1`" in text
    assert "upstream gap, not a build failure" in text
