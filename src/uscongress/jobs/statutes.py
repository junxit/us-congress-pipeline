"""Build ``us-congress-statutes`` -- the Statutes at Large, one commit per volume.

**The shape, and why.** One commit per volume, tagged ``stat-001`` through
``stat-137`` for the citation the volume carries, each holding every session law
that volume prints, one file per law under ``volume-NNN/``.

The obvious alternative -- one commit per public law, dated to its approval
date, so ``git log`` becomes the chronology of federal enactment since 1789 --
was considered and rejected. Four reasons, in order of weight:

* **A per-law history would not be a history.** The Statutes at Large is the
  permanent record: a session law is printed as passed and is never amended.
  A later Congress supersedes it; it does not rewrite the page. So no commit
  would ever touch anything a previous commit wrote, and 101,975 commits of pure
  addition is a sorted list, not a history. Diff, blame and revert would all have
  nothing to say.
* **It would make the one meaningful diff inexpressible.** What *does* change
  here is GPO's transcription. Volume 1 was re-digitised on 2025-11-03 and its
  bulk-data directory was rewritten again on 2026-04-09; three volumes carry a
  ``processedDate`` in 2026. A re-digitisation is the only real change this
  corpus ever sees, and per volume it is one new commit whose diff is exactly the
  correction. Per law it would rewrite five hundred commits in the middle of
  history, which git can only express by changing every SHA below them.
* **The chronology is not lost anyway.** Every law carries its own approval date
  in its frontmatter, every commit names the years its volume covers, and the
  volumes are strictly in order -- verified: the last approval date in each of
  the 137 is monotonically increasing. It could not be carried by the commit
  dates in any case: git stores nothing before 1970 and 82 of the volumes close
  before then, which is the one real cost of this corpus living in git at all.
  See :func:`commit_date`.
* **A per-law commit date would assert a publication that did not happen.**
  Volumes 1 to 8 were not published contemporaneously. They were compiled by
  Charles C. Little and James Brown -- the ``dc:publisher`` in volume 1 says so
  in as many words -- under the authorizing act of 3 March 1845. Dating a commit
  1789 would place it 56 years before the text existed in this form.

**Resumption keys on the tag**, exactly as ``uscode.seed`` does, so an
interrupted build restarts at the volume it stopped on rather than rebuilding
what is already there.

Two upstream traps, both measured against the live service:

* **Three volumes need an ``Accept`` header or they do not exist.** ``GET
  /bulkdata/STATUTE/107/STATUTE-107.xml`` with curl's default ``Accept: */*``
  answers **HTTP 200 with 67,225 bytes of "Govinfo Bulkdata Service Error"
  HTML**, while the JSON listing for the same directory advertises 13.7 MB of
  ``application/xml``. Volumes 107, 108 and 109 all do it, reproducibly, and no
  other volume does. ``Accept: application/xml`` returns all three in full. This
  is the same trap as the HTTP 406 the client already absorbs for listings,
  pointed the other way, and it is why every payload is checked before it is
  cached.
* **Volumes 7 and 8 contain no session law at all.** They are the Indian and
  foreign treaty volumes: 13,387 ``<presidentialDoc>`` elements across the
  corpus, and in those two volumes nothing else. A treaty is made by ratification
  rather than by bicameral passage and presentment, so it is a different
  instrument and is out of scope here -- but an unexplained hole at volumes 7 and
  8 reads as a build that failed, so they are recorded in ``GAPS.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .. import config
from ..gitbuild import GitRepo
from ..govinfo import GovInfoClient
from ..statutetext import VolumeRender, render_volume, to_file_map

REPO_NAME = "us-congress-statutes"

#: Collection path in govinfo bulk data.
COLLECTION = "STATUTE"

#: Directory prefix of every law file. README.md, LICENSE and GAPS.md sit on the
#: same branch, so anything counting laws has to filter on this rather than on
#: the size of the tree.
LAW_PREFIX = "volume-"


def _state_path(repo: GitRepo) -> Path:
    """Path of the sidecar recording what each volume yielded.

    Kept outside the repository and beside it, exactly as ``uscode`` keeps its
    tree manifest. ``GAPS.md`` describes the whole corpus, so a resumed build
    that touched three volumes must not rewrite it as though only three volumes
    existed.

    Args:
        repo: The repository being built.

    Returns:
        A JSON path beside the repository directory.
    """
    return repo.path.parent / f".{repo.path.name}.volumes.json"


@dataclass(frozen=True)
class Volume:
    """One Statutes at Large volume as govinfo publishes it.

    Attributes:
        number: Volume number, 1 through 137.
        url: Direct link to the volume's USLM 2.0 XML.
    """

    number: int
    url: str

    @property
    def tag(self) -> str:
        """Git tag for this volume, e.g. ``stat-117``.

        Named for the citation -- *117 Stat.* -- rather than ``volume-117``,
        because ``volume-117`` is also the name of a directory in the tree and
        git refuses an argument that is both: ``git log volume-117`` and ``git
        show volume-117`` fail with *ambiguous argument ... both revision and
        filename*, and ``git diff volume-116 volume-117`` fails the same way.
        The tag is the thing that has to move, since the directory listing is
        what a reader browses first and ``volume-117/`` is unmistakable there.
        """
        return f"stat-{self.number:03d}"


def volume_url(number: int) -> str:
    """Return the bulk-data URL of one volume's XML.

    Args:
        number: Volume number.

    Returns:
        The download URL.
    """
    return f"{config.GOVINFO_BULKDATA}/{COLLECTION}/{number}/{COLLECTION}-{number}.xml"


async def discover(client: GovInfoClient) -> list[Volume]:
    """List every Statutes at Large volume govinfo publishes, oldest first.

    The listing carries one entry per volume plus a ``resources`` folder holding
    ``readme.html`` and ``lockss.html``, which is not a volume and would be
    fetched as ``STATUTE-resources.xml`` if it were not filtered out.

    Args:
        client: HTTP client.

    Returns:
        Volumes in ascending order.
    """
    entries = await client.list_bulkdata(COLLECTION)
    numbers = sorted(int(e.name) for e in entries if e.name.isdigit())
    return [Volume(number=n, url=volume_url(n)) for n in numbers]


class VolumeUnavailable(Exception):
    """A volume's XML could not be downloaded."""


