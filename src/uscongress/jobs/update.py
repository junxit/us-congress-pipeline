"""The daily job: bring the generated repositories up to date, and be seen doing it.

Every predecessor of this project died the same way. `divegeek/uscode` and
`unitedstates/uscode` are archived; a wave of new mirrors appeared in March and
April 2026 and every one went silent within weeks. None of them announced it.
The upstream feeds stayed healthy the whole time -- govinfo publishes daily --
so what failed was the loop, not the data.

That shapes this module more than the fetching does.

**The failure mode is not an error, it is nothing happening.** A disabled
schedule, an expired token, a workflow quietly dropped after 60 days of repo
inactivity: none of these raise anything. Nobody gets an email. So this job does
not rely on being able to report its own failure. It writes :data:`STATUS_PATH`
on every run, and that file goes stale on its own if the job stops running at
all. "Last updated 3 weeks ago" on a public front page is legible to a stranger
who knows nothing about this project, which is the only signal that survives the
job's own death. :func:`check` turns the same fact into an exit code.

**Only what changed is rebuilt.** govinfo's incremental collections endpoint
reports 1,183 BILLSTATUS packages modified in the six days to 2026-08-07, about
170 a day, against roughly 18,000 to re-poll a Congress blindly.

**A changed measure has its branch rewritten from the root, not appended to.**
BILLSTATUS is one present-day snapshot of a whole measure, and ``metadata.md``
is filtered to each version's date, so a correction upstream can change what an
*old* commit should say. Appending cannot express that. Rewriting can, and it
costs nothing when nothing changed: the render is a pure function of the
document and the commit stamps come from the version dates, so an unchanged
measure rebuilds to byte-identical commits with the same SHAs and git records no
change at all. That is what makes running this twice a no-op.

**The watermark advances only after success.** A crash therefore re-fetches
rather than skips, and the window is widened by an hour on every read so a clock
difference between here and govinfo cannot open a gap. Re-processing is free, by
the paragraph above; missing a bill is not.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

from .. import config
from ..gitbuild import GitRepo
from ..govinfo import GovInfoClient
from . import bills, publish, uscode

#: Where the watermark lives. Deliberately *not* under ``data/``, which is
#: gitignored: a scheduled runner is a fresh machine every time, so a watermark
#: it cannot carry between runs is not a watermark. Committing it costs a few
#: hundred bytes, makes ``git log state/update.json`` a history of the loop, and
#: lets ``update --check`` answer from any clone rather than only from the
#: machine that happened to run the job.
STATE_PATH = config.STATE_DIR / "update.json"

#: The heartbeat, in the pipeline repository rather than under ``data/``,
#: because its whole purpose is to be visible to someone who is not running it.
STATUS_PATH = config.REPO_ROOT / "STATUS.md"

#: How far back to look on the very first run, when there is no watermark. A
#: daily job that has never run has no history to protect, and a week is enough
#: to catch anything in flight without re-polling a Congress.
FIRST_RUN_DAYS = 7

#: Re-ask for an hour either side of the watermark. govinfo stamps
#: ``lastModified`` on its own clock, and a build that is idempotent has nothing
#: to lose by asking twice.
OVERLAP = timedelta(hours=1)

#: How long a run may be missing before the heartbeat counts as stopped. Two
#: days, not one: a single missed daily run is a hiccup, two is a pattern.
STALE_AFTER = timedelta(days=2)

#: Largest page govinfo serves from the collections endpoint.
PAGE_SIZE = 1000

#: ``BILLSTATUS-119hr7283`` -> congress, measure type, number.
_PACKAGE_ID = re.compile(r"^BILLSTATUS-(\d+)([a-z]+)(\d+)$")

#: The ``offsetMark`` in a ``nextPage`` URL, left percent-encoded on purpose;
#: see :func:`changed_packages`.
_OFFSET_MARK = re.compile(r"[?&]offsetMark=([^&]*)")


@dataclass(frozen=True)
class Package:
    """One BILLSTATUS package govinfo reports as modified.

    Attributes:
        package_id: govinfo identifier, e.g. ``BILLSTATUS-119hr7283``.
        congress: Congress number.
        kind: Measure type, e.g. ``hr``.
        number: Measure number.
        last_modified: Upstream modification timestamp, as reported.
    """

    package_id: str
    congress: str
    kind: str
    number: str
    last_modified: str

    @property
    def filename(self) -> str:
        """BILLSTATUS filename, which is also the cache key."""
        return f"{self.package_id}.xml"

    @property
    def url(self) -> str:
        """Bulk-data URL of the document.

        Derived rather than listed. The listing endpoint returns every measure
        in a Congress -- 10,038 for House bills of the 119th alone -- to learn
        URLs that are entirely predictable from the identifier.
        """
        return (
            f"{config.GOVINFO_BULKDATA}/BILLSTATUS/{self.congress}/{self.kind}/"
            f"{self.filename}"
        )

    @property
    def branch(self) -> str:
        """Branch this measure occupies, e.g. ``hr-7283``."""
        return f"{self.kind}-{self.number}"


def parse_package_id(package_id: str) -> tuple[str, str, str] | None:
    """Split a BILLSTATUS package identifier into Congress, type and number.

    Args:
        package_id: Identifier such as ``BILLSTATUS-119hr7283``.

    Returns:
        A ``(congress, kind, number)`` triple, or None if it is not a
        BILLSTATUS identifier this pipeline can place.
    """
    match = _PACKAGE_ID.match(package_id.strip())
    if not match:
        return None
    congress, kind, number = match.groups()
    if kind not in bills.TYPES:
        return None
    return congress, kind, number


async def changed_packages(
    client: GovInfoClient, since: datetime, page_size: int = PAGE_SIZE
) -> tuple[list[Package], list[str]]:
    """List every BILLSTATUS package modified since a timestamp.

    ``offsetMark`` is not optional. Omitting it answers HTTP 400, and the token
    is base64, so it arrives percent-encoded and is passed on exactly as
    received: decoding it would turn a ``+`` in the token into a space by the
    time govinfo read it back.

    Args:
        client: HTTP client.
        since: Lower bound; govinfo compares it against ``lastModified``.
        page_size: Packages per request.

    Returns:
        A ``(packages, unplaceable)`` pair, where ``unplaceable`` holds any
        package identifier that could not be mapped to a measure. They are
        returned rather than dropped so the caller can report them: a package
        silently skipped is indistinguishable from one that never existed.
    """
    start = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = f"collections/BILLSTATUS/{start}"

    packages: list[Package] = []
    unplaceable: list[str] = []
    seen: set[str] = set()
    offset = "*"

    while offset:
        payload = await client.api_json(path, offsetMark=offset, pageSize=page_size)
        batch = payload.get("packages") or []
        for entry in batch:
            package_id = str(entry.get("packageId") or "")
            # Deduplicate before deciding what the identifier is, not after: a
            # measure touched twice inside one window is listed twice, and the
            # final page is served twice by the loop below. Deduplicating only
            # the placeable half reported the same unplaceable package once per
            # sighting.
            if package_id in seen:
                continue
            seen.add(package_id)
            parsed = parse_package_id(package_id)
            if parsed is None:
                unplaceable.append(package_id)
                continue
            congress, kind, number = parsed
            packages.append(
                Package(
                    package_id=package_id,
                    congress=congress,
                    kind=kind,
                    number=number,
                    last_modified=str(entry.get("lastModified") or ""),
                )
            )

        next_page = str(payload.get("nextPage") or "")
        match = _OFFSET_MARK.search(next_page)
        # govinfo keeps serving a nextPage on the final page, pointing back at
        # the mark just consumed. Without this the loop never ends.
        offset = match.group(1) if match and batch and unquote(match.group(1)) != unquote(offset) else ""

    return packages, unplaceable


@dataclass
class State:
    """What the last runs of this job recorded.

    Attributes:
        last_success: When the job last completed without error.
        last_run: When it last started, successful or not.
        last_outcome: ``ok``, or a short description of what went wrong.
        measures: Branches rewritten on the last successful run, by Congress.
        release_points: Tags added to ``us-congress-code`` on that run.
        pending_release_points: Release points published upstream that this
            corpus does not carry yet.
        unplaceable: Package identifiers the last successful run could not map
            to a measure.
        checked_since: The window the last successful run actually asked for.
    """

    last_success: datetime | None = None
    last_run: datetime | None = None
    last_outcome: str = ""
    measures: dict[str, list[str]] = field(default_factory=dict)
    release_points: list[str] = field(default_factory=list)
    pending_release_points: list[str] = field(default_factory=list)
    unplaceable: list[str] = field(default_factory=list)
    checked_since: datetime | None = None

    @property
    def since(self) -> datetime:
        """The window to ask govinfo for, widened for clock skew.

        Returns:
            The last success minus :data:`OVERLAP`, or
            :data:`FIRST_RUN_DAYS` back when there has never been one.
        """
        if self.last_success is None:
            return datetime.now(UTC) - timedelta(days=FIRST_RUN_DAYS)
        return self.last_success - OVERLAP

    @property
    def stale_for(self) -> timedelta | None:
        """How long past :data:`STALE_AFTER` the last success is.

        Returns:
            The overshoot, or None if the heartbeat is current. A job that has
            never succeeded is stale by definition rather than exempt.
        """
        if self.last_success is None:
            return timedelta.max
        overdue = (datetime.now(UTC) - self.last_success) - STALE_AFTER
        return overdue if overdue > timedelta(0) else None


def _parse_stamp(value: object) -> datetime | None:
    """Read an ISO timestamp back as an aware UTC datetime.

    Args:
        value: The stored value, of unknown type.

    Returns:
        The timestamp, or None if absent or unreadable.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def load_state(path: Path | None = None) -> State:
    """Read the watermark.

    A missing, empty or corrupt file reads as "never run", which makes the job
    do more work rather than less. The opposite default would have a damaged
    watermark silently skip everything published while it was broken.

    Args:
        path: Override the watermark location.

    Returns:
        The recorded state.
    """
    target = path or STATE_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return State()
    if not isinstance(payload, dict):
        return State()
    return State(
        last_success=_parse_stamp(payload.get("last_success")),
        last_run=_parse_stamp(payload.get("last_run")),
        last_outcome=str(payload.get("last_outcome") or ""),
        measures={
            str(k): [str(b) for b in v]
            for k, v in (payload.get("measures") or {}).items()
        },
        release_points=[str(t) for t in (payload.get("release_points") or [])],
        pending_release_points=[
            str(t) for t in (payload.get("pending_release_points") or [])
        ],
        unplaceable=[str(t) for t in (payload.get("unplaceable") or [])],
        checked_since=_parse_stamp(payload.get("checked_since")),
    )


