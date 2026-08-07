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
    bills.add_argument("--congress", required=True, help="Congress number, e.g. 113")
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
        "--congress", required=True, type=int, help="Congress number, e.g. 115"
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

    args = parser.parse_args(argv)

    if args.command == "comps":
        from .jobs import comps as comps_job

        asyncio.run(comps_job.snapshot(resume=not args.fresh))
        return 0

    if args.command == "index":
        from .jobs import index as index_job

        index_job.write()
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

    if args.command == "describe":
        from .jobs import describe as describe_job

        if args.check:
            return 1 if describe_job.check() else 0
        changed = describe_job.apply_all()
        print(f"\n{len(changed)} repositories updated")
        return 0

    if args.command == "seed-bills":
        from pathlib import Path

        from .govinfo import GovInfoClient
        from .jobs import bills as bills_job

        async def _seed_bills() -> None:
            async with GovInfoClient() as client:
                repo = await bills_job.seed(
                    client,
                    congress=args.congress,
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
        from pathlib import Path

        from .govinfo import GovInfoClient
        from .jobs import record as record_job

        async def _seed_record() -> None:
            async with GovInfoClient() as client:
                repo = await record_job.seed(
                    client,
                    congress=args.congress,
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
                    import os

                    token = os.environ.get("GITHUB_TOKEN", "").strip()
                    if not token:
                        parser.error("--publish needs GITHUB_TOKEN in the environment")

                result = await update_job.run(
                    client,
                    since=since,
                    state_path=state_path,
                    code=not args.no_code,
                    token=token,
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

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