def _cache_path(volume: Volume) -> Path:
    """Local cache path for one volume's XML.

    Args:
        volume: The volume.

    Returns:
        Path under ``data/raw/statutes/``.
    """
    return config.RAW_DIR / "statutes" / f"{COLLECTION}-{volume.number}.xml"


def looks_like_xml(payload: bytes) -> bool:
    """Report whether a payload is the XML document it claims to be.

    Two things have to be tolerated and one has to be caught.

    Tolerated: **a UTF-8 byte-order mark**, which 51 of the 137 volumes carry --
    volume 1 begins ``ef bb bf 3c 3f 78 6d 6c``. ``payload.lstrip()`` does not
    remove it, because a BOM is not whitespace, so the check ``bills._fetch_cached``
    uses would reject half this collection as not XML.

    Caught: **govinfo answering a missing file with a web page at HTTP 200.**
    Volumes 107, 108 and 109 return 67,225 bytes of "Govinfo Bulkdata Service
    Error" HTML unless the request sends ``Accept: application/xml``. Cached
    unchecked, that is a permanently poisoned cache entry for three volumes and
    2,048 laws, and nothing in the build would say so.

    Args:
        payload: The response body.

    Returns:
        True if the payload begins an XML document.
    """
    return payload.removeprefix(b"\xef\xbb\xbf").lstrip()[:512].startswith(b"<?xml")


