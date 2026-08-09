"""Roll-call votes, and the four ways the two chambers disagree with each other.

Nothing about these documents is shared. The House and the Senate use different
root elements, different member elements, different identifiers, different
session numbering and different date formats, and every one of those was found
by parsing a real document rather than by reading a schema.

The one that costs the most if missed is the date. BILLSTATUS timestamps a vote
in UTC and each chamber dates it locally, so a vote taken in the evening belongs
to a different day depending on which document is believed. It decides which
commit the vote lands on, and it is wrong for 60 of the 814 distinct vote stamps
in the 113th Congress.

The fixtures are trimmed from the real documents those facts come from:
``clerk.house.gov/evs/2013/roll129.xml`` and
``senate.gov/legislative/LIS/roll_call_votes/vote1131/vote_113_1_00142.xml``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

import pytest
from defusedxml.ElementTree import fromstring

from uscongress import members, votes
from uscongress.jobs import votes as votes_job

# --------------------------------------------------------------------------
# Fixtures, trimmed from real documents
# --------------------------------------------------------------------------

#: The House Clerk's header, verbatim. The DOCTYPE names an external DTD that
#: is not fetched, and the second processing instruction is a stylesheet; both
#: sit between the declaration and the root element.
_HOUSE_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>\r\n'
    '<!DOCTYPE rollcall-vote PUBLIC "-//US Congress//DTDs/vote v1.0 20031119 //EN"'
    ' "../vote.dtd">\r\n'
    '<?xml-stylesheet type="text/xsl" href="../vote.xsl"?>\r\n'
)


def _house(
    members: str = "",
    totals: str = (
        "<totals-by-vote><total-stub>Totals</total-stub><yea-total>2</yea-total>"
        "<nay-total>1</nay-total><present-total>0</present-total>"
        "<not-voting-total>0</not-voting-total></totals-by-vote>"
    ),
    action_date: str = "6-May-2013",
    legis_num: str = "H R 588",
    session: str = "1st",
) -> bytes:
    """Wrap member positions in a minimal House roll call."""
    members = members or (
        _legislator("A000055", "Aderholt", "R", "AL", "Yea")
        + _legislator("B000213", "Bishop", "D", "GA", "Yea")
        + _legislator("C001045", "Cotton", "R", "AR", "Nay")
    )
    return (
        _HOUSE_HEAD + "<rollcall-vote><vote-metadata>"
        "<majority>R</majority><congress>113</congress>"
        f"<session>{session}</session>"
        "<chamber>U.S. House of Representatives</chamber>"
        "<rollcall-num>129</rollcall-num>"
        f"<legis-num>{legis_num}</legis-num>"
        "<vote-question>On Motion to Suspend the Rules and Pass</vote-question>"
        "<vote-type>2/3 YEA-AND-NAY</vote-type>"
        "<vote-result>Passed</vote-result>"
        f"<action-date>{action_date}</action-date>"
        '<action-time time-etz="18:56">6:56 PM</action-time>'
        "<vote-desc>Vietnam Veterans Donor Acknowledgment Act</vote-desc>"
        f"<vote-totals>{totals}</vote-totals>"
        f"</vote-metadata><vote-data>{members}</vote-data></rollcall-vote>"
    ).encode()


def _legislator(bioguide: str, name: str, party: str, state: str, cast: str) -> str:
    """One House member's recorded position."""
    return (
        f'<recorded-vote><legislator name-id="{bioguide}" sort-field="{name}"'
        f' unaccented-name="{name}" party="{party}" state="{state}"'
        f' role="legislator">{name}</legislator><vote>{cast}</vote></recorded-vote>'
    )


