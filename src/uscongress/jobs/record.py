"""Build ``us-congress-record-{congress}`` -- the Congressional Record, by issue day.

Five decisions, and the measurements behind each.

**A commit is one issue day, and the history accumulates.** The Record is a
*serial* publication: the issue of 4 August 2026 does not revise the issue of
3 August, it succeeds it. So there is no meaningful diff between consecutive
issues, and what git can usefully add is chronology plus addressability. Each
commit therefore writes one day's documents under ``YYYY/MM-DD/`` and leaves
everything before it in place, so ``git log`` reads as the legislative calendar
and the diff of any commit is exactly what was published that day. That needs
``whole_tree=False``: the 119th is ~350 issue days holding ~63,000 documents,
and re-sending the accumulated tree on every commit would push roughly 66 GB
through fast-import to write about 500 MB.

**A branch is an edition.** ``daily`` is CREC, printed overnight; ``bound`` is
CRECB, the permanent edition GPO republishes years later with corrections folded
in and pages renumbered into one continuous run. They are two publications of
the same proceedings, not versions of one document, and interleaving them would
put 2018 in the log twice. Kept apart, ``git diff bound daily -- 2018/07-23/``
answers a real question: what changed between what was said and what was printed
permanently. ``main`` carries only the artifacts and :func:`gap_documents`.

**The two editions are reconciled on the issue day, the only unit they share.**
A CREC package is one issue day. A CRECB package is a bound volume *part* --
``CRECB-2018-pt10`` is 534 granules spanning 23 to 25 July 2018 -- so it is split
by each granule's own date.

**A day can be several packages, and a package can belong to another Congress.**
Both are measured, not hypothetical. Of the 346 CREC packages the 119th lists,
six do not have the plain ``CREC-{date}`` identifier: 11 March 2025 is published
as three overlapping packages whose granules union to the day's real contents,
4 January 2023 is two genuinely distinct issues, and 3 January of an odd year is
two volumes because the volume rolls over as the Congress does. So packages are
merged per day and **deduplicated on the granule identifier**, which is the only
stable identity -- ``CREC-2025-03-11`` and ``CREC-2025-03-11-i45`` share 267 of
their granules. Placement is by the package's own declared Congress, never by
its date: ``CREC-2025-01-03-v170`` is dated the day the 119th convened and
declares the **118th**, which adjourned sine die that morning. Placing it by date
would file it in the 119th; filtering the 119th on its declared Congress without
widening the 118th's window would lose it from both. Both shards therefore hold
a ``2025/01-03/``, each with its own Congress's proceedings, which is what
actually happened.

**Coverage is far narrower than "1873 to present", and most of the gap is
permanent.** Measured against the live API, CRECB holds 2,420 packages over
1873-2018 in four identifier shapes, and only one of them carries text:

===================================  =========  =========  ====
Identifier                           Years      Parts      Text
===================================  =========  =========  ====
``GPO-CRECB-{year}-pt{n}-v{volume}``  1873-1940        632  no
``GPO-CRECB-{year}-pt{n}``            1941-1998      1,417  no
``GPO-CRECB-{year}-pt{n}{A|B}``       1981-1998         34  no
``CRECB-{year}-pt{n}``                1999-2018        337  yes
===================================  =========  =========  ====

The 2,083 ``GPO-CRECB-…`` parts are scanned page images: ``/htm`` answers
HTTP 400, their MODS lists a PDF and nothing else, and they carry no per-granule
date either. CREC starts on 1994-01-01. So the machine-readable Record begins in
**1994**, in the 103rd Congress, and everything before it is unbuildable rather
than merely unbuilt. :func:`gap_documents` says so in every shard, because at
that scale an unexplained absence reads as a build that quietly failed -- and
:func:`parse_mods` reads the absence of a rendition out of the metadata, so a
pre-1999 Congress costs no requests at all rather than ~20,000 that can only
ever answer 400.
"""

from __future__ import annotations

import asyncio
import html as _html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote

from defusedxml.ElementTree import fromstring as _safe_fromstring

from .. import config
from ..gitbuild import GitRepo
from ..govinfo import GovInfoClient
from ..xmlrepair import repair

REPO_PREFIX = "us-congress-record"

#: The daily edition, printed overnight. One package per issue day.
DAILY = "daily"

#: The bound edition, republished years later. One package per volume *part*,
#: spanning several issue days.
BOUND = "bound"

#: govinfo collection behind each branch.
COLLECTION = {DAILY: "CREC", BOUND: "CRECB"}

#: First issue day that exists as text anywhere in govinfo; see the module
#: docstring.
FIRST_TEXT_DAY = date(1994, 1, 1)

#: Latest year the bound edition covers, from the collection listing: 2018 is
#: the newest ``CRECB-{year}-pt{n}``. GPO runs years behind, so a shard for the
#: 116th or later has no ``bound`` branch and will not until that volume exists.
LAST_BOUND_YEAR = 2018

#: Granule class to the directory it lands in. ``ISSUE`` occurs only in the
#: bound edition, where it is the per-day wrapper granule.
SECTIONS = {
    "SENATE": "senate",
    "HOUSE": "house",
    "EXTENSIONS": "extensions",
    "DAILYDIGEST": "daily-digest",
    "ISSUE": "issue",
}

#: Where a granule with an unrecognised class goes. Nothing outside
#: :data:`SECTIONS` appeared in the packages sampled, but a granule dropped for
#: having an unknown class would vanish with nothing to say so.
OTHER_SECTION = "other"

#: Print order of the daily edition's page prefixes: Senate pages are numbered
#: S1..., the House's H1..., Extensions of Remarks E1..., the Daily Digest D1...,
#: so the prefix is what orders the sections. The bound edition has no prefix at
#: all -- its pagination is one continuous run -- which is why an empty prefix
#: ranks with the first: there, the page number alone is already print order.
PAGE_RANK = {"": 0, "S": 0, "H": 1, "E": 2, "D": 3}

#: How many issue days to work on concurrently. Each is ~180 granule fetches, so
#: this is deliberately small: the client's rate limiter is the real ceiling
#: (9 req/s) and a wider fan-out only buys memory pressure.
BATCH = 4

#: Largest page govinfo serves from a listing endpoint.
PAGE_SIZE = 1000

#: How far before and after a Congress's own dates to look for its packages.
#:
#: A daily issue is dated the day it covers, so the only one to fall outside is
#: the outgoing Congress's sine-die sitting -- ``CREC-2025-01-03-v170`` is dated
#: one day past the 118th's last day. The window is widened both ways anyway
#: because it costs one listing call and the declared Congress filters the rest.
#:
#: A bound part is dated by the *last* day it covers, so a part is widened
#: **forward only**: one whose last day already falls before the Congress
#: convened cannot hold a day inside it, while one covering the final sitting is
#: filed after the Congress ended. Widening backwards would read a year of
#: irrelevant parts at 500-700 granules and 7 MB of MODS each.
WINDOW = {
    DAILY: (timedelta(days=30), timedelta(days=30)),
    BOUND: (timedelta(0), timedelta(days=365)),
}

#: The ``offsetMark`` in a ``nextPage`` URL, left percent-encoded on purpose;
#: the token is base64, so decoding it would turn a ``+`` into a space by the
#: time govinfo read it back. The same trap ``update.changed_packages`` absorbs.
_OFFSET_MARK = re.compile(r"[?&]offsetMark=([^&]*)")

#: The 20th Amendment moved the start of a Congress from 4 March to 3 January,
#: first effective for the 74th on 1935-01-03. Before that, a January date in an
#: odd year still belongs to the *previous* Congress -- which matters for placing
#: the pre-1935 bound volumes even though none of them carry text.
_JANUARY_START = date(1935, 1, 3)

#: MODS namespaces, as govinfo emits them.
_MODS = "{http://www.loc.gov/mods/v3}"
_XLINK = "{http://www.w3.org/1999/xlink}"

#: ``Congressional Record Volume 171, Issue 45, (March 11, 2025)`` and
#: ``Congressional Record (Bound Edition), Volume 164 (2018), Part 10``.
_VOLUME = re.compile(r"Volume\s+(\d+)", re.I)
_NUMBER = re.compile(r"\b(?:Issue|Part)\s+(\d+[A-Za-z]?)", re.I)