async def fetch_volume(client: GovInfoClient, volume: Volume) -> bytes:
    """Fetch one volume's XML, caching it on disk.

    The whole collection is 2.3 GB and a published volume changes only when GPO
    re-digitises it, so a second run refetches nothing.

    ``Accept: application/xml`` is sent on every request rather than as a retry
    for the three volumes that need it. The header is harmless on the 134 that do
    not, and a retry would mean the error page is fetched first every time.

    Args:
        client: HTTP client.
        volume: Volume to fetch.

    Returns:
        The raw XML bytes.

    Raises:
        VolumeUnavailable: If govinfo served something that is not XML.
    """
    cached = _cache_path(volume)
    if cached.is_file() and cached.stat().st_size > 0:
        payload = cached.read_bytes()
        if looks_like_xml(payload):
            return payload
        # A poisoned cache entry, from this run's predecessor or from a fetch
        # made before this check existed.
        cached.unlink()

    payload = await client.get_bytes(volume.url, headers={"Accept": "application/xml"})
    if not looks_like_xml(payload):
        raise VolumeUnavailable(
            f"{volume.url} served {len(payload):,} bytes that are not XML "
            "(govinfo answers a bulk-data failure with an HTML error page and "
            "HTTP 200, so the status code is not evidence)"
        )

    cached.parent.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_suffix(".tmp")
    tmp.write_bytes(payload)
    tmp.rename(cached)
    return payload


def _counts(render: VolumeRender) -> dict[str, int]:
    """Count a volume's laws by bucket.

    Args:
        render: What the volume yielded.

    Returns:
        Bucket name to count, largest first.
    """
    counts: dict[str, int] = {}
    for law in render.laws:
        counts[law.bucket] = counts.get(law.bucket, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


#: git will not store a timestamp before the Unix epoch. ``git commit`` exits 128
#: with ``fatal: invalid date format`` on ``1799-03-03``, and ``fast-import``
#: accepts a negative one only for ``git log --date=iso`` to render it as an
#: empty string -- which is worse, because a blank date reads as a broken build.
#: 82 of the 137 volumes close before 1970, so this is most of the corpus.
EPOCH = date(1970, 1, 1)


def commit_date(render: VolumeRender) -> date:
    """Return the timestamp to commit a volume under.

    The real date is not discarded: it is on the commit's subject line, in its
    ``Approved:`` field, and in the ``approved:`` frontmatter of every law in the
    volume. Only the git timestamp is clamped, because git has nowhere to put it.

    Args:
        render: What the volume yielded.

    Returns:
        The volume's last approval date, or the epoch if that is earlier.
    """
    return max(render.latest, EPOCH) if render.latest else EPOCH


def commit_message(volume: Volume, render: VolumeRender) -> str:
    """Build the commit message for one volume.

    Args:
        volume: The volume.
        render: What it yielded.

    Returns:
        The full commit message.
    """
    dates = sorted(law.approved for law in render.laws if law.approved)
    span = (
        f"{dates[0].isoformat()} to {dates[-1].isoformat()}"
        if dates
        else "no approval date recorded upstream"
    )
    congresses = sorted(
        {int(law.congress) for law in render.laws if law.congress.isdigit()}
    )

    # The years go in the subject because for 82 of the 137 volumes the commit's
    # own date cannot: git has no representation for anything before 1970, so
    # `git log --oneline` is the only place the chronology survives.
    years = (
        f" ({dates[0].year})"
        if dates and dates[0].year == dates[-1].year
        else f" ({dates[0].year}-{dates[-1].year})"
        if dates
        else ""
    )
    lines = [
        f"Statutes at Large, volume {volume.number}{years}",
        "",
        f"Volume:   {volume.number}",
        f"Laws:     {len(render.laws):,}",
        *(
            f"  {bucket:<12}{count:>7,}"
            for bucket, count in _counts(render).items()
        ),
        f"Approved: {span}",
    ]
    if congresses:
        lines.append(
            "Congress: "
            + (
                str(congresses[0])
                if len(congresses) == 1
                else f"{congresses[0]}-{congresses[-1]}"
            )
        )
    if render.undated:
        lines.append(f"Undated:  {render.undated:,} (see GAPS.md)")
    if render.presidential:
        lines.append(f"Treaties: {render.presidential:,} omitted (see GAPS.md)")
    if render.repair:
        lines.append(f"Repaired: {render.repair}")

    if render.latest and render.latest < EPOCH:
        lines.append(
            f"Dated:    1970-01-01, not {render.latest.isoformat()} -- git stores "
            "no date before the epoch"
        )

    lines += [
        "",
        "A volume of the Statutes at Large is the permanent record of what one",
        "or more sessions of Congress enacted, printed as passed. Nothing in it",
        "is ever amended -- a later Congress supersedes a law, it does not",
        "rewrite the page -- so a diff between two commits on this branch is not",
        "a change in the law. It is a change in GPO's transcription of it.",
        "",
        "Commit dates are the date of the last law in the volume, not the day the",
        "volume was printed: volumes 1 to 8 were compiled retrospectively by",
        "Charles C. Little and James Brown under the act of 3 March 1845, so no",
        "single printing date would be true of the laws inside them. git stores",
        "no date before 1970, so where that date is earlier the commit is stamped",
        "1970-01-01 and the Dated field above records the real one.",
        "",
        f"Source: {volume.url}",
    ]
    return "\n".join(lines) + "\n"


def _load_state(repo: GitRepo) -> dict[str, dict]:
    """Read the per-volume record written by previous runs.

    Args:
        repo: The repository being built.

    Returns:
        Volume number as a string, to what that volume yielded.
    """
    path = _state_path(repo)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(repo: GitRepo, state: dict[str, dict]) -> None:
    """Persist the per-volume record.

    Args:
        repo: The repository being built.
        state: Volume number as a string, to what that volume yielded.
    """
    path = _state_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, sort_keys=True, indent=1), encoding="utf-8")


