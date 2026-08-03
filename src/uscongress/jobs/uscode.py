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
from ..render import Section, render_title, to_file_map
from ..xmlrepair import repair
from .table3 import trailers as table3_trailers

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


def title_number_of(filename: str) -> str:
    """Extract the US Code title from an archive member name.

    Args:
        filename: Archive member, e.g. ``xml/usc31.xml`` or ``xml/usc11A.xml``.

    Returns:
        The title as OLRC writes it (``31``, ``11A``), or an empty string.
    """
    match = re.search(r"usc(\w+)\.xml$", filename)
    return match.group(1).lstrip("0") if match else ""


def render_archive(archive: bytes) -> tuple[list[Section], dict[str, str]]:
    """Render every title in a release point archive, tolerating bad files.

    OLRC archives are unreliable often enough that a single bad file must not
    abort the build. Two distinct defects have been observed:

    * **Unbalanced tags** -- ``usc16.xml`` at 113-46 closes elements it never
      opened. Repairable; see :mod:`uscongress.xmlrepair`.
    * **Corrupt bytes** -- ``usc31.xml`` at 113-65 contains binary garbage
      across six lines, including control characters, in the middle of a UUID.
      The ZIP's CRC passes, so OLRC published it that way. Not repairable.

    Args:
        archive: Raw zip bytes of ``xml_uscAll@...``.

    Returns:
        A ``(sections, damage)`` pair, where ``damage`` maps the US Code title
        number to a description of what went wrong with it.
    """
    sections: list[Section] = []
    damage: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        names = sorted(n for n in bundle.namelist() if n.endswith(".xml"))
        for name in names:
            title = title_number_of(name) or name
            raw = bundle.read(name)
            fixed, report = repair(raw)
            try:
                sections.extend(render_title(fixed))
            except Exception as exc:  # noqa: BLE001 - one bad title must not kill the build
                damage[title] = f"unparseable ({type(exc).__name__}: {exc})"
                continue
            if report.changed:
                damage[title] = report.describe()
    return sections, damage


def _title_of(path: str) -> str:
    """Return the top-level title directory of a repository path."""
    return path.split("/", 1)[0]


def repair_truncated_titles(
    files: dict[str, str],
    previous: dict[str, str],
    declared: tuple[int, ...],
    min_drop: float = 0.10,
    min_sections: int = 25,
) -> tuple[dict[str, str], list[str]]:
    """Carry forward titles that an archive dropped without explanation.

    Some archives are truncated without being malformed. ``usc46.xml`` shrinks
    from 7,326,729 to 4,705,104 bytes across release points 113-44 and 113-45 --
    912 sections down to 576 -- then returns to 912 at 113-46. The XML parses
    perfectly, so a structural check cannot catch it.

    Committing that verbatim would record 336 repeals and then un-repeal them
    two commits later: a history that never happened.

    The signal is that OLRC *declares* which titles a release point affects. If
    a title loses a large share of its sections and is not on that list, the
    archive is defective rather than the law having changed, so the previous
    snapshot's files for that title are carried forward.

    Args:
        files: Freshly rendered files for this release point.
        previous: Files as committed for the preceding release point.
        declared: Title numbers this release point claims to affect.
        min_drop: Fractional loss required before a title is suspect.
        min_sections: Absolute loss required, so tiny titles do not trip it.

    Returns:
        A ``(files, repaired_titles)`` pair.
    """
    if not previous:
        return files, []

    def counts(mapping: dict[str, str]) -> dict[str, int]:
        out: dict[str, int] = {}
        for path in mapping:
            out[_title_of(path)] = out.get(_title_of(path), 0) + 1
        return out

    now, before = counts(files), counts(previous)
    declared_dirs = {f"title-{n:02d}" for n in declared}

    repaired: list[str] = []
    for title, old_count in before.items():
        new_count = now.get(title, 0)
        lost = old_count - new_count
        if lost < min_sections or lost / old_count < min_drop:
            continue
        if title in declared_dirs:
            # OLRC says this title changed, so believe the archive.
            continue
        repaired.append(f"{title} ({old_count} -> {new_count} sections)")
        files = {k: v for k, v in files.items() if _title_of(k) != title}
        files.update({k: v for k, v in previous.items() if _title_of(k) == title})

    return files, repaired


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