def save_state(state: State, path: Path | None = None) -> Path:
    """Write the watermark.

    Args:
        state: State to persist.
        path: Override the watermark location.

    Returns:
        The path written.
    """
    target = path or STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    def stamp(when: datetime | None) -> str:
        return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if when else ""

    target.write_text(
        json.dumps(
            {
                "last_success": stamp(state.last_success),
                "last_run": stamp(state.last_run),
                "last_outcome": state.last_outcome,
                "measures": state.measures,
                "release_points": state.release_points,
                "pending_release_points": state.pending_release_points,
                "unplaceable": state.unplaceable,
                "checked_since": stamp(state.checked_since),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


@dataclass
class Result:
    """What one run of the job did.

    Attributes:
        started: When the run began.
        since: The window asked of govinfo.
        listed: Packages govinfo reported as modified.
        rebuilt: Branches whose commits actually changed, by Congress.
        unchanged: Measures rebuilt to identical commits, so nothing to publish.
        textless: Measures with no usable text, which get no branch.
        unreadable: Measures whose BILLSTATUS could not be parsed.
        unplaceable: Package identifiers that map to no measure.
        release_points: Tags added to ``us-congress-code``.
        pending_release_points: Release points OLRC has published that are not
            in the repository yet. Reported rather than built: see
            :func:`update_code`.
        pushed: Branches published to GitHub, by Congress.
        errors: Failures that mean the watermark must not advance.
    """

    started: datetime
    since: datetime
    listed: int = 0
    rebuilt: dict[str, list[str]] = field(default_factory=dict)
    unchanged: int = 0
    textless: int = 0
    unreadable: int = 0
    unplaceable: list[str] = field(default_factory=list)
    release_points: list[str] = field(default_factory=list)
    pending_release_points: list[str] = field(default_factory=list)
    pushed: dict[str, list[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the run may advance the watermark."""
        return not self.errors

    @property
    def measures_changed(self) -> int:
        """How many branches were rewritten."""
        return sum(len(v) for v in self.rebuilt.values())


async def update_bills(
    client: GovInfoClient,
    packages: list[Package],
    result: Result,
    token: str = "",
) -> None:
    """Rebuild every measure govinfo reports as changed.

    Each branch is rewritten from its root rather than appended to; see the
    module docstring for why, and why that is still a no-op when nothing moved.

    What counts as changed is read back out of git rather than taken from this
    function's own bookkeeping. A build that reported success while writing
    nothing is the one failure that looks exactly like a quiet day.

    Args:
        client: HTTP client.
        packages: Measures to rebuild.
        result: Run record, updated in place.
        token: When set, fetch the affected branches from GitHub before
            rebuilding and push them afterwards. This is how the job runs
            somewhere that holds no copy of the corpus; see
            :mod:`uscongress.jobs.publish` for why it fetches only the branches
            it is about to touch.
    """
    by_congress: dict[str, list[Package]] = {}
    for package in packages:
        by_congress.setdefault(package.congress, []).append(package)

    for congress in sorted(by_congress, key=int):
        batch = by_congress[congress]
        name = f"{bills.REPO_PREFIX}-{congress}"
        path = config.REPOS_DIR / name

        if token:
            repo = publish.prepare(
                path, publish.repo_url(name, token), [p.branch for p in batch]
            )
        else:
            repo = GitRepo(path)
            repo.init()
        before = repo.ref_map()

        print(f"  {congress}: {len(batch)} measures reported changed", flush=True)

        textless = unreadable = failed = 0
        for start in range(0, len(batch), bills.BATCH):
            window = batch[start : start + bills.BATCH]
            built = await asyncio.gather(
                *(
                    bills._build_measure(  # noqa: SLF001
                        client, congress, p.filename, p.url, refresh=True
                    )
                    for p in window
                ),
                return_exceptions=True,
            )
            renders: list[tuple[bills.Measure, list]] = []
            for package, outcome in zip(window, built):
                if isinstance(outcome, BaseException):
                    # One bad measure must not lose the rest of the run, but it
                    # must still hold the watermark back: advancing past a
                    # measure that failed is how a gap becomes permanent.
                    failed += 1
                    result.errors.append(
                        f"{package.package_id}: {type(outcome).__name__}: {outcome}"
                    )
                    continue
                if outcome is None:
                    unreadable += 1
                    continue
                measure, rendered = outcome
                if not rendered:
                    textless += 1
                    continue
                renders.append((measure, rendered))

            if not renders:
                continue
            with repo.fast_import(replace=True) as stream:
                for measure, rendered in renders:
                    for version, stamp, files in rendered:
                        stream.commit(
                            measure.branch,
                            files,
                            bills.commit_message(measure, version),
                            stamp,
                        )

        after = repo.ref_map()
        asked = {p.branch for p in batch}
        moved = {b for b, sha in after.items() if before.get(b) != sha}
        changed = sorted(moved & asked)
        if changed:
            result.rebuilt[congress] = changed

        # Every listed measure lands in exactly one bucket, and nothing outside
        # the list may move. A build that reported 10,617 branches for 10,637
        # measures once looked entirely successful with three separate bugs
        # behind the gap, so the arithmetic is checked against git rather than
        # against this function's own account of itself.
        current = len(batch) - len(changed) - textless - unreadable - failed
        result.unchanged += current
        result.textless += textless
        result.unreadable += unreadable

        stray = sorted(moved - asked)
        if stray:
            # `main` carries the README, the license and GAPS.md, and the
            # measure rebuild regenerates none of them. Anything moving here
            # means a branch was written that nothing asked for.
            result.errors.append(
                f"{name}: {len(stray)} branches changed that the run did not "
                "ask for: " + ", ".join(stray[:10])
            )
            print(f"  WARNING: {congress}: {len(stray)} stray branches", flush=True)
        if current < 0:
            result.errors.append(
                f"{congress}: buckets do not sum for {len(batch)} measures"
            )
            print(f"  WARNING: {congress}: buckets do not sum", flush=True)

        detail = f"{len(changed)} branches rewritten, {current} already current"
        if textless:
            detail += f", {textless} with no text"
        if unreadable:
            detail += f", {unreadable} unreadable"
        if failed:
            detail += f", {failed} failed"
        print(f"  {congress}: {detail}", flush=True)

        if token and changed and not publish.remote_exists(publish.repo_url(name)):
            # The 120th Congress convening is the ordinary way to reach this:
            # govinfo starts reporting a shard nothing has created yet. Say what
            # is actually wrong, and hold the watermark so the measures are not
            # skipped once the repository exists.
            result.errors.append(
                f"{name}: {len(changed)} branches built but the repository does "
                f"not exist on GitHub. Create it, run `uscongress artifacts` and "
                "`uscongress describe`, then re-run; the watermark has not moved."
            )
            print(f"  WARNING: {name}: no such repository on GitHub", flush=True)
        elif token and changed:
            report = publish.push(path, publish.repo_url(name, token), changed)
            result.pushed[congress] = report.pushed
            print(
                f"  {congress}: {len(report.pushed)} refs published "
                f"in {report.attempts} attempt(s)",
                flush=True,
            )
            if report.missing:
                # git reports success for refs that did not land, so this is
                # read back from the remote. Failing the run holds the watermark
                # so tomorrow tries these again.
                # git's own words come first. "N refs did not land" on its own
                # sends the reader hunting a transient failure when the cause
                # may be flatly stated in the output that was thrown away.
                result.errors.append(
                    f"{name}: {len(report.missing)} refs did not land"
                    + (f" — {report.errors[-1]}" if report.errors else "")
                    + "; first: "
                    + ", ".join(report.missing[:10])
                )
            if not publish.default_branch_is_main(name):
                # Cosmetic rather than a data failure, so it does not hold the
                # watermark -- but left alone it lands every visitor on
                # `hconres-1` instead of the README, so it is said out loud.
                print(
                    f"  WARNING: {name}: default branch is not `main`",
                    flush=True,
                )


async def update_code(client: GovInfoClient, result: Result) -> None:
    """Build any US Code release point that is not already tagged.

    ``seed`` is already resumable on exactly this test, so the local path is a
    full ``seed()`` with no limit: it walks the release points, skips every tag
    that exists, and does nothing at all when there is nothing new.

    **Where the repository is not present, new release points are reported and
    not built.** This is deliberate. Unlike a bill branch, a release point is a
    full snapshot of ~60,000 files built against the preceding one -- the
    truncation guard that keeps ``usc46.xml`` from recording 336 repeals and
    reversing them two commits later compares a release point against its
    predecessor's rendered tree. Building one without the repository would drop
    that guard, and a 90 MB archive rendered into a 2.4 GB history is not daily
    work anyway. OLRC publishes a release point every few weeks, so the honest
    behavior is to make the backlog visible on the heartbeat and let it be
    built where the history already is.

    Args:
        client: HTTP client.
        result: Run record, updated in place.
    """
    repo = GitRepo(config.REPOS_DIR / uscode.REPO_NAME)
    points = await uscode.discover(client)
    # OLRC lists 386 release points but only 383 are distinct: pl-113-165,
    # pl-115-95not91 and pl-115-117not91not96not97 each appear twice at adjacent
    # positions. Reporting the listing count would contradict the repository,
    # which has 383 tags and says so.
    distinct = len({p.tag for p in points})

    if not (repo.path / ".git").is_dir():
        published = publish.remote_tags(publish.repo_url(uscode.REPO_NAME))
        missing = [p for p in points if p.tag not in published]
        result.pending_release_points = [p.tag for p in missing]
        if missing:
            print(
                f"  us-congress-code: {len(missing)} release point(s) published "
                "upstream and not built here: " + ", ".join(p.tag for p in missing),
                flush=True,
            )
        else:
            print(
                f"  us-congress-code: current at {distinct} release points",
                flush=True,
            )
        return

    missing = [p for p in points if not repo.has_tag(p.tag)]
    if not missing:
        print(f"  us-congress-code: current at {distinct} release points", flush=True)
        return

    print(
        f"  us-congress-code: {len(missing)} new release point(s): "
        + ", ".join(p.tag for p in missing),
        flush=True,
    )
    await uscode.seed(client)
    result.release_points = [p.tag for p in missing if repo.has_tag(p.tag)]
    still_missing = [p.tag for p in missing if not repo.has_tag(p.tag)]
    if still_missing:
        # Not an error: seed() deliberately skips a release point whose archive
        # is damaged and records it in GAPS.md. Saying so keeps the difference
        # between "nothing new" and "new but unbuildable" visible.
        result.pending_release_points = still_missing
        print(
            "  us-congress-code: not built, see GAPS.md: " + ", ".join(still_missing),
            flush=True,
        )


async def run(
    client: GovInfoClient,
    since: datetime | None = None,
    state_path: Path | None = None,
    status_path: Path | None = None,
    code: bool = True,
    token: str = "",
    publish: bool = False,
) -> Result:
    """Run one daily update.

    Args:
        client: HTTP client.
        since: Override the watermark for this run only. The watermark itself is
            still advanced on success, so a manual backfill does not leave the
            next scheduled run reaching further back than it needs to.
        state_path: Override the watermark location.
        status_path: Override the heartbeat location.
        code: Also check for new US Code release points.
        token: GitHub credential. When set, the affected branches are fetched
            from GitHub before the rebuild and pushed afterwards, so the job can
            run somewhere that holds no copy of the corpus.
        publish: Whether publishing was asked for. Kept separate from ``token``
            so that asking to publish *without* a credential is a failure this
            run records, rather than one it exits on.

    Returns:
        What the run did.
    """
    state = load_state(state_path)
    started = datetime.now(UTC)
    window = since or state.since
    result = Result(started=started, since=window)

    if publish and not token:
        # This used to be an argparse error, which exits before anything is
        # written -- so the run failed, the workflow failed, and STATUS.md went
        # on saying "Outcome | ok" from the previous day. A public heartbeat
        # that reads healthy through a failed run is the exact thing this file
        # exists to prevent, and it took a real CI run with an empty secret to
        # find it. Recorded and returned instead, so the heartbeat tells the
        # truth and the watermark stays put.
        result.errors.append(
            "--publish was requested but GITHUB_TOKEN is empty: the credential "
            "is missing or the repository secret holds no value"
        )
        _finish(state, result, state_path, status_path)
        return result

    print(
        f"update: asking govinfo for everything modified since "
        f"{window.strftime('%Y-%m-%d %H:%M UTC')}",
        flush=True,
    )

    try:
        packages, unplaceable = await changed_packages(client, window)
    except Exception as exc:  # noqa: BLE001 - the run failed; say so and stop
        result.errors.append(f"listing changed packages: {type(exc).__name__}: {exc}")
        _finish(state, result, state_path, status_path)
        return result

    result.listed = len(packages)
    result.unplaceable = unplaceable
    print(f"  {len(packages)} BILLSTATUS packages changed", flush=True)
    if unplaceable:
        print(
            f"  WARNING: {len(unplaceable)} packages could not be placed: "
            + ", ".join(unplaceable[:5]),
            flush=True,
        )

    if packages:
        try:
            await update_bills(client, packages, result, token=token)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"rebuilding measures: {type(exc).__name__}: {exc}")

    if code:
        try:
            await update_code(client, result)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"checking release points: {type(exc).__name__}: {exc}")

    _finish(state, result, state_path, status_path)
    return result


def _finish(
    state: State,
    result: Result,
    state_path: Path | None,
    status_path: Path | None,
) -> None:
    """Persist the watermark and the heartbeat.

    The watermark advances only when nothing failed, so a crash re-fetches
    rather than skips. The heartbeat is written either way -- a run that failed
    is exactly the run whose outcome most needs recording.

    Args:
        state: State as loaded at the start of the run.
        result: What the run did.
        state_path: Override the watermark location.
        status_path: Override the heartbeat location.
    """
    state.last_run = result.started
    state.last_outcome = "ok" if result.ok else "; ".join(result.errors[:3])
    if result.ok:
        state.last_success = result.started
        state.checked_since = result.since
        state.measures = dict(result.rebuilt)
        state.release_points = list(result.release_points)
        state.pending_release_points = list(result.pending_release_points)
        state.unplaceable = list(result.unplaceable)
    save_state(state, state_path)
    write_status(state, result, status_path)


def render_status(state: State, result: Result | None = None) -> str:
    """Render the heartbeat.

    Written for someone who has never seen this project and is deciding whether
    the data can be trusted. The date comes first and is absolute, because the
    question being answered is "is this still alive", and a relative age
    computed at render time would itself go stale on the page.

    Args:
        state: State after the run.
        result: The run just completed, if there was one.

    Returns:
        The full document.
    """
    def stamp(when: datetime | None) -> str:
        return when.strftime("%Y-%m-%d %H:%M UTC") if when else "never"

    healthy = state.stale_for is None
    lines = [
        "# Status",
        "",
        f"**Last successful update — {stamp(state.last_success)}**",
        "",
        "This file is written by `uv run uscongress update`, which runs daily. It is",
        "here because the way a project like this dies is not with an error: a",
        "disabled schedule or an expired token raises nothing and notifies nobody.",
        "So the signal is inverted. Nothing has to fire for you to notice a problem —",
        "this date simply stops moving, and a stale date is visible to anyone who",
        "reads this page.",
        "",
        f"If that date is more than {STALE_AFTER.days} days old, the loop has stopped.",
        "",
        "| | |",
        "|---|---|",
        f"| **Heartbeat** | {'current' if healthy else '**stale**'} |",
        f"| Last run attempted | {stamp(state.last_run)} |",
        f"| Outcome | {state.last_outcome or 'never run'} |",
    ]

    if result is not None:
        lines += [
            f"| Window asked of govinfo | since {stamp(result.since)} |",
            f"| Measures govinfo reported modified | {result.listed:,} |",
            f"| Branches rewritten | {result.measures_changed:,} |",
            f"| Rebuilt to the commit already published | {result.unchanged:,} |",
        ]
        if result.textless:
            lines.append(f"| Modified but still carrying no text | {result.textless:,} |")
        if result.unreadable:
            lines.append(f"| Unreadable upstream | {result.unreadable:,} |")

    lines.append("")

    changed = state.measures
    if changed:
        lines += [
            "## Measures updated on the last successful run",
            "",
            "| Congress | Branches | Measures |",
            "|---|---|---|",
        ]
        for congress in sorted(changed, key=int):
            branches = changed[congress]
            shown = ", ".join(f"`{b}`" for b in branches[:12])
            if len(branches) > 12:
                shown += f", and {len(branches) - 12:,} more"
            lines.append(f"| {congress} | {len(branches):,} | {shown} |")
        lines.append("")
    elif state.last_success is not None:
        # Two different quiet days, and they should not read the same. Congress
        # not sitting is one thing; govinfo restamping hundreds of measures whose
        # content is unchanged is another, and reporting the second as "nothing
        # happened" hides the fact that the job did real work and found nothing.
        rebuilt = result.unchanged if result is not None else 0
        if rebuilt:
            lines += [
                f"No branch changed. {rebuilt:,} measures were rebuilt from freshly",
                "fetched upstream records and came out as the commits already",
                "published, so there was nothing to write. That is the ordinary",
                "result of a day on which nothing moved, and is not the same as the",
                "job having failed — the date above would show that.",
                "",
            ]
        else:
            lines += [
                "No measure changed on the last successful run. That is an ordinary",
                "result — Congress does not sit every day — and is not the same as the",
                "job having failed, which the date above would show.",
                "",
            ]

    if state.release_points:
        lines += [
            "## US Code release points added",
            "",
            *(f"- `{tag}`" for tag in state.release_points),
            "",
        ]

    if state.pending_release_points:
        pending = state.pending_release_points
        lines += [
            "## US Code release points not built yet",
            "",
            f"OLRC has published {len(pending)} release point(s) that "
            "`us-congress-code` does not carry:",
            "",
            *(f"- `{tag}`" for tag in pending),
            "",
            "A release point is a full snapshot of ~60,000 files built against the",
            "one before it — the guard that stops a truncated archive recording",
            "hundreds of repeals and reversing them two commits later compares the",
            "two trees — so it is built where that history already is, with",
            "`uv run uscongress seed-code`, rather than by the daily job. This",
            "backlog is stated rather than left to be noticed.",
            "",
        ]

    if state.unplaceable:
        lines += [
            "## Packages that could not be placed",
            "",
            "govinfo listed these as modified and this pipeline could not map them",
            "to a measure, so nothing was built for them:",
            "",
            *(f"- `{package}`" for package in state.unplaceable[:20]),
            "",
            "They do not fail the run. Holding the watermark for a package that can",
            "never be placed would freeze every other measure behind it, so the run",
            "goes on and the gap is stated here instead — where it stays visible",
            "until it is either fixed or explained.",
            "",
        ]

    lines += [
        "## What this does not cover",
        "",
        "Only measures govinfo reports as modified are rebuilt, and only the",
        "repositories this project has already built. A Congress that has finished",
        "legislating never changes again, so the shards below the current one are",
        "expected to sit still; see [`REPOSITORIES.md`](REPOSITORIES.md) for what",
        "exists.",
        "",
        "Generated — do not edit by hand.",
        "",
    ]
    return "\n".join(lines)


def write_status(
    state: State, result: Result | None = None, path: Path | None = None
) -> Path:
    """Write the heartbeat to disk.

    Args:
        state: State after the run.
        result: The run just completed, if there was one.
        path: Override the destination.

    Returns:
        The path written.
    """
    target = path or STATUS_PATH
    target.write_text(render_status(state, result), encoding="utf-8")
    return target


def check(state_path: Path | None = None) -> int:
    """Report whether the daily loop is still running.

    Mirrors ``check-links`` and ``describe --check``: it exits non-zero on
    drift, so it can be run by something that is not this job and does not
    depend on this job being able to report its own death.

    Args:
        state_path: Override the watermark location.

    Returns:
        0 if the heartbeat is current, 1 if it is not.
    """
    state = load_state(state_path)
    overdue = state.stale_for

    if state.last_success is None:
        print(
            "update has never completed successfully; the daily loop is not running",
            flush=True,
        )
        return 1
    if overdue is None:
        age = datetime.now(UTC) - state.last_success
        print(
            f"last successful update {_ago(age)} "
            f"({state.last_success.strftime('%Y-%m-%d %H:%M UTC')})",
            flush=True,
        )
        if state.last_outcome != "ok":
            print(f"  last run did not succeed: {state.last_outcome}", flush=True)
        return 0

    print(
        f"STALE: last successful update was "
        f"{_ago(datetime.now(UTC) - state.last_success)}, "
        f"{_span(overdue)} past the {STALE_AFTER.days}-day threshold",
        flush=True,
    )
    if state.last_outcome and state.last_outcome != "ok":
        print(f"  last outcome: {state.last_outcome}", flush=True)
    return 1


def _span(span: timedelta) -> str:
    """Render a duration.

    Args:
        span: The duration.

    Returns:
        A short phrase such as ``3 days`` or ``4 hours``.
    """
    if span >= timedelta.max - timedelta(days=1):
        return "for ever"
    hours = span.total_seconds() / 3600
    if hours < 1:
        return f"{int(span.total_seconds() // 60)} minutes"
    if hours < 48:
        return f"{int(hours)} hours"
    return f"{int(hours // 24)} days"


def _ago(span: timedelta) -> str:
    """Render how long ago something happened.

    Args:
        span: How long ago.

    Returns:
        A short phrase such as ``3 days ago`` or ``4 hours ago``.
    """
    if span >= timedelta.max - timedelta(days=1):
        return "never"
    return f"{_span(span)} ago"