#: The body of a granule's HTML rendition: a fixed-width text dump inside a
#: single ``<pre>``. See :func:`granule_text` for why it is cut out with a regex
#: rather than parsed.
_PRE = re.compile(rb"<pre>(.*?)</pre>", re.S | re.I)

#: The only three tag forms that occur inside that ``<pre>``, counted across 884
#: granules of three real packages: 1,786 ``<a>`` wrapping the gpo.gov credit,
#: 1,060 ``<DOC>`` separators, and 130 ``<bullet>`` markers.
_ANCHOR = re.compile(rb"</?a\b[^>]*>", re.I)
_DOC_MARKER = re.compile(rb"[ \t]*<DOC>[ \t]*\n?", re.I)

#: The last line of the header block govinfo repeats at the top of every
#: rendition. Anchoring on the credit rather than on the bracketed lines above
#: it is what makes the trim safe: the bracketed lines are not a fixed set --
#: 884 sampled granules produced 20 distinct shapes including the truncated
#: ``[HOUS]`` and ``[HO]`` -- while the credit appears exactly once in every one
#: of them, in the two spellings below. Everything above it is metadata already
#: reproduced in the document's own front matter.
_CREDIT = re.compile(r"^\[?From the .+$")

#: How far into the rendition to look for it. The header runs to at most five
#: lines, and "From the" opens plenty of ordinary sentences on the floor, so an
#: unbounded search would cut a speech off at its first paragraph.
_HEADER_LINES = 8

#: Trailing ordinal on a granule identifier -- ``…-PgS4415-10`` -> 10. Absent on
#: the first granule of a page and on ``…-PgS-FrontMatter``, which take 0.
_ORDINAL = re.compile(r"-(\d+)$")

#: A page reference splits into an optional chamber prefix and a number.
_PAGE = re.compile(r"^([A-Za-z]*)(\d*)")

#: Anything that cannot go in a filename, collapsed for the slug.
_UNSAFE = re.compile(r"[^a-z0-9]+")

#: How each measure type is written in a citation. Matches ``bills._CITATIONS``
#: so a reference here reads the same as the branch it names over there.
CITATIONS = {
    "hr": "H.R.",
    "s": "S.",
    "hjres": "H.J.Res.",
    "sjres": "S.J.Res.",
    "hconres": "H.Con.Res.",
    "sconres": "S.Con.Res.",
    "hres": "H.Res.",
    "sres": "S.Res.",
}

#: Above this many missing granules the list stops being something a person can
#: read and becomes something to grep, so it moves to a TSV. Same threshold and
#: same reasoning as the bills job's gap table.
INLINE_GAP_LIMIT = 200

#: How many to show inline once the full list has moved out.
GAP_SAMPLE = 50


def congress_of(when: date) -> int:
    """Return the Congress sitting on a date.

    Args:
        when: The date.

    Returns:
        The Congress number. ``2026-08-04`` is the 119th; ``2025-01-02`` is
        still the 118th, because the 119th convened on the 3rd.
    """
    congress = (when.year - 1789) // 2 + 1
    if when.year % 2 == 1:
        boundary = (
            date(when.year, 1, 3) if when >= _JANUARY_START else date(when.year, 3, 4)
        )
        if when < boundary:
            congress -= 1
    return congress


def congress_span(congress: int) -> tuple[date, date]:
    """Return the first and last day of a Congress.

    Args:
        congress: Congress number.

    Returns:
        A ``(first, last)`` pair. The 119th runs 2025-01-03 to 2027-01-02; the
        43rd, where the Record itself begins, ran 1873-03-04 to 1875-03-03 under
        the pre-20th-Amendment calendar.
    """

    def convenes(year: int) -> date:
        """Return the day a Congress convened in a given year."""
        january = date(year, 1, 3)
        return january if january >= _JANUARY_START else date(year, 3, 4)

    return convenes(2 * congress + 1787), convenes(2 * congress + 1789) - timedelta(days=1)


@dataclass(frozen=True)
class PackageRef:
    """One govinfo package a day's contents were read from.

    Attributes:
        package_id: govinfo identifier, e.g. ``CREC-2025-03-11-i45``.
        volume: Congressional Record volume, e.g. ``171``.
        number: Issue number in the daily edition, part number in the bound one.
        kind: What ``number`` counts -- ``issue`` or ``part``.
    """

    package_id: str
    volume: str = ""
    number: str = ""
    kind: str = "issue"

    @property
    def label(self) -> str:
        """How the package is cited, e.g. ``Volume 171, issue 45``."""
        if not self.volume:
            return self.package_id
        if not self.number:
            return f"Volume {self.volume}"
        return f"Volume {self.volume}, {self.kind} {self.number}"

    @property
    def url(self) -> str:
        """govinfo's own page for the package."""
        return f"https://www.govinfo.gov/app/details/{self.package_id}"


@dataclass(frozen=True)
class Granule:
    """One document within an issue -- a speech, a vote, a digest entry.

    Attributes:
        granule_id: govinfo identifier, e.g. ``CREC-2026-08-04-pt1-PgS4415-10``.
            Unique across the collection, and shared by every package that lists
            it, which is what makes it the right key for deduplicating a day
            published under several package identifiers.
        package_id: The package this copy was listed under, which is also the
            content path its rendition is served from.
        title: Heading as govinfo records it.
        section: Granule class, e.g. ``SENATE``. Decides the directory.
        when: The granule's own legislative day. Not always the issue date:
            ``CREC-1994-01-25`` carries granules dated 1993-11-23, which is the
            ordinary way Extensions of Remarks are held over.
        url: HTML rendition on the content host.
        page: First printed page, e.g. ``S4415`` in the daily edition or
            ``12725`` in the bound one.
        citation: Preferred citation, e.g. ``172 Cong. Rec. S4415``. Empty for
            the bound edition, whose package MODS carries no such identifier.
        measures: Bills the granule refers to, e.g. ``S. 5221``.
        speakers: ``(name, bioguide id, party, state)`` for each member recorded
            as speaking.
    """

    granule_id: str
    package_id: str
    title: str
    section: str
    when: date | None
    url: str
    page: str = ""
    citation: str = ""
    measures: tuple[str, ...] = ()
    speakers: tuple[tuple[str, str, str, str], ...] = ()

    @property
    def directory(self) -> str:
        """Section directory this granule lands in."""
        return SECTIONS.get(self.section.upper(), OTHER_SECTION)

    @property
    def order(self) -> tuple[int, int, int, str]:
        """Sort key putting granules in printed order.

        Page prefix first, then page number, then the ordinal on the identifier.
        The identifier itself breaks any remaining tie so the order is total: an
        unstable order would renumber files on a rebuild and rewrite the whole
        branch for a change nobody made.
        """
        prefix, number = split_page(self.page)
        return (PAGE_RANK.get(prefix, 9), number, _ordinal(self.granule_id), self.granule_id)


@dataclass(frozen=True)
class Issue:
    """One day's proceedings, from one edition.

    Attributes:
        edition: :data:`DAILY` or :data:`BOUND`.
        congress: The Congress this day is filed under. Held rather than derived
            because a date does not always answer it: 3 January 2025 was the
            118th until noon and the 119th after it, and both shards hold that
            day.
        when: The issue day.
        sources: Every package the day was read from, canonical first.
        granules: Every document of the day, deduplicated, in printed order.
    """

    edition: str
    congress: int
    when: date
    sources: tuple[PackageRef, ...]
    granules: tuple[Granule, ...]

    @property
    def directory(self) -> str:
        """Where the day's documents live, e.g. ``2026/08-04``."""
        return f"{self.when.year:04d}/{self.when.month:02d}-{self.when.day:02d}"


def split_page(page: str) -> tuple[str, int]:
    """Split a page reference into its chamber prefix and number.

    Args:
        page: Page as MODS records it, e.g. ``S4415``, ``12725`` or ``D``.

    Returns:
        A ``(prefix, number)`` pair; the number is 0 when there is none, which
        is how the earliest CREC issues are published -- ``CREC-1994-01-25``
        gives its Daily Digest pages as a bare ``D``.
    """
    match = _PAGE.match(page.strip())
    if not match:
        return "", 0
    prefix, digits = match.groups()
    return prefix.upper(), int(digits) if digits else 0