def laws_covered(
    point: ReleasePoint, previous: ReleasePoint | None
) -> list[str]:
    """Work out which public laws a release point newly incorporates.

    A release point is "current through PL N, except M". The laws a commit adds
    are therefore those newly in range, *plus* any previously excluded law that
    this point has since picked up -- the out-of-sequence codifications the
    ``not`` suffix records.

    Args:
        point: The release point being committed.
        previous: The preceding release point, or None for the first.

    Returns:
        Law identifiers such as ``["119-4", "119-5"]``, ascending.
    """
    excluded_now = set(point.excludes)

    if previous is None:
        # The first release point is a baseline: its tree is the whole Code as
        # accumulated since 1926, not the effect of the laws in this Congress.
        # Attributing it to those laws would badly misstate what the commit is.
        return []

    if previous.congress != point.congress:
        # A new Congress restarts numbering at 1.
        candidates = set(range(1, point.law_number + 1))
    else:
        candidates = set(range(previous.law_number + 1, point.law_number + 1))
        # Laws the previous point deliberately skipped that are now included.
        candidates |= set(previous.excludes)

    return [
        f"{point.congress}-{n}" for n in sorted(candidates - excluded_now)
    ]


def commit_message(
    point: ReleasePoint,
    section_count: int,
    law_ids: list[str] | None = None,
    attribution: list[str] | None = None,
) -> str:
    """Build the commit message for a release point.

    Args:
        point: The release point.
        section_count: Number of sections in the snapshot.
        law_ids: Public laws this point newly incorporates.
        attribution: Table III trailers naming the US Code sections each law
            touched.

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
    if law_ids:
        lines.append(f"Public laws:   {', '.join(law_ids)}")

    lines += [
        "",
        "A release point closes over several public laws at once, so this commit is",
        "not the effect of a single law.",
        "",
        "The trailers below name where each law's provisions are classified in the US",
        "Code according to OLRC Table III. Note this is PRESENT-DAY classification, not",
        "classification as of this snapshot: PL 113-40 (2013) is listed under Title 54,",
        "which did not exist until PL 113-287 created it in December 2014. The trailers",
        "therefore answer \"where does this law live now\", not \"what did this commit",
        "change\". For the latter, read the diff.",
        "",
        f"Source: {point.xml_url}",
    ]
    if attribution:
        lines += ["", *attribution]
    return "\n".join(lines)


async def seed(
    client: GovInfoClient,
    limit: int | None = None,
    granularity: str = "section",
    repo_path: Path | None = None,
    attribute: bool = True,
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
        attribute: Attach Table III per-law attribution trailers.

    Returns:
        The repository that was built.
    """
    points = await discover(client)
    if limit is not None:
        points = points[:limit]

    index: dict[str, list] = {}
    if attribute:
        from . import table3

        cached = table3.load_index()
        if cached is None:
            index = table3.build_index(await table3.fetch_archive(client))
            table3.cache_index(index)
        else:
            index = cached
        print(f"Table III: {table3.summarise(index)}", flush=True)

    repo = GitRepo(repo_path or config.REPOS_DIR / REPO_NAME)
    repo.init()
    skipped: list[tuple[ReleasePoint, dict[str, str]]] = []
    truncations: list[tuple[ReleasePoint, list[str]]] = []
    damaged_titles: list[tuple[ReleasePoint, dict[str, str]]] = []
    previous_files: dict[str, str] = {}

    for idx, point in enumerate(points):
        if repo.has_tag(point.tag):
            continue
        archive = await fetch_archive(client, point)
        sections, damage = render_archive(archive)

        declared = {str(t) for t in point.titles}
        unrecoverable = {t for t in damage if t in declared}
        if unrecoverable:
            # OLRC says these titles changed, and the archive for them is
            # unusable, so their new text is genuinely unknown. Carrying the old
            # text forward would assert they did not change, which is false.
            skipped.append((point, {t: damage[t] for t in unrecoverable}))
            print(
                f"  [{point.order:>3}] {point.tag:<26} SKIPPED - declared titles damaged: "
                + "; ".join(f"t{t}: {damage[t]}" for t in sorted(unrecoverable)),
                flush=True,
            )
            continue
        if damage:
            # Damaged titles that OLRC does not claim changed: the previous
            # text still stands, so carry it forward rather than lose them.
            damaged_titles.append((point, dict(damage)))
            print(
                f"       damaged but undeclared, carrying forward: "
                + ", ".join(f"t{t}" for t in sorted(damage)),
                flush=True,
            )

        files = (
            to_file_map(sections)
            if granularity == "section"
            else _group_by_chapter(sections)
        )

        for bad_title in damage:
            folder = f"title-{bad_title.zfill(2)}"
            files = {k: v for k, v in files.items() if _title_of(k) != folder}
            files.update(
                {k: v for k, v in previous_files.items() if _title_of(k) == folder}
            )

        files, repaired_titles = repair_truncated_titles(
            files, previous_files, point.titles
        )
        if repaired_titles:
            truncations.append((point, repaired_titles))
            print(
                f"       carried forward undeclared truncated titles: "
                + ", ".join(repaired_titles),
                flush=True,
            )
        previous_files = files
        # A release point is a full snapshot, but only a few hundred sections
        # move between consecutive points, so sync incrementally. Repeals still
        # surface as deletions.
        change = repo.sync_tree(files, manifest_path=repo.path.parent / f".{repo.path.name}.manifest.json")

        previous = points[idx - 1] if idx else None
        law_ids = laws_covered(point, previous)
        trailers = table3_trailers(index, law_ids) if index else None
        repo.commit(
            commit_message(point, len(sections), law_ids, trailers),
            when=point.published,
        )
        repo.tag(point.tag)
        print(
            f"  [{point.order:>3}] {point.tag:<26} "
            f"{len(sections):>6,} sections  "
            f"+{change.written:<5} -{change.removed:<4} of {change.total:,}",
            flush=True,
        )

    if skipped or truncations or damaged_titles:
        _write_gaps(repo, skipped, truncations, damaged_titles)
        repo.commit(
            "Record release points skipped for upstream archive damage\n\n"
            "See GAPS.md. These snapshots are omitted rather than committed with\n"
            "fabricated deletions.",
            when=None,
        )
        print(f"\n{len(skipped)} release point(s) skipped; see GAPS.md")

    return repo