def _senate(
    members: str = "",
    count: str = "<yeas>2</yeas><nays>1</nays><present/><absent>0</absent>",
    vote_date: str = "June 6, 2013,  10:34 AM",
    document: str = (
        "<document_congress>113</document_congress><document_type>S.</document_type>"
        "<document_number>1003</document_number><document_name>S. 1003</document_name>"
    ),
) -> bytes:
    """Wrap member positions in a minimal Senate roll call.

    Note the document has no whitespace between the declaration and the root,
    and a trailing space after ``<roll_call_vote>`` -- both as published.
    """
    members = members or (
        _senator("S289", "Alexander", "Lamar", "R", "TN", "Yea")
        + _senator("S354", "Baldwin", "Tammy", "D", "WI", "Yea")
        + _senator("S317", "Barrasso", "John", "R", "WY", "Nay")
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><roll_call_vote> \n'
        "<congress>113</congress><session>1</session>"
        "<congress_year>2013</congress_year><vote_number>142</vote_number>"
        f"<vote_date>{vote_date}</vote_date>"
        "<vote_question_text>On the Cloture Motion S. 1003</vote_question_text>"
        "<question>On the Cloture Motion</question>"
        "<vote_title>Motion to Invoke Cloture</vote_title>"
        "<majority_requirement>3/5</majority_requirement>"
        "<vote_result>Cloture Motion Rejected</vote_result>"
        f"<document>{document}</document>"
        f"<count>{count}</count>"
        f"<members>{members}</members></roll_call_vote>"
    ).encode()


def _senator(
    lis: str, last: str, first: str, party: str, state: str, cast: str
) -> str:
    """One senator's recorded position."""
    return (
        f"<member><member_full>{last} ({party}-{state})</member_full>"
        f"<last_name>{last}</last_name><first_name>{first}</first_name>"
        f"<party>{party}</party><state>{state}</state>"
        f"<vote_cast>{cast}</vote_cast><lis_member_id>{lis}</lis_member_id></member>"
    )


def _billstatus(recorded: str) -> ET.Element:
    """A ``<bill>`` element carrying actions, for the reference reader."""
    return fromstring(f"<billStatus><bill>{recorded}</bill></billStatus>").find("bill")


#: One ``<recordedVotes>`` block as BILLSTATUS writes it, inside an action item.
def _recorded(
    roll: str = "129",
    chamber: str = "House",
    stamp: str = "2013-05-06T22:58:32Z",
    session: str = "1",
) -> str:
    """One action item carrying one recorded vote."""
    return (
        "<actions><item><recordedVotes><recordedVote>"
        f"<rollNumber>{roll}</rollNumber>"
        f"<url>https://clerk.house.gov/evs/2013/roll{roll}.xml</url>"
        f"<chamber>{chamber}</chamber><congress>113</congress>"
        f"<date>{stamp}</date><sessionNumber>{session}</sessionNumber>"
        "</recordedVote></recordedVotes></item></actions>"
    )


#: The House Clerk's answer for a roll call that does not exist: a real 404,
#: measured at 1,245 bytes, whose body is XHTML rather than XML.
_CLERK_404 = (
    b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN"'
    b' "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">\r\n'
    b'<html xmlns="http://www.w3.org/1999/xhtml">\r\n<head>\r\n<title>404</title>'
)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_a_house_roll_call_parses_despite_naming_an_external_dtd() -> None:
    """The Clerk's documents carry ``<!DOCTYPE … "../vote.dtd">``.

    ``defusedxml`` permits the declaration and refuses to resolve it, which is
    exactly the wanted behavior -- but it is the kind of thing a hardened parser
    rejects outright, and rejecting it would have meant no House votes at all
    while the Senate's worked fine.
    """
    roll = votes.parse_house(_house())

    assert roll.chamber == votes.HOUSE
    assert roll.number == 129
    assert roll.result == "Passed"
    assert len(roll.positions) == 3


def test_the_clerk_publishes_the_bioguide_id_that_sponsors_already_use() -> None:
    """``name-id`` is the bioguide ID, so House votes join the sponsor trailer.

    This is the whole reason no member crosswalk is needed for the House: the
    identifier in ``Sponsored-By:`` and the one on a recorded vote are the same
    string, with no translation in between.
    """
    roll = votes.parse_house(_house())

    assert [p.member_id for p in roll.positions] == ["A000055", "B000213", "C001045"]
    assert {p.id_kind for p in roll.positions} == {"bioguide"}


