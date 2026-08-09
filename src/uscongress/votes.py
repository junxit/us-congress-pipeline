"""Parse and render roll-call votes as the two chambers publish them.

Every measure's BILLSTATUS already names the votes taken on it, in
``actions/item/recordedVotes/recordedVote``, and links each one to the chamber
that took it -- ``clerk.house.gov/evs/`` for the House and
``senate.gov/legislative/LIS/roll_call_votes/`` for the Senate. Neither host is
keyed, which is why this phase needs no credential: it is the same regime as
``www.govinfo.gov`` and ``uscode.house.gov``, both of which this pipeline
already fetches through :meth:`uscongress.govinfo.GovInfoClient.get_bytes`.

The Congress.gov API is deliberately not used. Its roll-call endpoint covers
"all House roll call votes in the 118th and 119th Congresses associated with a
piece of legislation", and it publishes no Senate roll-call endpoint at all,
against a corpus that starts at the 108th and holds both chambers -- so the key
the roadmap once called for would have reached 2 of 12 Congresses and 1 of 2
chambers.

The two documents are not two spellings of one schema. They disagree on the root
element, on the shape of the member list, on how a member is identified, on how
a session is numbered and on how a date is written. So there is one parser each,
and they meet at :class:`RollCall`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

from defusedxml.ElementTree import fromstring as _safe_fromstring

from .members import bioguide_for

#: Chamber names, spelled as BILLSTATUS spells them in ``<chamber>``.
HOUSE = "House"
SENATE = "Senate"

#: Root element each chamber writes. Checked rather than assumed, because the
#: cache is keyed by chamber and a mislabeled entry would otherwise be parsed
#: by the wrong reader and yield an empty vote rather than an error.
_ROOTS = {HOUSE: "rollcall-vote", SENATE: "roll_call_vote"}

#: Month number by the first three letters of its name, lowercased.
#:
#: ``strptime`` with ``%b`` or ``%B`` reads the *current locale*, so the same
#: document parses under ``LC_TIME=C`` and fails under ``LC_TIME=de_DE``. Every
#: date in this project is a commit timestamp, and a build whose output depends
#: on the operator's environment cannot be reproduced. Twelve entries is a
#: cheaper fix than a locale contract.
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

#: ``6-May-2013`` -- the House Clerk's ``<action-date>``.
_HOUSE_DATE = re.compile(r"^\s*(\d{1,2})-([A-Za-z]{3,})-(\d{4})\s*$")

#: ``June 6, 2013,  10:34 AM`` -- the Senate's ``<vote_date>``. The time is
#: discarded and the double space is real.
_SENATE_DATE = re.compile(r"^\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})")

#: How a raw cast sorts and which side of the tally it counts toward.
#:
#: The House writes ``Yea``/``Nay`` for a yea-and-nay vote and ``Aye``/``No``
#: for a recorded vote in the Committee of the Whole. They are not synonyms --
#: the distinction is which procedure the chamber was under -- so the cast is
#: kept verbatim in the rendered file and only the tally collapses them.
_BUCKETS = {
    "yea": ("yea", 0),
    "aye": ("yea", 0),
    "nay": ("nay", 1),
    "no": ("nay", 1),
    "present": ("present", 2),
    "present - announced": ("present", 2),
    "not voting": ("not_voting", 3),
    "absent": ("not_voting", 3),
}

#: Tally buckets, in the order they are reported.
BUCKETS = ("yea", "nay", "present", "not_voting")

#: How each bucket is written in prose.
_BUCKET_LABELS = {
    "yea": "Yea",
    "nay": "Nay",
    "present": "Present",
    "not_voting": "Not voting",
}


@dataclass(frozen=True)
class RecordedVote:
    """A vote BILLSTATUS names on a measure, before it has been fetched.

    This is the index, not the vote: BILLSTATUS says which roll calls touched a
    measure and where each is published, and the per-member positions live at
    the other end of :attr:`url`.

    Attributes:
        chamber: :data:`HOUSE` or :data:`SENATE`.
        congress: Congress number.
        session: Session number.
        number: Roll-call number within the session.
        url: Where the chamber publishes the vote.
        when: The date implied by the BILLSTATUS timestamp, which is a **UTC
            instant** rather than the chamber's own day. It is off by one for
            any vote taken after 7pm Eastern -- 60 of the 814 distinct vote
            stamps in the 113th Congress -- so it is used only to place a vote
            that could not be fetched. A vote that was fetched is always dated
            from the chamber's own document; see :func:`parse_house`.
    """

    chamber: str
    congress: str
    session: str
    number: int
    url: str
    when: date | None = None

    @property
    def key(self) -> str:
        """Stable identifier, matching :attr:`RollCall.key`."""
        return f"{self.chamber.lower()}-{self.congress}-{self.session}-{self.number:04d}"

    @property
    def citation(self) -> str:
        """The vote as cited in prose, e.g. ``House 113-1-129``."""
        return f"{self.chamber} {self.congress}-{self.session}-{self.number}"


def references(bill: ET.Element) -> tuple[RecordedVote, ...]:
    """Read the roll calls BILLSTATUS names for one measure.

    Two things about this path matter and both are silent when wrong.

    ``<recordedVotes>`` is nested inside each ``<actions>/<item>``, not carried
    once on the bill, and **the same vote is repeated on every action item that
    mentions it** -- roll 129 appears twice on H.R. 588 of the 113th. A
    descendant search that does not deduplicate reports a measure as having
    voted twice on one question, so entries are collapsed on
    ``(chamber, session, number)``. This is the same duplication trap
    :func:`uscongress.jobs.bills._committees` documents for committees of
    referral, reached by a different route.

    The identifying tuple is *not* the roll-call number alone. Numbering
    restarts each session and each chamber keeps its own sequence, so House
    roll 129 of session 1 and Senate roll 129 of session 2 are different votes
    that collide under any narrower key.

    Args:
        bill: The ``<bill>`` element of a BILLSTATUS document.

    Returns:
        The votes, ordered by chamber, session and number.
    """
    found: dict[tuple[str, str, int], RecordedVote] = {}
    for item in bill.findall(".//recordedVotes/recordedVote"):
        chamber = _find_text(item, "chamber")
        url = _find_text(item, "url")
        number = _int(_find_text(item, "rollNumber"))
        if chamber not in _ROOTS or not url or not number:
            continue
        session = _session(_find_text(item, "sessionNumber"))
        congress = _find_text(item, "congress")
        stamp = _find_text(item, "date")
        found.setdefault(
            (chamber, session, number),
            RecordedVote(
                chamber=chamber,
                congress=congress,
                session=session,
                number=number,
                url=url,
                when=_iso_day(stamp),
            ),
        )
    return tuple(found[k] for k in sorted(found))


@dataclass(frozen=True)
class MemberVote:
    """How one member voted.

    Attributes:
        member_id: The chamber's own identifier for the member.
        id_kind: ``bioguide`` for the House, ``lis`` for the Senate, or an
            empty string when the document names no identifier. The two
            chambers do not publish the same one and neither is translated
            here; see :func:`roll_markdown` for why that is said out loud.
        name: Member's name as the chamber writes it.
        party: One-letter party code, e.g. ``R``.
        state: Two-letter state code.
        cast: The vote as published -- ``Yea``, ``Aye``, ``Not Voting`` -- kept
            verbatim rather than normalized.
        bioguide: The bioguide identifier. For a House member this is what the
            Clerk published, so it equals :attr:`member_id`. For a senator it is
            **added** from the vendored crosswalk in
            :mod:`uscongress.members`, because the Senate publishes no bioguide
            ID -- and it is empty when that crosswalk's surname or state does
            not agree with this document. Rendered as its own column so a
            reader can tell what was published from what was joined.
    """

    member_id: str
    id_kind: str
    name: str
    party: str
    state: str
    cast: str
    bioguide: str = ""

    @property
    def bucket(self) -> str:
        """Which side of the tally this cast counts toward."""
        return _BUCKETS.get(self.cast.strip().lower(), ("other", 4))[0]

    @property
    def rank(self) -> int:
        """Sort position of this cast among the buckets."""
        return _BUCKETS.get(self.cast.strip().lower(), ("other", 4))[1]


@dataclass(frozen=True)
class RollCall:
    """One roll-call vote, from either chamber.

    Attributes:
        chamber: :data:`HOUSE` or :data:`SENATE`.
        congress: Congress number.
        session: Session number, ``1`` or ``2``.
        number: Roll-call number within the session.
        when: The date the chamber records, or None if unparseable.
        question: The question put, e.g. ``On Passage``.
        description: Free-text description of the matter, if published.
        result: Outcome as published, e.g. ``Passed``.
        vote_type: Procedure, e.g. ``2/3 YEA-AND-NAY`` or a majority
            requirement. Empty when the document publishes none.
        measure: The measure named by the vote document itself, normalized to
            a branch name -- ``hr-588``. Empty when the vote names none. This
            is what lets the link from BILLSTATUS be checked rather than
            trusted.
        positions: How each member voted.
        reported: The tally the document states, which is not assumed to match
            :attr:`tally`.
    """

    chamber: str
    congress: str
    session: str
    number: int
    when: date | None
    question: str
    description: str
    result: str
    vote_type: str
    measure: str
    positions: tuple[MemberVote, ...]
    reported: dict[str, int]

    @property
    def key(self) -> str:
        """Stable identifier, e.g. ``house-113-1-0129``.

        Zero-padded so a directory listing sorts in roll-call order rather than
        lexically, which would put roll 10 before roll 9.
        """
        return f"{self.chamber.lower()}-{self.congress}-{self.session}-{self.number:04d}"

    @property
    def citation(self) -> str:
        """The vote as cited in prose, e.g. ``House 113-1-129``."""
        return f"{self.chamber} {self.congress}-{self.session}-{self.number}"

    @property
    def tally(self) -> dict[str, int]:
        """Votes per bucket, counted from :attr:`positions`.

        Counted rather than read. The totals a document states are a separate
        claim about the same fact, and reconciling the two is the only way to
        notice a truncated member list -- which reads as a valid vote with
        fewer members, not as an error.
        """
        counts = dict.fromkeys(BUCKETS, 0)
        for position in self.positions:
            if position.bucket in counts:
                counts[position.bucket] += 1
        return counts

    @property
    def reconciles(self) -> bool:
        """Whether the counted tally matches the one the document states."""
        return all(self.reported.get(k, 0) == v for k, v in self.tally.items())

    @property
    def summary(self) -> str:
        """Yeas against nays, e.g. ``398-2``."""
        counted = self.tally
        return f"{counted['yea']}-{counted['nay']}"


def _int(value: str | None) -> int:
    """Read a count that may be published as an empty element.

    The Senate writes ``<present/>`` rather than ``<present>0</present>`` when
    nobody answered present, so ``int(text)`` raises on the ordinary case.

    Args:
        value: Element text, or None.

    Returns:
        The count, or 0.
    """
    if not value or not value.strip():
        return 0
    try:
        return int(value.strip())
    except ValueError:
        return 0


def _attr(element: ET.Element | None, name: str) -> str:
    """Return an attribute's stripped value.

    Args:
        element: The element, or None.
        name: Attribute name.

    Returns:
        The value, or an empty string.
    """
    if element is None:
        return ""
    return (element.get(name) or "").strip()


def _find_text(root: ET.Element, path: str) -> str:
    """Return a descendant's stripped text.

    Args:
        root: Element to search from.
        path: ElementTree path.

    Returns:
        The text, or an empty string.
    """
    found = root.findtext(path)
    return found.strip() if found else ""


def _iso_day(value: str) -> date | None:
    """Take the calendar day off a BILLSTATUS timestamp.

    Args:
        value: Timestamp, e.g. ``2013-05-06T22:58:32Z``.

    Returns:
        The UTC day, or None if unparseable. See :class:`RecordedVote` for why
        this is not the day the chamber says it voted.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _session(value: str) -> str:
    """Normalize a session number to its digits.

    The House writes ``<session>1st</session>`` and the Senate writes
    ``<session>1</session>``. Both name session one, and a cache keyed on the
    raw string would file the same session under two names and refetch every
    House vote on the second run.

    Args:
        value: Session as published.

    Returns:
        The digits, or an empty string.
    """
    match = re.match(r"\s*(\d+)", value)
    return match.group(1) if match else ""


