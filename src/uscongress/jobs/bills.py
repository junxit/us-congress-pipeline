"""Build ``us-congress-bills-{congress}`` -- one branch per measure.

Everything is driven from BILLSTATUS rather than the BILLS directory tree.
Each measure's ``<textVersions>`` block names its versions, dates them, and
links directly to the XML, which settles three problems at once:

* **Ordering.** The bill documents cannot order themselves. ``action-date`` is
  absent from engrossed, enrolled and received versions, and a reported version
  repeats the introduction date, so H.R. 588's seven versions cannot be
  sequenced from the files. Sorting ``textVersions`` by date reproduces the real
  progression: introduced, reported, engrossed, received, amended, enrolled.
* **Reach.** The BILLS bulk directories only start at the 113th Congress, but
  ``textVersions`` links resolve much further back -- sampled across House
  bills, 100% of versions from the 111th on and 88-92% for the 109th and 110th
  carry a working URL. Only the 108th is largely text-poor.
* **Labelling.** ``<type>`` gives the version its human name, so a commit can
  say "Engrossed Amendment Senate" rather than ``eas``.

Bills do not descend from a common trunk, so each branch is its own root and
there is no ``main`` for them to diverge from. Commits are written through
``git fast-import``; see :class:`uscongress.gitbuild.FastImport` for why a
working-tree build is not viable at this branch count.
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from defusedxml.ElementTree import fromstring as _safe_fromstring

from .. import amendments, config
from .. import votes as votes_text
from ..billtext import render_bill
from ..gitbuild import GitRepo
from ..govinfo import GovInfoClient
from ..xmlrepair import repair
from . import votes as votes_job

#: Measure types, in the order a listing walks them.
TYPES = ("hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres")

#: How many measures to fetch concurrently before streaming them into git.
#: The stream is sequential, so this overlaps network waiting with commit
#: writing without holding a whole Congress in memory.
BATCH = 40

REPO_PREFIX = "us-congress-bills"

#: Above this many omitted measures, the gap table stops being something a
#: person can read and becomes something to grep, so it moves to a TSV.
INLINE_GAP_LIMIT = 200

#: How many to show inline once the full list has moved out.
GAP_SAMPLE = 50


@dataclass(frozen=True)
class TextVersion:
    """One text version of a measure.

    Attributes:
        label: Human name, e.g. ``Engrossed Amendment Senate``.
        when: Publication date, or None if BILLSTATUS omits it.
        url: Direct link to the version's XML.
        code: Version abbreviation parsed from the filename, e.g. ``eas``.
    """

    label: str
    when: date | None
    url: str
    code: str


@dataclass(frozen=True)
class Committee:
    """One committee a measure was referred to.

    Attributes:
        name: Committee name as BILLSTATUS records it, e.g. ``Natural Resources
            Committee``.
        chamber: ``House`` or ``Senate``. Both chambers run an Armed Services,
            a Judiciary and an Appropriations Committee, so a bare name is
            ambiguous on any measure that has crossed over.
        since: Earliest recorded activity date, or None if BILLSTATUS dates
            none of them.
    """

    name: str
    chamber: str
    since: date | None

    @property
    def label(self) -> str:
        """Committee as written in ``metadata.md``, chamber first."""
        return f"{self.chamber} — {self.name}" if self.chamber else self.name


@dataclass(frozen=True)
class Measure:
    """One measure and the metadata BILLSTATUS records for it.

    Attributes:
        congress: Congress number.
        kind: Measure type, e.g. ``hr``.
        number: Measure number.
        title: Display title.
        introduced: Date introduced.
        sponsor: Sponsor's full name as recorded.
        sponsor_id: Sponsor's bioguide identifier.
        cosponsors: ``(name, bioguide id, date)`` for each cosponsor.
        committees: Committees of referral, earliest activity first.
        actions: ``(date, text)`` for each recorded action.
        law: Public law number, if the measure was enacted.
        versions: Text versions, oldest first.
        recorded_votes: Roll calls BILLSTATUS names on this measure. This is
            the index only; the positions live at the other end of each URL.
        rolls: Roll calls that were fetched, filled in by
            :func:`_build_measure`. Empty on a measure straight out of
            :func:`parse_status`, which does no I/O.
        votes_unavailable: Each roll call BILLSTATUS names that the chamber
            does not publish where it says it does, paired with why.
        derived: ``(reason, count)`` for the amendatory instructions read from
            the measure's **last committed version**, with ``executed`` as the
            reason for the ones that were. Only the last version is counted:
            an instruction usually survives from the introduced text to the
            enrolled one, so counting every version would report the same
            instruction three or four times over.
    """

    congress: str
    kind: str
    number: str
    title: str
    introduced: date | None
    sponsor: str
    sponsor_id: str
    cosponsors: tuple[tuple[str, str, date | None], ...]
    committees: tuple[Committee, ...]
    actions: tuple[tuple[date | None, str], ...]
    law: str
    versions: tuple[TextVersion, ...]
    recorded_votes: tuple[votes_text.RecordedVote, ...] = ()
    rolls: tuple[votes_text.RollCall, ...] = ()
    votes_unavailable: tuple[tuple[votes_text.RecordedVote, str], ...] = ()
    derived: tuple[tuple[str, int], ...] = ()

    @property
    def branch(self) -> str:
        """Branch name, predictable from a citation, e.g. ``hr-588``."""
        return f"{self.kind}-{self.number}"

    @property
    def citation(self) -> str:
        """Measure as commonly written, e.g. ``H.R. 588``."""
        return _CITATIONS.get(self.kind, self.kind.upper()) + f" {self.number}"


#: How each measure type is written in a citation.
_CITATIONS = {
    "hr": "H.R.",
    "s": "S.",
    "hjres": "H.J.Res.",
    "sjres": "S.J.Res.",
    "hconres": "H.Con.Res.",
    "sconres": "S.Con.Res.",
    "hres": "H.Res.",
    "sres": "S.Res.",
}


def _text(element: ET.Element | None, path: str, default: str = "") -> str:
    """Return a child element's stripped text.

    Args:
        element: Parent element, or None.
        path: ElementTree path to the child.
        default: Value when absent.

    Returns:
        The text, or ``default``.
    """
    if element is None:
        return default
    found = element.findtext(path)
    return found.strip() if found and found.strip() else default


def _date(value: str) -> date | None:
    """Parse a BILLSTATUS date, which may carry a time and zone.

    Args:
        value: Date text, e.g. ``2013-02-06`` or ``2013-06-03T04:00:00Z``.

    Returns:
        The date, or None if unparseable or absent.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _version_code(url: str) -> str:
    """Extract the version abbreviation from a BILLS filename.

    Args:
        url: Link to the version XML.

    Returns:
        The code, e.g. ``eas``, or an empty string.
    """
    stem = url.rsplit("/", 1)[-1].removeprefix("BILLS-").removesuffix(".xml")
    trailing = ""
    for char in reversed(stem):
        if char.isalpha():
            trailing = char + trailing
        else:
            break
    return trailing