def test_the_senate_publishes_no_bioguide_id_and_none_is_invented() -> None:
    """senate.gov identifies a member by LIS ID and nothing else.

    Recorded as published rather than crosswalked. Inferring a bioguide ID from
    a surname and a state is a join this project would have to get right 100
    times per vote and could not check, and a wrong one is worse than an honest
    LIS ID because it looks joinable.
    """
    roll = votes.parse_senate(_senate())

    assert [p.member_id for p in roll.positions] == ["S289", "S354", "S317"]
    assert {p.id_kind for p in roll.positions} == {"lis"}


def test_a_vote_is_dated_by_the_chamber_and_not_by_billstatus() -> None:
    """The two sources disagree, and only one of them is the chamber's own day.

    BILLSTATUS stamps a UTC instant. A vote at 01:30 UTC on 7 May was taken at
    9:30pm Eastern on 6 May, and the Clerk says so. Taking the day off the UTC
    stamp -- which is what the existing ``bills._date`` does for every other
    field -- moves the vote onto the wrong commit whenever the chamber sat late,
    which in the 113th Congress is 60 of 814 distinct vote stamps.
    """
    roll = votes.parse_house(_house(action_date="6-May-2013"))
    (reference,) = votes.references(
        _billstatus(_recorded(stamp="2013-05-07T01:30:00Z"))
    )

    assert roll.when == date(2013, 5, 6)
    assert reference.when == date(2013, 5, 7)


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        ("6-May-2013", date(2013, 5, 6)),
        ("30-Sep-2013", date(2013, 9, 30)),
        ("1-January-2014", date(2014, 1, 1)),
    ],
)
def test_house_dates_parse_without_consulting_the_locale(
    published: str, expected: date
) -> None:
    """Month names are read from a table, not from ``strptime``.

    ``%b`` and ``%B`` resolve against the running process's ``LC_TIME``. A build
    whose commit dates depend on the operator's environment cannot be
    reproduced, and this project's whole update model rests on the same input
    rendering to the same bytes on a laptop and on a CI runner.
    """
    assert votes.parse_house(_house(action_date=published)).when == expected


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        ("June 6, 2013,  10:34 AM", date(2013, 6, 6)),
        ("September 30, 2013,  11:59 PM", date(2013, 9, 30)),
        ("January 1, 2014", date(2014, 1, 1)),
    ],
)
def test_senate_dates_parse_with_or_without_a_time(
    published: str, expected: date
) -> None:
    """The Senate writes a different format again, and doubles a space in it."""
    assert votes.parse_senate(_senate(vote_date=published)).when == expected


def test_the_two_chambers_number_the_same_session_differently() -> None:
    """The House writes ``1st`` where the Senate writes ``1``.

    Left alone that files one session under two names, so the cache key for a
    House vote never matches on a second run and every one of them is fetched
    again for ever.
    """
    house = votes.parse_house(_house(session="1st"))
    senate = votes.parse_senate(_senate())

    assert house.session == "1"
    assert senate.session == "1"
    assert house.key == "house-113-1-0129"
    assert senate.key == "senate-113-1-0142"


def test_a_document_from_the_wrong_chamber_is_an_error() -> None:
    """The cache is keyed by chamber, so a misfiled document must not parse.

    Read by the other chamber's parser a roll call has no members it recognizes,
    which does not raise -- it yields a valid-looking vote in which nobody
    voted. An empty unanimous vote published on a real bill is far worse than a
    failure that stops the run.
    """
    with pytest.raises(ValueError, match="not a House roll call"):
        votes.parse(_senate(), votes.HOUSE)
    with pytest.raises(ValueError, match="not a Senate roll call"):
        votes.parse(_house(), votes.SENATE)