def _ordinal(granule_id: str) -> int:
    """Return the trailing ordinal of a granule identifier.

    Args:
        granule_id: e.g. ``CREC-2026-08-04-pt1-PgS4415-10``.

    Returns:
        The ordinal, or 0 when there is none. ``…-PgS-FrontMatter`` takes 0 and
        so sorts to the head of its page, which is where front matter belongs.
    """
    match = _ORDINAL.search(granule_id)
    return int(match.group(1)) if match else 0


def parse_title(title: str) -> tuple[str, str]:
    """Read the volume and issue or part number out of a package title.

    The ``published`` listing carries neither as a field -- only ``congress``,
    ``dateIssued``, ``docClass``, ``lastModified``, ``packageId``,
    ``packageLink`` and ``title`` -- so taking them from the title is what keeps
    a day self-describing without a second request per package.

    Args:
        title: e.g. ``Congressional Record Volume 171, Issue 45, (March 11,
            2025)`` or ``Congressional Record (Bound Edition), Volume 164
            (2018), Part 10``.

    Returns:
        A ``(volume, number)`` pair; either may be empty.
    """
    volume = _VOLUME.search(title or "")
    number = _NUMBER.search(title or "")
    return (volume.group(1) if volume else "", number.group(1) if number else "")


def slug(title: str, fallback: str) -> str:
    """Render a title as a filename stem.

    Args:
        title: Granule title, e.g. ``MEASURE PLACED ON THE CALENDAR--S. 5221``.
        fallback: Used when the title slugs to nothing, so a granule titled
            ``---`` still gets a distinct file rather than colliding with every
            other such granule in the day.

    Returns:
        A lowercase hyphenated stem, at most 60 characters.
    """
    text = _UNSAFE.sub("-", (title or "").lower()).strip("-")[:60].strip("-")
    return text or _UNSAFE.sub("-", fallback.lower()).strip("-") or "untitled"


def granule_text(payload: bytes) -> str:
    """Extract a granule's text from its HTML rendition.

    The rendition is a fixed-width text dump inside one ``<pre>``, and it is cut
    out with a regex rather than parsed on purpose: 34 of 884 sampled bodies
    carry a raw, unescaped ``&`` -- "Deputy Executive Director for Community &"
    on page E204 of 2025-03-11 -- so the document is not well formed and a parser
    is the wrong tool. Only three tag forms occur inside the block, all handled
    here.

    ``<bullet>`` is kept as ``●`` rather than dropped. It is not decoration: the
    Record uses it to mark material inserted into the printed proceedings that
    was never spoken on the floor, so deleting it would silently assert that a
    statement was made aloud.

    Args:
        payload: The rendition's bytes.

    Returns:
        The text, with the repeated govinfo header block removed. Every field in
        that block -- volume, issue, section, page -- is reproduced in the
        document's own front matter, so nothing is lost. A rendition with no
        recognisable header keeps all of its text: guessing at where one ended
        would eventually cut a speech off at its first paragraph.

    Raises:
        ValueError: If the payload holds no ``<pre>`` block. govinfo answers a
            missing granule with its ordinary web page and HTTP 200 -- 44,165
            bytes of it, measured -- so a body without ``<pre>`` is a soft 404
            and must never be cached or committed as text.
    """
    match = _PRE.search(payload)
    if match is None:
        raise ValueError(f"no <pre> block; soft 404? ({len(payload)} bytes)")

    body = match.group(1)
    body = _DOC_MARKER.sub(b"", body)
    body = _ANCHOR.sub(b"", body)
    body = body.replace(b"<bullet>", "●".encode())
    text = _html.unescape(body.decode("utf-8", "replace"))

    lines = text.splitlines()
    start = 0
    for index, line in enumerate(lines[:_HEADER_LINES]):
        if _CREDIT.match(line.strip()):
            start = index + 1
    # The credit is followed by blank lines, and in the bound edition by a line
    # holding a single space, so an empty-string test is not enough. Leaving
    # them opens every document on whitespace. Only *leading* blanks go: the
    # indentation of the first real line is the Record's own column layout and
    # must survive, which is why this is not a `strip()`.
    while start < len(lines) and not lines[start].strip():
        start += 1
    return "\n".join(lines[start:]).rstrip()


def parse_mods(xml_bytes: bytes) -> dict[str, dict[str, object]]:
    """Read per-granule metadata out of a package's MODS document.

    One request per package rather than one per granule is the whole reason this
    is worth doing: a 288-granule day is 3.4 MB of MODS against 288 separate
    summary calls, which across the corpus is the difference between a 34-hour
    crawl and a 67-hour one.

    Only the daily edition fills it in. The bound edition's package MODS carries
    no ``<extension>`` on its constituents at all -- no citation, no bill
    references, no members -- which is why :func:`granule_markdown` omits those
    sections rather than printing them empty.

    Args:
        xml_bytes: Raw MODS XML.

    Returns:
        Granule identifier to its fields; empty if the document is unusable. A
        package whose MODS cannot be read still builds: the granule listing
        already carries identity, class, date and title, so what is lost is page
        numbers and bill references, not the text.
    """
    repaired, _ = repair(xml_bytes)
    try:
        root = _safe_fromstring(repaired)
    except ET.ParseError:
        return {}

    found: dict[str, dict[str, object]] = {}
    for item in root.findall(f"{_MODS}relatedItem"):
        if item.get("type") != "constituent":
            continue
        identifier = (item.get("ID") or "").removeprefix("id-")
        extension = item.find(f"{_MODS}extension")
        if extension is not None:
            identifier = extension.findtext(f"{_MODS}accessId", identifier) or identifier
        if not identifier:
            continue

        citation = ""
        for entry in item.findall(f"{_MODS}identifier"):
            if entry.get("type") == "congressional record citation" and entry.text:
                citation = entry.text.strip()
                break

        # A measure may be referenced several times in one granule -- S. 5221 is
        # listed twice on page S4415, once as TITLE and once as OTHER -- so they
        # are deduplicated while keeping the order they were written in.
        measures: list[str] = []
        speakers: list[tuple[str, str, str, str]] = []
        if extension is not None:
            for bill in extension.findall(f"{_MODS}bill"):
                kind = (bill.get("type") or "").lower()
                number = bill.get("number") or ""
                label = f"{CITATIONS.get(kind, kind.upper())} {number}".strip()
                if number and label not in measures:
                    measures.append(label)
            for member in extension.findall(f"{_MODS}congMember"):
                name = ""
                for entry in member.findall(f"{_MODS}name"):
                    if entry.get("type") == "authority-lnf" and entry.text:
                        name = entry.text.strip()
                        break
                row = (
                    name,
                    member.get("bioGuideId") or "",
                    member.get("party") or "",
                    member.get("state") or "",
                )
                if name and row not in speakers:
                    speakers.append(row)

        # Whether a text rendition exists at all, which MODS states and nothing
        # else does. The 2,083 pre-1999 bound parts are scanned page images:
        # their constituents list a PDF and no ``.htm``, and asking for the text
        # answers HTTP 400. Reading that here turns roughly 6,000 doomed
        # requests per pre-1999 Congress -- each retried five times by the
        # client -- into none at all, and makes "there is no text" a fact this
        # job knows rather than one it rediscovers 400 at a time.
        rendition = any(
            (other.get(f"{_XLINK}href") or "").endswith(".htm")
            for other in item.findall(f"{_MODS}relatedItem")
            if other.get("type") == "otherFormat"
        )

        found[identifier] = {
            "page": item.findtext(f"{_MODS}part/{_MODS}extent/{_MODS}start", "") or "",
            "citation": citation,
            "measures": tuple(measures),
            "speakers": tuple(speakers),
            "rendition": rendition,
        }
    return found