def _house_date(value: str) -> date | None:
    """Parse the House Clerk's ``<action-date>``, e.g. ``6-May-2013``.

    Args:
        value: Date as published.

    Returns:
        The date, or None if unparseable.
    """
    match = _HOUSE_DATE.match(value)
    if not match:
        return None
    month = _MONTHS.get(match.group(2)[:3].lower())
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError:
        return None


def _senate_date(value: str) -> date | None:
    """Parse the Senate's ``<vote_date>``, e.g. ``June 6, 2013,  10:34 AM``.

    Args:
        value: Date as published.

    Returns:
        The date, or None if unparseable.
    """
    match = _SENATE_DATE.match(value)
    if not match:
        return None
    month = _MONTHS.get(match.group(1)[:3].lower())
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(2)))
    except ValueError:
        return None


#: How a measure type is written in a vote document, mapped to the branch
#: prefix this project uses. The House spaces the letters -- ``H R 588``,
#: ``H CON RES 25`` -- and the Senate writes ``S.``, ``H.J.RES.`` and so on.
_MEASURE_TYPES = {
    "hr": "hr",
    "s": "s",
    "hjres": "hjres",
    "sjres": "sjres",
    "hconres": "hconres",
    "sconres": "sconres",
    "hres": "hres",
    "sres": "sres",
}