# --------------------------------------------------------------------------
# Tallies
# --------------------------------------------------------------------------


def test_a_tally_is_counted_from_the_members_rather_than_read_off_the_totals() -> None:
    """The totals a document states are a second claim, not the same one.

    Counting the positions is what makes a truncated member list detectable. A
    document that lists 200 of 435 members and states the real totals parses
    perfectly and reports a vote that did not happen that way.
    """
    roll = votes.parse_house(_house())

    assert roll.tally == {"yea": 2, "nay": 1, "present": 0, "not_voting": 0}
    assert roll.reconciles
    assert roll.summary == "2-1"


def test_a_tally_that_disagrees_with_the_stated_totals_says_so_on_the_document() -> None:
    """Both numbers are reproduced and neither is corrected.

    Dropping the vote would leave an unexplained absence; silently preferring
    one number would leave a reader comparing this file with the chamber's own
    page to discover the difference themselves.
    """
    roll = votes.parse_house(
        _house(
            totals=(
                "<totals-by-vote><total-stub>Totals</total-stub>"
                "<yea-total>398</yea-total><nay-total>2</nay-total>"
                "<present-total>0</present-total><not-voting-total>32</not-voting-total>"
                "</totals-by-vote>"
            )
        )
    )

    assert not roll.reconciles
    text = votes.roll_markdown(roll)
    assert "published totals do not match the members listed" in text
    assert "Yea 398" in text  # what the document states
    assert "| Yea | 2 |" in text  # what its own member list adds up to


def test_ayes_count_with_yeas_but_are_not_relabeled() -> None:
    """A recorded vote in the Committee of the Whole is Aye/No, not Yea/Nay.

    They are not synonyms -- the words say which procedure the chamber was
    under -- so the tally adds them together and the rendered file keeps the
    word the Clerk actually published. Roll 200 of the 113th is 165 Aye to 261
    No, and calling those yeas and nays would misdescribe the vote.
    """
    roll = votes.parse_house(
        _house(
            members=_legislator("A000055", "Aderholt", "R", "AL", "Aye")
            + _legislator("C001045", "Cotton", "R", "AR", "No"),
            totals=(
                "<totals-by-vote><total-stub>Totals</total-stub>"
                "<yea-total>1</yea-total><nay-total>1</nay-total>"
                "<present-total>0</present-total><not-voting-total>0</not-voting-total>"
                "</totals-by-vote>"
            ),
        )
    )

    assert roll.tally == {"yea": 1, "nay": 1, "present": 0, "not_voting": 0}
    text = votes.roll_markdown(roll)
    assert "## Aye (1)" in text
    assert "## No (1)" in text
    assert "## Yea" not in text


def test_the_senate_writes_no_one_present_as_an_empty_element() -> None:
    """``<present/>`` rather than ``<present>0</present>``.

    ``int(element.text)`` raises ``TypeError`` on the ordinary case, which would
    make every unanimous-on-that-axis Senate vote unparseable.
    """
    roll = votes.parse_senate(_senate())

    assert roll.reported["present"] == 0
    assert roll.reconciles


def test_the_senate_calls_not_voting_absent() -> None:
    """One name is kept so the two chambers reconcile against one tally."""
    roll = votes.parse_senate(
        _senate(
            members=_senator("S289", "Alexander", "Lamar", "R", "TN", "Yea")
            + _senator("S354", "Baldwin", "Tammy", "D", "WI", "Not Voting"),
            count="<yeas>1</yeas><nays>0</nays><present/><absent>1</absent>",
        )
    )

    assert roll.tally["not_voting"] == 1
    assert roll.reconciles


# --------------------------------------------------------------------------
# The index BILLSTATUS carries
# --------------------------------------------------------------------------