def granule_markdown(granule: Granule, issue: Issue, body: str) -> str:
    """Render one granule as Markdown.

    The text is fenced rather than reflowed. The Record is set in fixed-width
    columns and the alignment carries meaning -- roll-call tallies, tables of
    appropriations, the indentation of quoted amendatory text -- so unwrapping it
    into prose would destroy information no later pass could recover.

    The fence is made longer than the longest run of backticks in the body,
    because the Record's own quoting convention produces them: three of the 884
    granules sampled contain ```` ``` ````, all from amendatory text of the form
    ``by striking ```national service''' and inserting``. A fixed three-backtick
    fence would have closed the block early and spilled the rest of the document
    onto the page as broken Markdown.

    Args:
        granule: The granule.
        issue: The issue it belongs to.
        body: Its text, from :func:`granule_text`.

    Returns:
        Markdown for one file.
    """
    longest = max(re.findall(r"`+", body), key=len, default="")
    fence = "`" * max(3, len(longest) + 1)

    lines = [
        "---",
        f"granule: {granule.granule_id}",
        f"date: {(granule.when or issue.when).isoformat()}",
        f"edition: {COLLECTION[issue.edition]}",
        f"section: {granule.section or 'unclassified'}",
    ]
    if granule.page:
        lines.append(f"page: {granule.page}")
    if granule.citation:
        lines.append(f"citation: {granule.citation}")
    lines += ["---", "", f"# {granule.title or '(untitled)'}", ""]

    # A granule dated before its own issue is the ordinary way Extensions of
    # Remarks are held over -- CREC-1994-01-25 carries granules from 1993-11-23
    # -- and a reader who saw only the directory would take the earlier speech
    # for a filing error.
    if granule.when is not None and granule.when != issue.when:
        lines += [
            f"> Submitted for {granule.when.isoformat()} and printed in the",
            f"> issue of {issue.when.isoformat()}.",
            "",
        ]
    if granule.speakers:
        lines += [
            "**Speaking:** "
            + ", ".join(
                f"{name} ({bid})" + (f" [{party}-{state}]" if party and state else "")
                for name, bid, party, state in granule.speakers
            ),
            "",
        ]
    if granule.measures:
        lines += ["**Measures:** " + ", ".join(granule.measures), ""]

    lines += [fence, body, fence, ""]
    return "\n".join(lines).rstrip() + "\n"


#: Print order of the sections, and how each is named in a heading.
_SECTION_ORDER = {"issue": 0, "senate": 1, "house": 2, "extensions": 3, "daily-digest": 4}
_SECTION_TITLES = {
    "issue": "Issue",
    "senate": "Senate",
    "house": "House of Representatives",
    "extensions": "Extensions of Remarks",
    "daily-digest": "Daily Digest",
    OTHER_SECTION: "Unclassified",
}


def issue_index(issue: Issue, written: dict[str, Granule]) -> str:
    """Render the day's table of contents.

    Args:
        issue: The issue.
        written: Repository path to the granule it holds, for this day only.

    Returns:
        Markdown for the day's ``README.md``.
    """
    edition = "Daily edition (CREC)" if issue.edition == DAILY else "Bound edition (CRECB)"
    lines = [
        f"# Congressional Record — {issue.when.isoformat()}",
        "",
        f"{edition} · {issue.congress}th Congress",
        "",
    ]
    # Every source is named, not just the first. A day published as three
    # overlapping packages is a fact about the day, and a reader checking this
    # against govinfo needs to know which ones it was assembled from.
    for source in issue.sources:
        lines.append(f"- {source.label} — [{source.package_id}]({source.url})")
    lines.append("")

    by_section: dict[str, list[tuple[str, Granule]]] = {}
    for path, granule in written.items():
        by_section.setdefault(granule.directory, []).append((path, granule))

    for directory in sorted(by_section, key=lambda d: _SECTION_ORDER.get(d, 9)):
        entries = sorted(by_section[directory], key=lambda e: e[0])
        lines += [
            f"## {_SECTION_TITLES.get(directory, directory.title())} ({len(entries)})",
            "",
            "| Page | Document |",
            "|---|---|",
        ]
        for path, granule in entries:
            name = path.rsplit("/", 1)[-1]
            title = (granule.title or "(untitled)").replace("|", "/")
            lines.append(f"| {granule.page or '—'} | [{title}]({directory}/{name}) |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def issue_documents(
    issue: Issue, bodies: dict[str, str]
) -> tuple[dict[str, str], dict[str, Granule]]:
    """Render one issue day into the files its commit writes.

    Ordinals are assigned over *every* listed granule, including any whose text
    could not be fetched, so a missing document leaves a hole in the numbering
    rather than shifting every file after it. Renumbering would rewrite the whole
    day on the next build for a change nobody made.

    Args:
        issue: The issue.
        bodies: Granule identifier to its text, for the granules that fetched.

    Returns:
        A ``(files, written)`` pair: repository paths to contents, and the same
        paths mapped to the granule each holds.
    """
    counters: dict[str, int] = {}
    files: dict[str, str] = {}
    written: dict[str, Granule] = {}

    for granule in sorted(issue.granules, key=lambda g: g.order):
        directory = granule.directory
        counters[directory] = counters.get(directory, 0) + 1
        body = bodies.get(granule.granule_id)
        if body is None:
            continue
        name = f"{counters[directory]:03d}-{slug(granule.title, granule.granule_id)}.md"
        path = f"{issue.directory}/{directory}/{name}"
        files[path] = granule_markdown(granule, issue, body)
        written[path] = granule

    if written:
        files[f"{issue.directory}/README.md"] = issue_index(issue, written)
    return files, written


def commit_message(issue: Issue, written: int, missing: int) -> str:
    """Build the commit message for one issue day.

    Args:
        issue: The issue.
        written: Documents this commit writes.
        missing: Granules govinfo listed for the day whose text could not be
            fetched. Stated on the commit rather than only in ``GAPS.md``, so a
            partially built day says so where anyone reading its history looks.

    Returns:
        The full message.
    """
    edition = "Daily edition" if issue.edition == DAILY else "Bound edition"
    sections = sorted(
        {g.directory for g in issue.granules}, key=lambda d: _SECTION_ORDER.get(d, 9)
    )
    speakers = {bid for g in issue.granules for _, bid, _, _ in g.speakers if bid}
    measures = {m for g in issue.granules for m in g.measures}

    # Two packages of one day can carry the same volume and issue -- govinfo
    # publishes 11 March 2025 as both `CREC-2025-03-11` and
    # `CREC-2025-03-11-i45`, both titled Volume 171, Issue 45 -- so the labels
    # are deduplicated here while the day's index still names every package.
    labels: list[str] = []
    for source in issue.sources:
        if source.label not in labels:
            labels.append(source.label)

    lines = [
        f"Congressional Record — {issue.when.isoformat()}",
        "",
        f"{written} documents across {len(sections)} sections: "
        + ", ".join(_SECTION_TITLES.get(s, s) for s in sections),
        "",
        f"Edition:  {edition} ({COLLECTION[issue.edition]})",
        f"Date:     {issue.when.isoformat()}",
        "Issue:    " + "; ".join(labels),
        f"Congress: {issue.congress}",
        "",
        "Source: " + " ".join(s.url for s in issue.sources),
        "",
    ]
    if measures:
        lines.append(f"Measures-Referenced: {len(measures)}")
    if speakers:
        lines.append(f"Members-Speaking: {len(speakers)}")
    if missing:
        lines.append(f"Granules-Without-Text: {missing}")
    return "\n".join(lines).rstrip() + "\n"


async def _page_listing(
    client: GovInfoClient, path: str, key: str, **params: str | int
) -> list[dict]:
    """Page a govinfo listing endpoint to exhaustion.

    ``offsetMark`` is not optional -- omitting it answers HTTP 400 -- and govinfo
    keeps serving a ``nextPage`` on the final page pointing back at the mark just
    consumed, so following it without noticing loops for ever against a live API.

    Args:
        client: HTTP client.
        path: API path below the root.
        key: Which array to collect, ``packages`` or ``granules``.
        **params: Extra query parameters.

    Returns:
        Every entry, deduplicated on its identifier.
    """
    found: list[dict] = []
    seen: set[str] = set()
    offset = "*"

    while offset:
        payload = await client.api_json(
            path, offsetMark=offset, pageSize=PAGE_SIZE, **params
        )
        batch = payload.get(key) or []
        for entry in batch:
            identifier = str(entry.get("packageId") or entry.get("granuleId") or "")
            if identifier and identifier not in seen:
                seen.add(identifier)
                found.append(entry)
        match = _OFFSET_MARK.search(str(payload.get("nextPage") or ""))
        offset = (
            match.group(1)
            if match and batch and unquote(match.group(1)) != unquote(offset)
            else ""
        )
    return found