#: Where committees of referral live, in the two BILLSTATUS spellings. Order
#: matters only in that the first path to match wins; no document uses both.
_COMMITTEE_PATHS = ("committees/item", "committees/billCommittees/item")


def _committees(bill: ET.Element) -> tuple[Committee, ...]:
    """Read the measure's committees of referral.

    Three things about the path matter, and getting any of them wrong is silent.

    The element is ``<item>``, not ``<committee>``. Asking for
    ``committees/committee`` matched nothing at all: sampled over 200 measures
    in each of the 108th, 111th, 113th, 116th and 119th Congresses, 96% carry
    committees and none of them were found, so every ``metadata.md`` in all
    160,190 branches was written without a Committees section.

    The path must be an exact child of ``<bill>``, not ``.//``. Each entry in
    ``<actions>`` carries its own ``<committees>`` block naming the committee
    that acted, so a descendant search counts referrals several times over --
    six items instead of two on H.R. 7283 of the 119th.

    And there are **two spellings**, exactly as there are for the measure
    number. Every one of the 13 documents in 171,916 that writes
    ``<billNumber>`` and ``<billType>`` also wraps this list one level deeper,
    in ``<billCommittees>``; 11 of them carry no committees at all, leaving
    H.R. 4200 of the 113th and H.R. 3354 of the 115th as the two measures whose
    output this changes. Two in 171,916 is far below what any sample finds,
    which is the reason to handle it rather than a reason not to: the failure is
    not an error but two measures missing their committees for ever, with
    nothing to say so.

    Args:
        bill: The ``<bill>`` element.

    Returns:
        Committees, earliest activity first.
    """
    items: list[ET.Element] = []
    for path in _COMMITTEE_PATHS:
        items = bill.findall(path)
        if items:
            break

    found: list[Committee] = []
    for item in items:
        name = _text(item, "name")
        if not name:
            continue
        # The referral date lives on the activities, not on the committee. Only
        # the earliest is kept: it is the point from which the committee holds
        # the measure, which is what decides whether it existed at a version.
        dates = [
            when
            for when in (
                _date(_text(act, "date")) for act in item.findall("activities/item")
            )
            if when is not None
        ]
        found.append(
            Committee(
                name=name,
                chamber=_text(item, "chamber"),
                since=min(dates) if dates else None,
            )
        )
    return tuple(sorted(found, key=lambda c: (c.since is None, c.since or date.min)))


def parse_status(xml_bytes: bytes) -> Measure:
    """Parse one BILLSTATUS document.

    Args:
        xml_bytes: Raw BILLSTATUS XML.

    Returns:
        The measure and its versions, oldest first.

    Raises:
        ValueError: If the document holds no ``<bill>`` element.
    """
    repaired, _ = repair(xml_bytes)
    root = _safe_fromstring(repaired)
    bill = root.find("bill") if root.find("bill") is not None else root

    # govinfo emits two BILLSTATUS spellings. Most measures use <number> and
    # <type>; a minority -- H.R. 4200 of the 113th among them -- use
    # <billNumber> and <billType>. Recognizing only the first silently drops
    # them, which is worse than failing, because the branch simply never
    # appears and nothing says why.
    number = _text(bill, "number") or _text(bill, "billNumber")
    kind = _text(bill, "type") or _text(bill, "billType")
    if bill is None or not number:
        raise ValueError("not a BILLSTATUS document")

    versions: list[TextVersion] = []
    for item in bill.findall(".//textVersions/item"):
        url = _text(item, ".//url")
        # A "Public Law" entry points at the PLAW collection, which is enacted
        # law rather than a version of the bill, and belongs to a later phase.
        if not url or "BILLS-" not in url:
            continue
        versions.append(
            TextVersion(
                label=_text(item, "type", "Unknown version"),
                when=_date(_text(item, "date")),
                url=url,
                code=_version_code(url),
            )
        )

    # Undated versions sort last rather than to the front: an entry with no date
    # is usually the enrolled bill, which is the end of the process, and letting
    # it sort first would invert the whole branch.
    versions.sort(key=lambda v: (v.when is None, v.when or date.min, v.code))

    cosponsors = tuple(
        (
            _text(item, "fullName"),
            _text(item, "bioguideId"),
            _date(_text(item, "sponsorshipDate")),
        )
        for item in bill.findall(".//cosponsors/item")
    )
    actions = tuple(
        (_date(_text(item, "actionDate")), _text(item, "text"))
        for item in bill.findall(".//actions/item")
    )
    law_item = bill.find(".//laws/item")
    law = (
        f"{_text(law_item, 'type')} {_text(law_item, 'number')}".strip()
        if law_item is not None
        else ""
    )

    return Measure(
        congress=_text(bill, "congress"),
        kind=kind.lower(),
        number=number,
        title=_text(bill, "title"),
        introduced=_date(_text(bill, "introducedDate")),
        sponsor=_text(bill, ".//sponsors/item/fullName"),
        sponsor_id=_text(bill, ".//sponsors/item/bioguideId"),
        cosponsors=cosponsors,
        committees=_committees(bill),
        actions=actions,
        law=law,
        versions=tuple(versions),
        recorded_votes=votes_text.references(bill),
    )