def test_a_vote_repeated_across_action_items_is_counted_once() -> None:
    """``<recordedVotes>`` is nested in every action item that mentions it.

    Roll 129 appears twice in the BILLSTATUS of H.R. 588 of the 113th. A
    descendant search that does not deduplicate reports the House as having
    voted twice on one question, and fetches the same document twice to do it.
    This is the same trap ``bills._committees`` documents, reached another way.
    """
    bill = _billstatus(_recorded() + _recorded())

    assert len(votes.references(bill)) == 1


def test_votes_are_distinguished_by_chamber_and_session_not_by_number() -> None:
    """Numbering restarts each session and each chamber keeps its own.

    House roll 129 of session 1 and Senate roll 129 of session 2 are different
    votes. Keyed on the number alone they collide, and one silently replaces the
    other.
    """
    bill = _billstatus(
        _recorded(roll="129", chamber="House", session="1")
        + _recorded(roll="129", chamber="Senate", session="2")
        + _recorded(roll="129", chamber="House", session="2")
    )

    assert len(votes.references(bill)) == 3
    assert {r.key for r in votes.references(bill)} == {
        "house-113-1-0129",
        "house-113-2-0129",
        "senate-113-2-0129",
    }


def test_a_vote_naming_no_measure_this_project_builds_names_none() -> None:
    """A quorum call and a nomination are votes on nothing with a branch.

    Roll 1 of the 108th is a call by states with 432 members present and no
    ``<legis-num>`` that resolves; forcing it into a measure would attach the
    opening of a Congress to whichever bill happened to parse out of it.
    """
    quorum = votes.parse_house(_house(legis_num="QUORUM"))
    bill = votes.parse_house(_house(legis_num="H CON RES 25"))

    assert quorum.measure == ""
    assert bill.measure == "hconres-25"


def test_a_measure_is_normalized_from_either_chambers_spelling() -> None:
    """The House spaces the letters and the Senate punctuates them.

    ``H R 588`` and ``S.`` name measures this project calls ``hr-588`` and
    ``s-1003``; neither chamber writes it the way the branch is named.
    """
    assert votes.parse_house(_house(legis_num="H R 588")).measure == "hr-588"
    assert votes.parse_senate(_senate()).measure == "s-1003"
    assert (
        votes.parse_senate(
            _senate(
                document=(
                    "<document_type>H.J.RES.</document_type>"
                    "<document_number>76</document_number>"
                )
            )
        ).measure
        == "hjres-76"
    )


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------


def test_the_clerks_404_page_is_rejected_rather_than_cached() -> None:
    """Both chambers serve a real 404, so unlike govinfo the status is evidence.

    The body is checked anyway. 1,245 bytes of XHTML is close enough to a
    document to sit in the cache under an ``.xml`` name for ever and fail to
    parse every time afterwards, with nothing recording why.
    """
    assert not votes_job.looks_like_xml(_CLERK_404)
    assert votes_job.looks_like_xml(_house())
    assert votes_job.looks_like_xml(_senate())


def test_a_house_doctype_is_not_mistaken_for_a_404_page() -> None:
    """Both begin with a DOCTYPE; only one begins with an XML declaration.

    A House roll call is ``<?xml … ?>`` then ``<!DOCTYPE rollcall-vote …>``, and
    the 404 page is ``<!DOCTYPE html …>`` with no declaration at all. Testing
    for the declaration separates them; testing for ``<!DOCTYPE`` would reject
    every House vote there is.
    """
    assert _house().lstrip().startswith(b"<?xml")
    assert b"<!DOCTYPE" in _house()
    assert votes_job.looks_like_xml(_house())


async def test_a_cached_roll_call_is_not_fetched_again(tmp_path, monkeypatch) -> None:
    """A published roll call does not change, so the cache is never revalidated."""
    monkeypatch.setattr(votes_job.config, "RAW_DIR", tmp_path)
    reference = votes.RecordedVote(
        chamber=votes.HOUSE,
        congress="113",
        session="1",
        number=129,
        url="https://example.invalid/roll129.xml",
    )
    cached = tmp_path / "votes" / "113" / "house-113-1-0129.xml"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(_house())

    class _Client:
        async def get_bytes(self, url: str) -> bytes:  # pragma: no cover
            raise AssertionError("should not fetch when cached")

    assert await votes_job.fetch(_Client(), reference) == _house()