def place(entry: dict, edition: str, congress: int) -> bool:
    """Decide whether a package belongs to this Congress's shard.

    The daily edition is placed by the Congress govinfo declares for the package,
    never by its date. ``CREC-2025-01-03-v170`` is dated the day the 119th
    convened and declares the 118th, which adjourned sine die that morning:
    placing it by date files it in the wrong shard, and the widened discovery
    window exists so the right shard can still find it.

    The bound edition declares a Congress too, but a part may straddle one --
    ``GPO-CRECB-1890-pt12-v21`` runs "March 4, 1889 to October 1, 1890" -- so its
    granules are placed individually by their own dates and every listed part is
    read.

    Args:
        entry: One package listing entry.
        edition: :data:`DAILY` or :data:`BOUND`.
        congress: Congress being built.

    Returns:
        Whether to read the package.
    """
    if edition == BOUND:
        return True
    declared = str(entry.get("congress") or "")
    if declared.isdigit():
        return int(declared) == congress
    when = _entry_date(entry)
    return when is not None and congress_of(when) == congress


async def discover(client: GovInfoClient, congress: int, edition: str) -> list[dict]:
    """List every package of one edition that can hold days of a Congress.

    The ``published`` service is used rather than ``collections``, and the
    difference is not cosmetic: ``collections/CREC/{start}`` filters on
    ``lastModified``, so asking it for 2026-08-04 returns the issue of 2026-06-18
    -- restamped upstream that week -- and misses everything published earlier.
    ``published`` filters on ``dateIssued``, which is the question being asked.

    Args:
        client: HTTP client.
        congress: Congress number.
        edition: :data:`DAILY` or :data:`BOUND`.

    Returns:
        The package entries that belong to this shard, oldest first, so a build
        that stops early has the earliest days rather than an arbitrary slice.
    """
    first, last = congress_span(congress)
    before, after = WINDOW[edition]
    entries = await _page_listing(
        client,
        f"published/{(first - before).isoformat()}/{(last + after).isoformat()}",
        "packages",
        collection=COLLECTION[edition],
    )
    kept = [e for e in entries if place(e, edition, congress)]
    return sorted(
        kept,
        key=lambda e: (
            _entry_date(e) or date.min,
            len(str(e.get("packageId"))),
            str(e.get("packageId")),
        ),
    )


def _cache_path(congress: int, name: str) -> Path:
    """Local cache path for one fetched document.

    Keyed by Congress, as the bills job keys by Congress, and the cost of that
    is bounded and known: the bound edition's discovery window reaches a year
    past the Congress, so ~15-20 parts are listed by two shards and their MODS
    -- about 15 MB each -- is fetched twice, roughly 3 GB across a full crawl.
    Granule renditions are not duplicated, because a part contributes *different*
    days to each shard and the out-of-Congress ones are dropped before any text
    is fetched. Bandwidth is not the binding constraint here; the 9 requests per
    second are, and this costs about 30 of them per Congress.

    Args:
        congress: Congress number.
        name: File name, possibly with directories.

    Returns:
        Path under ``data/raw/record/``.
    """
    return config.RAW_DIR / "record" / str(congress) / name