def _votes_as_of(
    measure: Measure, version: TextVersion
) -> tuple[
    tuple[votes_text.RollCall, ...], tuple[tuple[votes_text.RecordedVote, str], ...]
]:
    """Roll calls taken on or before a version's date, and the ones not found.

    The cutoff is the same one cosponsors, committees and actions use, and it
    is written once here so a vote cannot follow a different rule from the rest
    of the record it sits beside. A vote must never appear on a commit for text
    that predates it: the introduced version of a measure has not been voted
    on, and saying otherwise on its commit would be a claim about the past that
    the repository itself contradicts two commits later.

    Votes are ordered by date rather than by roll-call number. The two chambers
    number independently, so ordering by number interleaves a Senate vote from
    March with a House vote from July.

    Args:
        measure: The measure, with its votes already fetched.
        version: The version being committed.

    Returns:
        The votes as of this version, oldest first, and the unavailable ones
        that fall in the same window.
    """
    cutoff = version.when

    def within(when: date | None) -> bool:
        return cutoff is None or when is None or when <= cutoff

    rolls = sorted(
        (roll for roll in measure.rolls if within(roll.when)),
        key=lambda r: (r.when or date.min, r.chamber, r.session, r.number),
    )
    missing = sorted(
        (
            (reference, reason)
            for reference, reason in measure.votes_unavailable
            if within(reference.when)
        ),
        key=lambda pair: (
            pair[0].when or date.min,
            pair[0].chamber,
            pair[0].session,
            pair[0].number,
        ),
    )
    return tuple(rolls), tuple(missing)


def vote_documents(measure: Measure, version: TextVersion) -> dict[str, str]:
    """Render the vote files that belong in this version's tree.

    Args:
        measure: The measure, with its votes already fetched.
        version: The version being committed.

    Returns:
        Paths under ``votes/`` mapped to their Markdown.
    """
    rolls, _ = _votes_as_of(measure, version)
    return {f"votes/{roll.key}.md": votes_text.roll_markdown(roll) for roll in rolls}


def metadata_markdown(measure: Measure, version: TextVersion) -> str:
    """Render the measure's record as it stood at one version.

    Cosponsors, committees and actions are filtered to the version's date.
    BILLSTATUS is a single present-day snapshot, so writing it unfiltered onto
    every commit would have the introduced text already reporting that the bill
    became law -- the same trap as Table III's present-day classification in the
    US Code repository, and equally misleading.

    Committees follow the same rule: H.R. 7283 was referred to House Oversight
    on 2026-01-30 and to Senate Homeland Security on 2026-07-23, so listing both
    on the introduced version would have a bill that had not yet passed the
    House already sitting in a Senate committee.

    Args:
        measure: The measure.
        version: The version being committed.

    Returns:
        Markdown for ``metadata.md``.
    """
    cutoff = version.when
    cosponsors = [
        (name, bid)
        for name, bid, signed in measure.cosponsors
        if cutoff is None or signed is None or signed <= cutoff
    ]
    committees = [
        committee
        for committee in measure.committees
        if cutoff is None or committee.since is None or committee.since <= cutoff
    ]
    actions = [
        (when, text)
        for when, text in measure.actions
        if when is not None and (cutoff is None or when <= cutoff)
    ]

    lines = [
        "---",
        f"measure: {measure.citation}",
        f"congress: {measure.congress}",
        f"version: {version.label}",
        "---",
        "",
        f"# {measure.citation}",
        "",
        measure.title or "(untitled)",
        "",
        "> Recorded as of this version. Later cosponsors and actions are",
        "> omitted, so this file is the state of the measure at this point in",
        "> its progress, not its final record.",
        "",
        "## Sponsor",
        "",
        f"- {measure.sponsor or '(none recorded)'}"
        + (f" ({measure.sponsor_id})" if measure.sponsor_id else ""),
        "",
    ]
    if cosponsors:
        lines += [f"## Cosponsors ({len(cosponsors)})", ""]
        lines += [f"- {name} ({bid})" for name, bid in cosponsors]
        lines.append("")
    if committees:
        lines += [f"## Committees ({len(committees)})", ""]
        lines += [f"- {committee.label}" for committee in committees]
        lines.append("")
    rolls, unavailable = _votes_as_of(measure, version)
    if rolls or unavailable:
        lines += [f"## Recorded votes ({len(rolls) + len(unavailable)})", ""]
        for roll in rolls:
            counted = roll.tally
            when = roll.when.isoformat() if roll.when else "(date not recorded)"
            lines.append(
                f"- {when} — [{roll.citation}](votes/{roll.key}.md)"
                f" — {roll.question or 'question not recorded'}"
                + (f" — **{roll.result}**" if roll.result else "")
                + f" ({counted['yea']}–{counted['nay']})"
            )
        for reference, reason in unavailable:
            # Named upstream and not published where it says. Left visible
            # rather than dropped: a measure that shows three votes where the
            # chamber took four is indistinguishable from one that took three.
            when = reference.when.isoformat() if reference.when else "(date unknown)"
            lines.append(
                f"- {when} — {reference.citation} — **not retrievable** "
                f"([as published]({reference.url})): {reason}"
            )
        lines.append("")
    if actions:
        lines += ["## Actions", ""]
        lines += [
            f"- {when.isoformat()} — {text}" for when, text in sorted(actions)
        ]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def commit_message(measure: Measure, version: TextVersion) -> str:
    """Build the commit message for one text version.

    Args:
        measure: The measure.
        version: The version being committed.

    Returns:
        The full message.
    """
    when = (
        version.when.isoformat()
        if version.when
        else "not recorded upstream; dated from the preceding version"
    )
    lines = [
        f"{measure.citation} {version.label}",
        "",
        measure.title or "(untitled measure)",
        "",
        f"Version:  {version.label}"
        + (f" ({version.code})" if version.code else ""),
        f"Date:     {when}",
        f"Congress: {measure.congress}",
        "",
        f"Source: {version.url}",
        "",
    ]
    if measure.sponsor_id:
        lines.append(f"Sponsored-By: {measure.sponsor_id}")
    signed = [
        bid
        for _, bid, on in measure.cosponsors
        if bid and (version.when is None or on is None or on <= version.when)
    ]
    if signed:
        lines.append(f"Cosponsor-Count: {len(signed)}")

    # One trailer per roll call, in the same as-of-this-version window as the
    # cosponsor count above. The tally is counted from the members listed, not
    # copied from the totals the chamber states; where the two disagree the
    # vote file says so.
    rolls, unavailable = _votes_as_of(measure, version)
    for roll in rolls:
        when = roll.when.isoformat() if roll.when else "date-not-recorded"
        lines.append(
            f"Roll-Call: {roll.citation} {when}"
            + (f" {roll.result}" if roll.result else "")
            + f" {roll.summary}"
        )
    for reference, _ in unavailable:
        when = reference.when.isoformat() if reference.when else "date-not-recorded"
        lines.append(f"Roll-Call: {reference.citation} {when} not-retrievable")
    return "\n".join(lines).rstrip() + "\n"