async def test_a_poisoned_cache_entry_is_discarded_and_fetched_again(
    tmp_path, monkeypatch
) -> None:
    """An entry written before this check existed must not be trusted for ever.

    ``bills._fetch_cached`` trusts any file that exists, so a cache poisoned
    once stays poisoned until someone deletes it by hand. The statutes and US
    Code jobs recover instead, and so does this one.
    """
    monkeypatch.setattr(votes_job.config, "RAW_DIR", tmp_path)
    reference = votes.RecordedVote(
        chamber=votes.HOUSE,
        congress="113",
        session="1",
        number=129,
        url="https://example.invalid/roll129.xml",
    )
    cached = tmp_path / "votes" / "113" / "house-113-1-0129.xml"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(_CLERK_404)

    class _Client:
        async def get_bytes(self, url: str) -> bytes:
            return _house()

    assert await votes_job.fetch(_Client(), reference) == _house()
    assert cached.read_bytes() == _house()


async def test_a_vote_that_is_not_published_is_reported_rather_than_raised(
    tmp_path, monkeypatch
) -> None:
    """An upstream absence has to reach the commit, not end the run.

    A vote BILLSTATUS names and the chamber does not serve is a fact about the
    sources. Dropped, it would make the same measure render differently
    depending on whether a fetch happened to succeed -- and that is the one
    thing that must not vary between a local build and the daily loop, because
    the loop decides what to push by comparing bytes.
    """
    monkeypatch.setattr(votes_job.config, "RAW_DIR", tmp_path)
    reference = votes.RecordedVote(
        chamber=votes.HOUSE,
        congress="113",
        session="1",
        number=129,
        url="https://example.invalid/roll129.xml",
    )

    class _Client:
        async def get_bytes(self, url: str) -> bytes:
            return _CLERK_404

    rolls, missing = await votes_job.load(_Client(), (reference,))

    assert rolls == ()
    assert len(missing) == 1
    assert missing[0][0] is reference
    assert "not XML" in missing[0][1]


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def test_the_rendered_vote_says_which_identifier_it_carries() -> None:
    """A reader joining these to sponsors has to know which key is which.

    The House file can be joined to ``Sponsored-By:`` directly and the Senate
    file cannot, and that difference is invisible from the table itself: both
    columns are short opaque strings.
    """
    house = votes.roll_markdown(votes.parse_house(_house()))
    senate = votes.roll_markdown(votes.parse_senate(_senate()))

    assert "identified by bioguide ID" in house
    assert "the same identifier the `Sponsored-By:` trailer uses" in house
    assert "LIS member ID" in senate
    assert "added by this pipeline" in senate
    assert "| Member | Party | State | LIS | Bioguide |" in senate
    # The House table must not grow a column; that would rewrite every House
    # vote file in twelve repositories to say nothing new.
    assert "| Member | Party | State | ID |" in house
    assert "Bioguide" not in house


def test_members_are_ordered_so_the_file_is_stable() -> None:
    """Rendered bytes decide the commit SHA, so nothing may depend on input order.

    The Clerk lists members in roll order, which is neither alphabetical nor
    stable between documents; sorting here is what keeps a rebuild of an
    unchanged vote from producing a new commit.
    """
    forward = votes.parse_house(
        _house(
            members=_legislator("A000055", "Aderholt", "R", "AL", "Yea")
            + _legislator("B000213", "Bishop", "D", "GA", "Yea")
        )
    )
    reversed_ = votes.parse_house(
        _house(
            members=_legislator("B000213", "Bishop", "D", "GA", "Yea")
            + _legislator("A000055", "Aderholt", "R", "AL", "Yea")
        )
    )

    assert votes.roll_markdown(forward) == votes.roll_markdown(reversed_)


