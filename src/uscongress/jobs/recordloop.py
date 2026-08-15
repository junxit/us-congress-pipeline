"""The Congressional Record's daily loop.

The bills loop and this one are deliberately separate jobs on separate
schedules, because they fail differently and one of them is the project's
liveness signal. ``update`` rebuilding a measure wrongly is recoverable; the
bills heartbeat going quiet is the failure this project exists to make visible.
Folding the Record into that job would let a Record fetch error take the
heartbeat down with it, so the Record gets a schedule of its own and reports
into the same page. See :func:`uscongress.jobs.update._record_section`.

**This loop is driven by issue date, never by ``lastModified``.** That is not a
simplification, it is the whole design. govinfo re-indexes packages in bulk: on
2026-08-12 it restamped nine already-published CREC days, including two from
2025, with no change to their contents -- 1,469 documents before and 1,469
after. A modification-driven Record loop would have seen those nine, tried to
rebuild days sitting in the middle of a cumulative branch, and force-pushed the
entire history to produce byte-identical trees. Asking which issue days a branch
already holds, and building only what is missing, makes that impossible: the
work is append-only and every push is a fast-forward.

The rebuild path exists for correcting a rendering defect and is reached with
``seed-record --rebuild`` by hand. Nothing scheduled may use it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .. import config
from ..gitbuild import GitRepo
from ..govinfo import GovInfoClient
from . import publish, record, republish
from . import update as update_job


def _days_held(path: Path) -> int:
    """Count the issue days a shard already holds.

    Args:
        path: Repository directory, which need not exist.

    Returns:
        Issue days across both editions, or 0 if there is no repository there
        yet -- the first run on a new Congress builds from nothing.
    """
    if not (path / ".git").is_dir():
        return 0
    repo = GitRepo(path)
    return len(
        record.built_days(repo, record.DAILY) | record.built_days(repo, record.BOUND)
    )


async def run(
    congress: int | None = None,
    *,
    token: str = "",
    publish_changes: bool = True,
    state_path: Path | None = None,
) -> int:
    """Build the current Congress's Record shard and publish what moved.

    Resumable and idempotent by construction: :func:`uscongress.jobs.record.seed`
    skips issue days already committed before fetching anything, so a run on a
    day Congress did not sit costs a listing call and writes nothing.

    Args:
        congress: Congress to build. Defaults to the one sitting today, in UTC,
            so this keeps working on the day the next Congress convenes.
        token: GitHub credential. Without one nothing is pushed.
        publish_changes: Push what the build moved.
        state_path: Override the watermark location.

    Returns:
        Process exit status: 0 on success, 1 if anything failed.
    """
    started = datetime.now(UTC)
    state = update_job.load_record_state(state_path)
    state.last_run = started
    congress = congress or record.congress_of(started.date())
    state.congress = congress

    name = f"{record.REPO_PREFIX}-{congress}"
    path = config.REPOS_DIR / name
    errors: list[str] = []
    days_built = days_present = refs = 0

    try:
        # A scheduled runner is a fresh machine every time and `data/` is
        # gitignored, so without this the job finds no shard, concludes it holds
        # no issue days, and rebuilds the whole Congress from upstream -- a
        # crawl that outruns the workflow timeout and reproduces commits that
        # already exist. Blobless: `built_days` reads trees, never file
        # contents, so the blobs are never needed. The first run of a Congress
        # nobody has created yet finds no remote and builds from nothing, which
        # is correct; the push then fails loudly on the missing repository.
        url = publish.repo_url(name, token)
        if publish.remote_exists(url):
            publish.prepare_all(path, url)

        # Measured either side of the build rather than read from the last run's
        # state, so a shard someone seeded by hand in between is still counted
        # honestly.
        before = _days_held(path)
        async with GovInfoClient() as client:
            await record.seed(client, congress=congress)
        days_present = _days_held(path)
        days_built = max(days_present - before, 0)

        if publish_changes:
            if not token:
                raise RuntimeError("publishing was asked for but GITHUB_TOKEN is unset")
            refs = len(republish.compare(path, name, token).to_push)
            if republish.run([name], token=token, dry_run=False) != 0:
                raise RuntimeError(f"{name}: republish did not land what it pushed")
    except Exception as exc:  # noqa: BLE001 - the heartbeat must record any failure
        errors.append(f"{type(exc).__name__}: {exc}")

    state.last_outcome = "ok" if not errors else "; ".join(errors[:3])
    if not errors:
        state.last_success = started
        state.days_built = days_built
        state.days_present = days_present
        state.refs_published = refs

    # Only the watermark. STATUS.md is rendered by the bills loop, which runs
    # daily and reads this file -- deliberately, and not just to avoid two jobs
    # writing one file. `render_status` fills the bills detail rows from the run
    # that just happened, so re-rendering here, with no run of its own to
    # report, would strip those rows every day and the bills job would put them
    # back two hours later. The Record row therefore lags by up to a day, which
    # a two-day staleness threshold absorbs, and the property that matters is
    # kept: the bills loop redraws that row every morning, so a Record loop that
    # has stopped is visible on a page something else is still writing.
    update_job.save_record_state(state, state_path)

    print(
        f"\nRecord {congress}: {days_built} issue day(s) added, "
        f"{days_present} held, {refs} ref(s) published, "
        f"outcome {state.last_outcome}",
        flush=True,
    )
    return 1 if errors else 0