async def _fetch_cached(
    client: GovInfoClient, url: str, target: Path, kind: str = "html"
) -> bytes:
    """Fetch a URL, reusing a cached copy when present.

    Args:
        client: HTTP client.
        url: Absolute URL.
        target: Where to cache the body.
        kind: ``html`` for a granule rendition, ``xml`` for MODS.

    Returns:
        The document bytes.

    Raises:
        ValueError: If the response is not the document that was asked for.
            govinfo answers a missing one with its ordinary web page and HTTP
            200, measured at 44,165 bytes, so an unchecked fetch caches a web
            page under the granule's name and the document is dropped later for
            a reason nothing records. A rendition is recognised by its ``<pre>``,
            which the web page has not; MODS by its root element -- and note it
            carries **no XML declaration**, so the ``<?xml`` test that guards the
            bills job would reject every valid MODS document there is.
    """
    if target.is_file():
        return target.read_bytes()
    payload = await client.get_bytes(url)
    if kind == "xml":
        head = payload.lstrip()[:512].lower()
        if not head.startswith((b"<?xml", b"<mods")):
            raise ValueError(f"not MODS ({len(payload)} bytes): {url}")
    elif b"<pre>" not in payload[:4096].lower():
        raise ValueError(f"not a rendition ({len(payload)} bytes): {url}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return payload


def granule_url(package_id: str, granule_id: str) -> str:
    """Return a granule's HTML rendition on the content host.

    Derived rather than read from MODS. The content host needs no API key --
    exactly as the bills job fetches text -- so the ~1.1 million renditions of a
    full crawl do not spend the API quota, and a granule listed without a MODS
    entry is still reachable.

    Args:
        package_id: Package that listed the granule, which is also the content
            path it is served from.
        granule_id: Granule identifier.

    Returns:
        The absolute URL.
    """
    return f"https://www.govinfo.gov/content/pkg/{package_id}/html/{granule_id}.htm"


def mods_url(package_id: str) -> str:
    """Return a package's MODS document on the content host.

    Args:
        package_id: Package identifier.

    Returns:
        The absolute URL.
    """
    return f"https://www.govinfo.gov/metadata/pkg/{package_id}/mods.xml"


def _entry_date(entry: dict, fallback: date | None = None) -> date | None:
    """Read a package or granule listing entry's date.

    Args:
        entry: One listing entry.
        fallback: Used when the entry carries no usable date.

    Returns:
        The date, or ``fallback``.
    """
    raw = str(entry.get("dateIssued") or entry.get("granuleDate") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return fallback


@dataclass(frozen=True)
class DayPart:
    """What one package contributes to one issue day.

    Attributes:
        when: The issue day.
        source: The package it came from.
        granules: Its granules for that day.
    """

    when: date
    source: PackageRef
    granules: tuple[Granule, ...]


async def read_package(
    client: GovInfoClient, congress: int, edition: str, entry: dict
) -> list[DayPart]:
    """Turn one package into the issue days it contributes to.

    Args:
        client: HTTP client.
        congress: Congress the shard is being built for, for the cache path.
        edition: :data:`DAILY` or :data:`BOUND`.
        entry: The package's listing entry.

    Returns:
        One part for a daily package; one per legislative day for a bound part.
    """
    package_id = str(entry.get("packageId") or "")
    issued = _entry_date(entry)
    volume, number = parse_title(str(entry.get("title") or ""))
    source = PackageRef(
        package_id=package_id,
        volume=volume,
        number=number,
        kind="issue" if edition == DAILY else "part",
    )

    listing = await _page_listing(client, f"packages/{package_id}/granules", "granules")
    try:
        enrichment = parse_mods(
            await _fetch_cached(
                client,
                mods_url(package_id),
                _cache_path(congress, f"mods/{package_id}.xml"),
                kind="xml",
            )
        )
    except Exception:  # noqa: BLE001 - metadata is an enrichment, not the text
        enrichment = {}

    by_day: dict[date, list[Granule]] = {}
    for item in listing:
        granule_id = str(item.get("granuleId") or "")
        if not granule_id:
            continue
        when = _entry_date(item, issued)
        # A daily package *is* one issue day, even where a granule inside it is
        # dated earlier; a bound part has no such anchor, so the granule's own
        # date is the only thing that can place it.
        day = issued if edition == DAILY and issued is not None else when
        if day is None:
            continue
        extra = enrichment.get(granule_id, {})
        # An empty URL means MODS was read and says this granule has no text
        # rendition. A granule *absent* from MODS is a different case -- the
        # document may have failed to parse -- so it keeps its derived URL and
        # is fetched, because guessing "no text" from missing metadata would
        # drop real proceedings.
        no_text = granule_id in enrichment and not extra.get("rendition", True)
        by_day.setdefault(day, []).append(
            Granule(
                granule_id=granule_id,
                package_id=package_id,
                title=str(item.get("title") or ""),
                section=str(item.get("granuleClass") or ""),
                when=when,
                url="" if no_text else granule_url(package_id, granule_id),
                page=str(extra.get("page") or ""),
                citation=str(extra.get("citation") or ""),
                measures=tuple(extra.get("measures") or ()),  # type: ignore[arg-type]
                speakers=tuple(extra.get("speakers") or ()),  # type: ignore[arg-type]
            )
        )

    return [
        DayPart(when=day, source=source, granules=tuple(granules))
        for day, granules in sorted(by_day.items())
    ]


def merge_issues(edition: str, congress: int, parts: list[DayPart]) -> list[Issue]:
    """Combine every package's contribution into one issue per day.

    Deduplication is on the **granule** identifier, not the package, because that
    is the only stable identity: ``CREC-2025-03-11`` and ``CREC-2025-03-11-i45``
    are two overlapping publications of the same day and share 267 of their
    granules, while ``CREC-2023-01-04`` and ``CREC-2023-01-04-i2`` are two
    genuinely different issues of one day and share none. Merging on the package
    would double the first; treating each package as its own commit would have
    two commits writing the same directory, the second silently overwriting the
    first's numbering.

    Args:
        edition: :data:`DAILY` or :data:`BOUND`.
        congress: Congress being built.
        parts: Every package's contribution.

    Returns:
        One issue per day, oldest first. Bound days outside the Congress are
        dropped here, which is where a straddling part is finally resolved.
    """
    by_day: dict[date, list[DayPart]] = {}
    for part in parts:
        by_day.setdefault(part.when, []).append(part)

    issues: list[Issue] = []
    for day, group in sorted(by_day.items()):
        if edition == BOUND and congress_of(day) != congress:
            continue
        # The canonical package first, so a granule listed by several is
        # fetched from the shortest identifier -- which is the one the granule
        # identifiers themselves are prefixed with.
        group = sorted(group, key=lambda p: (len(p.source.package_id), p.source.package_id))
        seen: set[str] = set()
        granules: list[Granule] = []
        for part in group:
            for granule in part.granules:
                if granule.granule_id in seen:
                    continue
                seen.add(granule.granule_id)
                granules.append(granule)
        issues.append(
            Issue(
                edition=edition,
                congress=congress,
                when=day,
                sources=tuple(part.source for part in group),
                granules=tuple(granules),
            )
        )
    return issues


async def build_issue(
    client: GovInfoClient, congress: int, issue: Issue
) -> tuple[Issue, dict[str, str], dict[str, Granule], list[str]]:
    """Fetch and render every granule of one issue day.

    Args:
        client: HTTP client.
        congress: Congress number, for the cache path.
        issue: The issue to build.

    Returns:
        ``(issue, files, written, missing)`` where ``missing`` names the granules
        govinfo listed and this build could not read.
    """
    bodies: dict[str, str] = {}
    missing: list[str] = []

    async def fetch(granule: Granule) -> None:
        """Fetch and render one granule, recording it as missing if it fails."""
        if not granule.url:
            # MODS already said there is no rendition; see :func:`read_package`.
            missing.append(granule.granule_id)
            return
        target = _cache_path(
            congress, f"html/{granule.package_id}/{granule.granule_id}.htm"
        )
        try:
            bodies[granule.granule_id] = granule_text(
                await _fetch_cached(client, granule.url, target)
            )
        except Exception:  # noqa: BLE001 - one absent granule must not lose the day
            missing.append(granule.granule_id)

    await asyncio.gather(*(fetch(g) for g in issue.granules))
    files, written = issue_documents(issue, bodies)
    return issue, files, written, sorted(missing)


def built_days(repo: GitRepo, branch: str) -> set[date]:
    """Return the issue days a branch already holds.

    Read from the tree rather than from commit messages, and with
    :meth:`GitRepo.list_files` rather than :meth:`GitRepo.read_tree`: the latter
    costs one ``git show`` per file, which on a finished shard is ~63,000
    subprocesses to answer a question about ~350 directories.

    Args:
        repo: The repository.
        branch: Branch to inspect.

    Returns:
        Every day with a committed index.
    """
    days: set[date] = set()
    for path in repo.list_files(branch):
        parts = path.split("/")
        if len(parts) != 3 or parts[2] != "README.md":
            continue
        try:
            days.add(date.fromisoformat(f"{parts[0]}-{parts[1]}"))
        except ValueError:
            continue
    return days


async def seed(
    client: GovInfoClient,
    congress: int,
    limit: int | None = None,
    repo_path: Path | None = None,
    editions: tuple[str, ...] = (DAILY, BOUND),
    rebuild: bool = False,
) -> GitRepo:
    """Build one Congress's Congressional Record repository.

    Resumable and idempotent: an issue day whose index is already committed is
    skipped, for the daily edition before anything at all is fetched, so an
    interrupted crawl restarts at the cost of a listing call rather than a
    rebuild.

    Args:
        client: HTTP client.
        congress: Congress number, e.g. 119.
        limit: Build only the first N issue days *of each edition*. None builds
            all. Small values are a smoke test that still exercises both paths.
        repo_path: Override the repository location.
        editions: Which editions to build.
        rebuild: Rewrite each edition branch from its root rather than skipping
            the days already present. For correcting a rendering defect in
            commits already written: every SHA changes, so a repository already
            pushed needs a force push afterwards.

    Returns:
        The repository that was built.
    """
    repo = GitRepo(repo_path or config.REPOS_DIR / f"{REPO_PREFIX}-{congress}")
    repo.init()

    first, last = congress_span(congress)
    print(f"RECORD {congress}: {first.isoformat()} to {last.isoformat()}", flush=True)

    report: dict[str, dict[str, object]] = {}
    for edition in editions:
        report[edition] = await _seed_edition(
            client, congress, edition, repo, limit, rebuild
        )

    _write_gaps(repo, congress, report)
    return repo


async def _seed_edition(
    client: GovInfoClient,
    congress: int,
    edition: str,
    repo: GitRepo,
    limit: int | None,
    rebuild: bool,
) -> dict[str, object]:
    """Build one edition's branch.

    Args:
        client: HTTP client.
        congress: Congress number.
        edition: :data:`DAILY` or :data:`BOUND`.
        repo: Repository to write into.
        limit: Build only the first N issue days.
        rebuild: Rewrite the branch from its root.

    Returns:
        What this edition contributed, for :func:`gap_documents`.
    """
    collection = COLLECTION[edition]
    packages = await discover(client, congress, edition)
    print(f"  {collection}: {len(packages)} packages listed", flush=True)

    outcome: dict[str, object] = {
        "packages": len(packages),
        "days_upstream": 0,
        "days_built": 0,
        "days_skipped": 0,
        "days_empty": 0,
        "days_unreadable": 0,
        "documents": 0,
        "missing": [],
        "unreadable": [],
        "first": None,
        "last": None,
    }
    if not packages:
        _record_range(repo, edition, outcome)
        return outcome

    existing = set() if rebuild else built_days(repo, edition)
    skipped: set[date] = set()
    built = documents = 0

    # The two editions are planned differently, and the asymmetry is forced by
    # what a package *is*.
    #
    # A daily package is one issue day, named in the listing, so a day can be
    # read, rendered and committed on its own: a resume drops days already built
    # without fetching anything, ``limit`` reads only the packages it will use,
    # and the run commits from the first minute instead of after downloading
    # every package's MODS -- which on the 115th is 460 MB before the first
    # commit, all of it lost from a run interrupted early.
    #
    # A bound part is a range of *pages* whose day boundaries are known only
    # once it is listed. Four consecutive 2017 parts were measured not to share
    # a day, but nothing upstream promises that, so every part is read before
    # any day is merged and a day split across two parts still comes out whole.
    if edition == DAILY:
        by_day: dict[date, list[dict]] = {}
        for entry in packages:
            when = _entry_date(entry)
            if when is not None:
                by_day.setdefault(when, []).append(entry)
        outcome["days_upstream"] = len(by_day)
        skipped = {day for day in by_day if day in existing}
        wanted = sorted(day for day in by_day if day not in existing)
        if limit is not None:
            wanted = wanted[:limit]
        outcome["days_skipped"] = len(skipped)

        if not wanted:
            print(
                f"  {collection}: nothing to build ({len(skipped)} days present)",
                flush=True,
            )
            _record_range(repo, edition, outcome)
            return outcome
        print(
            f"  {collection}: {len(skipped)} days already present, "
            f"{len(wanted)} to build",
            flush=True,
        )

        for start in range(0, len(wanted), BATCH):
            window = wanted[start : start + BATCH]
            parts = await _read(
                client,
                congress,
                edition,
                [entry for day in window for entry in by_day[day]],
                outcome,
            )
            issues = merge_issues(edition, congress, parts)
            # A day whose only package could not be read yields no issue at all,
            # so it would fall through every bucket and trip the reconciliation
            # warning for a reason already recorded. Counting it here keeps the
            # arithmetic exact, which is what lets the warning mean "something
            # unexplained" rather than "something known".
            outcome["days_unreadable"] = int(outcome["days_unreadable"]) + len(
                set(window) - {i.when for i in issues}
            )
            added, wrote = await _commit(
                client, repo, congress, edition, issues, outcome,
                replace=rebuild and start == 0,
            )
            built += added
            documents += wrote
            print(
                f"    {min(start + BATCH, len(wanted)):>5}/{len(wanted)}  "
                f"days={built}  documents={documents}  "
                f"missing={len(outcome['missing'])}",  # type: ignore[arg-type]
                flush=True,
            )
    else:
        parts = []
        for start in range(0, len(packages), BATCH):
            parts += await _read(
                client, congress, edition, packages[start : start + BATCH], outcome
            )
            # A bound part is 500-700 granules and its MODS runs to 7 MB, so
            # reading every part of a Congress to then keep two days is minutes
            # of download thrown away. Stop as soon as enough unbuilt days are in
            # hand -- counting only days this shard will keep, because the
            # widened window deliberately lists parts belonging to the Congress
            # after.
            if limit is not None and limit <= len(
                {
                    p.when
                    for p in parts
                    if congress_of(p.when) == congress and p.when not in existing
                }
            ):
                break

        issues = merge_issues(edition, congress, parts)
        skipped = {i.when for i in issues} & existing
        if not rebuild:
            issues = [i for i in issues if i.when not in existing]
        outcome["days_skipped"] = len(skipped)
        outcome["days_upstream"] = len(skipped) + len(issues)

        issues.sort(key=lambda i: i.when)
        if limit is not None:
            issues = issues[:limit]
        if not issues:
            print(
                f"  {collection}: nothing to build ({len(skipped)} days present)",
                flush=True,
            )
            _record_range(repo, edition, outcome)
            return outcome
        print(
            f"  {collection}: {len(skipped)} days already present, "
            f"{len(issues)} to build",
            flush=True,
        )

        for start in range(0, len(issues), BATCH):
            added, wrote = await _commit(
                client,
                repo,
                congress,
                edition,
                issues[start : start + BATCH],
                outcome,
                replace=rebuild and start == 0,
            )
            built += added
            documents += wrote
            print(
                f"    {min(start + BATCH, len(issues)):>5}/{len(issues)}  "
                f"days={built}  documents={documents}  "
                f"missing={len(outcome['missing'])}",  # type: ignore[arg-type]
                flush=True,
            )

    outcome["days_built"] = built
    outcome["documents"] = documents
    _record_range(repo, edition, outcome)

    accounted = (
        built
        + len(skipped)
        + int(outcome["days_empty"])
        + int(outcome["days_unreadable"])
    )
    print(
        f"  {collection}: {built} days built, {len(skipped)} already present, "
        f"{outcome['days_empty']} with no text, "
        f"{outcome['days_unreadable']} unreadable upstream, "
        f"{len(outcome['missing'])} granules unreadable",  # type: ignore[arg-type]
        flush=True,
    )
    if limit is None and accounted != int(outcome["days_upstream"]):
        # Every issue day govinfo lists must land in exactly one bucket. A
        # mismatch means a day was dropped without being counted, which is the
        # one failure mode that looks exactly like success.
        print(
            f"  WARNING: {collection}: "
            f"{int(outcome['days_upstream']) - accounted} issue days unaccounted for",
            flush=True,
        )
    return outcome


async def _read(
    client: GovInfoClient,
    congress: int,
    edition: str,
    entries: list[dict],
    outcome: dict[str, object],
) -> list[DayPart]:
    """Read a batch of packages into the days they contribute to.

    A package that cannot be read is recorded and the rest of the batch goes on:
    one damaged package must not lose a Congress, but it must not vanish either,
    because a package silently skipped is indistinguishable from one that never
    existed.

    Args:
        client: HTTP client.
        congress: Congress number.
        edition: :data:`DAILY` or :data:`BOUND`.
        entries: Package listing entries.
        outcome: Run record, updated in place.

    Returns:
        Every day part read.
    """
    results = await asyncio.gather(
        *(read_package(client, congress, edition, e) for e in entries),
        return_exceptions=True,
    )
    parts: list[DayPart] = []
    for entry, result in zip(entries, results):
        if isinstance(result, BaseException):
            package_id = str(entry.get("packageId") or "?")
            outcome["unreadable"].append(package_id)  # type: ignore[union-attr]
            print(
                f"    {package_id}: unreadable — {type(result).__name__}: {result}",
                flush=True,
            )
            continue
        parts += result
    return parts


async def _commit(
    client: GovInfoClient,
    repo: GitRepo,
    congress: int,
    edition: str,
    issues: list[Issue],
    outcome: dict[str, object],
    replace: bool = False,
) -> tuple[int, int]:
    """Fetch, render and commit a batch of issue days.

    Args:
        client: HTTP client.
        repo: Repository to write into.
        congress: Congress number.
        edition: Branch to commit to.
        issues: Days to build.
        outcome: Run record, updated in place.
        replace: Rewrite the branch from its root. Only ever true for the first
            batch of a rebuild: after that the branch being written is the one
            this run just started, and replacing again would throw it away one
            batch at a time.

    Returns:
        A ``(days, documents)`` pair for what was actually committed.
    """
    if not issues:
        return 0, 0
    results = await asyncio.gather(*(build_issue(client, congress, i) for i in issues))

    built = documents = 0
    # Each issue day is one commit that *adds* to the branch; see the module
    # docstring for why the tree accumulates rather than being replaced.
    with repo.fast_import(replace=replace) as stream:
        for issue, files, written, missing in results:
            outcome["missing"].extend(missing)  # type: ignore[union-attr]
            if not written:
                outcome["days_empty"] = int(outcome["days_empty"]) + 1
                continue
            stream.commit(
                edition,
                files,
                commit_message(issue, len(written), len(missing)),
                issue.when,
                whole_tree=False,
            )
            built += 1
            documents += len(written)
    return built, documents


def _record_range(repo: GitRepo, edition: str, outcome: dict[str, object]) -> None:
    """Record what a branch actually holds, read back out of git.

    Read from the repository rather than from the run's own bookkeeping, for the
    same reason ``update`` compares ref maps: a build that reported success while
    writing nothing is the one failure that looks exactly like a quiet day.

    These are counts of the **repository**, never of the run. ``GAPS.md`` is
    built from them, so a re-run that finds everything already present renders
    the identical document and makes no commit on ``main``. Reporting "documents
    written by this run" there instead would have rewritten ``main`` every time
    the job was invoked, which is the churn a resumable build exists to avoid.

    Args:
        repo: The repository.
        edition: Branch to inspect.
        outcome: Run record, updated in place.
    """
    paths = repo.list_files(edition)
    days = built_days(repo, edition)
    outcome["days_present"] = len(days)
    outcome["documents_present"] = sum(1 for p in paths if not p.endswith("/README.md"))
    outcome["first"] = min(days).isoformat() if days else None
    outcome["last"] = max(days).isoformat() if days else None


def _write_gaps(
    repo: GitRepo, congress: int, report: dict[str, dict[str, object]]
) -> None:
    """Record what this shard does not hold, on a ``main`` branch.

    Args:
        repo: Repository to write into.
        congress: Congress number.
        report: What each edition contributed.
    """
    # fast-import sets a commit's whole tree, so main must be read first: writing
    # only the gap record would delete the README and licence that
    # `uscongress artifacts` puts on this branch.
    existing = repo.read_tree("main")
    bills = f"us-congress-bills-{congress}"
    merged = {
        **existing,
        **gap_documents(
            congress,
            report,
            bills_repo=bills if (config.REPOS_DIR / bills / ".git").is_dir() else "",
        ),
    }
    if merged == existing:
        return

    with repo.fast_import() as stream:
        stream.commit(
            "main",
            merged,
            f"Record what the {congress}th Congress's Record does not hold\n"
            "\n"
            "Coverage of each edition, the packages that could not be read, and\n"
            "the granules with no text. Stated rather than left as an\n"
            "unexplained absence.\n",
        )


def gap_documents(
    congress: int, report: dict[str, dict[str, object]], bills_repo: str = ""
) -> dict[str, str]:
    """Render the record of what this shard is missing.

    Args:
        congress: Congress number.
        report: What each edition contributed.
        bills_repo: Sibling bills repository to cross-link, or an empty string.
            Gated by the caller on that repository existing, because a link to a
            repository nobody has created is a 404 multiplied across every shard
            -- the exact failure ``uscongress check-links`` was written to catch.

    Returns:
        Filename to contents.
    """
    first, last = congress_span(congress)
    bound = report.get(BOUND, {})

    lines = [
        f"# What this repository does not hold — {congress}th Congress",
        "",
        f"The {congress}th Congress sat from {first.isoformat()} to {last.isoformat()}.",
        "",
        "## Coverage",
        "",
        "| Edition | Branch | Issue days | Documents | First | Last |",
        "|---|---|---|---|---|---|",
    ]
    for edition, name in (
        (DAILY, "Daily edition (CREC)"),
        (BOUND, "Bound edition (CRECB)"),
    ):
        entry = report.get(edition, {})
        lines.append(
            f"| {name} | `{edition}` | {int(entry.get('days_present') or 0):,} | "
            f"{int(entry.get('documents_present') or 0):,} | "
            f"{entry.get('first') or '—'} | {entry.get('last') or '—'} |"
        )
    lines += [
        "",
        "Every figure above is read back out of the branch itself, not counted by",
        "the run that wrote it — so this table describes the repository as it",
        "stands, and a re-run that finds everything already built renders it",
        "identically and commits nothing.",
        "",
    ]

    # "Not looked at" and "looked at and not there" are different claims, and a
    # run restricted to one edition must not report the other as absent
    # upstream. The distinction is the presence of the key, not its contents.
    unexamined = [
        name
        for edition, name in (
            (DAILY, "daily edition (CREC)"),
            (BOUND, "bound edition (CRECB)"),
        )
        if edition not in report
    ]
    if unexamined:
        lines += [
            "## Not examined by the run that wrote this",
            "",
            "This build was restricted to one edition, so the "
            + " and ".join(unexamined)
            + " was not asked for at all. Its row above reports the branch as it",
            "stands, which is not the same as a statement about what upstream holds.",
            "",
        ]

    if last < FIRST_TEXT_DAY:
        lines += [
            "## There is no machine-readable text for this Congress",
            "",
            "Both branches are empty, and that is upstream, not a build failure.",
            "",
            "The Congressional Record has been published since 1873, and govinfo",
            "carries the whole run as the bound edition — 2,420 volume parts",
            "covering 1873 to 2018. Only the 337 parts from 1999 onwards have an",
            "HTML rendition. The 2,083 before that are scanned page images: their",
            "metadata lists a PDF and nothing else, and asking for the text of one",
            "of their granules answers **HTTP 400**, not a document.",
            "",
            "The daily edition begins on **1 January 1994**. So the earliest",
            "Congressional Record text that exists in machine-readable form",
            "anywhere in govinfo falls in the 103rd Congress, and no amount of",
            "crawling will produce any for this one.",
            "",
            "The pages themselves are readable as PDF at",
            "<https://www.govinfo.gov/collection/congressional-record>. They are",
            "not mirrored here because a page image is not text, and this project",
            "does not pretend otherwise.",
            "",
        ]
    elif BOUND in report and not bound.get("packages"):
        lines += [
            "## The bound edition does not exist for this Congress yet",
            "",
            "There is no `bound` branch. The bound edition is republished years",
            "after the fact, with corrections folded in and pages renumbered into",
            f"one continuous run; the newest volume govinfo carries is {LAST_BOUND_YEAR}.",
            "",
            "So the daily edition is the only text there is for these years, and it",
            "is provisional: members may revise and extend their remarks, and those",
            "revisions appear in the bound edition rather than here. When GPO",
            "publishes it this shard gains a second branch, and the difference",
            "between what was said and what was printed becomes diffable.",
            "",
        ]

    if first < FIRST_TEXT_DAY <= last:
        lines += [
            "## The first sittings of this Congress predate the electronic Record",
            "",
            f"The daily edition begins on {FIRST_TEXT_DAY.isoformat()}, after this",
            f"Congress convened on {first.isoformat()}. The sittings before that date",
            "exist only as scanned pages in the bound edition, which carries no text",
            "for those years either — see above.",
            "",
        ]

    def collect(field: str) -> list[str]:
        """Gather one list-valued field across both editions, deduplicated."""
        found: set[str] = set()
        for entry in report.values():
            found |= {str(item) for item in (entry.get(field) or [])}  # type: ignore[union-attr]
        return sorted(found)

    unreadable = collect("unreadable")
    missing = collect("missing")
    empty = sum(int(entry.get("days_empty") or 0) for entry in report.values())

    if unreadable:
        lines += [
            "## Packages that could not be read",
            "",
            "govinfo listed these and this build could not retrieve their contents,",
            "so the issue days inside them have no commit:",
            "",
            *(f"- `{package}`" for package in unreadable[:GAP_SAMPLE]),
            "",
        ]
    if empty:
        lines += [
            "## Issue days with no readable document",
            "",
            f"{empty:,} issue day(s) were listed upstream and every granule in them",
            "failed to produce text, so they have no commit at all.",
            "",
        ]

    documents: dict[str, str] = {}
    if missing:
        lines += [
            "## Granules with no text",
            "",
            f"{len(missing):,} granule(s) are listed in their issue and have no",
            "readable HTML rendition, so the issue's index skips a number where each",
            "one should be. Ordinals are assigned over every listed granule",
            "precisely so the hole stays visible rather than being closed up.",
            "",
        ]
        if len(missing) <= INLINE_GAP_LIMIT:
            lines += [*(f"- `{granule}`" for granule in missing), ""]
        else:
            lines += [
                "The complete list is in [`GAPS.tsv`](GAPS.tsv), which is",
                "tab-separated so it can be grepped and diffed without a Markdown",
                "reader. The first few:",
                "",
                *(f"- `{granule}`" for granule in missing[:GAP_SAMPLE]),
                "",
            ]
            documents["GAPS.tsv"] = "\n".join(["granule", *missing]) + "\n"

    lines += [
        "## What this repository is not",
        "",
        "The Record is a record of *proceedings*, not of outcomes. It reports what",
        "was said and what was laid before each chamber; how each member voted is",
        "not derivable from it, and the text of a measure is not here either.",
        "",
    ]
    if bills_repo:
        lines += [
            "Measures named in a document are cross-referenced by citation in that",
            "document's front matter. The text of each one is a branch in",
            f"[`{bills_repo}`](https://github.com/junxit/{bills_repo}).",
            "",
        ]
    else:
        lines += [
            "Measures named in a document are cross-referenced by citation in that",
            "document's front matter, as plain text rather than as links: the",
            "sibling `us-congress-bills` shard for this Congress does not exist, and",
            "a link to a repository nobody has created is a 404 repeated across",
            "every document that carries it.",
            "",
        ]

    documents["GAPS.md"] = "\n".join(lines).rstrip() + "\n"
    return documents
