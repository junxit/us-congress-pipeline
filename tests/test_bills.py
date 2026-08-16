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

from uscongress import votes as votes_text
from uscongress.gitbuild import GitRepo
from uscongress.jobs import bills
from uscongress.jobs.bills import (
    Committee,
    Measure,
    _account_votes,
    _fetch_cached,
    _write_gaps,
    branch_of,
    gap_documents,
    TextVersion,
    commit_message,
    metadata_markdown,
    parse_status,
    vote_documents,
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
        "committees": (
            Committee("Natural Resources Committee", "House", date(2013, 2, 6)),
            Committee("Environment and Public Works Committee", "Senate", date(2013, 6, 20)),
        ),
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


#: The committees block as BILLSTATUS actually writes it, trimmed from
#: H.R. 7283 of the 119th: two referrals months apart, each dated only on its
#: activities, and one of them repeated inside an <actions> entry.
_COMMITTEES = (
    "<committees>"
    "<item><systemCode>hsgo00</systemCode>"
    "<name>Oversight and Government Reform Committee</name>"
    "<chamber>House</chamber><type>Standing</type>"
    "<activities>"
    "<item><name>Markup By</name><date>2026-02-04T14:57:30Z</date></item>"
    "<item><name>Referred To</name><date>2026-01-30T15:32:10Z</date></item>"
    "</activities></item>"
    "<item><systemCode>ssga00</systemCode>"
    "<name>Homeland Security and Governmental Affairs Committee</name>"
    "<chamber>Senate</chamber><type>Standing</type>"
    "<activities>"
    "<item><name>Referred To</name><date>2026-07-23T19:01:06Z</date></item>"
    "</activities></item>"
    "</committees>"
    "<actions><item><actionDate>2026-01-30</actionDate>"
    "<text>Referred to the House Committee on Oversight and Government Reform.</text>"
    "<committees><item><systemCode>hsgo00</systemCode>"
    "<name>Oversight and Government Reform Committee</name></item></committees>"
    "</item></actions>"
)


def test_committees_are_read_from_the_element_billstatus_writes() -> None:
    """The element is ``<item>``, not ``<committee>``.

    Asking for ``committees/committee`` matched nothing at all. Sampled over 200
    measures in each of the 108th, 111th, 113th, 116th and 119th Congresses, 96%
    carry committees and none were found, so every ``metadata.md`` across all
    160,190 branches was written without a Committees section.
    """
    measure = parse_status(
        _status(_item("Introduced in House", "2026-01-30", "ih"), extra=_COMMITTEES)
    )

    assert [c.name for c in measure.committees] == [
        "Oversight and Government Reform Committee",
        "Homeland Security and Governmental Affairs Committee",
    ]
    assert [c.chamber for c in measure.committees] == ["House", "Senate"]


def test_committees_are_not_double_counted_from_actions() -> None:
    """The path must be an exact child of ``<bill>``, not ``.//``.

    Every entry in ``<actions>`` carries its own ``<committees>`` block naming
    the committee that acted, so a descendant search counts referrals several
    times over -- six items instead of two on H.R. 7283 of the 119th.
    """
    measure = parse_status(
        _status(_item("Introduced in House", "2026-01-30", "ih"), extra=_COMMITTEES)
    )
    assert len(measure.committees) == 2


def test_a_committee_is_dated_from_its_earliest_activity() -> None:
    """The referral date lives on the activities, not on the committee.

    Only the earliest matters: it is the point from which the committee holds
    the measure. House Oversight's activities run 2026-02-04 then 2026-01-30, so
    reading them in document order would date the referral to the markup.
    """
    measure = parse_status(
        _status(_item("Introduced in House", "2026-01-30", "ih"), extra=_COMMITTEES)
    )
    assert measure.committees[0].since == date(2026, 1, 30)
    assert measure.committees[1].since == date(2026, 7, 23)


def test_committees_do_not_leak_the_future() -> None:
    """Committees follow the same as-of-this-version rule as cosponsors.

    H.R. 7283 was referred to House Oversight on 2026-01-30 and to Senate
    Homeland Security on 2026-07-23. Listing both on the introduced version
    would have a bill that had not yet passed the House already sitting in a
    Senate committee.
    """
    measure = parse_status(
        _status(_item("Introduced in House", "2026-01-30", "ih"), extra=_COMMITTEES)
    )
    introduced = metadata_markdown(
        measure, TextVersion("Introduced in House", date(2026, 1, 30), "u", "ih")
    )
    referred = metadata_markdown(
        measure, TextVersion("Referred in Senate", date(2026, 7, 23), "u", "rfs")
    )

    assert "## Committees (1)" in introduced
    assert "House — Oversight and Government Reform Committee" in introduced
    assert "Homeland Security" not in introduced
    assert "## Committees (2)" in referred
    assert "Senate — Homeland Security and Governmental Affairs Committee" in referred


def test_the_variant_schema_nests_committees_one_level_deeper() -> None:
    """The same documents that write ``<billNumber>`` wrap committees too.

    H.R. 4200 of the 113th holds its referrals under
    ``<committees><billCommittees><item>``, not ``<committees><item>``. All 13
    documents in 171,916 that use ``<billNumber>`` do this; 11 carry no
    committees, so only two measures change. Far below what any sample finds,
    which is precisely why it needs a test: the failure is not an error but two
    measures missing their committees for ever, with nothing to say so.
    """
    status = (
        "<billStatus><bill>"
        "<billNumber>4200</billNumber><billType>HR</billType>"
        "<congress>113</congress><title>A measure</title>"
        "<committees><billCommittees>"
        "<item><name>Financial Services Committee</name><chamber>House</chamber>"
        "<activities><item><name>Referred To</name>"
        "<date>2014-03-11T14:03:00Z</date></item></activities></item>"
        "</billCommittees></committees>"
        "<textVersions><item><type>Introduced in House</type>"
        "<date>2014-03-11</date><formats><item>"
        "<url>https://www.govinfo.gov/content/pkg/BILLS-113hr4200ih/xml/BILLS-113hr4200ih.xml</url>"
        "</item></formats></item></textVersions>"
        "</bill></billStatus>"
    ).encode()
    measure = parse_status(status)

    assert [c.label for c in measure.committees] == [
        "House — Financial Services Committee"
    ]
    assert measure.committees[0].since == date(2014, 3, 11)


def test_a_committee_names_its_chamber() -> None:
    """Both chambers run an Armed Services, a Judiciary and an Appropriations.

    A bare name is ambiguous on any measure that has crossed over.
    """
    assert Committee("Judiciary Committee", "Senate", None).label == (
        "Senate — Judiciary Committee"
    )
    assert Committee("Joint Economic Committee", "", None).label == (
        "Joint Economic Committee"
    )


def test_an_undated_committee_is_kept_rather_than_dropped() -> None:
    """Absence of a date is not evidence the referral had not happened.

    The same rule as an undated cosponsor: filtering it out would silently lose
    a real referral, which is worse than showing it a version early.
    """
    status = _status(
        _item("Introduced in House", "2013-02-06", "ih"),
        extra=(
            "<committees><item><name>Rules Committee</name>"
            "<chamber>House</chamber></item></committees>"
        ),
    )
    measure = parse_status(status)

    assert measure.committees[0].since is None
    text = metadata_markdown(
        measure, TextVersion("Introduced in House", date(2013, 2, 6), "u", "ih")
    )
    assert "House — Rules Committee" in text


def test_a_document_without_a_bill_is_rejected() -> None:
    """A truncated or unrelated document must not build an empty branch."""
    with pytest.raises(ValueError, match="not a BILLSTATUS document"):
        parse_status(b"<billStatus></billStatus>")


def test_variant_billstatus_schema_is_accepted() -> None:
    """govinfo emits two BILLSTATUS spellings.

    Most measures use ``<number>`` and ``<type>``; a minority -- H.R. 4200 of
    the 113th among them -- use ``<billNumber>`` and ``<billType>``. Recognizing
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


def _gaps(n: int) -> list[tuple[str, str, str]]:
    """n omitted measures."""
    return [(f"hr-{i}", f"H.R. {i}", f"Title {i}") for i in range(1, n + 1)]


def test_small_gap_list_stays_inline() -> None:
    """Most Congresses have a handful of gaps; a table is the right shape."""
    docs = gap_documents("113", _gaps(5))

    assert sorted(docs) == ["GAPS.md"]
    assert "## Every measure" in docs["GAPS.md"]
    assert "GAPS.tsv" not in docs["GAPS.md"]


def test_large_gap_list_moves_to_a_companion_file() -> None:
    """The 108th has 8,755 gaps, which ran to nearly a megabyte of Markdown.

    Past that size the table stops being readable and forges stop rendering it
    reliably, so the full list becomes a TSV and the document keeps a sample.
    """
    docs = gap_documents("108", _gaps(900))

    assert sorted(docs) == ["GAPS.md", "GAPS.tsv"]
    assert len(docs["GAPS.md"]) < 10_000
    assert docs["GAPS.tsv"].count("\n") == 901  # header + 900 rows


def test_the_companion_is_linked_only_when_it_is_written() -> None:
    """A link and the file it points at must never drift apart."""
    small = gap_documents("113", _gaps(5))
    large = gap_documents("108", _gaps(900))

    assert ("GAPS.tsv" in small["GAPS.md"]) is ("GAPS.tsv" in small)
    assert ("GAPS.tsv" in large["GAPS.md"]) is ("GAPS.tsv" in large)


def test_gaps_are_summarized_by_measure_type() -> None:
    """A count per type is what a reader can actually use at this scale."""
    mixed = [("hr-1", "H.R. 1", "a"), ("hres-2", "H.Res. 2", "b"), ("hres-3", "H.Res. 3", "c")]
    text = gap_documents("108", mixed)["GAPS.md"]

    assert "## By measure type" in text
    assert "| `hres` | 2 |" in text
    assert "| `hr` | 1 |" in text


def test_writing_gaps_preserves_the_readme_and_license(tmp_path) -> None:
    """fast-import sets the whole tree, so main must be read before writing.

    Writing only the gap record would delete the artifacts that
    `uscongress artifacts` puts on this branch -- silently, on the next build.
    """
    repo = GitRepo(tmp_path / "us-congress-bills-113")
    repo.init()
    with repo.fast_import() as stream:
        stream.commit("main", {"README.md": "readme\n", "LICENSE": "license\n"}, "Artifacts")

    _write_gaps(repo, "113", _gaps(3))

    assert sorted(repo.read_tree("main")) == ["GAPS.md", "LICENSE", "README.md"]


# --------------------------------------------------------------------------
# Roll-call votes
# --------------------------------------------------------------------------


def _roll(
    number: int = 129,
    chamber: str = votes_text.HOUSE,
    when: date | None = date(2013, 5, 6),
    result: str = "Passed",
    yeas: int = 2,
    nays: int = 1,
) -> votes_text.RollCall:
    """One roll call, already fetched and parsed."""
    positions = [
        votes_text.MemberVote(f"Y{i:06d}", "bioguide", f"Yes{i}", "R", "AL", "Yea")
        for i in range(yeas)
    ] + [
        votes_text.MemberVote(f"N{i:06d}", "bioguide", f"No{i}", "D", "GA", "Nay")
        for i in range(nays)
    ]
    return votes_text.RollCall(
        chamber=chamber,
        congress="113",
        session="1",
        number=number,
        when=when,
        question="On Motion to Suspend the Rules and Pass",
        description="Vietnam Veterans Donor Acknowledgment Act",
        result=result,
        vote_type="2/3 YEA-AND-NAY",
        measure="hr-588",
        positions=tuple(positions),
        reported={"yea": yeas, "nay": nays, "present": 0, "not_voting": 0},
    )


def _reference(
    number: int = 130, when: date | None = date(2013, 5, 7)
) -> votes_text.RecordedVote:
    """One vote BILLSTATUS names but the chamber does not publish."""
    return votes_text.RecordedVote(
        chamber=votes_text.HOUSE,
        congress="113",
        session="1",
        number=number,
        url=f"https://clerk.house.gov/evs/2013/roll{number}.xml",
        when=when,
    )


def test_a_measure_with_no_votes_renders_byte_for_byte_as_it_did_before() -> None:
    """Only 7,510 of 171,916 measures carry a recorded vote.

    The other 164,406 must render to the bytes they already have on GitHub. A
    commit's message and tree are what it hashes, so a stray space added to the
    voteless case would change every SHA in the corpus and turn a 7,510-branch
    correction into a 160,190-branch one -- and the daily loop would then
    rewrite and force-push every measure it touched for months.
    """
    version = TextVersion(
        "Introduced in House", date(2013, 2, 6), _URL.format(code="ih"), "ih"
    )
    measure = _measure(versions=(version,))

    assert commit_message(measure, version) == (
        "H.R. 588 Introduced in House\n"
        "\n"
        "Vietnam Veterans Donor Acknowledgment Act of 2013\n"
        "\n"
        "Version:  Introduced in House (ih)\n"
        "Date:     2013-02-06\n"
        "Congress: 113\n"
        "\n"
        f"Source: {_URL.format(code='ih')}\n"
        "\n"
        "Sponsored-By: Y000033\n"
        "Cosponsor-Count: 1\n"
    )
    assert vote_documents(measure, version) == {}
    assert "Recorded votes" not in metadata_markdown(measure, version)


def test_a_vote_does_not_appear_on_text_that_predates_it() -> None:
    """The introduced version of a measure has not been voted on.

    Every record on a commit here is the record as of that version -- the rule
    cosponsors, committees and actions already follow. Writing the passage vote
    onto the introduced text would have the commit contradict the two commits
    that come after it.
    """
    introduced = TextVersion("Introduced in House", date(2013, 2, 6), "u", "ih")
    engrossed = TextVersion("Engrossed in House", date(2013, 5, 6), "u", "eh")
    measure = _measure(versions=(introduced, engrossed), rolls=(_roll(),))

    assert "Roll-Call:" not in commit_message(measure, introduced)
    assert vote_documents(measure, introduced) == {}

    assert "Roll-Call: House 113-1-129 2013-05-06 Passed 2-1" in commit_message(
        measure, engrossed
    )
    assert sorted(vote_documents(measure, engrossed)) == ["votes/house-113-1-0129.md"]


def test_a_vote_is_written_again_on_every_later_commit() -> None:
    """fast-import's ``deleteall`` sets a commit's whole tree.

    A file present two commits ago and not re-emitted here is deleted by this
    commit, so the enrolled bill would show the measure's earlier votes
    vanishing one at a time as it progressed.
    """
    engrossed = TextVersion("Engrossed in House", date(2013, 5, 6), "u", "eh")
    enrolled = TextVersion("Enrolled Bill", date(2013, 6, 17), "u", "enr")
    measure = _measure(versions=(engrossed, enrolled), rolls=(_roll(),))

    assert vote_documents(measure, enrolled) == vote_documents(measure, engrossed)


def test_votes_are_ordered_by_date_not_by_roll_call_number() -> None:
    """The chambers number independently, so numbers interleave nonsensically.

    Senate roll 5 in March and House roll 400 in July sort the wrong way round
    by number, and the trailers would then contradict the dates beside them.
    """
    final = TextVersion("Enrolled Bill", date(2013, 8, 1), "u", "enr")
    measure = _measure(
        versions=(final,),
        rolls=(
            _roll(number=400, chamber=votes_text.HOUSE, when=date(2013, 7, 1)),
            _roll(number=5, chamber=votes_text.SENATE, when=date(2013, 3, 1)),
        ),
    )

    trailers = [
        line for line in commit_message(measure, final).splitlines()
        if line.startswith("Roll-Call:")
    ]
    assert trailers == [
        "Roll-Call: Senate 113-1-5 2013-03-01 Passed 2-1",
        "Roll-Call: House 113-1-400 2013-07-01 Passed 2-1",
    ]


def test_a_vote_that_cannot_be_fetched_is_marked_rather_than_dropped() -> None:
    """A branch showing three votes where the chamber took four reads complete.

    The marker is what makes the difference visible on the commit itself, which
    matters more than the GAPS.md total: nobody reading one measure's history
    goes and checks the repository's gap record first.
    """
    final = TextVersion("Enrolled Bill", date(2013, 6, 17), "u", "enr")
    measure = _measure(
        versions=(final,),
        rolls=(_roll(),),
        votes_unavailable=((_reference(), "HTTP 404"),),
    )

    message = commit_message(measure, final)
    assert "Roll-Call: House 113-1-129 2013-05-06 Passed 2-1" in message
    assert "Roll-Call: House 113-1-130 2013-05-07 not-retrievable" in message

    text = metadata_markdown(measure, final)
    assert "## Recorded votes (2)" in text
    assert "**not retrievable**" in text


def test_a_vote_taken_after_the_last_committed_version_is_recorded_as_a_gap() -> None:
    """There is no commit for such a vote to sit on, and that has to be said.

    A measure whose final vote came after its last published text keeps that
    vote nowhere. It is a limit of the shape of this repository rather than an
    upstream gap, and either way it is not something to leave to be discovered.
    """
    version = TextVersion("Engrossed in House", date(2013, 5, 6), "u", "eh")
    measure = _measure(
        versions=(version,),
        rolls=(_roll(), _roll(number=400, when=date(2013, 9, 1))),
    )

    missing: list[tuple[str, str, str]] = []
    late: list[tuple[str, str, str]] = []
    _account_votes(measure, version, missing, late)

    assert missing == []
    assert [entry[1] for entry in late] == ["House 113-1-400"]
    assert "after the last version committed" in late[0][2]


def test_an_undated_final_version_carries_every_vote_and_reports_no_gap() -> None:
    """The enrolled bill usually has no date, and a null cutoff admits them all.

    Recomputing "which votes reached no commit" from the dates, instead of
    asking the function that placed them, got this backwards: it called every
    vote later than the last *dated* version unplaced, while those votes were
    sitting on the undated commit after it. 124 of the 508 voted measures in the
    113th Congress end on an undated version, so the gap record would have named
    hundreds of votes as missing while publishing them.
    """
    engrossed = TextVersion("Engrossed in House", date(2013, 5, 6), "u", "eh")
    enrolled = TextVersion("Enrolled Bill", None, "u", "enr")
    measure = _measure(
        versions=(engrossed, enrolled),
        rolls=(_roll(), _roll(number=400, when=date(2013, 9, 1))),
    )

    assert len(vote_documents(measure, enrolled)) == 2

    missing: list[tuple[str, str, str]] = []
    late: list[tuple[str, str, str]] = []
    _account_votes(measure, enrolled, missing, late)

    assert late == []


def test_a_version_whose_text_failed_is_not_treated_as_the_cutoff() -> None:
    """The cutoff is the last version on the branch, not the last one listed.

    A version whose text could not be fetched is skipped and never committed, so
    a vote after the last one that *was* committed really has nowhere to sit --
    and using the BILLSTATUS listing instead would report it as safely placed.
    """
    engrossed = TextVersion("Engrossed in House", date(2013, 5, 6), "u", "eh")
    enrolled = TextVersion("Enrolled Bill", date(2013, 10, 1), "u", "enr")
    measure = _measure(
        versions=(engrossed, enrolled),
        rolls=(_roll(number=400, when=date(2013, 9, 1)),),
    )

    missing: list[tuple[str, str, str]] = []
    late: list[tuple[str, str, str]] = []
    _account_votes(measure, engrossed, missing, late)  # enrolled never committed

    assert [entry[1] for entry in late] == ["House 113-1-400"]


def test_the_gap_document_names_a_vote_category_only_when_it_occurs() -> None:
    """A Congress with every vote retrievable carries no heading saying so.

    The Record's gap document established this: an empty section reads as a
    finding, and thirteen repositories each reporting nothing missing is noise
    that buries the ones that do.
    """
    quiet = gap_documents("113", _gaps(2))["GAPS.md"]
    assert "not published where they are named" not in quiet
    assert "taken after the last published text" not in quiet

    noisy = gap_documents(
        "113",
        _gaps(2),
        votes_missing=[("hr-1", "House 113-1-130", "HTTP 404")],
        votes_late=[("hr-2", "Senate 113-2-5", "2014-01-01, after 2013-12-01")],
    )["GAPS.md"]
    assert "## Roll-call votes that are not published where they are named" in noisy
    assert "## Roll-call votes taken after the last published text" in noisy
    assert "`hr-1` | House 113-1-130" in noisy
    # Each table's third column is named for what it holds. It said "Vote"
    # twice, so the late-votes table read "| Measure | Vote | Vote |" over a
    # column of dates -- in a document whose whole job is to be legible.
    assert "| Measure | Vote | Reason |" in noisy
    assert "| Measure | Vote | When |" in noisy


def test_a_long_vote_gap_list_moves_to_its_own_companion() -> None:
    """The same threshold the measure list uses, for the same reason."""
    documents = gap_documents(
        "113",
        [],
        votes_late=[
            (f"hr-{i}", f"House 113-1-{i}", "after the last version")
            for i in range(1, 400)
        ],
    )

    assert sorted(documents) == ["GAPS-late-votes.tsv", "GAPS.md"]
    assert documents["GAPS-late-votes.tsv"].count("\n") == 400  # header + 399
    assert "GAPS-late-votes.tsv" in documents["GAPS.md"]


def test_the_gap_document_still_reports_measures_with_no_text() -> None:
    """The vote sections are additions, not a replacement.

    The 108th's 8,755 textless measures are still the largest thing this
    document has to explain.
    """
    text = gap_documents("108", _gaps(3))["GAPS.md"]

    assert "108th Congress" in text
    assert "upstream gap, not a build failure" in text
    assert "## By measure type" in text


def test_a_resumable_seed_does_not_strip_the_gap_record(tmp_path: Path) -> None:
    """Skipping branches measures no votes, so it must not rewrite GAPS.md.

    Votes and derived amendment totals are accumulated only for measures a run
    builds. A resumable run over an already-built shard builds none, so those
    two sections render empty -- and writing that out deletes them. It happened
    on `us-congress-bills-119`: a plain `seed-bills` published a GAPS.md with
    the roll-call and amendment-execution sections gone, while the textless
    list it did measure was correct, so the document looked healthy.
    """
    full = bills.gap_documents(
        "119",
        [("hr-1", "H.R. 1", "A bill")],
        votes_missing=[("hr-2", "H.R. 2", "not published")],
        votes_late=[("hr-3", "H.R. 3", "after the last version")],
        derived_totals={"no citation": 5},
    )["GAPS.md"]
    partial = bills.gap_documents("119", [("hr-1", "H.R. 1", "A bill")])["GAPS.md"]

    assert "Roll-call votes" in full
    assert "Roll-call votes" not in partial, (
        "a partial render drops the vote section, which is why seed must not "
        "write it after skipping branches"
    )