def _branch_of(kind: str, number: str) -> str:
    """Normalize a measure named by a vote document to a branch name.

    The chambers do not write a citation the way this project names a branch,
    and they do not write it the same way as each other: the House publishes
    ``H R 588`` and ``H CON RES 25``, the Senate publishes ``S.`` and
    ``H.J.RES.``. Both collapse to the branch once punctuation and spacing are
    dropped.

    Args:
        kind: Measure type as published.
        number: Measure number as published.

    Returns:
        The branch name, e.g. ``hr-588``, or an empty string if the type is not
        one this project builds -- a vote on a nomination or a treaty names no
        measure at all.
    """
    letters = re.sub(r"[^a-z]", "", kind.lower())
    digits = re.sub(r"\D", "", number)
    if letters not in _MEASURE_TYPES or not digits:
        return ""
    return f"{_MEASURE_TYPES[letters]}-{digits}"


def parse_house(xml_bytes: bytes) -> RollCall:
    """Parse one House roll call from ``clerk.house.gov/evs/``.

    The document carries a DOCTYPE naming an external DTD
    (``"../vote.dtd"``). ``defusedxml`` permits the declaration and refuses to
    resolve it, which is the behavior wanted: the file parses without the
    parser reaching back out to clerk.house.gov for every vote.

    Args:
        xml_bytes: Raw roll-call XML.

    Returns:
        The vote and every member's position.

    Raises:
        ValueError: If the document is not a House roll call.
    """
    root = _safe_fromstring(xml_bytes)
    if root.tag != _ROOTS[HOUSE]:
        raise ValueError(f"not a House roll call: <{root.tag}>")
    meta = root.find("vote-metadata")
    if meta is None:
        raise ValueError("House roll call has no <vote-metadata>")

    positions = []
    for recorded in root.findall(".//recorded-vote"):
        legislator = recorded.find("legislator")
        if legislator is None:
            continue
        positions.append(
            MemberVote(
                # The Clerk's `name-id` is the bioguide identifier -- the same
                # one BILLSTATUS uses for sponsors -- so House votes join onto
                # the `Sponsored-By:` trailer without a crosswalk.
                member_id=_attr(legislator, "name-id"),
                id_kind="bioguide" if _attr(legislator, "name-id") else "",
                bioguide=_attr(legislator, "name-id"),
                name=(legislator.text or "").strip()
                or _attr(legislator, "unaccented-name"),
                party=_attr(legislator, "party"),
                state=_attr(legislator, "state"),
                cast=_find_text(recorded, "vote"),
            )
        )

    totals = meta.find("vote-totals/totals-by-vote")
    reported = {
        "yea": _int(_find_text(totals, "yea-total") if totals is not None else ""),
        "nay": _int(_find_text(totals, "nay-total") if totals is not None else ""),
        "present": _int(
            _find_text(totals, "present-total") if totals is not None else ""
        ),
        "not_voting": _int(
            _find_text(totals, "not-voting-total") if totals is not None else ""
        ),
    }

    legis = _find_text(meta, "legis-num")
    kind, _, number = legis.rpartition(" ")

    return RollCall(
        chamber=HOUSE,
        congress=_find_text(meta, "congress"),
        session=_session(_find_text(meta, "session")),
        number=_int(_find_text(meta, "rollcall-num")),
        when=_house_date(_find_text(meta, "action-date")),
        question=_find_text(meta, "vote-question"),
        description=_find_text(meta, "vote-desc"),
        result=_find_text(meta, "vote-result"),
        vote_type=_find_text(meta, "vote-type"),
        measure=_branch_of(kind, number),
        positions=tuple(positions),
        reported=reported,
    )


