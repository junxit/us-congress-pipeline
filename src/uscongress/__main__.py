"""Command-line entry point.

Usage::

    uv run uscongress comps          # snapshot Statute Compilations
    uv run uscongress comps --fresh  # ignore today's manifest and refetch
    uv run uscongress update         # the daily job: rebuild what changed
    uv run uscongress update --check # fail if the daily loop has stopped
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to a job.

    Args:
        argv: Argument vector; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(prog="uscongress", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    comps = subparsers.add_parser("comps", help="snapshot Statute Compilations")
    comps.add_argument(
        "--fresh",
        action="store_true",
        help="ignore today's manifest and refetch every package",
    )

    subparsers.add_parser("index", help="regenerate REPOSITORIES.md")

    boot = subparsers.add_parser(
        "bootstrap",
        help="clone the generated repositories onto a machine that lacks them",
    )
    boot.add_argument("--repos-path", help="override where repositories are placed")
    boot.add_argument(
        "--only",
        nargs="+",
        metavar="NAME",
        help="bootstrap only these repositories",
    )

    subparsers.add_parser(
        "releasepoints", help="list OLRC release points, oldest first"
    )

    seed = subparsers.add_parser("seed-code", help="build the us-congress-code repo")
    seed.add_argument("--limit", type=int, help="build only the oldest N release points")
    seed.add_argument(
        "--granularity",
        choices=("section", "chapter"),
        default="section",
        help="one file per section (default) or per chapter",
    )
    seed.add_argument("--repo-path", help="override the repository location")

    subparsers.add_parser(
        "artifacts", help="write README and LICENSE into every generated repo"
    )

    subparsers.add_parser(
        "check-links", help="verify every link in every generated document resolves"
    )

    seed_comps = subparsers.add_parser(
        "seed-comps", help="build us-congress-comps from the local snapshot store"
    )
    seed_comps.add_argument("--repo-path", help="override the repository location")

    attention = subparsers.add_parser(
        "attention", help="report what currently needs a person, not a schedule"
    )
    attention.add_argument(
        "--check",
        action="store_true",
        help="report what is due and write nothing, like `describe --check`. "
        "Without it the list is also persisted for STATUS.md to render",
    )
    attention.add_argument(
        "--state-path", help="override where the computed list is written"
    )
    attention.add_argument(
        "--announce",
        action="store_true",
        help="open, update or close the single GitHub issue that carries this "
        "list. Off by default so a local run notifies nobody",
    )

    describe = subparsers.add_parser(
        "describe", help="set each repo's GitHub description and topics"
    )
    describe.add_argument(
        "--check",
        action="store_true",
        help="report repos whose description or topics are out of date, change nothing",
    )

    bills = subparsers.add_parser(
        "seed-bills", help="build a us-congress-bills-{congress} repo"
    )
    bills.add_argument(
        "--congress",
        help="Congress number, e.g. 113. Omit for the one sitting today, which "
        "is what a scheduled rebuild wants: a hardcoded number would go stale "
        "on the day the next Congress convenes and rebuild the wrong shard",
    )
    bills.add_argument("--limit", type=int, help="build only the first N measures")
    bills.add_argument("--repo-path", help="override the repository location")
    bills.add_argument(
        "--rebuild",
        action="store_true",
        help="rewrite every branch from its root; needs a force push afterwards",
    )

    statutes = subparsers.add_parser(
        "seed-statutes", help="build the us-congress-statutes repo"
    )
    statutes.add_argument("--limit", type=int, help="build only the first N volumes")
    statutes.add_argument("--repo-path", help="override the repository location")

    record = subparsers.add_parser(
        "seed-record", help="build a us-congress-record-{congress} repo"
    )
    record.add_argument(
        "--congress",
        type=int,
        help="Congress number, e.g. 115. Omit for the one sitting today, which "
        "is what a scheduled run wants: a hardcoded number would go stale on "
        "the day the next Congress convenes and skip it in silence",
    )
    record.add_argument(
        "--limit", type=int, help="build only the first N issue days of each edition"
    )
    record.add_argument("--repo-path", help="override the repository location")
    record.add_argument(
        "--rebuild",
        action="store_true",
        help="rewrite each edition branch from its root; needs a force push afterwards",
    )

    update = subparsers.add_parser(
        "update", help="rebuild whatever changed upstream since the last run"
    )
    update.add_argument(
        "--check",
        action="store_true",
        help="report whether the daily loop is still running, change nothing",
    )
    update.add_argument(
        "--since",
        help="override the watermark for this run, e.g. 2026-08-01 or "
        "2026-08-01T00:00:00Z",
    )
    update.add_argument(
        "--no-code",
        action="store_true",
        help="skip the US Code release-point check; rebuild bills only",
    )
    update.add_argument(
        "--dry-run",
        action="store_true",
        help="list what changed upstream and stop, writing nothing",
    )
    update.add_argument(
        "--publish",
        action="store_true",
        help="fetch the affected branches from GitHub and push them back; needs "
        "GITHUB_TOKEN. Without it the job only touches repositories already here",
    )
    update.add_argument(
        "--state-path", help="override where the watermark is read and written"
    )
    update.add_argument(
        "--status-path",
        help="override where the heartbeat is written. Without this a local run "
        "overwrites the tracked STATUS.md, which the next scheduled run commits",
    )

    update_record = subparsers.add_parser(
        "update-record",
        help="build the current Congress's Record shard and publish what moved",
    )
    update_record.add_argument(
        "--congress",
        type=int,
        help="Congress number. Omit for the one sitting today, which is what a "
        "scheduled run wants",
    )
    update_record.add_argument(
        "--publish",
        action="store_true",
        help="push what the build moved; needs GITHUB_TOKEN. Without it the "
        "shard is built locally and nothing leaves this machine",
    )
    update_record.add_argument(
        "--state-path",
        help="override where the watermark is read and written. This job does "
        "not write STATUS.md at all; the daily bills loop renders it from here",
    )

    republish = subparsers.add_parser(
        "republish",
        help="push branches a local rebuild changed; the force push "
        "`seed-bills --rebuild` asks for",
    )
    republish.add_argument(
        "--congress",
        action="append",
        help="Congress number, repeatable. Omit for every bills repository",
    )
    republish.add_argument(
        "--repo",
        action="append",
        help="repository name, repeatable, e.g. us-congress-statutes",
    )
    republish.add_argument(
        "--dry-run",
        action="store_true",
        help="report what differs from the remote and push nothing",
    )

    args = parser.parse_args(argv)

    if args.command == "comps":
        from .jobs import comps as comps_job

        asyncio.run(comps_job.snapshot(resume=not args.fresh))
        return 0

    if args.command == "index":
        from .jobs import index as index_job

        index_job.write()
        return 0

    if args.command == "bootstrap":
        from pathlib import Path

        from .jobs import bootstrap as bootstrap_job

        results = bootstrap_job.run(
            repos_dir=Path(args.repos_path) if args.repos_path else None,
            only=args.only,
        )
        bootstrap_job.report(results)
        # A repository named in the registry but not yet created is the ordinary
        # state of a planned phase, so it is reported rather than failed on.
        return 0

    if args.command == "releasepoints":
        from .govinfo import GovInfoClient
        from .jobs import uscode

        async def _list() -> None:
            async with GovInfoClient() as client:
                points = await uscode.discover(client)
            for point in points:
                when = point.published.isoformat() if point.published else "----------"
                flag = " (current)" if point.is_current else ""
                print(f"{point.order:>4}  {when}  {point.tag}{flag}")
            print(f"\n{len(points)} release points")

        asyncio.run(_list())
        return 0

    if args.command == "seed-code":
        from pathlib import Path

        from .govinfo import GovInfoClient
        from .jobs import uscode

        async def _seed() -> None:
            async with GovInfoClient() as client:
                repo = await uscode.seed(
                    client,
                    limit=args.limit,
                    granularity=args.granularity,
                    repo_path=Path(args.repo_path) if args.repo_path else None,
                )
            commits = repo.commit_count()
            size = repo.size_bytes(repack=True)
            print(
                f"\n{commits} commits, {size / 1e6:.0f} MB packed "
                f"({size / max(commits, 1) / 1e6:.1f} MB per commit)"
            )

        asyncio.run(_seed())
        return 0

    if args.command == "artifacts":
        from .jobs import artifacts as artifacts_job

        changed = artifacts_job.write_all()
        print(f"\n{len(changed)} repositories updated")
        return 0

    if args.command == "check-links":
        from .jobs import links as links_job

        return 1 if links_job.report() else 0

    if args.command == "seed-comps":
        from pathlib import Path

        from .jobs import compsrepo as compsrepo_job

        repo = compsrepo_job.seed(
            Path(args.repo_path) if args.repo_path else None
        )
        size = repo.size_bytes(repack=True)
        print(f"\n{repo.commit_count()} snapshots, {size / 1e6:.0f} MB packed")
        return 0

    if args.command == "attention":
        from pathlib import Path

        from . import config
        from .govinfo import GovInfoClient
        from .jobs import attention as attention_job

        async def _attention() -> int:
            # Without a key the upstream questions go unasked, and the check
            # says so rather than counting them as answered no.
            try:
                config.govinfo_api_key()
            except RuntimeError:
                due = await attention_job.check()
            else:
                async with GovInfoClient() as client:
                    due = await attention_job.check(client)
            if not args.check:
                attention_job.save(
                    due, Path(args.state_path) if args.state_path else None
                )
                if args.announce:
                    print(f"  {attention_job.announce(due)}", flush=True)
            return 1 if attention_job.report(due) else 0

        return asyncio.run(_attention())

    if args.command == "describe":
        from .jobs import describe as describe_job

        if args.check:
            return 1 if describe_job.check() else 0
        changed = describe_job.apply_all()
        print(f"\n{len(changed)} repositories updated")
        return 0

    if args.command == "seed-bills":
        from datetime import UTC, datetime
        from pathlib import Path

        from .govinfo import GovInfoClient
        from .jobs import bills as bills_job
        from .jobs import record as record_job

        # UTC rather than local time, so a scheduled run and a run from a laptop
        # west of Greenwich agree on which Congress is sitting.
        congress = args.congress or str(
            record_job.congress_of(datetime.now(UTC).date())
        )

        async def _seed_bills() -> None:
            async with GovInfoClient() as client:
                repo = await bills_job.seed(
                    client,
                    congress=congress,
                    limit=args.limit,
                    repo_path=Path(args.repo_path) if args.repo_path else None,
                    rebuild=args.rebuild,
                )
            branches = len(repo.branches())
            size = repo.size_bytes(repack=True)
            print(
                f"\n{branches} branches, {size / 1e6:.0f} MB packed "
                f"({size / max(branches, 1) / 1e3:.0f} KB per branch)"
            )

        asyncio.run(_seed_bills())
        return 0

    if args.command == "seed-statutes":
        from pathlib import Path

        from .govinfo import GovInfoClient
        from .jobs import statutes as statutes_job

        async def _seed_statutes() -> None:
            async with GovInfoClient() as client:
                repo = await statutes_job.seed(
                    client,
                    limit=args.limit,
                    repo_path=Path(args.repo_path) if args.repo_path else None,
                )
            laws = len(
                [p for p in repo.list_files("main") if p.startswith(statutes_job.LAW_PREFIX)]
            )
            size = repo.size_bytes(repack=True)
            print(
                f"\n{repo.commit_count()} commits, {laws:,} laws, "
                f"{size / 1e6:.0f} MB packed"
            )

        asyncio.run(_seed_statutes())
        return 0

    if args.command == "seed-record":
        from datetime import UTC, datetime
        from pathlib import Path

        from .govinfo import GovInfoClient
        from .jobs import record as record_job

        # UTC rather than local time, so a scheduled run and a run from a laptop
        # west of Greenwich agree on which Congress is sitting.
        congress = args.congress or record_job.congress_of(datetime.now(UTC).date())

        async def _seed_record() -> None:
            async with GovInfoClient() as client:
                repo = await record_job.seed(
                    client,
                    congress=congress,
                    limit=args.limit,
                    repo_path=Path(args.repo_path) if args.repo_path else None,
                    rebuild=args.rebuild,
                )
            days = sum(
                len(record_job.built_days(repo, edition))
                for edition in (record_job.DAILY, record_job.BOUND)
            )
            size = repo.size_bytes(repack=True)
            print(
                f"\n{days} issue days, {size / 1e6:.0f} MB packed "
                f"({size / max(days, 1) / 1e3:.0f} KB per day)"
            )

        asyncio.run(_seed_record())
        return 0

    if args.command == "update":
        from datetime import UTC, datetime

        from pathlib import Path

        from .govinfo import GovInfoClient
        from .jobs import update as update_job

        state_path = Path(args.state_path) if args.state_path else None
        status_path = Path(args.status_path) if args.status_path else None

        if args.check:
            return update_job.check(state_path)

        since = None
        if args.since:
            try:
                since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
            except ValueError:
                parser.error(f"--since is not a timestamp: {args.since}")
            since = since.replace(tzinfo=UTC) if since.tzinfo is None else since

        async def _update() -> int:
            async with GovInfoClient() as client:
                if args.dry_run:
                    state = update_job.load_state(state_path)
                    window = since or state.since
                    packages, unplaceable = await update_job.changed_packages(
                        client, window
                    )
                    print(
                        f"since {window.strftime('%Y-%m-%d %H:%M UTC')}: "
                        f"{len(packages)} measures changed"
                    )
                    by_congress: dict[str, int] = {}
                    for package in packages:
                        by_congress[package.congress] = (
                            by_congress.get(package.congress, 0) + 1
                        )
                    for congress in sorted(by_congress, key=int):
                        print(f"  {congress}: {by_congress[congress]}")
                    if unplaceable:
                        print(f"  unplaceable: {', '.join(unplaceable)}")
                    return 0

                token = ""
                if args.publish:
                    from . import config

                    token = config.github_token()

                result = await update_job.run(
                    client,
                    since=since,
                    state_path=state_path,
                    code=not args.no_code,
                    token=token,
                    publish=args.publish,
                    status_path=status_path,
                )

            print(
                f"\n{result.measures_changed:,} branches rewritten across "
                f"{len(result.rebuilt)} Congress(es); "
                f"{result.unchanged:,} already current"
            )
            if result.pushed:
                print(
                    f"published {sum(len(v) for v in result.pushed.values()):,} refs"
                )
            if result.release_points:
                print(f"new release points: {', '.join(result.release_points)}")
            if result.pending_release_points:
                print(
                    "release points not built here: "
                    + ", ".join(result.pending_release_points)
                )
            if result.errors:
                # The watermark has deliberately not advanced, so the next run
                # covers this window again. Say that, rather than leaving a
                # non-zero exit to be interpreted.
                print(f"\n{len(result.errors)} failure(s); watermark not advanced:")
                for message in result.errors[:10]:
                    print(f"  {message}")
                return 1
            return 0

        return asyncio.run(_update())

    if args.command == "update-record":
        from pathlib import Path

        from . import config
        from .jobs import recordloop as recordloop_job

        # Reported through the heartbeat rather than argparse, for the same
        # reason `update --publish` does it: a run that exits before writing
        # STATUS.md leaves no public trace that it ran at all.
        return asyncio.run(
            recordloop_job.run(
                args.congress,
                token=config.github_token() if args.publish else "",
                publish_changes=args.publish,
                state_path=Path(args.state_path) if args.state_path else None,
            )
        )

    if args.command == "republish":
        from . import config
        from .jobs import bills as bills_job
        from .jobs import republish as republish_job

        names: list[str] = []
        if args.congress:
            names += [f"{bills_job.REPO_PREFIX}-{c}" for c in args.congress]
        if args.repo:
            names += list(args.repo)
        if not names:
            names = config.built_shards(f"{bills_job.REPO_PREFIX}-{{congress}}")

        token = "" if args.dry_run else config.github_token()
        if not args.dry_run and not token:
            parser.error(
                "republish needs GITHUB_TOKEN to push; use --dry-run to compare only"
            )
        return republish_job.run(names, token=token, dry_run=args.dry_run)

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
