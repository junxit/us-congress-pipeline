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

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .. import config
from ..gitbuild import GitRepo
from ..govinfo import GovInfoClient
from ..render import Section, render_title

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


REPO_NAME = "us-congress-code"


def _cache_path(point: ReleasePoint) -> Path:
    """Local cache path for a release point's XML archive.

    Args:
        point: The release point.

    Returns:
        Path under ``data/raw/uscode/``.
    """
    return config.RAW_DIR / "uscode" / f"xml_uscAll@{point.congress}-{point.law_spec}.zip"


async def fetch_archive(client: GovInfoClient, point: ReleasePoint) -> bytes:
    """Fetch a release point's all-titles XML archive, caching it on disk.

    Each archive is ~108 MB and immutable once published, so re-running the
    build never refetches.

    Args:
        client: HTTP client.
        point: Release point to fetch.

    Returns:
        The raw zip bytes.
    """
    cached = _cache_path(point)
    if cached.is_file() and cached.stat().st_size > 0:
        return cached.read_bytes()
    payload = await client.get_bytes(point.xml_url)
    cached.parent.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_suffix(".tmp")
    tmp.write_bytes(payload)
    tmp.rename(cached)
    return payload


def render_archive(archive: bytes) -> list[Section]:
    """Render every title in a release point archive.

    Args:
        archive: Raw zip bytes of ``xml_uscAll@...``.

    Returns:
        Every rendered section across all titles.
    """
    sections: list[Section] = []
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        names = sorted(n for n in bundle.namelist() if n.endswith(".xml"))
        for name in names:
            sections.extend(render_title(bundle.read(name)))
    return sections


def _group_by_chapter(sections: list[Section]) -> dict[str, str]:
    """Concatenate sections into one file per chapter.

    The per-section layout gives the most readable diffs but multiplies tree
    objects; this is the fallback if repository size demands it.

    Args:
        sections: Rendered sections.

    Returns:
        Mapping of file path to contents.
    """
    grouped: dict[str, list[str]] = {}
    for section in sections:
        folder = section.path.rpartition("/")[0]
        grouped.setdefault(f"{folder}.md", []).append(section.markdown)
    return {path: "\n\n---\n\n".join(parts) for path, parts in grouped.items()}


def commit_message(point: ReleasePoint, section_count: int) -> str:
    """Build the commit message for a release point.

    Args:
        point: The release point.
        section_count: Number of sections in the snapshot.

    Returns:
        The full commit message.
    """
    excludes = (
        ", excluding "
        + ", ".join(f"{point.congress}-{n}" for n in point.excludes)
        if point.excludes
        else ""
    )
    when = point.published.isoformat() if point.published else "date not published"
    lines = [
        f"US Code through Public Law {point.congress}-{point.law_number}{excludes}",
        "",
        f"Release point: {point.congress}-{point.law_spec}",
        f"Published:     {when}",
        f"Sections:      {section_count:,}",
    ]
    if point.titles:
        lines.append(
            "Titles:        "
            + ", ".join(str(t) for t in sorted(set(point.titles)))
        )
    if point.excludes:
        lines += [
            "",
            "This snapshot deliberately omits the public laws listed above; OLRC",
            "codified them out of sequence. Ordering follows publication order, not",
            "law number.",
        ]
    lines += [
        "",
        "A release point closes over several public laws at once, so this commit is",
        "not the effect of a single law. Per-law attribution comes from Table III.",
        "",
        f"Source: {point.xml_url}",
    ]
    return "\n".join(lines)


async def seed(
    client: GovInfoClient,
    limit: int | None = None,
    granularity: str = "section",
    repo_path: Path | None = None,
) -> GitRepo:
    """Build the US Code repository from OLRC release points.

    Resumable: a release point whose tag already exists is skipped, so an
    interrupted build restarts cheaply.

    Args:
        client: HTTP client.
        limit: Build only the oldest N release points. None builds all.
        granularity: ``section`` for one file per section, ``chapter`` to
            concatenate sections into one file per chapter.
        repo_path: Override the repository location.

    Returns:
        The repository that was built.
    """
    points = await discover(client)
    if limit is not None:
        points = points[:limit]

    repo = GitRepo(repo_path or config.REPOS_DIR / REPO_NAME)
    repo.init()

    for point in points:
        if repo.has_tag(point.tag):
            continue
        archive = await fetch_archive(client, point)
        sections = render_archive(archive)

        files = (
            {s.path: s.markdown for s in sections}
            if granularity == "section"
            else _group_by_chapter(sections)
        )
        # A release point is a full snapshot; clear the tree so repealed
        # sections are recorded as deletions rather than silently persisting.
        repo.replace_tree(sorted({p.split("/")[0] for p in files}))
        for path, content in files.items():
            repo.write(path, content)

        repo.commit(commit_message(point, len(sections)), when=point.published)
        repo.tag(point.tag)
        print(
            f"  [{point.order:>3}] {point.tag:<26} "
            f"{len(sections):>6,} sections  {len(files):>6,} files",
            flush=True,
        )

    return repo