def branch_of(name: str) -> str:
    """Derive a measure's branch from its BILLSTATUS filename.

    Resumption depends on this being cheap. Parsing the document to learn the
    branch name would mean re-reading and re-rendering every measure already
    built, which turns a resume of one missing measure into a rebuild of the
    whole Congress.

    Args:
        name: BILLSTATUS filename, e.g. ``BILLSTATUS-113hr588.xml``.

    Returns:
        The branch name, e.g. ``hr-588``, or an empty string if unparseable.
    """
    match = re.fullmatch(r"BILLSTATUS-\d+([a-z]+)(\d+)\.xml", name)
    return f"{match.group(1)}-{match.group(2)}" if match else ""


def _cache_path(congress: str, name: str) -> Path:
    """Local cache path for one fetched document.

    Args:
        congress: Congress number.
        name: File name.

    Returns:
        Path under ``data/raw/bills/``.
    """
    return config.RAW_DIR / "bills" / congress / name


async def _fetch_cached(client: GovInfoClient, url: str, target: Path) -> bytes:
    """Fetch a URL, reusing a cached copy when present.

    Args:
        client: HTTP client.
        url: Absolute URL.
        target: Where to cache the body.

    Returns:
        The document bytes.

    Raises:
        ValueError: If the response is not XML. govinfo answers a missing
            document with its ordinary web page and HTTP 200 rather than a 404,
            so an unchecked fetch caches 44 KB of HTML under an ``.xml`` name and
            the version is dropped later, for a reason nothing records.
    """
    if target.is_file():
        return target.read_bytes()
    payload = await client.get_bytes(url)
    if not payload.lstrip()[:512].startswith(b"<?xml"):
        raise ValueError(f"not XML ({len(payload)} bytes): {url}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return payload


async def discover(client: GovInfoClient, congress: str) -> list[tuple[str, str]]:
    """List every measure in a Congress.

    Args:
        client: HTTP client.
        congress: Congress number.

    Returns:
        ``(filename, url)`` for each BILLSTATUS document, grouped by type.
    """
    found: list[tuple[str, str]] = []
    for kind in TYPES:
        try:
            entries = await client.list_bulkdata(f"BILLSTATUS/{congress}/{kind}")
        except Exception:  # noqa: BLE001 - a missing type is not fatal
            continue
        found += [(e.name, e.url) for e in entries if e.name.endswith(".xml")]
    return found


async def _build_measure(
    client: GovInfoClient,
    congress: str,
    name: str,
    url: str,
    refresh: bool = False,
) -> tuple[Measure, list[tuple[TextVersion, dict[str, str]]]] | None:
    """Fetch and render every version of one measure.

    Args:
        client: HTTP client.
        congress: Congress number.
        name: BILLSTATUS filename.
        url: BILLSTATUS URL.
        refresh: Discard the cached BILLSTATUS document and fetch it again.
            Only the status document: a bill text version is immutable once
            published, but BILLSTATUS is rewritten upstream whenever the measure
            moves, and re-reading yesterday's copy is exactly what the daily job
            exists to avoid.

    Returns:
        The measure and its rendered versions, or None if it has no usable text.
    """
    status_path = _cache_path(congress, name)
    if refresh:
        status_path.unlink(missing_ok=True)
    try:
        measure = parse_status(await _fetch_cached(client, url, status_path))
    except Exception as exc:  # noqa: BLE001 - one bad measure must not kill the build
        print(f"       {name}: unreadable BILLSTATUS - {type(exc).__name__}: {exc}", flush=True)
        return None

    # Votes are fetched here rather than in a pass of their own so that a build
    # with a cold cache renders what a build with a warm one renders. A vote the
    # chamber does not publish comes back as a marker; anything else -- a
    # timeout, a 500 that outlived five retries -- is left to propagate, because
    # committing the measure without a vote it actually took would publish an
    # incomplete record as a complete one.
    rolls, unavailable = await votes_job.load(client, measure.recorded_votes)
    measure = replace(measure, rolls=rolls, votes_unavailable=unavailable)

    rendered: list[tuple[TextVersion, date | None, dict[str, str]]] = []
    carried: date | None = measure.introduced
    for version in measure.versions:
        # BILLSTATUS leaves some versions undated -- the enrolled bill most
        # often, which is the last one. Committing those at the epoch would date
        # the final commit of every enacted measure to 1970 and break the
        # branch's chronology, so the preceding version's date is carried
        # forward. The message still reports the date as unrecorded.
        stamp = version.when or carried
        carried = stamp
        target = _cache_path(congress, f"text/{version.url.rsplit('/', 1)[-1]}")
        try:
            body = await _fetch_cached(client, version.url, target)
            doc = render_bill(body, legis_num=measure.citation)
        except Exception:  # noqa: BLE001 - a missing or odd version is recorded as absent
            continue
        # Parsed once and used twice: the rendered file and the tally below
        # read the same instructions. Parsing again per version would double
        # the cost across roughly 400,000 commits to learn nothing new.
        try:
            instructions = amendments.read_instructions(body)
        except Exception:  # noqa: BLE001 - a bill that will not parse still gets its text
            instructions = ()
        derived_counts = _count_instructions(instructions)
        rendered.append(
            (
                version,
                stamp,
                {
                    "bill.md": doc.markdown,
                    "metadata.md": metadata_markdown(measure, version),
                    **derived_documents(measure, version, instructions),
                    # fast-import replaces the whole tree on every commit, so
                    # the votes as of this version are re-emitted rather than
                    # inherited. A vote that appeared two commits ago and is
                    # not written again here would be deleted by this one.
                    **vote_documents(measure, version),
                },
            )
        )
    if rendered:
        measure = replace(measure, derived=derived_counts)
    return measure, rendered


async def seed(
    client: GovInfoClient,
    congress: str,
    limit: int | None = None,
    repo_path: Path | None = None,
    rebuild: bool = False,
) -> GitRepo:
    """Build one Congress's bills repository.

    Resumable: a measure whose branch already exists is skipped, so an
    interrupted build restarts cheaply.

    Args:
        client: HTTP client.
        congress: Congress number, e.g. ``113``.
        limit: Build only the first N measures. None builds all.
        repo_path: Override the repository location.
        rebuild: Rewrite every branch from its root rather than skipping the
            ones already present. For correcting a rendering defect in commits
            that are already written: the content changes, so every SHA changes,
            and a repository already pushed needs a force push afterwards.

    Returns:
        The repository that was built.
    """
    measures = await discover(client, congress)
    if limit is not None:
        measures = measures[:limit]

    repo = GitRepo(repo_path or config.REPOS_DIR / f"{REPO_PREFIX}-{congress}")
    repo.init()
    existing = repo.branches()

    print(f"BILLS {congress}: {len(measures)} measures listed", flush=True)

    built = skipped = textless = unreadable = failed = 0
    gaps: list[tuple[str, str, str]] = []
    votes_missing: list[tuple[str, str, str]] = []
    votes_late: list[tuple[str, str, str]] = []
    derived_totals: dict[str, int] = {}

    # Drop measures already on disk before any fetching or rendering, so a
    # resume costs a listing rather than a rebuild.
    pending: list[tuple[str, str]] = []
    for name, url in measures:
        branch = branch_of(name)
        if branch and branch in existing and not rebuild:
            skipped += 1
        else:
            pending.append((name, url))
    if skipped:
        print(f"  {skipped} branches already present, {len(pending)} to build", flush=True)
    if rebuild:
        print(f"  rebuilding all {len(pending)} from their roots", flush=True)

    for start in range(0, len(pending), BATCH):
        batch = pending[start : start + BATCH]
        # Exceptions are collected rather than raised. A measure whose vote
        # fetch outlived its retries used to be able to end a rebuild of 19,315
        # branches at branch 19,000, and a rebuild -- unlike a seed -- skips
        # nothing on the way back, so the whole pass was lost. The measure is
        # left unwritten either way; only the blast radius changes.
        results = await asyncio.gather(
            *(_build_measure(client, congress, name, url) for name, url in batch),
            return_exceptions=True,
        )
        with repo.fast_import(replace=rebuild) as stream:
            for (name, _), result in zip(batch, results):
                if isinstance(result, BaseException):
                    failed += 1
                    print(
                        f"       {name}: {type(result).__name__}: {result}",
                        flush=True,
                    )
                    continue
                if result is None:
                    unreadable += 1
                    continue
                measure, rendered = result
                if not rendered:
                    textless += 1
                    gaps.append((measure.branch, measure.citation, measure.title))
                    continue
                for version, stamp, files in rendered:
                    stream.commit(
                        measure.branch,
                        files,
                        commit_message(measure, version),
                        stamp,
                    )
                existing.add(measure.branch)
                built += 1
                _account_votes(measure, rendered[-1][0], votes_missing, votes_late)
                for reason, count in measure.derived:
                    derived_totals[reason] = derived_totals.get(reason, 0) + count
        print(
            f"  {min(start + BATCH, len(pending)):>6}/{len(pending)}  "
            f"branches={built}  skipped={skipped}  no-text={textless}  "
            f"unreadable={unreadable}  failed={failed}",
            flush=True,
        )

    # Votes and derived amendment totals are accumulated only for measures this
    # run actually built, so a resumable run over a shard that is already built
    # measures none of them. Writing GAPS.md from that would render those two
    # sections empty and *delete* them -- silently, because the textless list it
    # does measure is correct and the document looks fine. It has happened: a
    # plain `seed-bills --congress 119` over a built shard published a GAPS.md
    # with the roll-call and amendment sections gone. So the record is written
    # only by a run that walked every measure, and a partial run says why not.
    if skipped and not rebuild:
        if gaps:
            print(
                f"  GAPS.md left alone: {skipped:,} branches were skipped, so this "
                "run measured no votes or amendment totals and rewriting the "
                "record would drop those sections. Use --rebuild to refresh it.",
                flush=True,
            )
    elif gaps or votes_missing or votes_late or derived_totals:
        _write_gaps(repo, congress, gaps, votes_missing, votes_late, derived_totals)

    accounted = built + skipped + textless + unreadable + failed
    print(
        f"{repo.path.name}: {built} branches built, {skipped} already present, "
        f"{textless} with no usable text, {unreadable} unreadable, "
        f"{failed} failed",
        flush=True,
    )
    if derived_totals:
        executed = derived_totals.get("executed", 0)
        total = sum(derived_totals.values())
        print(
            f"  derived: {total:,} amendatory instructions, {executed:,} executed "
            f"({executed / total:.1%})",
            flush=True,
        )
    if votes_missing or votes_late:
        print(
            f"  votes: {len(votes_missing)} named upstream and not retrievable, "
            f"{len(votes_late)} taken after the last version committed",
            flush=True,
        )
    if accounted != len(measures):
        # Every listed measure must land in exactly one bucket. A mismatch means
        # something was dropped without being counted, which is the one failure
        # mode that looks like success.
        print(
            f"WARNING: {len(measures) - accounted} measures unaccounted for",
            flush=True,
        )
    return repo


def _account_votes(
    measure: Measure,
    last: TextVersion,
    missing: list[tuple[str, str, str]],
    late: list[tuple[str, str, str]],
) -> None:
    """Record the votes a built measure could not place on any of its commits.

    Two different absences, and neither is a build failure.

    A vote can be **named upstream and not published** where BILLSTATUS says it
    is, which :func:`uscongress.jobs.votes.load` already turns into a marker on
    the commit; it is collected here so the repository can state the total
    rather than leaving it to be found one branch at a time.

    A vote can also be **taken after the last version committed**. The record on
    a commit is the record as of that version, so a vote later than the last
    text on the branch has nowhere to sit.

    Which votes those are is decided by asking :func:`_votes_as_of` rather than
    by comparing dates again here. Written out a second time it got a different
    answer: an undated final version carries *every* vote, because a null cutoff
    admits them all, and 124 of the 508 voted measures in the 113th Congress end
    on an undated version. Recomputing the rule reported those votes as having
    nowhere to sit while they were sitting on the branch -- a false claim in the
    one document whose whole purpose is to be trusted about absences.

    The last *committed* version is the cutoff, not the last version BILLSTATUS
    lists. A version whose text could not be fetched is not on the branch, so a
    vote after the last one that was is genuinely unplaced.

    Args:
        measure: A measure that was built.
        last: The final version actually committed to the branch.
        missing: Accumulator of unretrievable votes.
        late: Accumulator of votes that reached no commit.
    """
    for reference, reason in measure.votes_unavailable:
        missing.append((measure.branch, reference.citation, reason))

    placed, _ = _votes_as_of(measure, last)
    carried = {roll.key for roll in placed}
    for roll in measure.rolls:
        if roll.key not in carried:
            late.append(
                (
                    measure.branch,
                    roll.citation,
                    f"{roll.when.isoformat() if roll.when else 'date not recorded'}, "
                    f"after the last version committed"
                    + (f" ({last.when.isoformat()})" if last.when else ""),
                )
            )


def _write_gaps(
    repo: GitRepo,
    congress: str,
    gaps: list[tuple[str, str, str]],
    votes_missing: list[tuple[str, str, str]] | None = None,
    votes_late: list[tuple[str, str, str]] | None = None,
    derived_totals: dict[str, int] | None = None,
) -> None:
    """Record what the repository leaves out, on a ``main`` branch.

    A measure with no usable text gets no branch, so without this it is absent
    from the repository with nothing to say it ever existed. In the 108th
    Congress that is 8,755 of 10,667 measures -- govinfo holds their BILLSTATUS
    record but links no bill text -- and an unexplained absence at that scale
    reads as a build that quietly failed.

    Votes have two absences of their own, and both are stated for the same
    reason; see :func:`_account_votes`.

    This follows the same principle as ``GAPS.md`` in the US Code repository:
    what is missing is stated rather than left to be inferred.

    Args:
        repo: Repository to write into.
        congress: Congress number.
        gaps: ``(branch, citation, title)`` for each omitted measure.
        votes_missing: ``(branch, citation, reason)`` for each vote named
            upstream that the chamber does not publish.
        votes_late: ``(branch, citation, detail)`` for each vote taken after
            every dated version of its measure.
    """
    # fast-import sets a commit's whole tree, so main must be read first.
    # Writing only the gap record would delete the README and license that
    # `uscongress artifacts` puts on this branch.
    existing = repo.read_tree("main")
    merged = {
        **existing,
        **gap_documents(congress, gaps, votes_missing, votes_late, derived_totals),
    }
    if merged == existing:
        return

    counts = [f"{len(gaps):,} measures with no text"]
    if votes_missing:
        counts.append(f"{len(votes_missing):,} votes not published")
    if votes_late:
        counts.append(f"{len(votes_late):,} votes after the last version")

    with repo.fast_import() as stream:
        stream.commit(
            "main",
            merged,
            f"Record what the {congress}th Congress's repository does not hold\n"
            "\n"
            f"{'; '.join(counts)}. Stated rather than left as an\n"
            "unexplained absence.\n",
        )


def _branch_number(branch: str) -> int:
    """Return a branch's trailing number, so measures sort numerically.

    Args:
        branch: Branch name, e.g. ``hr-588``.

    Returns:
        The number, or 0 if there is none.
    """
    tail = branch.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def gap_documents(
    congress: str,
    gaps: list[tuple[str, str, str]],
    votes_missing: list[tuple[str, str, str]] | None = None,
    votes_late: list[tuple[str, str, str]] | None = None,
    derived_totals: dict[str, int] | None = None,
) -> dict[str, str]:
    """Render the record of what this repository does not hold.

    A gap list is not always small. The 108th Congress has 8,755 of them, and
    the resulting table ran to nearly a megabyte of Markdown -- past the point
    where a reader can use it, and past the point where forges render it
    reliably. Above a threshold the document keeps the explanation and a summary,
    shows a sample, and moves the complete list into a TSV that greps cleanly.

    The link to that companion is emitted only in the branch that also writes
    it, so the document can never point at a file that is not there.

    Sections are emitted only when they have something to report, so a Congress
    with every vote retrievable does not carry a heading saying none are
    missing.

    Args:
        congress: Congress number.
        gaps: ``(branch, citation, title)`` for each omitted measure.
        votes_missing: ``(branch, citation, reason)`` for each vote named
            upstream that the chamber does not publish.
        votes_late: ``(branch, citation, detail)`` for each vote taken after
            every dated version of its measure.

    Returns:
        Filename to contents.
    """
    ordered = sorted(gaps, key=lambda g: (g[0].rsplit("-", 1)[0], _branch_number(g[0])))

    counts: dict[str, int] = {}
    for branch, _, _ in ordered:
        kind = branch.rsplit("-", 1)[0]
        counts[kind] = counts.get(kind, 0) + 1

    lines = [f"# What this repository does not hold — {congress}th Congress", ""]
    documents: dict[str, str] = {}

    if ordered:
        lines += [
            f"{len(ordered):,} measures are recorded in BILLSTATUS but have no bill text",
            "linked in any of their `textVersions` entries, so they have no branch in",
            "this repository.",
            "",
            "This is an upstream gap, not a build failure. It is heavily",
            "concentrated in the older Congresses: govinfo's coverage of bill text",
            "thins out before the 111th, and House organizing resolutions -- electing",
            "officers, adopting rules -- generally carry no published text in any",
            "Congress.",
            "",
            "## By measure type",
            "",
            "| Type | Without text |",
            "|---|---|",
            *(
                f"| `{kind}` | {count:,} |"
                for kind, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ),
            "",
        ]

        def row(entry: tuple[str, str, str]) -> str:
            _, citation, title = entry
            return f"| `{citation}` | {(title or '(untitled)').replace('|', '/')} |"

        if len(ordered) <= INLINE_GAP_LIMIT:
            lines += ["## Every measure", "", "| Measure | Title |", "|---|---|"]
            lines += [row(entry) for entry in ordered]
        else:
            shown = ordered[:GAP_SAMPLE]
            lines += [
                f"## The first {len(shown)}",
                "",
                f"The complete list of {len(ordered):,} is in [`GAPS.tsv`](GAPS.tsv), which",
                "is tab-separated so it can be grepped and diffed without a Markdown",
                "reader.",
                "",
                "| Measure | Title |",
                "|---|---|",
            ]
            lines += [row(entry) for entry in shown]
            documents["GAPS.tsv"] = "\n".join(
                ["measure\ttitle", *(f"{c}\t{t or ''}" for _, c, t in ordered)]
            ) + "\n"
        lines.append("")

    if votes_missing:
        lines += _vote_gap_section(
            "Roll-call votes that are not published where they are named",
            [
                f"{len(votes_missing):,} roll calls are named in a measure's BILLSTATUS",
                "record, with a link to the chamber that took them, and the chamber does",
                "not serve a document at that address. The measure's commits carry an",
                "explicit marker where the vote would be, so a branch showing three votes",
                "where the chamber took four does not read as a complete record.",
                "",
                "This is an upstream gap, not a build failure.",
            ],
            votes_missing,
            "Reason",
            documents,
            "GAPS-votes.tsv",
        )

    if votes_late:
        lines += _vote_gap_section(
            "Roll-call votes taken after the last published text",
            [
                f"{len(votes_late):,} roll calls were taken later than the most recent",
                "dated text version of their measure, so there is no commit for them to",
                "sit on. Every record in this repository is the record *as of* the version",
                "it accompanies -- see the caveat in the README -- and a vote cannot be",
                "written onto text that predates it.",
                "",
                "This is a limit of the shape of this repository, not an upstream gap and",
                "not a build failure. The votes themselves are published; they are listed",
                "here with the address the chamber serves them from.",
            ],
            votes_late,
            "When",
            documents,
            "GAPS-late-votes.tsv",
        )

    if derived_totals:
        lines += _derived_section(derived_totals)

    documents["GAPS.md"] = "\n".join(lines).rstrip() + "\n"
    return documents


def _vote_gap_section(
    heading: str,
    preamble: list[str],
    entries: list[tuple[str, str, str]],
    detail_header: str,
    documents: dict[str, str],
    companion: str,
) -> list[str]:
    """Render one conditional vote-gap section, moving a long list to a TSV.

    Args:
        heading: Section heading, without the ``##``.
        preamble: Explanatory lines, stating what kind of absence this is.
        entries: ``(branch, citation, detail)`` rows.
        detail_header: Column header for the third field.
        documents: Companion files, added to in place when the list is long.
        companion: Name for the companion TSV.

    Returns:
        Markdown lines for the section.
    """
    ordered = sorted(
        entries, key=lambda e: (e[0].rsplit("-", 1)[0], _branch_number(e[0]), e[1])
    )
    lines = [f"## {heading}", "", *preamble, ""]

    def row(entry: tuple[str, str, str]) -> str:
        branch, citation, detail = entry
        return f"| `{branch}` | {citation} | {detail.replace('|', '/')} |"

    header = ["| Measure | Vote | " + detail_header + " |", "|---|---|---|"]
    if len(ordered) <= INLINE_GAP_LIMIT:
        lines += header
        lines += [row(entry) for entry in ordered]
    else:
        shown = ordered[:GAP_SAMPLE]
        lines += [
            f"The complete list of {len(ordered):,} is in [`{companion}`]({companion}),",
            "which is tab-separated so it can be grepped and diffed without a",
            "Markdown reader. The first few:",
            "",
            *header,
        ]
        lines += [row(entry) for entry in shown]
        documents[companion] = "\n".join(
            [
                "measure\tvote\t" + detail_header.lower(),
                *(f"{b}\t{c}\t{d}" for b, c, d in ordered),
            ]
        ) + "\n"
    lines.append("")
    return lines


def derived_documents(
    measure: Measure,
    version: TextVersion,
    instructions: tuple[amendments.Instruction, ...],
) -> dict[str, str]:
    """Render the derived account of what this version would do to existing law.

    A pure function of the bill document, deliberately. Reading the target
    section out of ``us-congress-code`` would divide the build: the daily loop
    runs where no copy of that corpus exists, so it would render *unapplied*
    where a local build rendered a result and force-push the weaker version over
    the stronger one every day, with nothing reporting an error. See
    :mod:`uscongress.amendments`.

    Nothing is written for a measure that amends nothing -- a resolution
    congratulating a team, or an original Act that adds law without touching
    any. An empty file saying so on 45% of branches is noise, and the count is
    already in ``GAPS.md``.

    Args:
        measure: The measure.
        version: The version being committed.
        instructions: What the bill instructs, already parsed.

    Returns:
        Paths under ``derived/`` mapped to their Markdown, possibly empty.
    """
    if not instructions:
        return {}
    rendered = amendments.derived_markdown(
        measure.citation, measure.congress, version.label, instructions
    )
    return {"derived/amendments.md": rendered} if rendered else {}


def _count_instructions(
    instructions: tuple[amendments.Instruction, ...],
) -> tuple[tuple[str, int], ...]:
    """Count one version's amendatory instructions by outcome.

    Args:
        instructions: What the bill instructs.

    Returns:
        ``(reason, count)`` pairs, with ``executed`` for the ones carried out.
        Empty for a bill that amends nothing.
    """
    counts: dict[str, int] = {}
    for instruction in instructions:
        key = "executed" if instruction.applied else instruction.reason
        counts[key] = counts.get(key, 0) + 1
    return tuple(sorted(counts.items()))


def _derived_section(totals: dict[str, int]) -> list[str]:
    """Render what the derived amendment execution could and could not do.

    Counts only, with the detail on each measure's own
    ``derived/amendments.md``: there are tens of thousands of instructions in a
    Congress, and a list of them here would be unreadable while the one that
    matters to a given reader is already on the branch they are looking at.

    Args:
        totals: Instruction counts by outcome, ``executed`` for the ones done.

    Returns:
        Markdown lines.
    """
    executed = totals.get("executed", 0)
    total = sum(totals.values())
    reasons = sorted(
        ((r, n) for r, n in totals.items() if r != "executed"),
        key=lambda pair: (-pair[1], pair[0]),
    )
    return [
        "## What the derived amendment execution could not do",
        "",
        f"{total:,} amendatory instructions were read from the measures in this",
        f"repository, and **{executed:,} of them ({executed / total:.1%}) were",
        "carried out**. Each measure's `derived/amendments.md` holds its own,",
        "with the reason beside every one that was not.",
        "",
        "This is not a build failure and it is not going to improve much. A bill",
        "is a list of instructions *about* law, and most of them refer to the law",
        "by structure — *strike subsection (k)* — so the words being removed are",
        "in the US Code and not in the bill. Nothing here guesses them. An",
        "instruction is carried out only where the bill states both the text",
        "removed and the text inserted, so the result follows from the bill alone",
        "and can be checked against it.",
        "",
        "| Why an instruction was not carried out | Instructions |",
        "|---|---|",
        *(f"| {reason} | {count:,} |" for reason, count in reasons),
        "",
        "**The rate varies enormously between Congresses, and that is upstream.**",
        "An instruction can only be placed if GPO tagged the citation it names,",
        "and whether they did is a fact about the year rather than about the",
        "bill: sampled at 1,500 documents per Congress, 64% of the 108th's carry",
        "a machine-readable US Code citation, 55% of the 113th's — and 5% of the",
        "111th's and 5% of the 112th's. So a Congress here may report a very low",
        "share carried out while the reading of it worked perfectly. Compare this",
        "table with a neighbouring Congress before concluding anything about the",
        "bills themselves.",
        "",
        "Counted on each measure's last committed version. An instruction",
        "usually survives from the introduced text to the enrolled one, so",
        "counting every version would report the same instruction several times.",
        "",
    ]