async def seed(
    client: GovInfoClient,
    limit: int | None = None,
    repo_path: Path | None = None,
) -> GitRepo:
    """Build the Statutes at Large repository, one commit per volume.

    Resumable and idempotent: a volume whose tag exists is skipped before
    anything is fetched or rendered, so re-running a finished build costs one
    listing call.

    Args:
        client: HTTP client.
        limit: Build only the first N volumes. None builds all 137.
        repo_path: Override the repository location.

    Returns:
        The repository that was built.
    """
    volumes = await discover(client)
    if limit is not None:
        volumes = volumes[:limit]

    repo = GitRepo(repo_path or config.REPOS_DIR / REPO_NAME)
    repo.init()
    state = _load_state(repo)

    print(f"{COLLECTION}: {len(volumes)} volumes listed", flush=True)

    built = skipped = lawless = unavailable = 0
    laws_written = 0

    for volume in volumes:
        if repo.has_tag(volume.tag):
            skipped += 1
            continue

        try:
            payload = await fetch_volume(client, volume)
        except VolumeUnavailable as exc:
            unavailable += 1
            state[str(volume.number)] = {"unavailable": str(exc)}
            print(f"  [{volume.number:>3}] UNAVAILABLE - {exc}", flush=True)
            continue

        render = render_volume(payload, volume.number)
        files = to_file_map(render.laws)
        if len(files) != len(render.laws):
            # to_file_map suffixes genuine duplicate citations rather than
            # dropping them, so this can only mean a law lost its file.
            print(
                f"WARNING: volume {volume.number} rendered {len(render.laws)} laws "
                f"into {len(files)} files",
                flush=True,
            )

        state[str(volume.number)] = {
            "laws": len(render.laws),
            "presidential": render.presidential,
            "undated": render.undated,
            "repair": render.repair,
            "buckets": _counts(render),
        }

        if not render.laws:
            # Volumes 7 and 8 print treaties and nothing else. Committing an
            # empty tree would tag a commit that says nothing, and tagging
            # without committing would point stat-007 at volume 6's tree.
            lawless += 1
            print(
                f"  [{volume.number:>3}] {volume.tag:<12} no session laws - "
                f"{render.presidential:,} treaties and proclamations only",
                flush=True,
            )
            continue

        for path, text in files.items():
            repo.write(path, text)
        made = repo.commit(commit_message(volume, render), when=commit_date(render))
        if not made:
            print(
                f"WARNING: volume {volume.number} wrote {len(files)} files but "
                "git saw no change; not tagging",
                flush=True,
            )
            continue
        repo.tag(volume.tag)
        built += 1
        laws_written += len(files)
        stamp = render.latest.isoformat() if render.latest else "----------"
        print(
            f"  [{volume.number:>3}] {volume.tag:<12} {stamp}  "
            f"{len(files):>6,} laws  "
            + "  ".join(f"{k}={v:,}" for k, v in _counts(render).items()),
            flush=True,
        )
        _save_state(repo, state)

    _save_state(repo, state)
    if _write_gaps(repo, state):
        repo.commit(
            "Record what this repository leaves out\n"
            "\n"
            "Treaties and proclamations, volumes that print nothing else, and\n"
            "the laws GPO records no usable date for. Stated rather than left\n"
            "as an unexplained absence.\n",
            when=None,
        )

    accounted = built + skipped + lawless + unavailable
    print(
        f"\n{repo.path.name}: {built} volumes built ({laws_written:,} laws), "
        f"{skipped} already present, {lawless} with no session laws, "
        f"{unavailable} unavailable",
        flush=True,
    )
    if accounted != len(volumes):
        # Every listed volume must land in exactly one bucket. A mismatch means
        # something was dropped without being counted, which is the one failure
        # mode that looks like success.
        print(
            f"WARNING: {len(volumes) - accounted} volumes unaccounted for",
            flush=True,
        )

    recorded = sum(entry.get("laws", 0) for entry in state.values())
    tracked = len([p for p in repo.list_files("main") if p.startswith(LAW_PREFIX)])
    if recorded and tracked and recorded != tracked:
        # The sidecar counts what the renderer produced; git counts what landed.
        # They are independent, and they must agree.
        print(
            f"WARNING: {recorded:,} laws rendered across all runs but "
            f"{tracked:,} law files on main",
            flush=True,
        )
    return repo