def parse_senate(xml_bytes: bytes) -> RollCall:
    """Parse one Senate roll call from ``senate.gov``.

    The Senate identifies a member by ``<lis_member_id>`` and publishes no
    bioguide identifier, so the two chambers' votes are not keyed alike. That
    is recorded as published rather than translated; see :func:`roll_markdown`.

    Args:
        xml_bytes: Raw roll-call XML.

    Returns:
        The vote and every member's position.

    Raises:
        ValueError: If the document is not a Senate roll call.
    """
    root = _safe_fromstring(xml_bytes)
    if root.tag != _ROOTS[SENATE]:
        raise ValueError(f"not a Senate roll call: <{root.tag}>")

    positions = []
    for member in root.findall(".//members/member"):
        identifier = _find_text(member, "lis_member_id")
        full = _find_text(member, "member_full")
        state = _find_text(member, "state")
        positions.append(
            MemberVote(
                member_id=identifier,
                id_kind="lis" if identifier else "",
                # Added, not published. The gate refuses a row whose surname or
                # state disagrees with this document; see `members.bioguide_for`.
                bioguide=bioguide_for(identifier, full, state),
                name=_find_text(member, "member_full")
                or " ".join(
                    part
                    for part in (
                        _find_text(member, "first_name"),
                        _find_text(member, "last_name"),
                    )
                    if part
                ),
                party=_find_text(member, "party"),
                state=_find_text(member, "state"),
                cast=_find_text(member, "vote_cast"),
            )
        )

    count = root.find("count")
    reported = {
        "yea": _int(_find_text(count, "yeas") if count is not None else ""),
        "nay": _int(_find_text(count, "nays") if count is not None else ""),
        "present": _int(_find_text(count, "present") if count is not None else ""),
        # The Senate calls this "absent"; the House calls the same thing "not
        # voting". One name is kept so the two reconcile against one tally.
        "not_voting": _int(_find_text(count, "absent") if count is not None else ""),
    }

    return RollCall(
        chamber=SENATE,
        congress=_find_text(root, "congress"),
        session=_session(_find_text(root, "session")),
        number=_int(_find_text(root, "vote_number")),
        when=_senate_date(_find_text(root, "vote_date")),
        question=_find_text(root, "question") or _find_text(root, "vote_question_text"),
        description=_find_text(root, "vote_title"),
        result=_find_text(root, "vote_result"),
        vote_type=_find_text(root, "majority_requirement"),
        measure=_branch_of(
            _find_text(root, "document/document_type"),
            _find_text(root, "document/document_number"),
        ),
        positions=tuple(positions),
        reported=reported,
    )


