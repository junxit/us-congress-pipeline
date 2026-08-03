"""Command-line entry point.

Usage::

    uv run uscongress comps          # snapshot Statute Compilations
    uv run uscongress comps --fresh  # ignore today's manifest and refetch
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

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