def _write_gaps(repo: GitRepo, state: dict[str, dict]) -> bool:
    """Write ``GAPS.md`` from the record of every volume seen so far.

    Rendered from the sidecar rather than from this run, so a resumed build that
    touched three volumes does not rewrite the document as though only three
    volumes existed. Returns False when nothing changed, so re-running the job
    makes no commit.

    Args:
        repo: The repository being built.
        state: Volume number as a string, to what that volume yielded.

    Returns:
        True if the file was written or changed.
    """
    if not state:
        return False

    lawless = sorted(
        (int(k), v) for k, v in state.items() if v.get("laws") == 0
    )
    unavailable = sorted(
        (int(k), v["unavailable"]) for k, v in state.items() if "unavailable" in v
    )
    repaired = sorted(
        (int(k), v["repair"]) for k, v in state.items() if v.get("repair")
    )
    presidential = sum(v.get("presidential", 0) for v in state.values())
    undated = sum(v.get("undated", 0) for v in state.values())
    volumes = len([v for v in state.values() if "unavailable" not in v])

    lines = [
        "# What this repository leaves out",
        "",
        "Everything below is an upstream fact or a deliberate scope decision,",
        "not a build failure. It is written down because an unexplained absence",
        "reads as a build that quietly went wrong.",
        "",
        "## Treaties, proclamations and executive agreements",
        "",
        "The Statutes at Large prints these alongside the session laws --",
        f"{presidential:,} of them across the {volumes} volumes read so far --",
        "and none of them are here. A treaty is made by Senate ratification and",
        "a proclamation by the President alone; neither is an act of Congress",
        "passed by both chambers and presented for signature, which is what this",
        "repository holds.",
        "",
    ]

    if lawless:
        lines += [
            "### Volumes that print nothing else",
            "",
            f"{len(lawless)} of the volumes read so far contain no session law",
            "at all. They are not missing and they did not fail to build:",
            "volume 7 is the Indian treaties and volume 8 the foreign treaties",
            "and international agreements. They have no commit and no tag,",
            "because a commit with an empty tree would say nothing and a tag",
            "without one would point at the previous volume's text.",
            "",
            "| Volume | Treaties and proclamations |",
            "|---|---|",
            *(
                f"| {number} | {entry.get('presidential', 0):,} |"
                for number, entry in lawless
            ),
            "",
        ]

    if undated:
        lines += [
            "## Laws with no date",
            "",
            f"{undated:,} laws carry no usable date of approval anywhere in the",
            "source: not in `<meta>`, not on the `<approvedDate>` printed in the",
            "margin. They are committed with the rest of their volume and their",
            "frontmatter simply has no `approved:` line, rather than being given",
            "a guessed one.",
            "",
            "A handful carry a date that is recorded but impossible -- volume 32",
            "dates a law to 16 April 1110 and volume 34 dates three to January and",
            "February 1007, in volumes covering 1901-1903 and 1905-1907. Those are",
            "rejected and counted here rather than published as fact.",
            "",
            "A few more are possible but wrong, and those cannot be detected at all.",
            "One resolution in volume 2 is dated 3 March **1845** in a volume that",
            "runs 1799 to 1813, so that commit's subject line reads",
            "*volume 2 (1799-1845)*. The years on a commit are the first and last",
            "dates the volume actually carries, not a correction of them.",
            "",
        ]

    if repaired:
        lines += [
            "## Volumes that needed repair before they would parse",
            "",
            "Government XML is not always well formed. These parsed only after",
            "unmatched end tags were dropped or bare ampersands escaped; no text",
            "was removed.",
            "",
            "| Volume | Repair |",
            "|---|---|",
            *(f"| {number} | {detail} |" for number, detail in repaired),
            "",
        ]

    if unavailable:
        lines += [
            "## Volumes govinfo would not serve",
            "",
            "The bulk-data service answered these with something that is not XML.",
            "It answers a failure with an HTML error page and HTTP 200, so the",
            "status code is not evidence and the payload is checked instead.",
            "",
            "| Volume | What was served |",
            "|---|---|",
            *(f"| {number} | {detail} |" for number, detail in unavailable),
            "",
        ]

    lines += [
        "## Dates before 1970",
        "",
        "**Every commit for a volume that closes before 1970 is timestamped",
        "1970-01-01.** git stores no date earlier than the Unix epoch: `git",
        "commit` refuses `1799-03-03` outright, and writing a negative timestamp",
        "through `fast-import` succeeds only for `git log` to render it as a",
        "blank. That is everything up to volume 82, which is most of this",
        "repository, so most of `git log --date` here is meaningless.",
        "",
        "The real dates are not lost. Each commit's subject line carries the",
        "years the volume covers, its message carries the first and last approval",
        "date in it, and every law carries its own `approved:` date in its",
        "frontmatter. The order of the commits is the order of the volumes.",
        "",
        "## What is not a gap",
        "",
        "Private laws **are** here, under `private/` in each volume. They are not",
        "general law -- a private act relieves one named person or firm -- but",
        "they are law, and in the older volumes they outnumber the public acts.",
        "",
        "Marginal notes are here too, collected under a heading of their own",
        "rather than left in the sentence. In the printed volume they sit in the",
        "margin beside the text, and the source XML puts them inline: mid-word in",
        "many cases, so reproducing that position would splice a note into the",
        "middle of a clause.",
        "",
    ]

    body = "\n".join(lines).rstrip() + "\n"
    existing = repo.path / "GAPS.md"
    if existing.is_file() and existing.read_text(encoding="utf-8") == body:
        return False
    repo.write("GAPS.md", body)
    return True