def parse(xml_bytes: bytes, chamber: str) -> RollCall:
    """Parse a roll call from the chamber that took it.

    Args:
        xml_bytes: Raw roll-call XML.
        chamber: :data:`HOUSE` or :data:`SENATE`, from BILLSTATUS.

    Returns:
        The vote.

    Raises:
        ValueError: If the chamber is unknown, or the document does not match
            it. The mismatch is worth an error rather than a fallback: the
            cache is keyed by chamber, so a document filed under the wrong one
            would otherwise be read by a parser that finds no members and
            reports a unanimous vote of nobody.
    """
    if chamber == HOUSE:
        return parse_house(xml_bytes)
    if chamber == SENATE:
        return parse_senate(xml_bytes)
    raise ValueError(f"unknown chamber: {chamber!r}")


def roll_markdown(roll: RollCall) -> str:
    """Render one roll call as the file that sits on a measure's branch.

    Args:
        roll: The vote.

    Returns:
        Markdown for ``votes/{key}.md``.
    """
    counted = roll.tally
    lines = [
        "---",
        f"chamber: {roll.chamber}",
        f"congress: {roll.congress}",
        f"session: {roll.session}",
        f"roll-call: {roll.number}",
        f"date: {roll.when.isoformat() if roll.when else '(not recorded)'}",
        "---",
        "",
        (
            f"# {roll.chamber} Roll Call {roll.number}"
            f" — {roll.congress}th Congress, session {roll.session}"
        ),
        "",
    ]
    if roll.question:
        lines += [f"**{roll.question}**", ""]
    if roll.description:
        lines += [roll.description, ""]

    lines += [
        "| | |",
        "|---|---|",
        f"| Date | {roll.when.isoformat() if roll.when else '(not recorded)'} |",
    ]
    if roll.vote_type:
        lines.append(f"| Vote type | {roll.vote_type} |")
    if roll.result:
        lines.append(f"| Result | {roll.result} |")
    lines += [f"| {_BUCKET_LABELS[b]} | {counted[b]:,} |" for b in BUCKETS]
    lines.append("")

    if not roll.reconciles:
        # Said on the document rather than only in a log. A vote whose member
        # list and stated totals disagree is still published here, because
        # dropping it would leave an unexplained absence -- but a reader
        # comparing this file to the chamber's own page must not have to
        # discover the discrepancy themselves.
        stated = ", ".join(
            f"{_BUCKET_LABELS[b]} {roll.reported.get(b, 0):,}" for b in BUCKETS
        )
        lines += [
            "> **The published totals do not match the members listed.** This",
            f"> document states {stated}. The table above counts the positions",
            "> actually recorded below. Both are reproduced as found; neither",
            "> has been corrected.",
            "",
        ]

    # The two chambers publish different identifiers and neither is translated
    # into the other. Saying which one this file carries is the difference
    # between a reader joining it to the sponsor trailers correctly and joining
    # it to nothing.
    kinds = {p.id_kind for p in roll.positions if p.id_kind}
    if kinds == {"bioguide"}:
        lines += [
            (
                "Members are identified by bioguide ID, as the Clerk publishes"
                " them — the same identifier the `Sponsored-By:` trailer uses."
            ),
            "",
        ]
    elif kinds == {"lis"}:
        lines += [
            (
                "Members are identified by the Senate's own LIS member ID, which"
                " is what senate.gov publishes; the Senate publishes no bioguide"
                " ID. The `Bioguide` column is **added by this pipeline** from a"
                " vendored crosswalk, not taken from the vote document — it is"
                " what makes a senator's votes joinable to the `Sponsored-By:`"
                " trailer and to House votes. A row is filled only where surname"
                " and state agree between both sources; see"
                " `src/uscongress/members.py`."
            ),
            "",
        ]
        unmatched = [p for p in roll.positions if not p.bioguide]
        if unmatched:
            # Stated rather than left as an em dash to be noticed. An identifier
            # that is silently absent for some members and present for others
            # reads as an upstream inconsistency rather than a refusal here.
            lines += [
                (
                    f"> {len(unmatched)} of {len(roll.positions)} members could"
                    " not be matched to a bioguide ID and are left blank. A"
                    " mapping whose surname or state disagrees with this"
                    " document is refused rather than guessed: a vote"
                    " attributed to the wrong senator would be wrong in a way"
                    " that reads as authoritative."
                ),
                "",
            ]

    # The Senate table carries a column the House table does not, so the two are
    # built separately rather than by widening one. Adding an empty column to
    # every House file would rewrite 12 repositories' worth of commits to say
    # nothing.
    senate = kinds == {"lis"}
    header = (
        "| Member | Party | State | LIS | Bioguide |"
        if senate
        else "| Member | Party | State | ID |"
    )
    rule = "|---|---|---|---|---|" if senate else "|---|---|---|---|"

    def row(member: MemberVote) -> str:
        cells = f"| {member.name} | {member.party or '—'} | {member.state or '—'} |"
        cells += f" {member.member_id or '—'} |"
        if senate:
            cells += f" {member.bioguide or '—'} |"
        return cells

    for cast in sorted(
        {p.cast for p in roll.positions if p.cast},
        key=lambda c: (_BUCKETS.get(c.strip().lower(), ("other", 4))[1], c),
    ):
        members = sorted(
            (p for p in roll.positions if p.cast == cast),
            key=lambda p: (p.name, p.member_id),
        )
        lines += [
            f"## {cast} ({len(members):,})",
            "",
            header,
            rule,
            *(row(p) for p in members),
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"
