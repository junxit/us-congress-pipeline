"""Discover OLRC release points for the US Code.

A *release point* is a snapshot of the Code "current through Public Law N".
OLRC publishes one every few weeks, roughly every 5-6 public laws, and keeps
every prior one downloadable back to July 2013.

Three traps in the source data, all verified against the live page:

* **Release points are not ordered by law number.** 97 of them carry a ``not``
  suffix -- ``119-102not101`` means "through PL 119-102 but *excluding* 119-101",
  because those laws were codified out of sequence. Sorting numerically produces
  a silently wrong history.
* **Not every entry has a parseable date.** ``115-40u1`` is an editorial
  reclassification update with no bracketed date at all, and ``116-155`` writes
  "Pub. L." with a single-digit date. Ordering therefore keys on **list
  position**, which the page guarantees is reverse-chronological; dates are
  parsed as metadata and used only to validate that assumption.
* **The current release point is HTML-commented out** on the prior-release-points
  page, because it lives on the main download page instead. It must be added
  back or the newest snapshot is silently missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from ..govinfo import GovInfoClient

PRIOR_URL = "https://uscode.house.gov/download/priorreleasepoints.htm"
CURRENT_URL = "https://uscode.house.gov/download/download.shtml"
_BASE = "https://uscode.house.gov/download/releasepoints/us/pl"

_LINK = re.compile(
    r'href="releasepoints/us/pl/(\d+)/([0-9A-Za-z]+)/usc-rp@[^"]+">([^<]*)</a>'
)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_DATE = re.compile(r"\((\d{1,2}/\d{1,2}/\d{4})\)")
_TITLES = re.compile(r"affecting titles?\s+([^.]*)", re.I)
_EXCLUDES = re.compile(r"except\s+([\d\-,\sand]+)", re.I)


@dataclass(frozen=True)
class ReleasePoint:
    """One OLRC release point.

    Attributes:
        congress: Congress number, e.g. 119.
        law_spec: Path segment as published, e.g. ``102``, ``102not101``, ``40u1``.
        law_number: Leading public law number, e.g. 102.
        excludes: Public law numbers this snapshot deliberately omits.
        published: Publication date, or None for the one entry that lacks it.
        titles: US Code titles this release point touches.
        order: Position in chronological order; 0 is the oldest.
        is_current: Whether this is the live release point.
    """

    congress: int
    law_spec: str
    law_number: int
    excludes: tuple[int, ...]
    published: date | None
    titles: tuple[int, ...]
    order: int
    is_current: bool

    @property
    def tag(self) -> str:
        """Git tag for this release point, e.g. ``pl-119-102not101``."""
        return f"pl-{self.congress}-{self.law_spec}"

    @property
    def xml_url(self) -> str:
        """URL of the all-titles USLM XML archive."""
        spec = f"{self.congress}-{self.law_spec}"
        return f"{_BASE}/{self.congress}/{self.law_spec}/xml_uscAll@{spec}.zip"

    def title_xml_url(self, title: str) -> str:
        """URL of a single title's USLM XML archive.

        Args:
            title: Title identifier as OLRC writes it, e.g. ``01`` or ``26``.

        Returns:
            The download URL.
        """
        spec = f"{self.congress}-{self.law_spec}"
        return f"{_BASE}/{self.congress}/{self.law_spec}/xml_usc{title}@{spec}.zip"


def _parse_int_list(blob: str) -> tuple[int, ...]:
    """Extract every integer from a comma/and-separated fragment.

    Args:
        blob: Text such as ``"5, 16, and 50"``.

    Returns:
        The integers found, in order.
    """
    return tuple(int(n) for n in re.findall(r"\d+", blob))


def _parse_entry(congress: str, law_spec: str, text: str) -> dict:
    """Parse one release-point list item.

    Args:
        congress: Congress number as a string.
        law_spec: Path segment, e.g. ``102not101``.
        text: Anchor text describing the release point.

    Returns:
        Keyword arguments for :class:`ReleasePoint`, minus ``order`` and
        ``is_current``.
    """
    match = _DATE.search(text)
    published: date | None = None
    if match:
        published = datetime.strptime(match.group(1), "%m/%d/%Y").date()

    titles_match = _TITLES.search(text)
    titles = _parse_int_list(titles_match.group(1)) if titles_match else ()

    # "not" in the path is authoritative; the prose "except" is a cross-check.
    excludes = tuple(int(n) for n in re.findall(r"not(\d+)", law_spec))
    if not excludes:
        excludes_match = _EXCLUDES.search(text)
        if excludes_match:
            # Prose reads "except 119-101"; keep only the law number.
            excludes = tuple(
                int(n) for n in re.findall(r"\d+-(\d+)", excludes_match.group(1))
            )

    return {
        "congress": int(congress),
        "law_spec": law_spec,
        "law_number": int(re.match(r"\d+", law_spec).group()),
        "excludes": excludes,
        "published": published,
        "titles": titles,
    }


def parse_prior(html: str) -> list[dict]:
    """Parse the prior-release-points page, newest first.

    HTML comments are stripped first: the current release point is commented out
    on this page and is fetched separately from the download page.

    Args:
        html: Raw HTML of ``priorreleasepoints.htm``.

    Returns:
        Parsed entries in page order (newest first).
    """
    return [
        _parse_entry(congress, law_spec, text)
        for congress, law_spec, text in _LINK.findall(_COMMENT.sub("", html))
    ]


def parse_current(html: str) -> tuple[int, str] | None:
    """Find the current release point on the main download page.

    Args:
        html: Raw HTML of ``download.shtml``.

    Returns:
        A ``(congress, law_spec)`` pair, or None if not found.
    """
    match = re.search(r"releasepoints/us/pl/(\d+)/([0-9A-Za-z]+)/xml_uscAll@", html)
    if not match:
        return None
    return int(match.group(1)), match.group(2)


async def discover(client: GovInfoClient) -> list[ReleasePoint]:
    """Fetch and order every known release point, oldest first.

    Args:
        client: HTTP client (reused for its pacing and retries; these are
            uscode.house.gov URLs, not govinfo).

    Returns:
        Release points in chronological order, ``order`` running from 0.

    Raises:
        ValueError: If the page is not in reverse-chronological order, which
            would invalidate position-based ordering.
    """
    prior_html = (await client.get_bytes(PRIOR_URL)).decode("utf-8", "replace")
    entries = parse_prior(prior_html)

    dated = [e["published"] for e in entries if e["published"]]
    if any(a < b for a, b in zip(dated, dated[1:])):
        raise ValueError(
            "priorreleasepoints.htm is no longer reverse-chronological; "
            "position-based ordering is unsafe. Re-verify before seeding."
        )

    current_html = (await client.get_bytes(CURRENT_URL)).decode("utf-8", "replace")
    current = parse_current(current_html)
    known = {(e["congress"], e["law_spec"]) for e in entries}
    if current and current not in known:
        # Current RP is commented out on the prior page; re-add it as newest.
        entries.insert(
            0,
            {
                "congress": current[0],
                "law_spec": current[1],
                "law_number": int(re.match(r"\d+", current[1]).group()),
                "excludes": tuple(int(n) for n in re.findall(r"not(\d+)", current[1])),
                "published": None,
                "titles": (),
            },
        )

    current_key = current if current else None
    oldest_first = list(reversed(entries))
    return [
        ReleasePoint(
            **entry,
            order=index,
            is_current=(entry["congress"], entry["law_spec"]) == current_key,
        )
        for index, entry in enumerate(oldest_first)
    ]