def _write_gaps(
    repo: GitRepo,
    skipped: list[tuple[ReleasePoint, dict[str, str]]],
    truncations: list[tuple[ReleasePoint, list[str]]] | None = None,
    damaged_titles: list[tuple[ReleasePoint, dict[str, str]]] | None = None,
) -> None:
    """Record archive defects in the generated repository.

    Args:
        repo: The repository being built.
        skipped: Release points omitted, with the damage found.
        truncations: Release points where an undeclared title was truncated and
            carried forward from the previous snapshot.
    """
    lines = [
        "# Gaps in this history",
        "",
        "Release points omitted because the official OLRC archive was",
        "structurally damaged. They are skipped rather than committed, because",
        "committing a truncated snapshot would invent a mass deletion followed",
        "by a restoration -- a history that never happened.",
        "",
        "| Release point | Published | Damage |",
        "|---|---|---|",
    ]
    for point, damage in skipped:
        when = point.published.isoformat() if point.published else "unknown"
        detail = "; ".join(f"`{k}` {v}" for k, v in damage.items())
        lines.append(f"| `{point.tag}` | {when} | {detail} |")
    if truncations:
        lines += [
            "",
            "## Titles carried forward",
            "",
            "These archives parse cleanly but are truncated: a title loses a large",
            "share of its sections while OLRC does not list it as affected, and the",
            "content returns in a later release point. `usc46.xml` drops from 912",
            "sections to 576 across 113-44 and 113-45, then returns to 912.",
            "",
            "Committing that verbatim would record hundreds of repeals and then",
            "reverse them, so the previous snapshot's text is carried forward instead.",
            "",
            "| Release point | Published | Titles carried forward |",
            "|---|---|---|",
        ]
        for point, titles in truncations:
            when = point.published.isoformat() if point.published else "unknown"
            lines.append(f"| `{point.tag}` | {when} | {', '.join(titles)} |")

    if damaged_titles:
        lines += [
            "",
            "## Damaged titles carried forward",
            "",
            "The archive for these titles was unusable, but OLRC did not list them as",
            "affected by the release point, so the previous snapshot's text still",
            "stands and was carried forward.",
            "",
            "| Release point | Published | Titles |",
            "|---|---|---|",
        ]
        for point, dmg in damaged_titles:
            when = point.published.isoformat() if point.published else "unknown"
            detail = "; ".join(f"t{k}: {v}" for k, v in sorted(dmg.items()))
            lines.append(f"| `{point.tag}` | {when} | {detail} |")

    lines.append("")
    repo.write("GAPS.md", "\n".join(lines))