def test_a_rendered_vote_ends_with_exactly_one_newline() -> None:
    """Every generated document in this project ends the same way."""
    text = votes.roll_markdown(votes.parse_house(_house()))

    assert text.endswith("\n")
    assert not text.endswith("\n\n")


# --------------------------------------------------------------------------
# The members crosswalk
# --------------------------------------------------------------------------


def test_a_senator_gains_a_bioguide_id_the_senate_never_published() -> None:
    """This is the whole point of the crosswalk.

    senate.gov publishes ``<lis_member_id>S289</lis_member_id>`` and no bioguide
    ID, so a senator's votes could not be joined to the `Sponsored-By:` trailer
    or to House votes without a lookup the reader had to build themselves.
    """
    roll = votes.parse_senate(_senate())

    assert [p.member_id for p in roll.positions] == ["S289", "S354", "S317"]
    assert [p.bioguide for p in roll.positions] == ["A000360", "B001230", "B001261"]
    assert {p.id_kind for p in roll.positions} == {"lis"}  # what was *published*


def test_a_mapping_that_disagrees_with_the_document_is_refused() -> None:
    """A vote attributed to the wrong senator is worse than one with no ID.

    It is wrong in a way that reads as authoritative and that nothing
    downstream could detect. This exact pairing was a real mistake in these
    fixtures: S330 is Bennet of Colorado, and it was written here as Barrasso
    of Wyoming. The gate refused it, which is how the error was found.
    """
    roll = votes.parse_senate(
        _senate(members=_senator("S330", "Barrasso", "John", "R", "WY", "Yea"))
    )

    (member,) = roll.positions
    assert member.member_id == "S330"
    assert member.bioguide == ""
    text = votes.roll_markdown(roll)
    assert "could not be matched to a bioguide ID" in text


def test_a_house_member_carries_the_bioguide_the_clerk_published() -> None:
    """No crosswalk is involved: ``name-id`` already *is* the bioguide ID."""
    roll = votes.parse_house(_house())

    assert [p.bioguide for p in roll.positions] == ["A000055", "B000213", "C001045"]
    assert [p.bioguide for p in roll.positions] == [p.member_id for p in roll.positions]


def test_a_name_change_and_a_diacritic_are_not_disagreements() -> None:
    """The two sources spell the same person differently, in small ways.

    senate.gov writes ``Lujan`` where the crosswalk writes ``Luján``, and
    ``Graham`` where it records ``Graham Nordone``. Comparing raw strings
    rejected 2 of the 246 senators in this corpus -- both of them real, both
    correctly mapped.
    """
    assert members.bioguide_for("S409", "Lujan (D-NM)", "NM")
    assert members.bioguide_for("S409", "Luján (D-NM)", "NM")
    assert members.bioguide_for("S441", "Graham (R-SC)", "SC")


def test_party_is_not_part_of_the_gate() -> None:
    """Senators change party mid-career, and the record is right either way.

    Specter, Jeffords and Manchin all did. Gating on party would refuse an
    accurate vote document for being accurate.
    """
    assert members.bioguide_for("S289", "Alexander (R-TN)", "TN") == "A000360"
    assert members.bioguide_for("S289", "Alexander (D-TN)", "TN") == "A000360"
    assert members.bioguide_for("S289", "Alexander (I-TN)", "TN") == "A000360"


def test_every_lis_id_this_corpus_uses_resolves() -> None:
    """Measured before the phase was planned: 246 of 246, with no exceptions.

    A senator missing from the table is not an error the render can recover
    from -- their votes simply carry no joinable identifier -- so the coverage
    is asserted rather than assumed.
    """
    assert len(members.SENATORS) > 300
    for lis, (bioguide, surname, states) in members.SENATORS.items():
        assert lis.startswith("S")
        assert len(bioguide) == 7 and bioguide[0].isalpha()
        assert surname and states
